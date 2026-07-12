"""Phase 1: move Inbox links into 'To Be Processed' (no LLM).

Reads the native Trello Inbox and moves every card that has a LINK (a URL in the
name/desc, or carried on card.url from a Trello attachment) into the review list.
Link-less cards (pure todos / notes) stay in the Inbox. No classification, no LLM —
Jay's review of 'To Be Processed' catches anything he doesn't actually want to read
(he drags it back to the Inbox), then presses 'Sort readables' (Phase 2). Dry-run by
default.
"""
from __future__ import annotations

import argparse
import asyncio

from .. import config
from ..extract import find_url
from ..trello import InboxAuthError


# Alert card posted to the board when the Inbox session cookie has expired, so the failure is
# visible in Trello (not just a /logs line). Prefix lets us dedup it across repeated presses.
_COOKIE_ALERT_PREFIX = "⚠️ Trello Inbox cookie expired"
_COOKIE_ALERT = (_COOKIE_ALERT_PREFIX + " — 'Extract readables' can't read your Inbox. "
                 "Refresh it: pbpaste | uv run python scripts/refresh_inbox_cookie.py")


def _has_link(card) -> bool:
    """True if the card carries a URL — in the name/desc or on card.url from a Trello
    attachment. A card with a link is treated as reading material; no link => a todo."""
    return bool(find_url(card) or (card.url or ""))


async def run_phase1(client, *, apply: bool = False, log=None) -> dict:
    dest = client.ensure_list(config.TO_BE_PROCESSED_LIST_NAME)
    # The native Inbox is reachable ONLY via the session cookie (API tokens hard-401 it).
    # If the cookie is missing/expired, surface it clearly but still dedup 'To Be Processed'.
    inbox_error = None
    try:
        cards = client.get_inbox_cards()
    except InboxAuthError as e:
        inbox_error = str(e)
        cards = []
        if log:
            log.error(f"INBOX UNAVAILABLE — {e}. Skipping Inbox move; running dedup only.")

    linked = [c for c in cards if _has_link(c)]
    no_link = [c for c in cards if not _has_link(c)]
    if log and not inbox_error:
        log.info(f"inbox {len(cards)} cards: moving {len(linked)} with links -> "
                 f"'{config.TO_BE_PROCESSED_LIST_NAME}', keeping {len(no_link)} link-less in inbox")

    moved, failed = [], []
    for card in linked:
        if apply:
            try:
                # cross-board move: native Inbox (hidden board) -> Home base list, via cookie.
                client.move_inbox_card(card.id, dest, config.BOARD_ID)
            except InboxAuthError as e:  # cookie died mid-run — no point continuing moves
                inbox_error = str(e)
                if log:
                    log.error(f"session cookie expired mid-run ({e}); stopping moves")
                break
            except Exception as e:  # noqa: BLE001 — one stubborn card must not abort the batch
                failed.append({"card_id": card.id, "name": card.name})
                if log:
                    log.warning(f"  [skip] {card.name[:55]}: {type(e).__name__}: {str(e)[:60]}")
                continue
        moved.append({"card_id": card.id, "name": card.name})
        if log:
            log.info(f"  [move] {card.name[:60]}")

    if log and failed:
        log.warning(f"{len(failed)} cards failed to move (left in Inbox), retry later")

    # If the session cookie is dead, drop a VISIBLE alert card on the board so Jay notices
    # (a /logs line he'd never check isn't enough). Idempotent — one alert, not one per press.
    if inbox_error and apply:
        try:
            existing = {c.name for c in client.get_cards(dest)}
            if not any(n.startswith(_COOKIE_ALERT_PREFIX) for n in existing):
                client.create_card(dest, _COOKIE_ALERT, pos="top")
                if log:
                    log.error("posted a cookie-expired ALERT card to the board")
        except Exception as e:  # noqa: BLE001 — alerting must never crash the run
            if log:
                log.warning(f"could not post alert card: {type(e).__name__}: {str(e)[:60]}")

    # Dedup 'To Be Processed' AFTER moving: archive any card whose URL already appears in the
    # reading lists / Listen Queue or earlier in the list, so the same article never fans out
    # into the lists twice. (Whole-board dedup; conservative URL match — see dedup.py.)
    from ..dedup import dedup_list
    queue_id = client.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    against = [config.SYSTEM1_LIST_ID, config.SYSTEM2_LIST_ID,
               config.LIFE_OPTIM_LIST_ID, queue_id]
    dedup = dedup_list(client, dest, against, apply=apply, log=log)

    return {"inbox": len(cards), "with_links": len(linked), "no_link_kept": len(no_link),
            "moved_to_review": len(moved), "failed": len(failed),
            "deduped": dedup["archived"], "moved": moved, "failed_cards": failed,
            "dedup": dedup, "inbox_error": inbox_error, "applied": apply}


async def _build_and_run(apply: bool, log=None) -> dict:
    from ..trello import TrelloClient
    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN,
                          session_cookie=config.TRELLO_SESSION_COOKIE)
    return await run_phase1(client, apply=apply, log=log)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 1: move Inbox links -> To Be Processed")
    ap.add_argument("--apply", action="store_true", help="move cards (default: dry run)")
    args = ap.parse_args()
    from ..logging_setup import setup_logging
    log = setup_logging("phase1")
    res = asyncio.run(_build_and_run(args.apply, log=log))
    log.info(f"inbox={res['inbox']} -> moved={res['moved_to_review']} "
             f"deduped={res['deduped']} link_less_kept={res['no_link_kept']} "
             f"applied={res['applied']}")


if __name__ == "__main__":
    main()
