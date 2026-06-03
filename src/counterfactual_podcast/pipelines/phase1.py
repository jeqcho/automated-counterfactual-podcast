"""Phase 1: triage the Inbox, move reading material to 'To Be Processed'.

Reads the native Trello Inbox, classifies each card read-vs-do (cheap Haiku call on
the title/URL — no extraction), and moves only the reading material into the review
list. Todos/notes stay in the Inbox. Title-only: no markers are written here. Jay
then reviews 'To Be Processed' and drags the keepers into '▶ Ready to Process' for
Phase 2. Defaults to dry-run.
"""
from __future__ import annotations

import argparse
import asyncio

from .. import config
from ..inbox import resolve_inbox_list_id


async def run_phase1(client, triager, *, apply: bool = False, log=None) -> dict:
    src = resolve_inbox_list_id(client)
    dest = client.ensure_list(config.TO_BE_PROCESSED_LIST_NAME)
    cards = client.get_cards(src)
    if log:
        log.info(f"inbox has {len(cards)} cards — triaging read-vs-do…")

    verdicts = await asyncio.gather(*[triager.atriage(c) for c in cards])
    moved, kept = [], []
    for card, v in zip(cards, verdicts):
        if v["kind"] == "read":
            if apply:
                client.move_card(card.id, dest, pos="bottom")
            moved.append({"card_id": card.id, "name": card.name, "why": v["why"]})
        else:
            kept.append({"card_id": card.id, "name": card.name, "why": v["why"]})
        if log:
            log.info(f"  [{v['kind']}] {card.name[:55]}")

    return {"inbox": len(cards), "moved_to_review": len(moved),
            "kept_as_todo": len(kept), "moved": moved, "kept": kept, "applied": apply}


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
