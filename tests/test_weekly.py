import pytest

from counterfactual_podcast import config
from counterfactual_podcast.models import Card, CardFeatures
from counterfactual_podcast.pipelines.weekly import run_weekly


class FakeClient:
    def __init__(self, inbox, existing_by_list):
        self._inbox = inbox
        self.existing = existing_by_list
        self.moved = []

    def inbox_list_id(self):
        return "INBOX"

    def ensure_list(self, name):
        return "TBP"

    def get_cards(self, list_id):
        if list_id == "INBOX":
            return list(self._inbox)
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


async def test_weekly_routes_and_invokes_queue_and_publish():
    inbox = [Card("i1", "i1"), Card("i2", "i2")]
    existing = {config.SYSTEM2_LIST_ID: [Card("e1", "e1")]}
    feats = {
        "i1": CardFeatures("i1", "p5", 10, "a dense paper", "html", True),
        "i2": CardFeatures("i2", "p2", 4, "a quick newsletter", "html", True),
        "e1": CardFeatures("e1", "p9", 20, "existing", "html", True),
    }
    labels = {"i1": "system2", "i2": "system1"}
    client = FakeClient(inbox, existing)

    calls = {"queue": 0, "publish": 0}

    async def ensure_queue_fn():
        calls["queue"] += 1
        return {"hours": 20}

    def publish_fn():
        calls["publish"] += 1
        return {"feed_url": "x"}

    res = await run_weekly(client, cache=None, enricher=FakeEnricher(feats),
                           classifier=FakeClassifier(labels), comparator=FakeComparator(),
                           ensure_queue_fn=ensure_queue_fn, publish_fn=publish_fn,
                           apply=True)

    assert res["processed"] == 2
    routed = {r["card_id"]: r["label"] for r in res["routed"]}
    assert routed == {"i1": "system2", "i2": "system1"}
    # i1 routed to the System 2 list, i2 to System 1
    assert ("i1", config.SYSTEM2_LIST_ID) in client.moved
    assert ("i2", config.SYSTEM1_LIST_ID) in client.moved
    assert calls["queue"] == 1 and calls["publish"] == 1


async def test_weekly_dry_run_does_not_mutate():
    inbox = [Card("i1", "i1")]
    feats = {"i1": CardFeatures("i1", "p5", 10, "d", "html", True)}
    client = FakeClient(inbox, {})

    async def ensure_queue_fn():
        raise AssertionError("queue should not run in dry-run")

    def publish_fn():
        raise AssertionError("publish should not run in dry-run")

    res = await run_weekly(client, None, FakeEnricher(feats),
                           FakeClassifier({"i1": "system1"}), FakeComparator(),
                           ensure_queue_fn, publish_fn, apply=False)
    assert res["processed"] == 1
    assert client.moved == []                 # inbox collect moves to TBP, but no routing moves
    assert res["queue"] == {"skipped": "dry-run"}
