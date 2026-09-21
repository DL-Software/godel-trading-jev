"""A local preview of the enriched Godel news API.

Proxies the real `GET /v1/news-items` and `GET /v1/news-item-stream` from api.godelterminal.com, and adds a
`companies` array to every item: one linked company per tagged symbol with a stable id, a name,
a link confidence, a relevance score, and the price at publication. It also serves a preview
`GET /v1/prices`.

Everything the real API will compute is *mocked* here:

- **relevance** comes from a mention heuristic (name in the title beats name in the lead beats
  name anywhere beats nothing), not from an entity model;
- **confidence** is high when we know the company's name and it is mentioned, low otherwise;
- **prices** are synthetic: a deterministic base price per symbol and a seeded random walk in
  five-minute buckets, so a position marked later moves. They are not market data.

Run it with `godel-jev-trader mock` and point the tool at it with `GODEL_API_URL=http://127.0.0.1:8090`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

UPSTREAM = os.environ.get("GODEL_UPSTREAM_URL", "https://api.godelterminal.com").rstrip("/")

# Symbol -> "Name; alias; alias". The real API has a full instrument master; this is enough to make
# the demo honest for names that show up on the feed.
NAMES: dict[str, str] = {
    "AAPL": "Apple", "NVDA": "NVIDIA; Nvidia", "MSFT": "Microsoft", "GOOG": "Alphabet; Google",
    "GOOGL": "Alphabet; Google", "AMZN": "Amazon", "META": "Meta Platforms; Meta; Facebook",
    "TSLA": "Tesla", "AMD": "Advanced Micro Devices; AMD", "JPM": "JPMorgan Chase; JPMorgan",
    "XOM": "ExxonMobil; Exxon", "BA": "Boeing", "PFE": "Pfizer", "DKNG": "DraftKings",
    "WBD": "Warner Bros. Discovery; Warner Bros", "FUBO": "fuboTV; Fubo", "COIN": "Coinbase",
    "HAS": "Hasbro", "SFNC": "Simmons First National; Simmons Bank", "SAABB": "Saab", "SAABY": "Saab",
    "KOS": "Kosmos Energy; Kosmos", "COS": "Coniagas Battery Metals; Coniagas", "AMPY": "Amplify Energy",
    "NYM": "Narryer Metals", "BCP": "Banco Comercial Português; Millennium bcp; BCP", "G": "Genpact",
    "H": "Hyatt Hotels; Hyatt", "SMMT": "Summit Therapeutics", "EXEL": "Exelixis", "POWL": "Powell Industries",
    "MSM": "MSC Industrial", "AWI": "Armstrong World Industries", "HMB": "H&M; Hennes & Mauritz",
    "SERV": "Elevate Service; Serve Robotics", "SI": "Silvergate", "BTG": "B2Gold", "STX": "Seagate",
    "4863": "Telekom Malaysia", "005930": "Samsung Electronics; Samsung", "066570": "LG Electronics",
    "JPXGY": "Japan Exchange Group", "NMG": "Nation Media Group; NMG", "NBB": "Norman Broadbent",
    "GKI": "Grupa Kapitalowa Immobile; Immobile", "BEA": "Belmont Resources; Belmont",
}


def instrument_id(symbol: str) -> str:
    return "inst_" + hashlib.sha1(symbol.upper().encode()).hexdigest()[:10]


def _names(symbol: str) -> tuple[str | None, list[str]]:
    raw = NAMES.get(symbol.upper())
    if not raw:
        return None, []
    parts = [p.strip() for p in raw.split(";")]
    return parts[0], parts


def _mentions(term: str, text: str) -> bool:
    return bool(term) and re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text) is not None


_TITLE_STOP = re.compile(
    r"\s+(?:\(|Stock|Shares?|Sees|Experiences?|trades?|is |are |has |announces?|reports?|posts?|to |and |"
    r"atualiza|anuncia|aprueba|approves|-|–|—|:|,|\d)",
    re.IGNORECASE,
)


def guess_name(title: str) -> str | None:
    """Company name from the front of an auto-generated headline, e.g. 'Zuma Resources Ltd. Stock ...'.

    Stands in for the instrument master the real API has. Returns None when the title does not
    start with a plausible proper name.
    """
    head = _TITLE_STOP.split(title, maxsplit=1)[0].strip(" .'\"")
    words = head.split()
    if not 1 <= len(words) <= 5 or len(head) > 48:
        return None
    if sum(w[0].isupper() for w in words if w[0].isalpha()) < max(1, len(words) - 1):
        return None
    return head


def link(symbol: str, title: str, content: str, n_symbols: int = 1) -> dict:
    """Mock relevance and confidence for one tagged symbol."""
    name, aliases = _names(symbol)
    known = name is not None
    t, c = title.lower(), content.lower()
    lead = c[:600]
    if known and any(_mentions(a, t) for a in aliases):
        rel, conf = 0.93, 0.96
    elif _mentions(symbol, t):
        rel, conf = 0.85, 0.96 if known else 0.75
    elif known and any(_mentions(a, lead) for a in aliases):
        rel, conf = 0.72, 0.96
    elif known and any(_mentions(a, c) for a in aliases):
        rel, conf = 0.55, 0.96
    elif _mentions(symbol, c):
        rel, conf = 0.45, 0.96 if known else 0.6
    elif known:
        rel, conf = 0.12, 0.82
    elif n_symbols <= 2 and (guessed := guess_name(title)):
        # Unknown to our table, but the tagger attached only this symbol and the headline
        # leads with a company name: a real instrument master would resolve it.
        name, rel, conf = guessed, 0.82, 0.72
    else:
        rel, conf = 0.15, 0.30
    if not known and rel > 0.4 and n_symbols <= 2:
        name = name or guess_name(title)
    rng = random.Random(f"{symbol}|{title}")
    rel = min(0.99, max(0.01, rel + rng.uniform(-0.04, 0.04)))
    return {"id": instrument_id(symbol), "symbol": symbol, "name": name or symbol,
            "confidence": round(conf, 2), "relevance": round(rel, 2)}


def _epoch(at: str) -> float:
    return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()


def mock_price(symbol: str, at: str) -> dict:
    """Synthetic price for `symbol` at time `at`. Deterministic; moves in 5-minute buckets."""
    h = int(hashlib.sha1(symbol.upper().encode()).hexdigest()[:8], 16)
    base = math.exp(math.log(3) + (h / 0xFFFFFFFF) * (math.log(600) - math.log(3)))
    bucket = int(_epoch(at) // 300)
    rng = random.Random(f"{symbol}:{bucket}")
    change = rng.gauss(0, 1.8)
    if rng.random() < 0.08:
        change += rng.choice([-1, 1]) * rng.uniform(5, 20)
    change = round(max(-40.0, min(60.0, change)), 2)
    return {"last": round(base * (1 + change / 100), 2), "changePct": change, "asOf": at}


def enrich(item: dict) -> dict:
    companies = []
    symbols = item.get("symbols") or ()
    for s in symbols:
        c = link(s, item.get("title", ""), item.get("content", ""), len(symbols))
        c["price"] = mock_price(s, item["createdAt"])
        companies.append(c)
    companies.sort(key=lambda c: -c["relevance"])
    return {**item, "companies": companies}


class Handler(BaseHTTPRequestHandler):
    server_version = "godel-mock/0.1"

    def log_message(self, fmt, *args):  # quieter than the default
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _auth(self) -> dict:
        auth = self.headers.get("Authorization")
        return {"Authorization": auth} if auth else {}

    def _json(self, status: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if status < 400 else "application/problem+json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/v1/news-item-stream":
            return self._stream(url.query)
        if url.path == "/v1/news-items":
            r = httpx.get(f"{UPSTREAM}{url.path}", params=url.query, headers=dict(self._auth(), Accept="application/json"), timeout=30)
            if r.status_code != 200:
                return self._json(r.status_code, r.json())
            return self._json(200, [enrich(i) for i in r.json()])
        if url.path == "/v1/prices":
            if not self._auth():
                return self._json(401, {"title": "Unauthorized", "status": 401, "errorCode": "unauthenticated"})
            symbols = [s for s in parse_qs(url.query).get("symbols", [""])[0].split(",") if s]
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return self._json(200, [{"symbol": s.upper(), **mock_price(s, now)} for s in symbols])
        if url.path == "/actuator/health":
            return self._json(200, {"status": "UP", "mock": True, "upstream": UPSTREAM})
        return self._json(404, {"title": "Not Found", "status": 404})

    def _stream(self, query: str) -> None:
        headers = dict(self._auth(), Accept="text/event-stream")
        if self.headers.get("Last-Event-ID"):
            headers["Last-Event-ID"] = self.headers["Last-Event-ID"]
        with httpx.stream("GET", f"{UPSTREAM}/v1/news-item-stream", params=query, headers=headers,
                          timeout=httpx.Timeout(30, read=None)) as r:
            if r.status_code != 200:
                r.read()
                return self._json(r.status_code, r.json())
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for line in r.iter_lines():
                    if line.startswith("data:"):
                        payload = line[5:].lstrip()
                        line = "data: " + json.dumps(enrich(json.loads(payload)), ensure_ascii=False)
                    self.wfile.write((line + "\n").encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return


def serve(port: int = 8090, host: str = "127.0.0.1") -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"godel mock API on http://{host}:{port}  (upstream {UPSTREAM})")
    print(f"  export GODEL_API_URL=http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8090)
