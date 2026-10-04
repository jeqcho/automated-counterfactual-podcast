"""Recover failed cards (incl. paywalled cards via og:description abstracts) and re-rank
them into their list at the proper position.

For each failed card in the target list: re-extract with current code. If it now yields
readable text OR an 'abstract' (og:description) row, regenerate its digest and mark it a
"mover". Then surgically re-insert the movers into the already-sorted list via binary
insertion (the anchors keep their order — minimal disruption, ~log n comparisons per
mover), and renumber every card's rank marker. Genuinely-dead cards stay [unreadable] at
the bottom. Abstract cards are ranked but remain ok=False (excluded from the podcast).

--ids-from FILE (a JSON list of {"card_id": ...}, e.g. outputs/tracker_card_fixes.json)
targets exactly those cards instead of the failed ones: each is re-extracted even if its old
extraction "worked", and its cached pairwise comparisons are deleted first (they were judged
on the old digest, and the pairwise cache is keyed by card id only). Used 2026-10-04 to
re-rank newsletter cards that had been ranked on their Mailchimp tracker URL.

Pulls + pushes the R2 cache (integrity-checked push). Dry-run by default.

Run:
    uv run python scripts/recover_and_resort.py                 # dry run, System 1
    uv run python scripts/recover_and_resort.py --apply
    uv run python scripts/recover_and_resort.py --list system2 \
        --ids-from outputs/tracker_card_fixes.json --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache, push_cache_to_r2
from counterfactual_podcast.enrich import Enricher
from counterfactual_podcast.extract import extract as do_extract
from counterfactual_podcast.llm_compare import Comparator
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.models import CardFeatures
from counterfactual_podcast.r2 import r2_client
from counterfactual_podcast.sort import insert_sorted
from counterfactual_podcast.trello import TrelloClient

LISTS = {"system1": config.SYSTEM1_LIST_ID, "system2": config.SYSTEM2_LIST_ID,
         "life_optim": config.LIFE_OPTIM_LIST_ID}


def _failed(ec, d) -> bool:
    """Needs a re-extraction attempt. 'abstract' rows already succeeded (ok=False but with
    a real og:description), so they're NOT failed."""
    if ec is None:
        return True
    if ec.kind == "abstract":
        return False
    if not ec.ok:
        return True
    return bool(d and (d.digest or "").startswith("[unreadable"))


async def main_async(args, log):
    tmp = tempfile.mktemp(suffix=".sqlite3")
    r2_client().download_file(config.R2_BUCKET, "state/cache.sqlite3", tmp)
    cache = Cache(tmp)
    cl = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    profile = config.PROFILE_DOC.read_text(encoding="utf-8")
    enricher = Enricher(cache=cache, profile_doc=profile)
    comparator = Comparator(cache=cache, profile_doc=profile)

    lid = LISTS.get(args.list, args.list)
    cards = cl.get_cards(lid)
    by_id = {c.id: c for c in cards}
    if args.ids_from:
        wanted = {r["card_id"] for r in json.load(open(args.ids_from))}
        failed = [c for c in cards if c.id in wanted]
        log.info(f"[{args.list}] {len(cards)} cards, {len(failed)} targeted by "
                 f"{args.ids_from} — re-extracting")
    else:
        failed = [c for c in cards
                  if _failed(cache.get_extracted(c.id), cache.get_digest(c.id))]
        log.info(f"[{args.list}] {len(cards)} cards, {len(failed)} failed — re-extracting")

    # 1. Re-extract failures (parallel, network-bound).
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=8) as pool:
        new_ecs = await asyncio.gather(
            *[loop.run_in_executor(pool, do_extract, c) for c in failed])

    movers, recovered, abstract, still = [], 0, 0, 0
    for card, ec in zip(failed, new_ecs):
        readable = ec.text.strip() and (ec.ok or ec.kind == "abstract")
        if not args.apply:
            if readable:
                movers.append(card)
                if ec.kind == "abstract":
                    abstract += 1
                else:
                    recovered += 1
            else:
                still += 1
            continue
        cache.put_extracted(ec)
        if readable:
            digest = await enricher._ask_digest(ec.title, ec.text)
            movers.append(card)
            recovered += ec.kind != "abstract"
            abstract += ec.kind == "abstract"
            log.info(f"  ✓ {'ABSTRACT' if ec.kind=='abstract' else 'RECOVERED'} "
                     f"{card.name[:42]} ({len(ec.text)} ch)")
        else:
            digest = f"[unreadable: {ec.note or ec.kind}] {ec.title}"
            still += 1
        cache.put_digest(CardFeatures(card.id, ec.title, ec.est_minutes, digest,
                                      ec.kind, ec.ok), model=enricher.model)
    log.info(f"recovered {recovered}, abstract {abstract}, still hard {still}")

    if not args.apply:
        for c in movers:
            log.info(f"  would re-rank {c.name[:55]}")
        print("DRYRUN_DONE")
        return
    if args.ids_from and movers:
        # Their cached comparisons were judged on the OLD digest; drop them so the
        # re-insertion actually re-asks the model.
        ids = [c.id for c in movers]
        marks = ",".join("?" * len(ids))
        n = cache.conn.execute(
            f"DELETE FROM pairwise WHERE a_id IN ({marks}) OR b_id IN ({marks})",
            ids + ids).rowcount
        cache.conn.commit()
        log.info(f"purged {n} stale pairwise rows for {len(ids)} movers")
    if not movers:
        cache.close()
        if not push_cache_to_r2(path=tmp):
            raise SystemExit("cache push to R2 failed/refused — nothing written to R2")
        log.info("no movers; cache pushed")
        print("APPLY_DONE")
        return

    # 2. Re-rank: anchors keep order; binary-insert each mover.
    mover_ids = {c.id for c in movers}
    anchors = [cache.get_digest(c.id) for c in cards if c.id not in mover_ids]
    ordered = [f for f in anchors if f is not None]
    for c in movers:
        ordered = await insert_sorted(cache.get_digest(c.id), ordered, comparator.acompare)
        log.info(f"  inserted {c.name[:45]} -> rank "
                 f"#{next(i for i, f in enumerate(ordered) if f.card_id == c.id) + 1}")

    # 3. Apply positions + renumber every marker (digests from cache).
    for i, f in enumerate(ordered):
        cl.set_card_position(f.card_id, (i + 1) * 1000.0)
        cl.set_rank_marker(by_id[f.card_id], i + 1, f.est_minutes, f.digest or "")

    cache.close()
    if not push_cache_to_r2(path=tmp):
        raise SystemExit("cache push to R2 failed/refused — Trello was updated but R2 wasn't")
    log.info(f"re-ranked {len(movers)} movers into {len(ordered)} cards; cache pushed")
    print("APPLY_DONE")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="mutate (default: dry run)")
    ap.add_argument("--list", default="system1", help="system1/system2/life_optim or raw id")
    ap.add_argument("--ids-from", help="JSON list of {card_id} to re-extract + re-rank")
    args = ap.parse_args()
    log = setup_logging("recover-resort")
    asyncio.run(main_async(args, log))


if __name__ == "__main__":
    main()
