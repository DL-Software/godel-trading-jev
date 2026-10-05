from godel_jev_trader.decide import Thresholds, decide
from godel_jev_trader.godel import LinkedCompany, Price
from godel_jev_trader.questions import build_questions, key

PRICE = Price(last=120.0, change_pct=1.2, as_of="2026-09-21T14:00:00Z")
NVDA = LinkedCompany("NVDA", "NVIDIA", listing="NVDA:US", confidence=0.97, relevance=0.92, price=PRICE)
PLAIN = LinkedCompany("NVDA", "NVIDIA", listing="NVDA:US")  # linked, but no relevance: Jev is asked


def answers(material=0.9, new=0.9, tone=3.6, tone_conf=0.8, event="earnings", event_conf=0.9, subject=None):
    a = {
        "event_type": {"type": "choice", "choice": event, "confidence": event_conf, "probabilities": {event: event_conf}},
        "new_information": {"type": "noul", "noul": new},
        key("NVDA:US", "material"): {"type": "noul", "noul": material},
        key("NVDA:US", "tone"): {"type": "score", "score": tone, "confidence": tone_conf, "legend": {}, "probabilities": {}},
    }
    if subject is not None:
        a[key("NVDA:US", "subject")] = {"type": "noul", "noul": subject}
    return a


def test_long_on_positive_material_news():
    d = decide(NVDA, answers())
    assert d.action == "LONG"
    assert d.key == "NVDA:US" and d.price == 120.0
    assert d.conviction == round(0.92 * 0.9 * 0.9 * 0.8, 3)


def test_short_on_negative_tone():
    assert decide(NVDA, answers(tone=0.6)).action == "SHORT"


def test_api_relevance_gates_without_asking_jev():
    far = LinkedCompany("NVDA", "NVIDIA", listing="NVDA:US", confidence=0.95, relevance=0.2, price=PRICE)
    d = decide(far, answers())  # no subject answer present, and none needed
    assert d.action == "IGNORE" and "relevance" in d.reason


def test_uncertain_link_is_ignored_first():
    shaky = LinkedCompany("NVDA", "NVIDIA", listing="NVDA:US", confidence=0.4, relevance=0.95, price=PRICE)
    assert decide(shaky, answers()).reason.startswith("uncertain link")


def test_no_relevance_falls_back_to_jev_subject():
    assert decide(PLAIN, answers(subject=0.95)).action == "LONG"
    assert decide(PLAIN, answers(subject=0.1)).action == "IGNORE"
    assert key("NVDA:US", "subject") in build_questions([PLAIN])
    assert key("NVDA:US", "subject") not in build_questions([NVDA])


def test_already_moved_is_a_watch():
    ran = LinkedCompany("NVDA", "NVIDIA", listing="NVDA:US", confidence=0.97, relevance=0.92,
                        price=Price(140.0, 9.5, "2026-09-21T14:00:00Z"))
    d = decide(ran, answers())
    assert d.action == "WATCH" and "already moved" in d.reason


def test_ignore_confident_noise():
    assert decide(NVDA, answers(event="noise", event_conf=0.9)).action == "IGNORE"


def test_watch_when_recap_or_immaterial_or_unclear():
    assert decide(NVDA, answers(new=0.2)).action == "WATCH"
    assert decide(NVDA, answers(material=0.3)).action == "WATCH"
    assert decide(NVDA, answers(tone_conf=0.2)).action == "WATCH"
    assert decide(NVDA, answers(tone=2.0)).action == "WATCH"


def test_thresholds_are_tunable():
    assert decide(NVDA, answers(tone=2.6), Thresholds(long_tone=2.5)).action == "LONG"


def test_question_keys_survive_odd_tickers():
    qs = build_questions([LinkedCompany("BRK.B", "Berkshire Hathaway", listing="BRK.B:US"), LinkedCompany("005930", "Samsung Electronics")])
    assert "brk_b_us_subject" in qs and "005930_tone" in qs
    assert all(q["type"] in ("choice", "noul", "score") for q in qs.values())


def test_trend_is_a_word_for_jev():
    assert Price(1, 7.0, "").trend == "up_strong"
    assert Price(1, -1.5, "").trend == "down"
    assert Price(1, 0.3, "").trend == "flat"


def test_close_price_skips_priced_in_gate():
    closed = LinkedCompany("NVDA", "NVIDIA", listing="NVDA:US", confidence=0.97, relevance=0.92,
                           price=Price(140.0, None, "2026-09-20T20:00:00Z", "CLOSE"))
    d = decide(closed, answers())
    assert d.action == "LONG" and d.change_pct is None


def test_same_ticker_on_two_venues_gets_separate_questions():
    qs = build_questions([LinkedCompany("BA", "Boeing", listing="BA:US"), LinkedCompany("BA", "BAE Systems", listing="BA:LN")])
    assert "ba_us_tone" in qs and "ba_ln_tone" in qs
