from godel_jev_trader.book import PaperBook
from godel_jev_trader.decide import Decision
from godel_jev_trader.godel import NewsItem, Price
from godel_jev_trader.mock_api import enrich, guess_name, instrument_id, link, mock_price


def item(at: str, id_: str = "n1") -> NewsItem:
    return NewsItem(id=id_, title="t", content="", created_at=at, symbols=("TSLA",))


def decision(action="LONG", price=100.0, conviction=0.5, key="inst_tsla") -> Decision:
    return Decision(key=key, symbol="TSLA", name="Tesla", action=action, conviction=conviction, event="earnings",
                    event_confidence=0.9, tone=3.5, tone_confidence=0.8, relevance=0.9, link_confidence=0.95,
                    material=0.8, new_information=0.9, price=price, change_pct=1.0, reason="test")


def test_book_sizes_by_conviction_and_marks_pnl(tmp_path):
    b = PaperBook(tmp_path / "s.jsonl")
    assert b.record(item("2026-09-21T14:00:00Z"), decision(), 200, "jev") is None
    pos = b.positions["inst_tsla"]
    assert pos.units == 50.0 and pos.entry == 100.0
    b.mark({"TSLA": Price(110.0, 10.0, "")})
    assert pos.pnl == 500.0
    assert "+500.00" in b.summary()


def test_repeat_signal_inside_cooldown_is_logged_not_added(tmp_path):
    b = PaperBook(tmp_path / "s.jsonl")
    b.record(item("2026-09-21T14:00:00Z", "a"), decision(), 200, "jev")
    note = b.record(item("2026-09-21T14:10:00Z", "b"), decision(), 200, "jev")
    assert note and note.startswith("duplicate")
    assert b.positions["inst_tsla"].units == 50.0
    assert b.record(item("2026-09-21T15:00:00Z", "c"), decision(), 200, "jev") is None
    assert b.positions["inst_tsla"].units == 100.0
    assert len((tmp_path / "s.jsonl").read_text().splitlines()) == 3


def test_short_pnl_sign(tmp_path):
    b = PaperBook(tmp_path / "s.jsonl")
    b.record(item("2026-09-21T14:00:00Z"), decision(action="SHORT"), 200, "jev")
    b.mark({"TSLA": Price(90.0, -10.0, "")})
    assert b.positions["inst_tsla"].pnl == 500.0


def test_mock_relevance_prefers_title_mentions():
    in_title = link("TSLA", "Czech Republic approves Tesla's FSD", "")
    in_body = link("TSLA", "Regulator news", "x" * 700 + " Tesla was also mentioned")
    absent = link("TSLA", "Coniagas trades higher", "nothing here")
    assert in_title["relevance"] > in_body["relevance"] > absent["relevance"]
    assert in_title["confidence"] > absent["confidence"]
    assert in_title["name"] == "Tesla" and in_title["id"] == instrument_id("TSLA")


def test_mock_unknown_symbol_has_low_confidence():
    c = link("ZZZQ", "With a trading volume of USD 3 mn HAS trades lower", "unrelated", n_symbols=6)
    assert c["confidence"] < 0.7 and c["name"] == "ZZZQ"


def test_mock_guesses_name_from_headline_for_lone_tags():
    c = link("ZUMA", "Zuma Resources Ltd. Stock Experiences Moderate Move on Pakistan Stock Exchange", "", n_symbols=1)
    assert c["name"] == "Zuma Resources Ltd" and c["confidence"] >= 0.7 and c["relevance"] > 0.75
    assert guess_name("With a trading volume of USD 337.6 mn HAS trades -1.34 percent lower today") is None
    assert guess_name("Telekom Malaysia Bhd trades 0 percent unchanged on Monday") == "Telekom Malaysia Bhd"


def test_mock_price_is_deterministic_and_moves_across_buckets():
    a = mock_price("TSLA", "2026-09-21T14:00:00Z")
    b = mock_price("TSLA", "2026-09-21T14:01:00Z")  # same 5-minute bucket
    assert (a["last"], a["changePct"]) == (b["last"], b["changePct"])
    later = mock_price("TSLA", "2026-09-21T15:00:00Z")
    assert a["last"] > 0 and (a["last"] != later["last"] or a["changePct"] != later["changePct"])


def test_enrich_adds_sorted_companies():
    e = enrich({"id": "x", "title": "Tesla recalls cars", "content": "", "createdAt": "2026-09-21T14:00:00Z",
                "symbols": ["ZZZQ", "TSLA"]})
    assert [c["symbol"] for c in e["companies"]] == ["TSLA", "ZZZQ"]
    assert e["companies"][0]["price"]["asOf"] == "2026-09-21T14:00:00Z"
