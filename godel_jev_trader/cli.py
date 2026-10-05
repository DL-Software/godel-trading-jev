"""`godel-jev-trader` command line: replay recent news, follow the live stream, or run the mock API."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from .book import PaperBook
from .decide import Thresholds, decide
from .godel import GodelNews, LinkedCompany, NewsItem
from .jev import Jev
from .questions import build_questions, build_state

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
COLOR = {"LONG": "\033[32m", "SHORT": "\033[31m", "WATCH": "\033[33m", "IGNORE": "\033[2m"}


def load_watchlist(path: Path) -> dict[str, str]:
    """`TICKER:VENUE` listing -> company name."""
    with path.open("rb") as f:
        data = tomllib.load(f)
    watchlist = {sym.upper(): name for sym, name in data["companies"].items()}
    if bare := [s for s in watchlist if ":" not in s]:
        raise SystemExit(f"{path}: write listings as TICKER:VENUE, e.g. \"AAPL:US\" (not {', '.join(bare[:3])})")
    return watchlist


def companies_for(item: NewsItem, watchlist: dict[str, str], everything: bool) -> list[LinkedCompany]:
    """The companies to score on this item.

    Linked instruments: use them (filtered to the watchlist unless --all).
    None linked: build a company per tagged ticker; Jev will be asked who the item is about.
    """
    if item.companies:
        return [c for c in item.companies if everything or c.listing in watchlist]
    if everything:
        return [LinkedCompany(t, t) for t in item.tickers]
    return [LinkedCompany(s.split(":")[0], watchlist[s], listing=s) for s in item.symbols if s in watchlist]


def process(item: NewsItem, companies: list[LinkedCompany], jev: Jev, book: PaperBook, t: Thresholds, color: bool) -> None:
    resp = jev.ask(build_state(item, companies), build_questions(companies))
    dim, reset = (DIM, RESET) if color else ("", "")
    print(f"{dim}{item.published_at[11:19]}{reset}  {item.title[:100]}")
    for c in companies:
        d = decide(c, resp.answers, t)
        note = book.record(item, d, resp.latency_ms, resp.model)
        tag = f"{COLOR[d.action]}{BOLD}{d.action:<6}{RESET}" if color else f"{d.action:<6}"
        px = f"{d.price:9.2f} {f'{d.change_pct:+5.1f}%' if d.change_pct is not None else '':6} {c.price.type.lower():<8}" if c.price else f"{'':25}"
        conf = f"conf {d.link_confidence:.2f}" if d.link_confidence is not None else "conf  -  "
        print(
            f"          {c.symbol:<7} {c.name[:20]:<20} {tag} {d.event:<16} "
            f"rel {d.relevance:.2f} {conf} {px}  tone {d.tone:.1f} mat {d.material:.2f} new {d.new_information:.2f}"
            f"  {dim}{d.reason}{'; ' + note if note else ''}  ({resp.latency_ms:.0f} ms){reset}"
        )


def finish(book: PaperBook) -> None:
    print("\n" + book.summary())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="godel-jev-trader", description="Godel news -> Jev -> paper trading signals")
    p.add_argument("command", choices=["replay", "run", "mock"],
                   help="replay recent items once, run on the live stream, or serve the mock API")
    p.add_argument("--watchlist", type=Path, default=Path("watchlist.toml"))
    p.add_argument("--limit", type=int, default=50, help="replay: how many recent items to fetch (max 200)")
    p.add_argument("--all", action="store_true", help="score every linked instrument, not only the watchlist")
    p.add_argument("--book", type=Path, default=Path("signals.jsonl"), help="where decisions are appended")
    p.add_argument("--from-file", type=Path, help="replay: read items from a saved JSON array instead of the API")
    p.add_argument("--port", type=int, default=8090, help="mock: port to listen on")
    p.add_argument("--no-color", action="store_true")
    a = p.parse_args(argv)

    if a.command == "mock":
        from .mock_api import serve
        serve(a.port)
        return 0

    watchlist = load_watchlist(a.watchlist)
    symbols = () if a.all else tuple(watchlist)
    jev = Jev()
    api = GodelNews()
    book = PaperBook(a.book)
    t = Thresholds()
    color = not a.no_color and sys.stdout.isatty()

    if a.command == "replay":
        if a.from_file:
            items = [NewsItem.from_json(d) for d in json.loads(a.from_file.read_text())][: a.limit]
        else:
            items = api.recent(symbols, limit=min(a.limit, 200))
        items.reverse()  # oldest first, like the stream
        print(f"{sum(1 for i in items if companies_for(i, watchlist, a.all))} items to score\n")
        for item in items:
            book.observe(item)
            if cs := companies_for(item, watchlist, a.all):
                process(item, cs, jev, book, t, color)
        finish(book)
        return 0

    print(f"following {'all symbols' if a.all else ', '.join(symbols)} ... (Ctrl-C to stop)\n")
    try:
        for item in api.stream(symbols):
            book.observe(item)
            if cs := companies_for(item, watchlist, a.all):
                process(item, cs, jev, book, t, color)
    except KeyboardInterrupt:
        finish(book)
    return 0


if __name__ == "__main__":
    sys.exit(main())
