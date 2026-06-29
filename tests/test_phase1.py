import pytest

from counterfactual_podcast import config
from counterfactual_podcast.models import Card
from counterfactual_podcast.pipelines.phase1 import run_phase1


class FakeClient:
    def __init__(self, inbox):
        self._inbox = inbox
        self.moved = []

    def ensure_list(self, name):
        return "TBP"

    def get_inbox_cards(self):
        return list(self._inbox)

    def move_card(self, card_id, list_id, pos="bottom", board_id=None, retry_unauthorized=False):
        self.moved.append((card_id, list_id))


async def test_phase1_moves_every_linked_card_no_llm():
    # All three have links -> all move (no read/do classification). n1 has no link -> stays.
    inbox = [
        Card("r1", "article", url="https://x.org/a"),
        Card("d1", "apply to fellowship", url="https://apply.org"),   # has a link -> moves now
        Card("r2", "paper", url="https://arxiv.org/abs/1"),
        Card("n1", "Lesson: a link-less note"),                       # no url -> stays in inbox
    ]
    client = FakeClient(inbox)
    res = await run_phase1(client, apply=True)
    assert res["with_links"] == 3 and res["no_link_kept"] == 1
    assert res["moved_to_review"] == 3
    moved_ids = {cid for cid, _ in client.moved}
    assert moved_ids == {"r1", "d1", "r2"}   # every linked card moved, including the "todo"
    assert "n1" not in moved_ids             # link-less card stays in inbox


async def test_phase1_skips_linkless_cards():
    inbox = [Card("n1", "a note"), Card("n2", "another note")]
    res = await run_phase1(FakeClient(inbox), apply=True)
    assert res["with_links"] == 0 and res["no_link_kept"] == 2
    assert res["moved_to_review"] == 0


async def test_phase1_finds_link_in_name_or_desc():
    inbox = [
        Card("a", "read this https://blog.test/x"),           # url in name
        Card("b", "thoughts", desc="see http://ref.test/y"),  # url in desc
    ]
    client = FakeClient(inbox)
    res = await run_phase1(client, apply=True)
    assert res["moved_to_review"] == 2
    assert {cid for cid, _ in client.moved} == {"a", "b"}


async def test_phase1_dry_run_moves_nothing():
    inbox = [Card("r1", "article", url="https://x.org/a")]
    client = FakeClient(inbox)
    res = await run_phase1(client, apply=False)
    assert res["moved_to_review"] == 1   # would move
    assert client.moved == []            # but didn't (dry run)
