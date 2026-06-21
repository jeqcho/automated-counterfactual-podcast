"""Recover failed cards (incl. paywalled cards via og:description abstracts) and re-rank
them into their list at the proper position.

For each failed card in the target list: re-extract with current code. If it now yields
readable text OR an 'abstract' (og:description) row, regenerate its digest and mark it a
"mover". Then surgically re-insert the movers into the already-sorted list via binary
insertion (the anchors keep their order — minimal disruption, ~log n comparisons per
mover), and renumber every card's rank marker. Genuinely-dead cards stay [unreadable] at
the bottom. Abstract cards are ranked but remain ok=False (excluded from the podcast).

Pulls + pushes the R2 cache. Dry-run by default.

Run:
    uv run python scripts/recover_and_resort.py                 # dry run, System 1
    uv run python scripts/recover_and_resort.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
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
    if not movers:
        r2_client().upload_file(tmp, config.R2_BUCKET, "state/cache.sqlite3")
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

    r2_client().upload_file(tmp, config.R2_BUCKET, "state/cache.sqlite3")
    log.info(f"re-ranked {len(movers)} movers into {len(ordered)} cards; cache pushed")
    print("APPLY_DONE")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="mutate (default: dry run)")
    ap.add_argument("--list", default="system1", help="system1/system2/life_optim or raw id")
    args = ap.parse_args()
    log = setup_logging("recover-resort")
    asyncio.run(main_async(args, log))


if __name__ == "__main__":
    main()
