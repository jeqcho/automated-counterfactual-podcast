"""Measure real extractable yield across System 1 + Life Optimization (NO LLM, free).

Answers: how many cards are extractable, and how many audio-hours of clean text
exist? Tells us whether the 20h listen-queue target is reachable before we build the
queue for real. Read-only; never mutates the board. Writes outputs/yield_report.json.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from counterfactual_podcast import config
from counterfactual_podcast.extract import extract
from counterfactual_podcast.trello import TrelloClient

SOURCES = {"system1": config.SYSTEM1_LIST_ID, "life_optim": config.LIFE_OPTIM_LIST_ID}


def main():
    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    cards = []
    for name, lid in SOURCES.items():
        for c in client.get_cards(lid):
            cards.append((name, c))
    print(f"Extracting {len(cards)} cards (System1 + LifeOptim), no LLM…")

    rows = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(extract, c): (name, c) for name, c in cards}
        done = 0
        for fut in as_completed(futs):
            name, c = futs[fut]
            try:
                ec = fut.result()
            except Exception as e:  # extract never raises, but be safe
                ec = None
            done += 1
            if done % 50 == 0:
                print(f"  …{done}/{len(cards)}")
            if ec is None:
                rows.append({"list": name, "card_id": c.id, "ok": False,
                             "kind": "error", "minutes": 0, "note": "exception"})
            else:
                rows.append({"list": name, "card_id": c.id, "ok": ec.ok,
                             "kind": ec.kind, "minutes": ec.est_minutes,
                             "note": ec.note[:60]})

    ok = [r for r in rows if r["ok"]]
    hard = [r for r in rows if not r["ok"]]
    minutes = sum(r["minutes"] for r in ok)
    by_reason = {}
    for r in hard:
        key = (r["note"].split(":")[0] or r["kind"])[:30]
        by_reason[key] = by_reason.get(key, 0) + 1

    summary = {
        "total_cards": len(rows),
        "extractable": len(ok),
        "unreadable": len(hard),
        "extractable_audio_hours_est": round(minutes / 60, 1),
        "unreadable_by_reason": by_reason,
        "target_hours": config.TARGET_QUEUE_HOURS,
        "reaches_20h": minutes / 60 >= config.TARGET_QUEUE_HOURS,
    }
    out = config.OUTPUTS / "yield_report.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print("\n=== YIELD SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
