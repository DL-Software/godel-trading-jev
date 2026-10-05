"""Godel public news API client: recent items and the live SSE stream.

Docs: https://platform.godelterminal.com/docs

Items carry `instruments`: the instruments the API linked to the item, one per company, each with
its name, ticker, `TICKER:VENUE` symbol, and the price when the item was delivered. Items whose
source linked none have an empty tuple, and the tool falls back to asking Jev who the item is
about. The local mock (`godel-jev-trader mock`) adds `relevance` and `confidence` to each
instrument, which the API does not send yet; they are read when present.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Iterable, Iterator

import httpx

DEFAULT_URL = "https://api.godelterminal.com"
KEYS_URL = "https://platform.godelterminal.com/api/keys"


@dataclass(frozen=True)
class Price:
    last: float
    change_pct: float | None  # since previous close; None for an end-of-day close
    as_of: str
    type: str = "DELAYED"     # REALTIME, DELAYED, or CLOSE

    @property
    def trend(self) -> str | None:
        """A label Jev can read. Numbers stay in code; the model sees words."""
        p = self.change_pct
        if p is None:
            return None
        if p >= 5:
            return "up_strong"
        if p >= 1:
            return "up"
        if p <= -5:
            return "down_strong"
        if p <= -1:
            return "down"
        return "flat"

    @classmethod
    def from_json(cls, d: dict) -> "Price":
        """The API's `InstrumentPrice`: `{type, value, change?, changePercent?, asOf}`."""
        return cls(d["value"], d.get("changePercent"), d["asOf"], d.get("type") or "DELAYED")


@dataclass(frozen=True)
class LinkedCompany:
    symbol: str                       # ticker, for display and Jev
    name: str
    listing: str | None = None        # TICKER:VENUE, the `symbols` filter value
    confidence: float | None = None   # P(the link to this instrument is correct); mock only
    relevance: float | None = None    # P(the company is what the item is about); mock only
    price: Price | None = None

    @property
    def key(self) -> str:
        return self.listing or self.symbol

    @classmethod
    def from_instrument(cls, d: dict) -> "LinkedCompany":
        """The API's `Instrument`: `{name, ticker?, symbol?, price?}`."""
        p = d.get("price")
        return cls(
            symbol=d.get("ticker") or d["name"],
            name=d["name"],
            listing=d.get("symbol"),
            confidence=d.get("confidence"),
            relevance=d.get("relevance"),
            price=Price.from_json(p) if p else None,
        )


@dataclass(frozen=True)
class NewsItem:
    id: str
    title: str
    content: str
    published_at: str
    tickers: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()     # every linked listing, TICKER:VENUE
    companies: tuple[LinkedCompany, ...] = ()

    @classmethod
    def from_json(cls, d: dict) -> "NewsItem":
        return cls(
            id=d["id"],
            title=d["title"],
            content=d.get("content") or "",
            published_at=d.get("publishedAt") or d["createdAt"],
            tickers=tuple(d.get("tickers") or ()),
            symbols=tuple(d.get("symbols") or ()),
            companies=tuple(LinkedCompany.from_instrument(c) for c in d.get("instruments") or ()),
        )


class GodelNews:
    """Thin client over `GET /v1-beta/news-items` and `GET /v1-beta/news-item-stream`."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get("GODEL_API_KEY")
        if not self.api_key:
            raise SystemExit(f"GODEL_API_KEY is not set. Create a key at {KEYS_URL}")
        self.base_url = (base_url or os.environ.get("GODEL_API_URL") or DEFAULT_URL).rstrip("/")
        self._headers = {"Authorization": f"Bearer {self.api_key}"}

    def recent(self, symbols: Iterable[str] = (), limit: int = 50) -> list[NewsItem]:
        params: dict[str, str | int] = {"limit": limit}
        symbols = list(symbols)
        if symbols:
            params["symbols"] = ",".join(symbols)
        for attempt in range(4):
            r = httpx.get(f"{self.base_url}/v1-beta/news-items", params=params,
                          headers=dict(self._headers, Accept="application/json"), timeout=30)
            if r.status_code in (429, 503) and attempt < 3:
                time.sleep(_retry_after(r, 2.0 ** attempt))
                continue
            _refuse(r)
            r.raise_for_status()
            return [NewsItem.from_json(d) for d in r.json()]
        raise RuntimeError("unreachable")

    def stream(self, symbols: Iterable[str] = (), last_event_id: str | None = None) -> Iterator[NewsItem]:
        """Yield items from the SSE stream indefinitely, resuming after disconnects.

        The server's `id:` line is a cursor; sending it back as `Last-Event-ID` replays anything
        missed while we were away. Items are de-duplicated by their own `id` field.
        """
        symbols = list(symbols)
        params = {"symbols": ",".join(symbols)} if symbols else {}
        seen: dict[str, None] = {}
        backoff = 1.0
        while True:
            headers = dict(self._headers, Accept="text/event-stream")
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            try:
                with httpx.stream(
                    "GET",
                    f"{self.base_url}/v1-beta/news-item-stream",
                    params=params,
                    headers=headers,
                    timeout=httpx.Timeout(30, read=None),
                ) as r:
                    if r.status_code >= 400:
                        r.read()
                        _refuse(r)
                    r.raise_for_status()
                    backoff = 1.0
                    for event_id, event, data in _sse_frames(r.iter_lines()):
                        if event_id:
                            last_event_id = event_id
                        if event != "news-item" or not data:
                            continue
                        item = NewsItem.from_json(json.loads(data))
                        if item.id in seen:
                            continue
                        seen[item.id] = None
                        if len(seen) > 5000:
                            seen.pop(next(iter(seen)))
                        yield item
            except httpx.HTTPStatusError as e:
                # 429 stream_limit_exceeded: another connection on this account is still open.
                time.sleep(_retry_after(e.response, backoff))
                backoff = min(backoff * 2, 30)
            except httpx.HTTPError:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)


def _refuse(r: httpx.Response) -> None:
    """Exit on errors retrying cannot fix: a bad key, or an account without a subscription."""
    if r.status_code in (400, 401, 403):
        try:
            detail = r.json().get("detail") or r.text
        except ValueError:
            detail = r.text
        hint = f" Create a key at {KEYS_URL}" if r.status_code == 401 else ""
        raise SystemExit(f"Godel API refused the request ({r.status_code}): {detail}.{hint}")


def _retry_after(r: httpx.Response, default: float) -> float:
    try:
        return float(r.headers["Retry-After"])
    except (KeyError, ValueError):
        return default


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
