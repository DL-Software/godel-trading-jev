"""Godel public news API client: recent items and the live SSE stream.

Docs: https://developers.godelterminal.com

Items carry `instruments`: the linked instruments the API resolved for the item, one per company,
each with a stable id, name, ticker, and the (delayed) price at publication. Items from an API
that does not send them have an empty tuple, and the tool falls back to asking Jev who the item
is about. The `companies` shape of the local mock (`godel-jev-trader mock`), which adds relevance
and link confidence, is still read.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Iterable, Iterator

import httpx

DEFAULT_URL = "https://api.godelterminal.com"


@dataclass(frozen=True)
class Price:
    last: float
    change_pct: float       # since previous close
    as_of: str

    @property
    def trend(self) -> str:
        """A label Jev can read. Numbers stay in code; the model sees words."""
        p = self.change_pct
        if p >= 5:
            return "up_strong"
        if p >= 1:
            return "up"
        if p <= -5:
            return "down_strong"
        if p <= -1:
            return "down"
        return "flat"


@dataclass(frozen=True)
class LinkedCompany:
    symbol: str
    name: str
    id: str | None = None
    confidence: float | None = None   # P(the link to this instrument is correct)
    relevance: float | None = None    # P(the company is what the item is about)
    price: Price | None = None

    @property
    def key(self) -> str:
        return self.id or self.symbol

    @classmethod
    def from_json(cls, d: dict) -> "LinkedCompany":
        """The mock's `companies` entry."""
        p = d.get("price")
        return cls(
            symbol=d["symbol"],
            name=d.get("name") or d["symbol"],
            id=d.get("id"),
            confidence=d.get("confidence"),
            relevance=d.get("relevance"),
            price=Price(p["last"], p["changePct"], p["asOf"]) if p else None,
        )

    @classmethod
    def from_instrument(cls, d: dict) -> "LinkedCompany":
        """The API's `instruments` entry: `{id, name, ticker?, price?: {value, change?, changePercent?, asOf}}`."""
        p = d.get("price")
        return cls(
            symbol=d.get("ticker") or d["name"],
            name=d["name"],
            id=str(d["id"]),
            price=Price(p["value"], p.get("changePercent") or 0.0, p["asOf"]) if p else None,
        )


@dataclass(frozen=True)
class NewsItem:
    id: str
    title: str
    content: str
    created_at: str
    symbols: tuple[str, ...]
    companies: tuple[LinkedCompany, ...] = ()

    @classmethod
    def from_json(cls, d: dict) -> "NewsItem":
        return cls(
            id=d["id"],
            title=d["title"],
            content=d.get("content") or "",
            created_at=d["createdAt"],
            symbols=tuple(d.get("tickers") or d.get("symbols") or ()),
            companies=tuple(LinkedCompany.from_instrument(c) for c in d.get("instruments") or ())
            or tuple(LinkedCompany.from_json(c) for c in d.get("companies") or ()),
        )


class GodelNews:
    """Thin client over `GET /v1/news-items`, `GET /v1/news-item-stream`, and (preview) `GET /v1/prices`."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get("GODEL_API_KEY")
        if not self.api_key:
            raise SystemExit("GODEL_API_KEY is not set. Get a key at https://dev.godelterminal.com/api/keys")
        self.base_url = (base_url or os.environ.get("GODEL_API_URL") or DEFAULT_URL).rstrip("/")
        self._headers = {"Authorization": f"Bearer {self.api_key}"}

    def recent(self, symbols: Iterable[str] = (), limit: int = 50) -> list[NewsItem]:
        params: dict[str, str | int] = {"limit": limit}
        symbols = list(symbols)
        if symbols:
            params["tickers"] = ",".join(symbols)
        r = httpx.get(f"{self.base_url}/v1/news-items", params=params, headers=dict(self._headers, Accept="application/json"), timeout=30)
        r.raise_for_status()
        return [NewsItem.from_json(d) for d in r.json()]

    def prices(self, symbols: Iterable[str]) -> dict[str, Price]:
        """Current prices by symbol. Returns {} if the API has no price endpoint (yet)."""
        symbols = list(symbols)
        if not symbols:
            return {}
        r = httpx.get(f"{self.base_url}/v1/prices", params={"symbols": ",".join(symbols)}, headers=self._headers, timeout=30)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return {d["symbol"]: Price(d["last"], d["changePct"], d["asOf"]) for d in r.json()}

    def stream(self, symbols: Iterable[str] = (), last_event_id: str | None = None) -> Iterator[NewsItem]:
        """Yield items from the SSE stream indefinitely, resuming after disconnects.

        The server's `id:` line is a cursor; sending it back as `Last-Event-ID` replays anything
        missed while we were away. Items are de-duplicated by their own `id` field.
        """
        symbols = list(symbols)
        params = {"tickers": ",".join(symbols)} if symbols else {}
        seen: dict[str, None] = {}
        backoff = 1.0
        while True:
            headers = dict(self._headers, Accept="text/event-stream")
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            try:
                with httpx.stream(
                    "GET",
                    f"{self.base_url}/v1/news-item-stream",
                    params=params,
                    headers=headers,
                    timeout=httpx.Timeout(30, read=None),
                ) as r:
                    r.raise_for_status()
                    backoff = 1.0
                    for event_id, event, data in _sse_frames(r.iter_lines()):
                        if event_id:
                            last_event_id = event_id
                        if event != "news" or not data:
                            continue
                        item = NewsItem.from_json(json.loads(data))
                        if item.id in seen:
                            continue
                        seen[item.id] = None
                        if len(seen) > 5000:
                            seen.pop(next(iter(seen)))
                        yield item
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise SystemExit(f"Godel API refused the key: {e.response.text}") from e
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except httpx.HTTPError:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)


def _sse_frames(lines: Iterable[str]) -> Iterator[tuple[str | None, str | None, str | None]]:
    """Parse text/event-stream lines into (id, event, data) frames."""
    event_id = event = None
    data: list[str] = []
    for line in lines:
        if line == "":
            if data or event or event_id:
                yield event_id, event, "\n".join(data) if data else None
            event_id = event = None
            data = []
        elif line.startswith(":"):
            continue  # keep-alive comment
        else:
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "id":
                event_id = value
            elif field == "event":
                event = value
            elif field == "data":
                data.append(value)
