"""A paper book: every decision is appended to a JSONL file, positions are tracked in memory.

Positions are keyed by instrument id, so the same company under two tickers or the same story
in three languages is one position. A repeat signal on the same instrument inside the cooldown
window is logged but not added. With prices, the book marks to market.

There is deliberately no broker here. Swap `PaperBook` for something that talks to yours.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .decide import Decision
from .godel import NewsItem, Price


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


@dataclass
class Position:
    key: str
    symbol: str
    name: str
    units: float = 0.0          # positive long, negative short
    cost: float = 0.0           # sum(units * entry price), for the average entry
    last_price: float | None = None
    signals: int = 0
    last_signal_at: float = 0.0
    last_reason: str = ""

    @property
    def entry(self) -> float | None:
        return self.cost / self.units if self.units else None

    @property
    def pnl(self) -> float | None:
        if self.last_price is None or not self.units or self.entry is None:
            return None
        return self.units * (self.last_price - self.entry)


@dataclass
class PaperBook:
    path: Path
    base_units: float = 100.0   # units per full-conviction signal
    cooldown_s: float = 30 * 60  # one signal per instrument per half hour
    positions: dict[str, Position] = field(default_factory=dict)

    def record(self, item: NewsItem, decision: Decision, latency_ms: float, model: str) -> str | None:
        """Log the decision; apply it if it is a signal. Returns a note when it was not applied."""
        note = None
        pos = self.positions.get(decision.key)
        if decision.action in ("LONG", "SHORT"):
            at = _ts(item.created_at)
            if pos and at - pos.last_signal_at < self.cooldown_s:
                note = "duplicate: same instrument within cooldown"
            elif decision.price is None:
                note = "no price: signal logged, not sized"
            else:
                pos = pos or self.positions.setdefault(decision.key, Position(decision.key, decision.symbol, decision.name))
                sign = 1 if decision.action == "LONG" else -1
                units = sign * self.base_units * decision.conviction
                pos.units += units
                pos.cost += units * decision.price
                pos.signals += 1
                pos.last_signal_at = at
                pos.last_reason = decision.reason
        if pos and decision.price is not None:
            pos.last_price = decision.price

        row = {"at": item.created_at, "item_id": item.id, "title": item.title, **asdict(decision),
               "applied": decision.action in ("LONG", "SHORT") and note is None,
               "note": note, "jev_model": model, "jev_latency_ms": round(latency_ms)}
        with self.path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return note

    def mark(self, prices: dict[str, Price]) -> None:
        for p in self.positions.values():
            if p.symbol in prices:
                p.last_price = prices[p.symbol].last

    def summary(self) -> str:
        if not self.positions:
            return "paper book: flat"
        lines = ["paper book:"]
        total = 0.0
        marked = False
        for p in sorted(self.positions.values(), key=lambda p: -abs(p.units)):
            side = "long" if p.units > 0 else "short"
            line = f"  {p.symbol:<8} {p.name[:22]:<22} {side:<5} {abs(p.units):7.1f} units"
            if p.entry is not None:
                line += f"  @ {p.entry:9.2f}"
            if p.pnl is not None:
                marked = True
                total += p.pnl
                line += f"  last {p.last_price:9.2f}  P&L {p.pnl:+9.2f}"
            line += f"  ({p.signals} signal{'s' if p.signals != 1 else ''}: {p.last_reason})"
            lines.append(line)
        if marked:
            lines.append(f"  {'total P&L':<50} {total:+9.2f}")
        return "\n".join(lines)
