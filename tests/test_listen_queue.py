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
    async def synth(feats, card=None):
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


async def test_synth_many_parallel_renders_misses_reuses_hits_skips_unreadable(
        monkeypatch, tmp_path):
    """synth.many: cache hits reused (no render), misses rendered (in parallel for
    thread-safe engines), ok=False skipped -> None. Cache I/O stays on the main thread."""
    from counterfactual_podcast import audio as audio_mod
    from counterfactual_podcast.cache import Cache
    from counterfactual_podcast.listen_queue import make_synth
    from counterfactual_podcast.models import ExtractedContent

    rendered = []

    def fake_render(card_id, text, *, engine=None, out_dir=None, title="",
                    author="", source="", date=""):
        rendered.append(card_id)
        return AudioAsset(card_id, f"/tmp/{card_id}.mp3", 100.0, "fakepar")

    monkeypatch.setattr(audio_mod, "render_audio", fake_render)
    monkeypatch.setattr(config, "PARALLEL_SAFE_TTS", frozenset({"fakepar"}))
    monkeypatch.setattr(config, "SYNTH_CONCURRENCY", 4)

    cache = Cache(":memory:")
    for cid in ("a", "b", "c"):
        cache.put_extracted(ExtractedContent(card_id=cid, title=cid, text="hi",
                                             word_count=1, est_minutes=1, kind="text", ok=True))
    # pre-cache audio for 'a' with an existing local file -> should be reused, not rendered
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    cache.put_audio(AudioAsset("a", str(f), 50.0, "fakepar"))

    class FakeEngine:
        name = "fakepar"

    synth = make_synth(cache, engine=FakeEngine())
    assert synth.concurrency == 4  # parallel-safe engine -> concurrent

    def feats(cid, ok=True):
        return CardFeatures(cid, cid, 1, "digest", "text", ok)

    res = await synth.many([(feats("a"), None), (feats("b"), None),
                            (feats("c", ok=False), None)])
    assert res["a"].seconds == 50.0   # cache hit reused
    assert res["b"].seconds == 100.0  # miss rendered
    assert res["c"] is None           # ok=False skipped
    assert rendered == ["b"]          # only the genuine miss was rendered


async def test_synth_concurrency_is_one_for_unsafe_engine(monkeypatch):
    from counterfactual_podcast.cache import Cache
    from counterfactual_podcast.listen_queue import make_synth
    monkeypatch.setattr(config, "PARALLEL_SAFE_TTS", frozenset({"google", "openai"}))

    class Kokoro:
        name = "kokoro"
    synth = make_synth(Cache(":memory:"), engine=Kokoro())
    assert synth.concurrency == 1  # espeak not thread-safe -> sequential
