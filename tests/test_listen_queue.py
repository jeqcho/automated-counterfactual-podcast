import pytest

from counterfactual_podcast import config
from counterfactual_podcast.listen_queue import ensure_listen_queue
from counterfactual_podcast.models import AudioAsset, Card, CardFeatures


class FakeClient:
    def __init__(self, cards_by_list, queue_id="QUEUE"):
        self.cards_by_list = cards_by_list
        self.queue_id = queue_id
        self.moved = []          # (card_id, list_id)
        self.positions = []
        self.queried_lists = []

    def ensure_list(self, name):
        return self.queue_id

    def get_cards(self, list_id):
        self.queried_lists.append(list_id)
        return list(self.cards_by_list.get(list_id, []))

    def move_card(self, card_id, list_id, pos="bottom"):
        self.moved.append((card_id, list_id))
        # reflect the move so the final re-rank "sees" it in the queue
        self.cards_by_list.setdefault(list_id, []).append(Card(card_id, card_id))

    def set_card_position(self, card_id, pos):
        self.positions.append((card_id, pos))


class FakeEnricher:
    def __init__(self, feats):
        self.feats = feats

    async def aenrich_many(self, cards):
        return [self.feats[c.id] for c in cards]


class FakeComparator:
    async def acompare(self, a, b):
        # higher priority encoded in title "pN" ranks first
        return a if int(a.title[1:]) >= int(b.title[1:]) else b


async def fake_synth_factory(seconds_each=3600.0, skip_ids=()):
    async def synth(feats):
        if feats.card_id in skip_ids:
            return None
        return AudioAsset(feats.card_id, f"/tmp/{feats.card_id}.mp3", seconds_each, "fake")
    return synth


async def test_tops_up_to_target_from_system1_and_lifeoptim_only(monkeypatch):
    # 4 candidate cards across System1 + LifeOptim; target 2h, each clip 1h -> add 2
    s1 = [Card("a", "a"), Card("b", "b")]
    lo = [Card("c", "c"), Card("d", "d")]
    cards_by_list = {config.SYSTEM1_LIST_ID: s1, config.LIFE_OPTIM_LIST_ID: lo,
                     "QUEUE": []}
    feats = {
        "a": CardFeatures("a", "p9", 5, "da", "html", True),
        "b": CardFeatures("b", "p7", 5, "db", "html", True),
        "c": CardFeatures("c", "p3", 5, "dc", "html", True),
        "d": CardFeatures("d", "p1", 5, "dd", "html", True),
    }
    client = FakeClient(cards_by_list)
    synth = await fake_synth_factory(seconds_each=3600.0)

    res = await ensure_listen_queue(client, cache=_NoCache(), enricher=FakeEnricher(feats),
                                    comparator=FakeComparator(), synth=synth, target_hours=2)
    assert res["reached_target"] is True
    # added the two highest-priority cards, in impact order
    assert res["added"] == ["a", "b"]
    # System 2 list was never queried
    assert config.SYSTEM2_LIST_ID not in client.queried_lists
    # both highest were moved into the queue
    assert ("a", "QUEUE") in client.moved and ("b", "QUEUE") in client.moved


async def test_skips_unsynthesizable_and_stops_when_pool_exhausted(monkeypatch):
    s1 = [Card("a", "a"), Card("b", "b")]
    cards_by_list = {config.SYSTEM1_LIST_ID: s1, config.LIFE_OPTIM_LIST_ID: [], "QUEUE": []}
    feats = {
        "a": CardFeatures("a", "p9", 5, "da", "html", True),
        "b": CardFeatures("b", "p1", 5, "db", "html", True),
    }
    client = FakeClient(cards_by_list)
    # 'a' fails to synthesize -> skipped; only 'b' added; target never reached -> soft floor
    synth = await fake_synth_factory(seconds_each=600.0, skip_ids={"a"})
    res = await ensure_listen_queue(client, cache=_NoCache(), enricher=FakeEnricher(feats),
                                    comparator=FakeComparator(), synth=synth, target_hours=20)
    assert res["added"] == ["b"]
    assert res["reached_target"] is False


class _NoCache:
    def get_audio(self, card_id):
        return None
