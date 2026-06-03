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

    def move_card(self, card_id, list_id, pos="bottom"):
        self.moved.append((card_id, list_id))


class FakeTriager:
    def __init__(self, kinds):
        self.kinds = kinds

    async def atriage(self, card):
        return {"kind": self.kinds[card.id], "why": "x"}


async def test_phase1_moves_only_reading_material():
    inbox = [Card("r1", "article"), Card("d1", "todo"), Card("r2", "paper")]
    kinds = {"r1": "read", "d1": "do", "r2": "read"}
    client = FakeClient(inbox)
    res = await run_phase1(client, FakeTriager(kinds), apply=True)
    assert res["moved_to_review"] == 2 and res["kept_as_todo"] == 1
    moved_ids = {cid for cid, _ in client.moved}
    assert moved_ids == {"r1", "r2"}                 # only reading material moved
    assert all(dest == "TBP" for _, dest in client.moved)


async def test_phase1_dry_run_moves_nothing():
    inbox = [Card("r1", "article")]
    client = FakeClient(inbox)
    res = await run_phase1(client, FakeTriager({"r1": "read"}), apply=False)
    assert res["moved_to_review"] == 1               # would move
    assert client.moved == []                        # but didn't (dry run)
