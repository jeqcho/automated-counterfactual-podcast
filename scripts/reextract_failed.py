"""Re-extract cards whose cached extraction FAILED and recover the ones that now work.

Many "unreadable" cards are stale failures — fetched before the browser-UA fix, before
two-pass extraction, or during a transient network blip. Re-running extraction with the
current code recovers a good chunk. For each failed card this: re-extracts; if it now
succeeds, regenerates the Haiku digest, updates the cache, and re-renders the card's
description marker (preserving its rank) so it no longer reads "[unreadable]". Genuinely
hard sources (paywalls, archive.ph, X/YouTube, true homepages) stay failed.

NOTE: recovering content does NOT re-rank the card — it keeps its current bottom-ish
position until the list is re-sorted. (Run scripts/run_oneshot.sh to re-rank.)

Pulls + pushes the R2 cache (preserves all other rows). Dry-run by default.

Run:
    uv run python scripts/reextract_failed.py                  # dry run, System 1
    uv run python scripts/reextract_failed.py --apply
    uv run python scripts/reextract_failed.py --list system1 --list life_optim --apply
"""
from __future__ import annotations

import argparse
import asyncio
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.enrich import Enricher
from counterfactual_podcast.extract import extract as do_extract
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.models import CardFeatures
from counterfactual_podcast.r2 import r2_client
from counterfactual_podcast.trello import TrelloClient

LISTS = {"system1": config.SYSTEM1_LIST_ID, "system2": config.SYSTEM2_LIST_ID,
         "life_optim": config.LIFE_OPTIM_LIST_ID}
_RANK = re.compile(r"<!--cf-->\[#(\d+)")


def _failed(ec, d) -> bool:
    if ec is None or not ec.ok:
        return True
    return bool(d and (d.digest or "").startswith("[unreadable"))


async def main_async(args, log):
    tmp = tempfile.mktemp(suffix=".sqlite3")
    r2_client().download_file(config.R2_BUCKET, "state/cache.sqlite3", tmp)
    cache = Cache(tmp)
    cl = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    profile = config.PROFILE_DOC.read_text(encoding="utf-8")
    enricher = Enricher(cache=cache, profile_doc=profile)

    failed = []  # (card, rank)
    for name in (args.lists or ["system1"]):
        lid = LISTS.get(name, name)
        for card in cl.get_cards(lid):
            ec, d = cache.get_extracted(card.id), cache.get_digest(card.id)
            if _failed(ec, d):
                m = _RANK.search(card.desc or "")
                failed.append((card, int(m.group(1)) if m else None))
    log.info(f"{len(failed)} failed cards across {args.lists or ['system1']}")

    if not args.apply:
        for card, _ in failed:
            log.info(f"  would re-extract {card.name[:55]}")
        print("DRYRUN_DONE")
        return

    # Re-extract in parallel (network-bound).
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=8) as pool:
        new_ecs = await asyncio.gather(
            *[loop.run_in_executor(pool, do_extract, c) for c, _ in failed])

    recovered = still = 0
    for (card, rank), ec in zip(failed, new_ecs):
        cache.put_extracted(ec)
        if ec.ok and ec.text.strip():
            try:
                digest = await enricher._ask_digest(ec.title, ec.text)
            except Exception as e:  # noqa: BLE001 — digest LLM unavailable (e.g. no credits)
                log.error(f"DIGEST API FAILED ({type(e).__name__}: {str(e)[:120]}). "
                          f"Aborting WITHOUT pushing — fix the API and re-run.")
                print("ABORTED_API")
                return
            recovered += 1
            if rank is not None:
                cl.set_rank_marker(card, rank, ec.est_minutes, digest)
            log.info(f"  ✓ RECOVERED {card.name[:45]} ({len(ec.text)} chars)")
        else:
            digest = f"[unreadable: {ec.note or ec.kind}] {ec.title}"
            still += 1
            log.info(f"  · still hard: {card.name[:42]} — {(ec.note or '')[:40]}")
        cache.put_digest(CardFeatures(card.id, ec.title, ec.est_minutes, digest,
                                      ec.kind, ec.ok), model=enricher.model)

    r2_client().upload_file(tmp, config.R2_BUCKET, "state/cache.sqlite3")
    log.info(f"recovered {recovered}, still hard {still}; cache pushed to R2")
    print("APPLY_DONE")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="re-extract + mutate (default: dry run)")
    ap.add_argument("--list", action="append", dest="lists",
                    help="system1/system2/life_optim or raw id; repeatable (default: system1)")
    args = ap.parse_args()
    log = setup_logging("reextract-failed")
    asyncio.run(main_async(args, log))


if __name__ == "__main__":
    main()
