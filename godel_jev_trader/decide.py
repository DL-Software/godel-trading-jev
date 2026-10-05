"""Turn Jev's typed answers into a trading decision. Pure code, no model.

This is the part you are meant to change. Godel tells you *who* the item is about and *at what
price*. Jev tells you *what the text says*, with calibrated probabilities. Whether that is worth
a position, and how big, is your strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .godel import LinkedCompany
from .questions import key

Action = Literal["LONG", "SHORT", "WATCH", "IGNORE"]


@dataclass(frozen=True)
class Thresholds:
    link_confidence: float = 0.70   # API: P(the symbol->company link is right); below: IGNORE
    relevance: float = 0.80         # API relevance, or Jev's subject noul when the API has none
    new_information: float = 0.60   # noul: not a recap / listing / opinion
    material: float = 0.70          # noul: a trader would care
    event_confidence: float = 0.50  # choice confidence before we trust the event label
    tone_confidence: float = 0.50   # score confidence before we act on tone
    priced_in_pct: float = 5.0      # |move since previous close| at or above this: WATCH, it already happened
    long_tone: float = 3.0          # score >= this (of 4) -> LONG
    short_tone: float = 1.0         # score <= this (of 4) -> SHORT


@dataclass(frozen=True)
class Decision:
    key: str                    # TICKER:VENUE listing when the API gives one, else the ticker
    symbol: str
    name: str
    action: Action
    conviction: float           # 0..1, product of the probabilities we relied on
    event: str
    event_confidence: float
    tone: float                 # 0..4
    tone_confidence: float
    relevance: float            # API relevance, or Jev subject
    link_confidence: float | None
    material: float
    new_information: float
    price: float | None
    change_pct: float | None
    reason: str


def decide(company: LinkedCompany, answers: dict, t: Thresholds = Thresholds()) -> Decision:
    event = answers["event_type"]
    new_info = answers["new_information"]["noul"]
    relevance = company.relevance if company.relevance is not None else answers[key(company.key, "subject")]["noul"]
    material = answers[key(company.key, "material")]["noul"]
    tone_a = answers[key(company.key, "tone")]
    tone, tone_conf = tone_a["score"], tone_a["confidence"]
    price = company.price

    def out(action: Action, reason: str, conviction: float = 0.0) -> Decision:
        return Decision(
            key=company.key, symbol=company.symbol, name=company.name, action=action,
            conviction=round(conviction, 3), event=event["choice"], event_confidence=event["confidence"],
            tone=tone, tone_confidence=tone_conf, relevance=relevance, link_confidence=company.confidence,
            material=material, new_information=new_info,
            price=price.last if price else None, change_pct=price.change_pct if price else None, reason=reason,
        )

    # Gates, cheapest first. Each one is a single probability compared to a threshold.
    if company.confidence is not None and company.confidence < t.link_confidence:
        return out("IGNORE", f"uncertain link (confidence {company.confidence:.2f})")
    if relevance < t.relevance:
        return out("IGNORE", f"not about {company.symbol} (relevance {relevance:.2f})")
    if event["choice"] == "noise" and event["confidence"] >= t.event_confidence:
        return out("IGNORE", "noise")
    if new_info < t.new_information:
        return out("WATCH", f"recap or opinion (new {new_info:.2f})")
    if material < t.material:
        return out("WATCH", f"not material (material {material:.2f})")
    if price and price.change_pct is not None and abs(price.change_pct) >= t.priced_in_pct:
        return out("WATCH", f"already moved {price.change_pct:+.1f}% today")
    if tone_conf < t.tone_confidence:
        return out("WATCH", f"tone unclear (confidence {tone_conf:.2f})")

    conviction = relevance * material * new_info * tone_conf
    if tone >= t.long_tone:
        return out("LONG", f"{event['choice']}, tone {tone:.1f}/4", conviction)
    if tone <= t.short_tone:
        return out("SHORT", f"{event['choice']}, tone {tone:.1f}/4", conviction)
    return out("WATCH", f"material but neutral (tone {tone:.1f}/4)")
