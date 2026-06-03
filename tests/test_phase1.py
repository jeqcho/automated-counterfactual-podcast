import pytest

from counterfactual_podcast import config
from counterfactual_podcast.models import Card
from counterfactual_podcast.pipelines.phase1 import run_phase1


class FakeClient:
    def __init__(self, inbox):
        self._inbox = inbox
        self.moved = []

    def inbox_list_id(self):
        return "INBOX"

    def ensure_list(self, name):
        return "TBP"

    def get_cards(self, list_id):
        return list(self._inbox) if list_id == "INBOX" else []

    def move_card(self, card_id, list_id, pos="bottom", board_id=None):
        self.moved.append((card_id, list_id))


class FakeTriager:
    def __init__(self, kinds):
        self.kinds = kinds

    async def atriage(self, card):
        return {"kind": self.kinds[card.id], "why": "x"}


async def test_phase1_moves_only_linked_reading_material():
    # r1/r2 have links; d1 has a link but is a todo; n1 has NO link.
    inbox = [
        Card("r1", "article", url="https://x.org/a"),
        Card("d1", "apply to fellowship", url="https://apply.org"),
        Card("r2", "paper", url="https://arxiv.org/abs/1"),
        Card("n1", "Lesson: a link-less note"),   # no url -> never triaged/moved
    ]
    kinds = {"r1": "read", "d1": "do", "r2": "read"}  # n1 not triaged
    client = FakeClient(inbox)
    res = await run_phase1(client, FakeTriager(kinds), apply=True)
    assert res["with_links"] == 3 and res["no_link_kept"] == 1
    assert res["moved_to_review"] == 2 and res["kept_as_todo"] == 1
    moved_ids = {cid for cid, _ in client.moved}
    assert moved_ids == {"r1", "r2"}                 # only LINKED reading material moved
    assert "n1" not in moved_ids                     # link-less card stays in inbox


async def test_phase1_skips_linkless_cards_entirely():
    inbox = [Card("n1", "a note"), Card("n2", "another note")]
    triager = FakeTriager({})                        # would KeyError if called
    res = await run_phase1(FakeClient(inbox), triager, apply=True)
    assert res["with_links"] == 0 and res["no_link_kept"] == 2
    assert res["moved_to_review"] == 0


async def test_phase1_dry_run_moves_nothing():
    inbox = [Card("r1", "article", url="https://x.org/a")]
    client = FakeClient(inbox)
    res = await run_phase1(client, FakeTriager({"r1": "read"}), apply=False)
    assert res["moved_to_review"] == 1               # would move
    assert client.moved == []                        # but didn't (dry run)
