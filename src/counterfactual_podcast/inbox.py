"""Collect the native Trello Inbox into the working list.

The weekly pipeline starts by draining Jay's Trello **Inbox** (the native
member inbox, resolved via ``member.inbox.idList``) into a normal board list
("To Be Processed") where the rest of the pipeline can pick the cards up.

The operation is idempotent and safe on an empty inbox. If the native inbox
can't be resolved (missing / API hiccup), we degrade gracefully to a regular
board list named ``"Inbox"`` via :func:`resolve_inbox_list_id`.
"""
from __future__ import annotations

from . import config
from .models import Card


def resolve_inbox_list_id(client, fallback_name: str = "Inbox") -> str:
    """Return the inbox list id, falling back to a regular board list.

    Tries ``client.inbox_list_id()`` first (the native Trello Inbox). If that
    raises or returns a falsy value, fall back to ``client.ensure_list(name)``
    so a missing native inbox degrades gracefully.
    """
    try:
        src = client.inbox_list_id()
    except Exception:
        src = None
    if not src:
        src = client.ensure_list(fallback_name)
    return src


def collect_inbox(
    client, *, to_list_name: str = config.TO_BE_PROCESSED_LIST_NAME
) -> list[Card]:
    """Move every inbox card into ``to_list_name`` and return the moved cards.

    Idempotent and safe on an empty inbox (returns ``[]``). If the resolved
    inbox list id happens to equal the destination, nothing is moved.
    """
    src = resolve_inbox_list_id(client)
    dest = client.ensure_list(to_list_name)
    if src == dest:
        return []

    cards = client.get_cards(src)
    moved: list[Card] = []
    for card in cards:
        client.move_card(card.id, dest, pos="bottom")
        card.list_id = dest
        moved.append(card)
    return moved
