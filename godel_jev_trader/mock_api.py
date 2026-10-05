"""A local preview of relevance and link confidence on the Godel news API.

Proxies the real `GET /v1-beta/news-items` and `GET /v1-beta/news-item-stream` from
api.godelterminal.com, and adds two fields to every linked instrument the API returns:

- **relevance**, the probability the company is what the item is about, from a mention heuristic
  (name in the title beats name in the lead beats name anywhere beats nothing), not from an
  entity model;
- **confidence**, the probability the link itself is right: high when the company is mentioned,
  lower when it is not.

Names, tickers, and prices are the API's own. Items with no linked instruments pass through as
they are.

Run it with `godel-jev-trader mock` and point the tool at it with `GODEL_API_URL=http://127.0.0.1:8090`.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx

UPSTREAM = os.environ.get("GODEL_UPSTREAM_URL", "https://api.godelterminal.com").rstrip("/")

# Ticker -> extra names the press uses, beyond the instrument's own name.
ALIASES: dict[str, str] = {
    "NVDA": "Nvidia", "GOOG": "Alphabet; Google", "GOOGL": "Alphabet; Google", "META": "Meta; Facebook",
    "AMD": "AMD", "JPM": "JPMorgan", "XOM": "Exxon", "WBD": "Warner Bros", "FUBO": "Fubo",
    "SFNC": "Simmons Bank", "KOS": "Kosmos", "COS": "Coniagas", "BCP": "Millennium bcp",
    "HMB": "H&M", "005930": "Samsung",
}

_SUFFIX = re.compile(
    r"[\s,]+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|sa|ag|nv|se|asa|ab|oyj|spa|bhd|"
    r"holdings?|group|class [a-z]|s\.a|n\.v|s\.p\.a)\.?$",
    re.IGNORECASE,
)


def aliases(name: str, ticker: str | None) -> list[str]:
    """The instrument's name, the name without corporate suffixes, and any known extras."""
    out = [name]
    short = name
    while (stripped := _SUFFIX.sub("", short).strip()) and stripped != short:
        short = stripped
        out.append(short)
    if ticker and ticker.upper() in ALIASES:
        out += [a.strip() for a in ALIASES[ticker.upper()].split(";")]
    return out


def _mentions(term: str, text: str) -> bool:
    return bool(term) and re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text) is not None


def link(instrument: dict, title: str, content: str) -> dict:
    """Mock relevance and confidence for one linked instrument."""
    ticker = instrument.get("ticker") or ""
    names = aliases(instrument["name"], ticker)
    t, c = title.lower(), content.lower()
    lead = c[:600]
    if any(_mentions(a, t) for a in names):
        rel, conf = 0.93, 0.96
    elif _mentions(ticker, t):
        rel, conf = 0.85, 0.96
    elif any(_mentions(a, lead) for a in names):
        rel, conf = 0.72, 0.96
    elif any(_mentions(a, c) for a in names):
        rel, conf = 0.55, 0.96
    elif _mentions(ticker, c):
        rel, conf = 0.45, 0.90
    else:
        rel, conf = 0.12, 0.82
    rng = random.Random(f"{instrument.get('symbol') or instrument['name']}|{title}")
    rel = min(0.99, max(0.01, rel + rng.uniform(-0.04, 0.04)))
    return {**instrument, "confidence": round(conf, 2), "relevance": round(rel, 2)}


def enrich(item: dict) -> dict:
    title, content = item.get("title", ""), item.get("content", "")
    instruments = [link(i, title, content) for i in item.get("instruments") or ()]
    instruments.sort(key=lambda i: -i["relevance"])
    return {**item, "instruments": instruments}


class Handler(BaseHTTPRequestHandler):
    server_version = "godel-mock/0.2"

    def log_message(self, fmt, *args):  # quieter than the default
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _auth(self) -> dict:
        auth = self.headers.get("Authorization")
        return {"Authorization": auth} if auth else {}

    def _json(self, status: int, body, headers: dict | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if status < 400 else "application/problem+json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _relay_error(self, r: httpx.Response) -> None:
        try:
            body = r.json()
        except ValueError:
            body = {"title": r.reason_phrase, "status": r.status_code, "detail": r.text[:1000]}
        keep = {k: r.headers[k] for k in ("Retry-After",) if k in r.headers}
        self._json(r.status_code, body, keep)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/v1-beta/news-item-stream":
            return self._stream(url.query)
        if url.path == "/v1-beta/news-items":
            r = httpx.get(f"{UPSTREAM}{url.path}", params=url.query, headers=dict(self._auth(), Accept="application/json"), timeout=30)
            if r.status_code != 200:
                return self._relay_error(r)
            return self._json(200, [enrich(i) for i in r.json()])
        if url.path == "/actuator/health":
            return self._json(200, {"status": "UP", "mock": True, "upstream": UPSTREAM})
        return self._json(404, {"title": "Not Found", "status": 404})

    def _stream(self, query: str) -> None:
        headers = dict(self._auth(), Accept="text/event-stream")
        if self.headers.get("Last-Event-ID"):
            headers["Last-Event-ID"] = self.headers["Last-Event-ID"]
        with httpx.stream("GET", f"{UPSTREAM}/v1-beta/news-item-stream", params=query, headers=headers,
                          timeout=httpx.Timeout(30, read=None)) as r:
            if r.status_code != 200:
                r.read()
                return self._relay_error(r)
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
