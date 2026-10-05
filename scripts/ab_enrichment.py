"""A/B: does the API's instrument enrichment change what Jev concludes?

For every saved item that carries `instruments`, Jev is asked the same questions twice:

  plain     — companies as the bare `tickers` describe them, name = ticker
  enriched  — companies as `instruments` describe them: real name, ticker, price trend

Both variants ask the `subject` question (the API sends no relevance), so subject, material, tone
and event can be compared one to one on the companies both variants name. Output:

  ab_pairs.csv     one row per (item, company) with both variants' answers and an empty
                   `truth_subject` column to fill in by hand (1 = the company is a primary
                   subject of the item, 0 = not); scored with --labels
  ab_summary.txt   aggregate deltas

Usage:
  python scripts/ab_enrichment.py items.json            # run Jev, write ab_pairs.csv + ab_summary.txt
  python scripts/ab_enrichment.py --label-sheet items.json ab_pairs.csv   # rows with the lead, to label
  python scripts/ab_enrichment.py --labels ab_pairs.csv # after filling truth_subject: accuracy, Brier
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from godel_jev_trader.decide import Thresholds, decide  # noqa: E402
from godel_jev_trader.godel import LinkedCompany, NewsItem  # noqa: E402
from godel_jev_trader.jev import Jev  # noqa: E402
from godel_jev_trader.questions import build_questions, build_state, key  # noqa: E402

MAX_COMPANIES = 6  # per item per variant; bounds Jev cost on items tagged with dozens of symbols


def load_env(path: Path) -> None:
    if path.exists():
        import os
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def plain_companies(item: NewsItem) -> list[LinkedCompany]:
    return [LinkedCompany(t, t) for t in item.tickers[:MAX_COMPANIES]]


def enriched_companies(item: NewsItem) -> list[LinkedCompany]:
    return list(item.companies[:MAX_COMPANIES])


def ask(jev: Jev, item: NewsItem, companies: list[LinkedCompany]) -> tuple[dict, float, dict]:
    r = jev.ask(build_state(item, companies), build_questions(companies))
    return r.answers, r.latency_ms, r.usage


def run(items_path: Path) -> None:
    load_env(Path(__file__).resolve().parents[1] / ".env")
    jev = Jev()
    items = [NewsItem.from_json(d) for d in json.loads(items_path.read_text())]
    items = [i for i in items if i.companies and i.tickers]
    print(f"{len(items)} items with instruments", file=sys.stderr)

    rows: list[dict] = []
    lat = {"plain": [], "enriched": []}
    tokens = {"plain": 0, "enriched": 0}
    for n, item in enumerate(items, 1):
        plain, enriched = plain_companies(item), enriched_companies(item)
        a_p, l_p, u_p = ask(jev, item, plain)
        a_e, l_e, u_e = ask(jev, item, enriched)
        lat["plain"].append(l_p); lat["enriched"].append(l_e)
        tokens["plain"] += u_p.get("total_tokens", 0) or 0; tokens["enriched"] += u_e.get("total_tokens", 0) or 0
        by_ticker_e = {c.symbol: c for c in enriched}
        for c in plain:
            e = by_ticker_e.get(c.symbol)
            d_p = decide(c, a_p)
            d_e = decide(e, a_e) if e else None
            rows.append({
                "item_id": item.id, "published_at": item.published_at, "title": item.title[:140].replace("\n", " "),
                "symbol": c.symbol, "name_enriched": e.name if e else "",
                "in_enriched": int(e is not None),
                "subject_plain": round(a_p[key(c.key, "subject")]["noul"], 3),
                "subject_enriched": round(a_e[key(e.key, "subject")]["noul"], 3) if e else "",
                "material_plain": round(d_p.material, 3), "material_enriched": round(d_e.material, 3) if d_e else "",
                "tone_plain": d_p.tone, "tone_enriched": d_e.tone if d_e else "",
                "event_plain": d_p.event, "event_enriched": d_e.event if d_e else "",
                "action_plain": d_p.action, "action_enriched": d_e.action if d_e else "",
                "reason_plain": d_p.reason, "reason_enriched": d_e.reason if d_e else "",
                "truth_subject": "",
            })
        # companies the enrichment names that no ticker matched (name-only listings, e.g. HK codes)
        for e in enriched:
            if e.symbol not in {c.symbol for c in plain}:
                d_e = decide(e, a_e)
                rows.append({
                    "item_id": item.id, "published_at": item.published_at, "title": item.title[:140].replace("\n", " "),
                    "symbol": e.symbol, "name_enriched": e.name, "in_enriched": 1,
                    "subject_plain": "", "subject_enriched": round(a_e[key(e.key, "subject")]["noul"], 3),
                    "material_plain": "", "material_enriched": round(d_e.material, 3),
                    "tone_plain": "", "tone_enriched": d_e.tone, "event_plain": "", "event_enriched": d_e.event,
                    "action_plain": "", "action_enriched": d_e.action, "reason_plain": "", "reason_enriched": d_e.reason,
                    "truth_subject": "",
                })
        print(f"  {n}/{len(items)} {item.title[:60]}  plain {l_p:.0f} ms / enriched {l_e:.0f} ms", file=sys.stderr)

    out = Path("ab_pairs.csv")
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    both = [r for r in rows if r["subject_plain"] != "" and r["subject_enriched"] != ""]
    sp = [float(r["subject_plain"]) for r in both]; se = [float(r["subject_enriched"]) for r in both]
    def decisive(xs): return statistics.mean(abs(x - 0.5) for x in xs) if xs else float("nan")
    changed = sum(1 for r in both if r["action_plain"] != r["action_enriched"])
    lines = [
        f"items: {len(items)}   company rows: {len(rows)}   compared on both variants: {len(both)}",
        f"plain pairs per item: {sum(1 for r in rows if r['subject_plain'] != '')/len(items):.1f}   enriched: {sum(1 for r in rows if r['in_enriched'])/len(items):.1f}",
        f"subject p: plain mean {statistics.mean(sp):.2f} (decisiveness {decisive(sp):.2f})   enriched mean {statistics.mean(se):.2f} (decisiveness {decisive(se):.2f})",
        f"subject |shift| mean {statistics.mean(abs(a-b) for a,b in zip(sp,se)):.2f}; moved >0.25 on {sum(1 for a,b in zip(sp,se) if abs(a-b)>0.25)} of {len(both)}",
        f"decision changed on {changed} of {len(both)} pairs; event label changed on {sum(1 for r in both if r['event_plain'] != r['event_enriched'])}",
        f"Jev latency p50: plain {statistics.median(lat['plain']):.0f} ms, enriched {statistics.median(lat['enriched']):.0f} ms; tokens plain {tokens['plain']}, enriched {tokens['enriched']}",
        "", "fill truth_subject in ab_pairs.csv, then: python scripts/ab_enrichment.py --labels ab_pairs.csv",
    ]
    Path("ab_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def score(labels_path: Path) -> None:
    """Two views. Per variant, on every labelled row that variant produced: how well does each
    pipeline identify the story's subjects among the companies *it* names (the product question —
    `tickers` and `instruments` name different companies). Then on the rows both variants share:
    the effect of the name and price alone, same company, same text."""
    rows = [r for r in csv.DictReader(labels_path.open()) if r["truth_subject"] in ("0", "1")]
    if not rows:
        sys.exit("no labelled rows")

    def metrics(sub, col):
        ps = [float(r[col]) for r in sub]; ys = [int(r["truth_subject"]) for r in sub]
        acc = sum((p >= 0.5) == bool(y) for p, y in zip(ps, ys)) / len(ps)
        brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)
        ll = -sum(math.log(max(1e-6, p if y else 1 - p)) for p, y in zip(ps, ys)) / len(ps)
        pos = sum(ys)
        tp = sum(1 for p, y in zip(ps, ys) if p >= 0.5 and y); fp = sum(1 for p, y in zip(ps, ys) if p >= 0.5 and not y)
        prec = tp / (tp + fp) if tp + fp else float("nan"); rec = tp / pos if pos else float("nan")
        return f"n={len(sub):<3} subjects={pos:<3} accuracy {acc:.1%}  precision {prec:.2f}  recall {rec:.2f}  Brier {brier:.3f}  log-loss {ll:.3f}"

    print("each variant on the companies it names:")
    for name, col in (("plain", "subject_plain"), ("enriched", "subject_enriched")):
        sub = [r for r in rows if r[col] != ""]
        if sub:
            print(f"  {name:9} {metrics(sub, col)}")
    both = [r for r in rows if r["subject_plain"] != "" and r["subject_enriched"] != ""]
    if both:
        print("same company, same text, name and price added:")
        for name, col in (("plain", "subject_plain"), ("enriched", "subject_enriched")):
            print(f"  {name:9} {metrics(both, col)}")


def label_sheet(items_path: Path, pairs_path: Path) -> None:
    """Print every unlabelled row with the item's lead, for filling truth_subject in."""
    content = {d["id"]: d.get("content") or "" for d in json.loads(items_path.read_text())}
    for n, r in enumerate(csv.DictReader(pairs_path.open())):
        if r["truth_subject"] in ("0", "1"):
            continue
        lead = " ".join(content.get(r["item_id"], "").split())[:400]
        print(f"[{n}] {r['symbol']}  {r['name_enriched'] or '(no enrichment)'}\n    {r['title']}\n    {lead}\n")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--labels":
        score(Path(sys.argv[2]))
    elif len(sys.argv) == 4 and sys.argv[1] == "--label-sheet":
        label_sheet(Path(sys.argv[2]), Path(sys.argv[3]))
    elif len(sys.argv) == 2:
        run(Path(sys.argv[1]))
    else:
        sys.exit(__doc__)
