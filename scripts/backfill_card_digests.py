"""Re-render every ranked card's description marker to include the FULL impact digest.

The marker used to carry only an 80-char digest snippet; now it shows the one-line rank
tag plus the full digest (for glance-reading on the board). This rewrites existing cards
from the cache — the single source of truth — preserving each card's current rank number
(parsed from its existing marker) and using the cache's digest + est_minutes.

Cards with no existing rank marker, or no cached digest, are left untouched. Dry-run by
default; --apply mutates the board. Pulls the R2 cache read-only (never pushes).

Run:
    uv run python scripts/backfill_card_digests.py            # dry run
    uv run python scripts/backfill_card_digests.py --apply
"""
from __future__ import annotations

import argparse
import re
import tempfile

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.r2 import r2_client
from counterfactual_podcast.trello import TrelloClient

_RANK = re.compile(r"<!--cf-->\[#(\d+)")
LISTS = [
    ("System 1", config.SYSTEM1_LIST_ID),
    ("System 2", config.SYSTEM2_LIST_ID),
    ("Life Optimization", config.LIFE_OPTIM_LIST_ID),
    ("Listen Queue", None),  # resolved by name below
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="mutate the board (default: dry run)")
    args = ap.parse_args()
    log = setup_logging("backfill-digests")

    tmp = tempfile.mktemp(suffix=".sqlite3")
    r2_client().download_file(config.R2_BUCKET, "state/cache.sqlite3", tmp)
    cache = Cache(tmp)
    cl = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)

    total = updated = no_rank = no_digest = 0
    for name, lid in LISTS:
        lid = lid or cl.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
        cards = cl.get_cards(lid)
        n = 0
        for card in cards:
            total += 1
            m = _RANK.search(card.desc or "")
            if not m:
                no_rank += 1
                continue
            rank = int(m.group(1))
            d = cache.get_digest(card.id)
            if not d or not (d.digest or "").strip():
                no_digest += 1
                continue
            if args.apply:
                cl.set_rank_marker(card, rank, d.est_minutes, d.digest)
            n += 1
            updated += 1
        log.info(f"[{name}] {len(cards)} cards — {n} {'updated' if args.apply else 'to update'}")

    log.info(f"{'updated' if args.apply else 'would update'} {updated}/{total} cards "
             f"({no_rank} no rank marker, {no_digest} no cached digest)")
    print("APPLY_DONE" if args.apply else "DRYRUN_DONE")


if __name__ == "__main__":
    main()
