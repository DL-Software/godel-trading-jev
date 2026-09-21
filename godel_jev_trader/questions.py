"""The questions we ask Jev about each news item.

Each one is atomic: a single judgment the model can make from the text alone. Anything that
needs arithmetic, price history, or portfolio state lives in `decide.py`, in code.

When the API has already linked companies to the item (with a relevance score), we do not ask
Jev who the item is about. Godel says who; Jev says what it means.
"""

from __future__ import annotations

import re

from .godel import LinkedCompany, NewsItem

EVENT_TYPES = {
    "earnings": "Quarterly results, guidance, or a pre-announcement",
    "m_and_a": "Merger, acquisition, divestiture, or takeover interest",
    "regulatory_legal": "Regulator, court, legislation, or government action affecting the company",
    "product_deal": "Product launch, contract win, partnership, or customer deal",
    "management": "Executive or board change, governance",
    "capital": "Buyback, dividend, debt or equity issuance, credit rating change",
    "analyst": "Broker rating, price target, or research note",
    "macro": "Economy-wide, political, or sector-wide news",
    "noise": "Listings, sports, entertainment, recaps, or content unrelated to business",
}

TONE_LEVELS = [
    "Clearly negative for shareholders",
    "Somewhat negative",
    "Neutral or unclear",
    "Somewhat positive",
    "Clearly positive for shareholders",
]

MAX_CONTENT_CHARS = 12_000  # Jev's state budget is 32k tokens; keep well inside it.


def key(symbol: str, suffix: str) -> str:
    """A question key that is safe for any ticker (`BRK.B`, `005930`, `RDS-A`)."""
    return f"{re.sub(r'[^A-Za-z0-9]', '_', symbol).lower()}_{suffix}"


def build_state(item: NewsItem, companies: list[LinkedCompany]) -> dict:
    linked = []
    for c in companies:
        entry: dict = {"symbol": c.symbol, "name": c.name}
        if c.price:
            entry["price_trend_today"] = c.price.trend  # a word, not a number
        linked.append(entry)
    return {
        "title": item.title,
        "content": item.content[:MAX_CONTENT_CHARS],
        "published_at": item.created_at,
        "companies": linked,
    }


def build_questions(companies: list[LinkedCompany]) -> dict:
    """Item-level questions plus two (or three) per company. All evaluated in one Jev call."""
    q: dict = {
        "event_type": {
            "type": "choice",
            "instructions": "What kind of event does this news item report?",
            "criteria": EVENT_TYPES,
        },
        "new_information": {
            "type": "noul",
            "instructions": "The item reports a new development, rather than a recap, opinion piece, listing, or promotional content.",
        },
    }
    for c in companies:
        who = f"{c.name} ({c.symbol})"
        if c.relevance is None:  # API didn't say who the item is about; ask Jev
            q[key(c.symbol, "subject")] = {
                "type": "noul",
                "instructions": f"{who} is a primary subject of this item, not an incidental mention.",
            }
        q[key(c.symbol, "material")] = {
            "type": "noul",
            "instructions": f"A professional trader holding {who} would consider this item material to its share price.",
            "criteria": {
                "true": "The item could plausibly move the share price when the market reads it",
                "false": "The item is routine, already priced in, or irrelevant to the share price",
            },
        }
        q[key(c.symbol, "tone")] = {
            "type": "score",
            "instructions": f"Implication of this item for {who} shareholders.",
            "criteria": TONE_LEVELS,
        }
    return q
