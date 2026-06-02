import json

import pytest

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.models import Card, CardFeatures
from counterfactual_podcast.pipelines import oneshot_sort


class FakeClient:
    def __init__(self, cards):
        self._cards = cards
        self.positions = []        # (card_id, pos) in call order
        self.markers = []          # (card_id, rank)

    def get_cards(self, list_id):
        return list(self._cards)

    def set_card_position(self, card_id, pos):
        self.positions.append((card_id, pos))

    def set_rank_marker(self, card, rank, est_min, why):
        self.markers.append((card.id, rank))
        return "marked"


class FakeEnricher:
    """Returns CardFeatures whose digest encodes a numeric priority via est_minutes."""
    def __init__(self, feats_by_id):
        self.feats = feats_by_id

    async def aenrich_many(self, cards):
        return [self.feats[c.id] for c in cards]


class FakeComparator:
    """Higher 'priority' (lower est_minutes here) ranks first — deterministic."""
    async def acompare(self, a, b):
        # rank by descending priority stored in title (e.g. "p9")
        pa, pb = int(a.title[1:]), int(b.title[1:])
        return a if pa >= pb else b


def _make(n):
    cards = [Card(f"c{i}", f"Card {i}") for i in range(n)]
    feats = {f"c{i}": CardFeatures(f"c{i}", f"p{i}", i + 1, f"digest {i}", "html", True)
             for i in range(n)}
    return cards, feats


async def test_dry_run_writes_snapshots_no_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    cards, feats = _make(5)
    client = FakeClient(cards)
    res = await oneshot_sort.sort_list(
        client, Cache(), FakeEnricher(feats), FakeComparator(),
        config.LIFE_OPTIM_LIST_ID, apply=False, copeland_head=0)
    assert res["applied"] is False
    assert client.positions == []        # board untouched
    assert client.markers == []
    # snapshots exist and post is ranked by descending priority (p4..p0)
    post = json.loads((tmp_path / res["post_snapshot"].split("/")[-1]).read_text())
    assert [p["title"] for p in post] == ["p4", "p3", "p2", "p1", "p0"]


async def test_apply_reorders_in_ranked_order(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    cards, feats = _make(5)
    client = FakeClient(cards)
    res = await oneshot_sort.sort_list(
        client, Cache(), FakeEnricher(feats), FakeComparator(),
        config.LIFE_OPTIM_LIST_ID, apply=True, copeland_head=0)
    assert res["applied"] is True
    # positions assigned top->bottom in ranked order, increasing pos
    ordered_ids = [cid for cid, _ in client.positions]
    assert ordered_ids == ["c4", "c3", "c2", "c1", "c0"]
    assert [pos for _, pos in client.positions] == [1000, 2000, 3000, 4000, 5000]
    assert [cid for cid, _ in client.markers] == ["c4", "c3", "c2", "c1", "c0"]
