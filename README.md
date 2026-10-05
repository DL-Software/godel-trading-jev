# godel-jev-trader

**Every headline, read and scored in about 400 ms. Your code decides what to do with it.**

[Godel](https://godelterminal.com)'s live news firehose, wired to [TypeSafe's Jev](https://typesafe.ai). Each item comes off the stream tagged with its tickers, Jev answers a handful of typed questions about it with calibrated probabilities, and a few lines of plain Python turn those numbers into a paper trading signal. No prompt engineering. No JSON to parse out of prose. No hallucinated fields.

```
Godel /v1-beta/news-item-stream  ──►  linked instruments + price  ──►  typed questions  ──►  Jev  ──►  probabilities  ──►  decide()  ──►  LONG / SHORT / WATCH / IGNORE  ──►  paper book, marked to market
```

Godel says **who** the story is about and **at what price**. Jev says **what it means**. Your code decides.

Paper signals only. There is no broker in this repo, and it is not investment advice.

> Each item arrives with `instruments`: the companies the API linked to it, with name, `TICKER:VENUE` listing, and price. The API does not yet say how relevant each company is to the item; `godel-jev-trader mock` previews that by proxying the real feed and adding it. See [Preview: relevance and link confidence](#preview-relevance-and-link-confidence).

## See it run

Real output, excerpted from `godel-jev-trader replay --limit 200 --all` against the live API on 21 Sep 2026, before the API linked instruments, so Jev was asked who each item is about. Timings are the Jev round trip for the whole item.

```
63 items to score

14:05:56  Coniagas Battery Metals trades 10 percent higher on Monday 21st of September
          COS      IGNORE noise            tone 3.1  subj 0.97  mat 0.22  new 0.47  noise  (167 ms)
          SOLR     IGNORE noise            tone 2.0  subj 0.08  mat 0.11  new 0.47  not about SOLR (subject 0.08)  (167 ms)
          SI       IGNORE noise            tone 0.7  subj 0.08  mat 0.14  new 0.47  not about SI (subject 0.08)  (167 ms)
14:05:56  Czech Republic approves Tesla's FSD after earlier safety concerns
          TSLA     WATCH  regulatory_legal tone 3.2  subj 0.98  mat 0.59  new 0.96  not material (material 0.59)  (189 ms)
14:06:09  Alert in the United States: Beloved Bank to Close 26 Branches; Will Texas Be Affected?
          SFNC     WATCH  management       tone 1.6  subj 0.88  mat 0.61  new 0.91  not material (material 0.61)  (245 ms)
14:06:11  2026年9月7日-21日投资者关系活动记录表
          002920   LONG   product_deal     tone 4.0  subj 0.90  mat 0.74  new 0.68  product_deal, tone 4.0/4  (195 ms)
14:06:14  República Tcheca aprova o FSD da Tesla após preocupações anteriores com a segurança
          TSLA     WATCH  regulatory_legal tone 3.3  subj 0.98  mat 0.58  new 0.94  not material (material 0.58)  (241 ms)
14:06:17  With a trading volume of USD 337.6 mn HAS trades -1.34 percent lower today
          HAS      IGNORE noise            tone 0.8  subj 0.97  mat 0.17  new 0.24  noise  (214 ms)
          SMMT     IGNORE noise            tone 2.5  subj 0.11  mat 0.29  new 0.24  not about SMMT (subject 0.11)  (214 ms)

paper book:
  002920   long     43.5 units  (1 signals, last: product_deal, tone 4.0/4)
```

Every line is one company on one item. The numbers are Jev's answers; the verdict is ours. Of 164 company-item pairs in that replay, 143 were ignored, 20 were flagged to watch, and one cleared every gate. The same Tesla story arrived in three languages and got the same answer each time.

With the mock's relevance and link confidence (real feed through `godel-jev-trader mock`, same day), each line also carries those and the price at publication, and the book marks to market:

```
17:37:59  NJ Attorney General Davenport Announces Settlement With Paramount Skydance; Deal Settlement Includes
          WBD     Warner Bros. Discove IGNORE regulatory_legal rel 0.16 conf 0.82    295.64  +0.5%  tone 1.9 mat 0.50 new 0.87  not about WBD (relevance 0.16)  (194 ms)
          PSKY    PSKY                 IGNORE regulatory_legal rel 0.14 conf 0.30    115.17  +0.8%  tone 1.9 mat 0.64 new 0.87  uncertain link (confidence 0.30)  (194 ms)
17:41:06  IREN (IREN) Stock Surges on Bitcoin Rally and Bullish AI Infrastructure Outlook
          IREN    IREN                 LONG   analyst          rel 0.87 conf 0.75    127.31  +1.1%  tone 3.7 mat 0.81 new 0.60  analyst, tone 3.7/4  (201 ms)

paper book:
  IREN     IREN                   long     31.3 units  @    127.31  last    127.31  P&L     +0.00  (1 signal: analyst, tone 3.7/4)
```

No `subject` question was asked on those items: the mock's relevance answered it, so the Jev call was two questions per company instead of three. The mock synthesized names and prices on that day; the API now supplies both.

## Why this shape works

- **News is text, trading is numbers.** A chat model bridges that gap with prose you then have to parse and trust. Jev's [System One](https://docs.typesafe.ai/concepts/system-one.md) primitives skip the prose: a `choice` returns a distribution over your labels, a `score` returns a position on your rubric, a `noul` returns a single probability that a statement is true.
- **One call per item, however many questions.** Jev evaluates every question in a request in parallel and in isolation, so scoring five companies on one article costs about the same as scoring one.
- **Companies arrive attached.** Godel links each item to the instruments it is about, with name, listing, and price, so the stream can be filtered server-side to your watchlist and a name can be put in front of Jev for each one.
- **The model judges the text. The code judges the trade.** Thresholds, sizing, and portfolio state live in [`decide.py`](godel_jev_trader/decide.py) where you can read, test, and change them.

## Quickstart

You need two keys:

1. A Godel API key (`godel_sk_...`) from [platform.godelterminal.com/api/keys](https://platform.godelterminal.com/api/keys). News comes from the feeds your Godel subscription includes.
2. A TypeSafe key from [console.typesafe.ai](https://console.typesafe.ai)

```bash
git clone https://github.com/DL-Software/godel-jev-trader && cd godel-jev-trader
python -m venv .venv && . .venv/bin/activate && pip install -e .

export GODEL_API_KEY=...      # or copy .env.example to .env and source it
export TYPESAFE_API_KEY=...

godel-jev-trader replay --limit 50   # score the last 50 items on your watchlist
godel-jev-trader run                 # follow the live stream
```

Edit [`watchlist.toml`](watchlist.toml) to choose your listings, written `TICKER:VENUE` (`AAPL:US`). Pass `--all` to score every linked company on the feed, not only your list. Decisions are appended to `signals.jsonl`, and the paper book is printed, marked to the latest price seen on the feed, when a run ends. The API allows one stream connection per account.

## The questions

All are sent in one request. Two are about the item, three are repeated per company.

| Key | Type | Asks |
|---|---|---|
| `event_type` | choice | Which of nine event kinds this is: earnings, M&A, regulatory, product or deal, management, capital, analyst, macro, or noise |
| `new_information` | noul | Is this a new development rather than a recap, opinion, listing, or promo? |
| `{listing}_subject` | noul | Is the company a primary subject, not an incidental mention? Only asked when the item has no `relevance` (the API does not send one yet; the mock does). |
| `{listing}_material` | noul | Would a trader holding it consider this material to the share price? |
| `{listing}_tone` | score | Implication for shareholders, on five levels from clearly negative to clearly positive |

They are deliberately atomic. Jev is calibrated for single judgments a reader could make from the text. Anything needing arithmetic, price history, or "what's already priced in" is left to code, which is where Jev's own docs tell you to put it.

## The decision rules

The whole strategy is [`decide()`](godel_jev_trader/decide.py). Defaults:

```python
class Thresholds:
    link_confidence = 0.70   # mock: below this the link to the company itself is doubtful: IGNORE
    relevance = 0.80         # mock relevance (else Jev's subject answer): below this IGNORE
    new_information = 0.60   # below this: WATCH, it's a recap or opinion
    material = 0.70          # below this: WATCH
    priced_in_pct = 5.0      # |move since previous close| at or above this: WATCH, it already happened
    tone_confidence = 0.50   # below this: WATCH, Jev isn't sure of the tone
    long_tone = 3.0          # tone score (0..4) at or above -> LONG
    short_tone = 1.0         # at or below -> SHORT

conviction = relevance * material * new_information * tone_confidence
```

Confident `noise` is ignored outright. Position size in the paper book scales with conviction, and the book is keyed by listing, which the API links once per company, so one company under two tickers or one story in three languages is one position. A repeat signal on the same instrument inside 30 minutes is logged but not added. Change any of it and the tests in [`tests/`](tests/) still tell you what the rules do.

## What we learned building it

- **Name the company.** With only a ticker in the question, relevance answers came back hedged around 0.6. With the company name next to it, they became decisive. Jev reads text, not exchange listings, so every question carries the API's company name.
- **Confidence is a routing axis, not decoration.** Low `tone_confidence` on a material item is a real state: something happened, the direction is unclear. That is a WATCH, not a coin flip.
- **Relevance filtering is the quiet win.** Most tagged items are incidental mentions, auto-generated "X trades 2% higher" notes, or recaps. The `subject`, `noise`, and `new_information` gates removed 87% of company-item pairs in our replay before any tone was considered. Whatever your strategy does next, it does it on the items that are actually news.
- **Multilingual for free.** The feed carries Spanish, Portuguese, French, and Chinese items alongside English. Jev's answers on the Portuguese and English versions of the same Tesla story agreed to within a few hundredths. TypeSafe notes English is where accuracy is best, so weight non-English decisions accordingly.
- **It is cheap.** Jev bills input tokens only, at $0.042 per million. A typical wire item with five companies is under 2,000 tokens, so scoring 10,000 items a day costs well under a dollar.

## Preview: relevance and link confidence

The news API links each item to instruments, one per company, at the company's primary listing:

```json
"instruments": [{
  "type": "EQUITY", "currency": "USD", "name": "NVIDIA Corp", "ticker": "NVDA",
  "venueShortCode": "US", "symbol": "NVDA:US",
  "price": {"type": "DELAYED", "value": 182.41, "change": 1.27, "changePercent": 0.7, "asOf": "2026-09-19T15:23:10.812Z"}
}]
```

That gives the tool a name for every company, so Jev is decisive rather than hedging on bare tickers, and `--all` works on every company the API knows. It gives the price for the priced-in gate and the paper book, which is keyed by listing and marked to the latest price seen on the feed. And it makes the `symbols` filter possible, which follows the instrument through ticker changes.

Jev sees the price as a word (`price_trend_today: "up_strong"`), never as a number. TypeSafe's own guidance is that Jev is not a calculator, so the arithmetic stays in [`decide.py`](godel_jev_trader/decide.py). A `CLOSE` price has no move since the previous close, so for it the trend is left out and the priced-in gate is skipped.

What the API does not send yet is how much each company matters to the item. Two fields would let the tool drop a question and add a gate:

- **`relevance`**, the probability the company is what the item is about. When it arrives, the `subject` question is not asked. On the feed most linked pairs fail the relevance gate, and now they fail it before a token is spent.
- **`confidence`**, the probability the link itself is right. A low-confidence link is dropped as a tagging error rather than judged.

Both are in the same 0 to 1 sense as Jev's answers, so they multiply straight into conviction. The tool reads them from each instrument when present.

### Running against the mock

```bash
godel-jev-trader mock --port 8090          # in one terminal: proxies api.godelterminal.com, adds relevance and confidence
export GODEL_API_URL=http://127.0.0.1:8090 # in another
godel-jev-trader replay --limit 200 --all
godel-jev-trader run --all
```

The mock forwards your real Godel key upstream, so the news, names, and prices are live. What it adds is not:

| Field | In the mock | In the API |
|---|---|---|
| `relevance` | Mention heuristic: name in the title beats ticker in the title beats name in the lead beats name anywhere | Entity relevance model |
| `confidence` | High when the company is mentioned, lower when it is not | Link resolution confidence |

So the mock's `not about X` lines mean the mock found no mention of the company, not that a model judged it incidental.

## Extend it

- **Route by confidence.** Send low-confidence, high-materiality items to a larger model or a human, the pattern TypeSafe calls [confidence-gated routing](https://docs.typesafe.ai/patterns/confidence-routing.md).
- **Add questions.** Novelty against your own recent items, sector spillover, or whether the item names a number that beats guidance. Each is one more key in [`questions.py`](godel_jev_trader/questions.py) and costs almost no latency.
- **Replace the book.** `PaperBook` is the only thing that touches "positions". Swap it for your OMS.

## Links

- Godel API docs: [platform.godelterminal.com/docs](https://platform.godelterminal.com/docs)
- Get a Godel API key: [platform.godelterminal.com/api/keys](https://platform.godelterminal.com/api/keys)
- Jev docs: [docs.typesafe.ai](https://docs.typesafe.ai)

## Disclaimer

This is a demonstration of two APIs, not a trading system. It places no orders, holds no money, and makes no claim that its signals are profitable. Nothing here is investment advice. Check the licence terms of any news you redistribute and the exchange rules of any market data you add.

MIT licensed.
