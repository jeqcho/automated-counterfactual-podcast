import pytest

from counterfactual_podcast import config
from counterfactual_podcast.models import Card, CardFeatures
from counterfactual_podcast.pipelines.phase2 import run_phase2


class FakeClient:
    def __init__(self, trigger_cards, existing_by_list):
        self.trigger = trigger_cards
        self.existing = existing_by_list
        self.moved = []

    def ensure_list(self, name):
        return "READY"

    def get_cards(self, list_id):
        if list_id == "READY":
            return list(self.trigger)
        return list(self.existing.get(list_id, []))

    def move_card(self, card_id, list_id, pos="bottom"):
        self.moved.append((card_id, list_id))

    def set_rank_marker(self, card, rank, est, why):
        return "ok"


class FakeEnricher:
    def __init__(self, feats):
        self.feats = feats

    async def aenrich(self, card):
        return self.feats[card.id]

    async def aenrich_many(self, cards):
        return [self.feats[c.id] for c in cards]


class FakeClassifier:
    def __init__(self, labels):
        self.labels = labels

    async def aclassify(self, feats):
        return {"label": self.labels[feats.card_id], "why": "x"}


class FakeComparator:
    async def acompare(self, a, b):
        return a if int(a.title[1:]) >= int(b.title[1:]) else b


async def test_phase2_routes_drains_and_publishes():
    trigger = [Card("t1", "t1"), Card("t2", "t2")]
    existing = {config.SYSTEM1_LIST_ID: [Card("e1", "e1")]}
    feats = {
        "t1": CardFeatures("t1", "p5", 10, "dense paper", "html", True),
        "t2": CardFeatures("t2", "p2", 4, "quick read", "html", True),
        "e1": CardFeatures("e1", "p9", 20, "existing", "html", True),
    }
    labels = {"t1": "system2", "t2": "system1"}
    client = FakeClient(trigger, existing)
    calls = {"q": 0, "p": 0}

    async def ensure_queue_fn():
        calls["q"] += 1
        return {"hours": 20}

    def publish_fn():
        calls["p"] += 1
        return {"feed_url": "x"}

    res = await run_phase2(client, None, FakeEnricher(feats), FakeClassifier(labels),
                           FakeComparator(), ensure_queue_fn, publish_fn, apply=True)
    assert res["processed"] == 2
    assert ("t1", config.SYSTEM2_LIST_ID) in client.moved
    assert ("t2", config.SYSTEM1_LIST_ID) in client.moved
    assert calls["q"] == 1 and calls["p"] == 1


async def test_phase2_empty_trigger_is_noop_route():
    client = FakeClient([], {})

    async def ensure_queue_fn():
        return {"hours": 0}

    def publish_fn():
        return {}

    res = await run_phase2(client, None, FakeEnricher({}), FakeClassifier({}),
                           FakeComparator(), ensure_queue_fn, publish_fn, apply=True)
    assert res["processed"] == 0 and client.moved == []
