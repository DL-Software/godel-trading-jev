from godel_jev_trader.book import PaperBook
from godel_jev_trader.decide import Decision
from godel_jev_trader.godel import LinkedCompany, NewsItem, Price
from godel_jev_trader.mock_api import aliases, enrich, link


def item(at: str, id_: str = "n1", companies=()) -> NewsItem:
    return NewsItem(id=id_, title="t", content="", published_at=at, tickers=("TSLA",), companies=companies)


def decision(action="LONG", price=100.0, conviction=0.5, key="TSLA:US") -> Decision:
    return Decision(key=key, symbol="TSLA", name="Tesla", action=action, conviction=conviction, event="earnings",
                    event_confidence=0.9, tone=3.5, tone_confidence=0.8, relevance=0.9, link_confidence=0.95,
                    material=0.8, new_information=0.9, price=price, change_pct=1.0, reason="test")


def priced(last: float) -> NewsItem:
    tsla = LinkedCompany("TSLA", "Tesla Inc", listing="TSLA:US", price=Price(last, 1.0, "", "REALTIME"))
    return item("2026-09-21T16:00:00Z", "later", (tsla,))


def test_book_sizes_by_conviction_and_marks_pnl(tmp_path):
    b = PaperBook(tmp_path / "s.jsonl")
    assert b.record(item("2026-09-21T14:00:00Z"), decision(), 200, "jev") is None
    pos = b.positions["TSLA:US"]
    assert pos.units == 50.0 and pos.entry == 100.0
    b.observe(priced(110.0))
    assert pos.pnl == 500.0
    assert "+500.00" in b.summary()


def test_repeat_signal_inside_cooldown_is_logged_not_added(tmp_path):
    b = PaperBook(tmp_path / "s.jsonl")
    b.record(item("2026-09-21T14:00:00Z", "a"), decision(), 200, "jev")
    note = b.record(item("2026-09-21T14:10:00Z", "b"), decision(), 200, "jev")
    assert note and note.startswith("duplicate")
    assert b.positions["TSLA:US"].units == 50.0
    assert b.record(item("2026-09-21T15:00:00Z", "c"), decision(), 200, "jev") is None
    assert b.positions["TSLA:US"].units == 100.0
    assert len((tmp_path / "s.jsonl").read_text().splitlines()) == 3


def test_short_pnl_sign(tmp_path):
    b = PaperBook(tmp_path / "s.jsonl")
    b.record(item("2026-09-21T14:00:00Z"), decision(action="SHORT"), 200, "jev")
    b.observe(priced(90.0))
    assert b.positions["TSLA:US"].pnl == 500.0


def test_api_item_parses():
    d = {"id": "x", "title": "Nvidia beats", "content": "", "publishedAt": "2026-09-19T15:23:11Z",
         "createdAt": "2026-09-19T15:23:11Z", "tickers": ["NVDA"], "symbols": ["NVDA:US", "NVD:GY"],
         "instruments": [{"type": "EQUITY", "name": "NVIDIA Corp", "ticker": "NVDA", "symbol": "NVDA:US",
                          "price": {"type": "CLOSE", "value": 182.41, "asOf": "2026-09-18T20:00:00Z"}}]}
    i = NewsItem.from_json(d)
    c = i.companies[0]
    assert i.published_at == "2026-09-19T15:23:11Z" and i.symbols == ("NVDA:US", "NVD:GY")
    assert (c.symbol, c.name, c.key, c.relevance) == ("NVDA", "NVIDIA Corp", "NVDA:US", None)
    assert c.price.type == "CLOSE" and c.price.change_pct is None and c.price.trend is None


TSLA = {"name": "Tesla Inc", "ticker": "TSLA", "symbol": "TSLA:US"}


def test_mock_relevance_prefers_title_mentions():
    in_title = link(TSLA, "Czech Republic approves Tesla's FSD", "")
    in_body = link(TSLA, "Regulator news", "x" * 700 + " Tesla was also mentioned")
    absent = link(TSLA, "Coniagas trades higher", "nothing here")
    assert in_title["relevance"] > in_body["relevance"] > absent["relevance"]
    assert in_title["confidence"] > absent["confidence"]
    assert in_title["name"] == "Tesla Inc" and in_title["symbol"] == "TSLA:US"


def test_mock_aliases_strip_corporate_suffixes_only():
    assert aliases("Meta Platforms Inc", "META")[:2] == ["Meta Platforms Inc", "Meta Platforms"]
    assert "Facebook" in aliases("Meta Platforms Inc", "META")
    assert aliases("Bank of NY", None) == ["Bank of NY"]
    assert "Hyatt Hotels" in aliases("Hyatt Hotels Corp Class A", "H")


def test_enrich_sorts_instruments_and_passes_through_unlinked_items():
    e = enrich({"id": "x", "title": "Tesla recalls cars", "content": "",
                "instruments": [{"name": "Hasbro Inc", "ticker": "HAS"}, TSLA]})
    assert [i["ticker"] for i in e["instruments"]] == ["TSLA", "HAS"]
    assert enrich({"id": "y", "title": "t", "instruments": []})["instruments"] == []
