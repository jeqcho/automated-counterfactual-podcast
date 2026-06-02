"""Tests for inbox collection — fully mocked client (no real network)."""
from __future__ import annotations

import pytest

from counterfactual_podcast import config
from counterfactual_podcast.inbox import collect_inbox, resolve_inbox_list_id
from counterfactual_podcast.models import Card


class FakeClient:
    """Minimal in-memory stand-in for TrelloClient (no HTTP)."""

    def __init__(self, *, inbox_id="INBOX", cards=None, inbox_raises=False):
        self._inbox_id = inbox_id
        self._inbox_raises = inbox_raises
        # Map list name -> list id, seeded so ensure_list is deterministic.
        self._lists = {config.TO_BE_PROCESSED_LIST_NAME: "TBP", "Inbox": "FALLBACK"}
        # Map list id -> cards in it.
        self._cards = {inbox_id: list(cards or [])}
        self.move_calls: list[tuple[str, str, str]] = []
        self.ensure_calls: list[str] = []

    def inbox_list_id(self):
        if self._inbox_raises:
            raise RuntimeError("no native inbox")
        return self._inbox_id

    def ensure_list(self, name):
        self.ensure_calls.append(name)
        if name not in self._lists:
            self._lists[name] = f"id-{name}"
        return self._lists[name]

    def get_cards(self, list_id):
        return list(self._cards.get(list_id, []))

    def move_card(self, card_id, list_id, pos="bottom"):
        self.move_calls.append((card_id, list_id, pos))


def _cards(n):
    return [Card(id=f"c{i}", name=f"Card {i}", list_id="INBOX") for i in range(n)]


def test_moves_all_inbox_cards_to_destination():
    n = 3
    client = FakeClient(cards=_cards(n))
    moved = collect_inbox(client)

    assert len(moved) == n
    assert len(client.move_calls) == n
    assert all(call[1] == "TBP" for call in client.move_calls)
    assert all(call[2] == "bottom" for call in client.move_calls)
    assert [c.id for c in moved] == ["c0", "c1", "c2"]
    assert all(c.list_id == "TBP" for c in moved)


def test_empty_inbox_returns_empty_and_no_moves():
    client = FakeClient(cards=[])
    moved = collect_inbox(client)

    assert moved == []
    assert client.move_calls == []


def test_src_equals_dest_skips():
    # Native inbox resolves to the same id as the destination list.
    client = FakeClient(inbox_id="TBP", cards=_cards(2))
    moved = collect_inbox(client)

    assert moved == []
    assert client.move_calls == []


def test_resolve_falls_back_when_inbox_raises():
    client = FakeClient(inbox_raises=True)
    src = resolve_inbox_list_id(client)

    assert src == "FALLBACK"
    assert client.ensure_calls == ["Inbox"]


def test_resolve_falls_back_when_inbox_falsy():
    client = FakeClient(inbox_id="")
    src = resolve_inbox_list_id(client)

    assert src == "FALLBACK"
    assert client.ensure_calls == ["Inbox"]


def test_resolve_uses_native_inbox_when_available():
    client = FakeClient(inbox_id="INBOX")
    src = resolve_inbox_list_id(client)

    assert src == "INBOX"
    assert client.ensure_calls == []


def test_collect_falls_back_to_named_inbox_list():
    # No native inbox -> collect_inbox drains the fallback "Inbox" list.
    client = FakeClient(inbox_raises=True)
    client._cards["FALLBACK"] = _cards(2)
    moved = collect_inbox(client)

    assert len(moved) == 2
    assert len(client.move_calls) == 2
    assert all(call[1] == "TBP" for call in client.move_calls)
    assert client.ensure_calls == ["Inbox", config.TO_BE_PROCESSED_LIST_NAME]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
