"""Phase 1: triage the Inbox, move reading material to 'To Be Processed'.

Reads the native Trello Inbox, classifies each card read-vs-do (cheap Haiku call on
the title/URL — no extraction), and moves only the reading material into the review
list. Todos/notes stay in the Inbox. Title-only: no markers are written here. Jay
then reviews 'To Be Processed', drags any wrong cards back to the Inbox, and presses
'Sort readables' (Phase 2) to process whatever remains. Defaults to dry-run.
"""
from __future__ import annotations

import argparse
import asyncio

from .. import config
from ..extract import find_url
from ..inbox import resolve_inbox_list_id


def _has_link(card) -> bool:
    """A card is a candidate readable only if it actually has a URL — in the name/desc
    or carried on card.url from a Trello attachment. No link => nothing to read."""
    return bool(find_url(card) or (card.url or ""))


async def run_phase1(client, triager, *, apply: bool = False, log=None) -> dict:
    src = resolve_inbox_list_id(client)
    dest = client.ensure_list(config.TO_BE_PROCESSED_LIST_NAME)
    cards = client.get_cards(src)

    # Gate on links FIRST: only run Haiku on cards that have a URL.
    linked = [c for c in cards if _has_link(c)]
    no_link = [c for c in cards if not _has_link(c)]
    if log:
        log.info(f"inbox {len(cards)} cards: {len(linked)} have links (triaging), "
                 f"{len(no_link)} link-less (kept in inbox)")

    verdicts = await asyncio.gather(*[triager.atriage(c) for c in linked])
    moved, kept = [], []
    for card, v in zip(linked, verdicts):
        if v["kind"] == "read":
            if apply:
                # cross-board move: native Inbox (hidden board) -> Home base list
                client.move_card(card.id, dest, pos="bottom", board_id=config.BOARD_ID)
            moved.append({"card_id": card.id, "name": card.name, "why": v["why"]})
        else:
            kept.append({"card_id": card.id, "name": card.name, "why": v["why"]})
        if log:
            log.info(f"  [{v['kind']}] {card.name[:55]}")

    return {"inbox": len(cards), "with_links": len(linked), "no_link_kept": len(no_link),
            "moved_to_review": len(moved), "kept_as_todo": len(kept),
            "moved": moved, "kept": kept, "applied": apply}


async def _build_and_run(apply: bool, log=None) -> dict:
    from ..triage import InboxTriager
    from ..trello import TrelloClient
    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    return await run_phase1(client, InboxTriager(), apply=apply, log=log)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 1: triage Inbox -> To Be Processed")
    ap.add_argument("--apply", action="store_true", help="move cards (default: dry run)")
    args = ap.parse_args()
    from ..logging_setup import setup_logging
    log = setup_logging("phase1")
    res = asyncio.run(_build_and_run(args.apply, log=log))
    log.info(f"inbox={res['inbox']} -> review={res['moved_to_review']} "
             f"todos_kept={res['kept_as_todo']} applied={res['applied']}")


if __name__ == "__main__":
    main()
