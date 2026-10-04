"""Rename existing newsletter click-tracker cards (us.list-manage.com/…) on the whole board.

New cards get this in Phase 1 (``link_titles.tidy_cards``); this backfills cards that were
moved before that existed. For each open card whose name is a tracker URL: unwrap it to the
real article, attach that URL, rename to the page title (slug + domain if the page blocks
us). Dry-run by default; --apply mutates. Old names go to outputs/tracker_card_fixes.json
(undo manifest).

Note: cards already ranked were ranked on the tracker (often a failed, title-only
extraction); renaming doesn't re-rank them.

Run:
    uv run python scripts/fix_tracker_cards.py            # dry run
    uv run python scripts/fix_tracker_cards.py --apply
"""
from __future__ import annotations

import argparse
import json

from counterfactual_podcast import config
from counterfactual_podcast.extract import find_url
from counterfactual_podcast.link_titles import is_tracker, tidy_cards
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.trello import TrelloClient


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually mutate (default: dry run)")
    args = ap.parse_args()
    log = setup_logging("fix-tracker-cards")
    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)

    lists = client._request("GET", f"/1/boards/{config.BOARD_ID}/lists", fields="name")
    targets = []
    for lst in lists:
        cards = [c for c in client.get_cards(lst["id"])
                 if is_tracker(find_url(c) or c.url) and is_tracker(c.name.strip())]
        if cards:
            log.info(f"[{lst['name']}] {len(cards)} tracker cards")
            targets += cards

    plans = tidy_cards(client, targets, apply=args.apply, log=log)
    out = config.OUTPUTS / "tracker_card_fixes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plans, indent=2))
    done = sum(1 for p in plans if p.get("applied"))
    log.info(f"{len(targets)} tracker cards, {len(plans)} fixable, {done} applied. "
             f"Manifest -> {out}")


if __name__ == "__main__":
    main()
