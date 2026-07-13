import pytest

from counterfactual_podcast import config
from counterfactual_podcast.models import Card, CardFeatures
from counterfactual_podcast.pipelines.phase2 import run_phase2


class FakeClient:
    def __init__(self, trigger_cards, existing_by_list):
        self.trigger = trigger_cards
        self.existing = existing_by_list
        self.moved = []
        self.marks = {}   # card_id -> rank (from set_rank_marker)

    def ensure_list(self, name):
        return "READY"

    def get_cards(self, list_id):
        if list_id == "READY":
            return list(self.trigger)
        return list(self.existing.get(list_id, []))

    def move_card(self, card_id, list_id, pos="bottom"):
        self.moved.append((card_id, list_id))

    def set_rank_marker(self, card, rank, est, why):
        self.marks[card.id] = rank
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


async def test_phase2_checkpoints_cache_periodically(monkeypatch):
    # 5 routed cards, checkpoint_every=2 -> checkpoint fires after cards 2 and 4 (2 times).
    # Persisting mid-run means a container kill loses at most `checkpoint_every` cards of work
    # instead of the whole run (breaks the re-press-forever loop). Exercises the SEQUENTIAL
    # path's per-card cadence (the parallel path checkpoints on its own schedule).
    monkeypatch.setattr(config, "PHASE2_PARALLEL_SORT", False)
    trigger = [Card(f"t{i}", f"t{i}") for i in range(1, 6)]
    feats = {f"t{i}": CardFeatures(f"t{i}", f"p{i}", 5, "d", "html", True) for i in range(1, 6)}
    feats["e1"] = CardFeatures("e1", "p9", 20, "existing", "html", True)
    existing = {config.SYSTEM1_LIST_ID: [Card("e1", "e1")]}
    labels = {f"t{i}": "system1" for i in range(1, 6)}
    client = FakeClient(trigger, existing)
    ckpts = {"n": 0}

    res = await run_phase2(client, None, FakeEnricher(feats), FakeClassifier(labels),
                           FakeComparator(), _noop, lambda: {}, apply=True,
                           checkpoint=lambda: ckpts.__setitem__("n", ckpts["n"] + 1),
                           checkpoint_every=2)
    assert res["processed"] == 5
    assert ckpts["n"] == 2          # after card 2 and card 4; the 5th is covered by the end push


async def test_phase2_no_checkpoint_when_dry_run():
    trigger = [Card("t1", "t1")]
    feats = {"t1": CardFeatures("t1", "p1", 5, "d", "html", True)}
    client = FakeClient(trigger, {})
    ckpts = {"n": 0}
    await run_phase2(client, None, FakeEnricher(feats), FakeClassifier({"t1": "system1"}),
                     FakeComparator(), _noop, lambda: {}, apply=False,
                     checkpoint=lambda: ckpts.__setitem__("n", ckpts["n"] + 1),
                     checkpoint_every=1)
    assert ckpts["n"] == 0          # dry run mutates nothing, so nothing to checkpoint


async def _noop():
    return {}


async def test_phase2_empty_trigger_is_noop_route():
    client = FakeClient([], {})

    async def ensure_queue_fn():
        return {"hours": 0}

    def publish_fn():
        return {}

    res = await run_phase2(client, None, FakeEnricher({}), FakeClassifier({}),
                           FakeComparator(), ensure_queue_fn, publish_fn, apply=True)
    assert res["processed"] == 0 and client.moved == []


# --- parallel routing (PHASE2_PARALLEL_SORT) ----------------------------------------------
from counterfactual_podcast.pipelines.phase2 import _assign_positions


class _F:
    """Minimal stand-in for a merged-order element (needs card_id)."""
    def __init__(self, cid):
        self.card_id = cid


def test_assign_positions_single_newcomers_between_existing():
    # ordered: e(100), NEW n1, e(200), NEW n2, e(300)
    ordered = [_F("e1"), _F("n1"), _F("e2"), _F("n2"), _F("e3")]
    pos = {"e1": 100.0, "e2": 200.0, "e3": 300.0}
    out = _assign_positions(ordered, pos, {"n1", "n2"})
    assert 100.0 < out["n1"] < 200.0
    assert 200.0 < out["n2"] < 300.0


def test_assign_positions_spaces_a_run_of_newcomers():
    # two consecutive newcomers share the gap 100..200 -> evenly spaced & ordered
    ordered = [_F("e1"), _F("n1"), _F("n2"), _F("e2")]
    pos = {"e1": 100.0, "e2": 200.0}
    out = _assign_positions(ordered, pos, {"n1", "n2"})
    assert 100.0 < out["n1"] < out["n2"] < 200.0   # n1 before n2, both inside the gap


async def test_phase2_parallel_routes_and_ranks(monkeypatch):
    monkeypatch.setattr(config, "PHASE2_PARALLEL_SORT", True)
    # existing lists already in priority order (higher pN = higher priority = first)
    existing = {
        config.SYSTEM1_LIST_ID: [Card("e9", "e9", pos=100.0), Card("e5", "e5", pos=200.0),
                                 Card("e1", "e1", pos=300.0)],
        config.SYSTEM2_LIST_ID: [Card("s6", "s6", pos=100.0), Card("s2", "s2", pos=200.0)],
    }
    trigger = [Card("n7", "n7"), Card("n3", "n3"), Card("n8", "n8")]
    feats = {cid: CardFeatures(cid, f"p{cid[1:]}", 5, "d", "html", True)
             for cid in ["e9", "e5", "e1", "s6", "s2", "n7", "n3", "n8"]}
    labels = {"n7": "system1", "n3": "system1", "n8": "system2"}
    client = FakeClient(trigger, existing)

    res = await run_phase2(client, None, FakeEnricher(feats), FakeClassifier(labels),
                           FakeComparator(), _noop, lambda: {}, apply=True)
    assert res["processed"] == 3
    # right lists
    assert ("n7", config.SYSTEM1_LIST_ID) in client.moved
    assert ("n3", config.SYSTEM1_LIST_ID) in client.moved
    assert ("n8", config.SYSTEM2_LIST_ID) in client.moved
    # correct impact ranks: system1 merged p9,p7,p5,p3,p1 -> n7=#2, n3=#4; system2 p8,p6,p2 -> n8=#1
    assert client.marks["n7"] == 2
    assert client.marks["n3"] == 4
    assert client.marks["n8"] == 1


async def test_phase2_parallel_same_list_relative_order(monkeypatch):
    # two newcomers into the SAME list must be ordered relative to each other (Jay's point 1)
    monkeypatch.setattr(config, "PHASE2_PARALLEL_SORT", True)
    existing = {config.SYSTEM1_LIST_ID: [Card("e9", "e9", pos=100.0), Card("e1", "e1", pos=200.0)]}
    trigger = [Card("n7", "n7"), Card("n4", "n4")]   # both system1, between e9 and e1
    feats = {cid: CardFeatures(cid, f"p{cid[1:]}", 5, "d", "html", True)
             for cid in ["e9", "e1", "n7", "n4"]}
    client = FakeClient(trigger, existing)
    await run_phase2(client, None, FakeEnricher(feats),
                     FakeClassifier({"n7": "system1", "n4": "system1"}),
                     FakeComparator(), _noop, lambda: {}, apply=True)
    # merged p9,p7,p4,p1 -> n7 ranks above n4
    assert client.marks["n7"] == 2 and client.marks["n4"] == 3
