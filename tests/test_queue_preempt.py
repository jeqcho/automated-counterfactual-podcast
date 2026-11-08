"""Listen Queue preemption (plans/0001-queue-preemption.md)."""
import time

import pytest

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.listen_queue import ensure_listen_queue
from counterfactual_podcast.models import AudioAsset, Card, CardFeatures

S1, LO, Q, TBP = config.SYSTEM1_LIST_ID, config.LIFE_OPTIM_LIST_ID, "QUEUE", "TBP"
H = 3600.0


class Client:
    """Lists keyed by id; move_card really moves; records every call in order."""

    def __init__(self, lists):
        self.lists = {k: [Card(i, i, list_id=k, pos=float(n)) for n, i in enumerate(v)]
                      for k, v in lists.items()}
        self.calls = []

    def ensure_list(self, name):
        return TBP if name == config.TO_BE_PROCESSED_LIST_NAME else Q

    def get_cards(self, lid):
        return list(self.lists.get(lid, []))

    def move_card(self, cid, lid, pos="bottom"):
        self.calls.append(("move", cid, lid))
        card = None
        for k, v in self.lists.items():
            for c in v:
                if c.id == cid:
                    card = c
            self.lists[k] = [c for c in v if c.id != cid]
        self.lists.setdefault(lid, []).append(card or Card(cid, cid))

    def set_card_position(self, cid, pos):
        self.calls.append(("pos", cid, pos))

    def ids(self, lid):
        return [c.id for c in self.lists.get(lid, [])]


class Enricher:
    def __init__(self, prio):
        self.prio = prio  # card_id -> priority (higher = better)

    def f(self, cid):
        return CardFeatures(cid, f"p{self.prio[cid]}", 5, "d", "html", True)

    async def aenrich_many(self, cards):
        return [self.f(c.id) for c in cards]

    async def aenrich(self, card):
        return self.f(card.id)


class Comparator:
    def __init__(self):
        self.n = 0

    async def acompare(self, a, b):
        self.n += 1
        return a if int(a.title[1:]) >= int(b.title[1:]) else b


def make_synth(lengths, fail=()):
    calls = []

    async def synth(feats, card=None):
        calls.append(feats.card_id)
        if feats.card_id in fail:
            return None
        return AudioAsset(feats.card_id, f"/tmp/{feats.card_id}.mp3", lengths[feats.card_id], "x")
    synth.calls = calls
    return synth


def setup(queue, s1=(), lo=(), prio=None, lengths=None):
    """queue/s1/lo: card ids (queue in impact order). Queue audio pre-cached."""
    cache = Cache(":memory:")
    for cid in queue:
        cache.put_audio(AudioAsset(cid, f"/tmp/{cid}.mp3", lengths[cid], "x"))
    client = Client({Q: list(queue), S1: list(s1), LO: list(lo)})
    return client, cache, Enricher(prio), Comparator()


async def run(client, cache, enr, cmp, synth, target=2, **kw):
    return await ensure_listen_queue(client, cache, enr, cmp, synth, target_hours=target, **kw)


def total_hours(client, cache):
    return sum(cache.get_audio(c).seconds for c in client.ids(Q)) / H


async def test_beating_card_is_admitted_and_bottom_evicted():
    prio = {"q1": 9, "q2": 5, "n": 7, "low": 1}
    lengths = {"q1": H, "q2": H, "n": H, "low": H}
    client, cache, enr, cmp = setup(["q1", "q2"], s1=["n", "low"], prio=prio, lengths=lengths)
    res = await run(client, cache, enr, cmp, make_synth(lengths))
    assert res["preempted"] == ["n"] and res["evicted"] == ["q2"]
    assert set(client.ids(Q)) == {"q1", "n"}
    assert "q2" in client.ids(TBP)                       # no reinsert_fn -> To Be Processed
    assert cache.get_origin("n") == S1
    assert "q2" in cache.cooldown_ids()
    pos = [c[1] for c in client.calls if c[0] == "pos"]
    assert pos == ["q1", "n"]                             # impact order
    assert res["hours"] == 2 and res["reached_target"]


async def test_loser_changes_nothing_one_comparison_per_list():
    prio = {"q1": 9, "q2": 5, "a": 3, "b": 2}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q1", "q2"], s1=["a"], lo=["b"], prio=prio, lengths=lengths)
    synth = make_synth(lengths)
    res = await run(client, cache, enr, cmp, synth)
    assert res["preempted"] == [] and res["evicted"] == []
    assert synth.calls == []
    # one comparison per source list (merging the queue with no newcomers is free)
    assert cmp.n == 2


async def test_challenger_cap(monkeypatch):
    monkeypatch.setattr(config, "QUEUE_PREEMPT_MAX", 2)
    prio = {"q1": 1, "q2": 1, "a": 9, "b": 8, "c": 7}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q1", "q2"], s1=["a", "b", "c"], prio=prio, lengths=lengths)
    res = await run(client, cache, enr, cmp, make_synth(lengths))
    assert res["preempted"] == ["a", "b"]


async def test_eviction_cap_blocks_long_challenger(monkeypatch):
    monkeypatch.setattr(config, "QUEUE_EVICT_MAX", 3)
    q = [f"q{i}" for i in range(8)]
    prio = {**{c: 5 for c in q}, "long": 9}
    lengths = {**{c: 0.25 * H for c in q}, "long": 2 * H}
    client, cache, enr, cmp = setup(q, s1=["long"], prio=prio, lengths=lengths)
    res = await run(client, cache, enr, cmp, make_synth(lengths))
    assert res["preempted"] == [] and res["evicted"] == []
    assert client.ids(Q) == q


async def test_short_challenger_cannot_evict_long_bottom_if_undershoot():
    prio = {"q1": 9, "big": 5, "s": 7}
    lengths = {"q1": H, "big": 2 * H, "s": 0.25 * H}
    client, cache, enr, cmp = setup(["q1", "big"], s1=["s"], prio=prio, lengths=lengths)
    res = await run(client, cache, enr, cmp, make_synth(lengths), target=2)
    # prefix q1 + s = 1.25h < 2h, so big stays in the prefix; s admitted, nothing evicted
    assert res["evicted"] == []
    assert set(client.ids(Q)) == {"q1", "s", "big"}


async def test_second_lowest_that_outranks_a_challenger_stays():
    prio = {"q1": 9, "q2": 6, "q3": 1, "a": 8, "b": 5, "c": 4}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q1", "q2", "q3"], s1=["a", "b", "c"], prio=prio,
                                    lengths=lengths)
    res = await run(client, cache, enr, cmp, make_synth(lengths), target=3)
    # combined: q1 a q2 b c q3 -> prefix (3h) = q1 a q2 ; q2 outranks b/c and stays
    assert res["preempted"] == ["a"]
    assert "q2" in client.ids(Q) and res["evicted"] == ["q3"]


@pytest.mark.parametrize("overfill", [False, True])
async def test_no_ratchet_over_three_runs(overfill):
    q = [f"q{i}" for i in range(6 if overfill else 4)]       # 0.5h each: 3h (1.5x) or 2h
    prio = {c: 50 - i for i, c in enumerate(q)}
    lengths = {c: 0.5 * H for c in q}
    for r in range(3):
        prio[f"n{r}"] = 60 + r
        lengths[f"n{r}"] = 0.75 * H
    client, cache, enr, cmp = setup(q, prio=prio, lengths=lengths)
    pre = total_hours(client, cache)
    synth = make_synth(lengths)
    for r in range(3):
        client.lists[S1] = [Card(f"n{r}", f"n{r}", list_id=S1)]
        await run(client, cache, enr, cmp, synth, target=2)
        for cid in client.ids(Q):
            if cache.get_audio(cid) is None:
                cache.put_audio(AudioAsset(cid, "/tmp/x", lengths[cid], "x"))
        assert total_hours(client, cache) <= max(pre, 2 + 0.75) + 1e-9
        assert f"n{r}" in client.ids(Q)


async def test_over_target_without_challengers_untouched():
    q = ["a", "b", "c"]
    prio = {"a": 9, "b": 8, "c": 7}
    lengths = {k: H for k in q}
    client, cache, enr, cmp = setup(q, prio=prio, lengths=lengths)
    res = await run(client, cache, enr, cmp, make_synth(lengths), target=1)
    assert res["evicted"] == [] and client.ids(Q) == q


async def test_empty_queue_and_pool_no_crash():
    client, cache, enr, cmp = setup([], prio={}, lengths={})
    res = await run(client, cache, enr, cmp, make_synth({}))
    assert res["size"] == 0 and res["preempted"] == []


async def test_evicted_card_cooldown_blocks_return():
    prio = {"q1": 9, "q2": 5, "e": 8}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q1", "q2"], s1=["e"], prio=prio, lengths=lengths)
    cache.put_cooldown("e", "evicted")
    res = await run(client, cache, enr, cmp, make_synth(lengths))
    assert res["preempted"] == []


async def test_synth_failure_cooldown_is_short_and_separate():
    prio = {"q1": 9, "q2": 5, "f": 8}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q1", "q2"], s1=["f"], prio=prio, lengths=lengths)
    synth = make_synth(lengths, fail={"f"})
    res = await run(client, cache, enr, cmp, synth)
    assert res["preempted"] == [] and res["evicted"] == []
    assert synth.calls == ["f"]
    await run(client, cache, enr, cmp, synth)
    assert synth.calls == ["f"]                               # skipped on the next run
    now = time.time()
    later = now + (config.QUEUE_SYNTH_FAIL_COOLDOWN_HOURS + 1) * 3600
    assert "f" not in cache.cooldown_ids(later)              # short window
    cache.put_cooldown("f", "evicted")
    cache.put_cooldown("f", "synth_failed")                  # must not overwrite evicted
    assert "f" in cache.cooldown_ids(later)


async def test_idempotent_second_run():
    prio = {"q1": 9, "q2": 5, "n": 7, "low": 1}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q1", "q2"], s1=["n", "low"], prio=prio, lengths=lengths)
    synth = make_synth(lengths)
    await run(client, cache, enr, cmp, synth)
    for cid in client.ids(Q):
        if cache.get_audio(cid) is None:
            cache.put_audio(AudioAsset(cid, "/tmp/x", H, "x"))
    res = await run(client, cache, enr, cmp, synth)
    assert res["preempted"] == [] and res["evicted"] == []


async def test_checkpoint_before_moves_and_evict_before_admit():
    prio = {"q1": 9, "q2": 5, "n": 7}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q1", "q2"], s1=["n"], prio=prio, lengths=lengths)
    seen = []

    async def checkpoint():
        seen.append(len([c for c in client.calls if c[0] == "move"]))
    reinserted = []

    async def reinsert(cards, exclude=()):
        assert exclude == {"n"}
        reinserted.extend(c.id for c in cards)
        client.calls.append(("move", cards[0].id, "HOME"))
    await run(client, cache, enr, cmp, make_synth(lengths), checkpoint=checkpoint,
              reinsert_fn=reinsert)
    assert seen == [0]
    assert reinserted == ["q2"]
    moves = [c for c in client.calls if c[0] == "move"]
    assert moves[0][1] == "q2" and moves[1] == ("move", "n", Q)


async def test_sequential_synth_many_swallows_render_error(monkeypatch):
    from counterfactual_podcast import audio as audio_mod
    from counterfactual_podcast import listen_queue as lq
    from counterfactual_podcast.models import ExtractedContent

    def boom(*a, **k):
        raise RuntimeError("tts down")
    monkeypatch.setattr(audio_mod, "synthesize_card", boom)
    cache = Cache(":memory:")
    cache.put_extracted(ExtractedContent("a", "a", "hi", 1, 1, "text", True))

    class Kokoro:
        name = "kokoro"
    synth = lq.make_synth(cache, engine=Kokoro())
    res = await synth.many([(CardFeatures("a", "a", 1, "d", "text", True), None)])
    assert res == {"a": None}


async def test_overfilled_queue_keeps_tail_positions_and_totals():
    q = ["a", "b", "c", "d"]                                 # 4h queue, target 2h
    prio = {"a": 9, "b": 8, "c": 7, "d": 6, "n": 8.5}
    prio = {k: int(v * 10) for k, v in prio.items()}
    lengths = {**{k: H for k in q}, "n": H}
    client, cache, enr, cmp = setup(q, s1=["n"], prio=prio, lengths=lengths)
    res = await run(client, cache, enr, cmp, make_synth(lengths), target=2)
    assert res["preempted"] == ["n"] and res["evicted"] == ["d"]
    pos = [c[1] for c in client.calls if c[0] == "pos"]
    assert pos == ["a", "n", "b", "c"]
    assert res["size"] == 4 and res["hours"] == 4


async def test_make_reinsert_uses_origin_then_classifier():
    from counterfactual_podcast.routing import make_reinsert

    class C(Client):
        def set_rank_marker(self, card, rank, est, digest=""):
            self.calls.append(("rank", card.id, rank))

    class Cls:
        n = 0

        async def aclassify(self, feats):
            Cls.n += 1
            return {"label": "life_optim"}

    prio = {"x": 5, "y": 5, "s_top": 9, "s_low": 1, "l_top": 9}
    client = C({Q: ["x", "y"], S1: ["s_top", "s_low"], LO: ["l_top"]})
    cache = Cache(":memory:")
    cache.put_origin("x", S1)
    await make_reinsert(client, cache, Enricher(prio), Cls(), Comparator())(
        client.get_cards(Q))
    assert Cls.n == 1                                         # only y needed classifying
    assert "x" in client.ids(S1)
    assert ("rank", "x", 2) in client.calls
    assert "y" in client.ids(LO) and ("rank", "y", 2) in client.calls


async def test_topup_then_preempt_same_call_and_topup_respects_cooldown():
    # queue: q_low (1h) < 2h target. Top-up skips cooling 'cool' (best in System 1), adds 'a'
    # (2h reached). Preemption then admits 'b' (beats bar q_low) and evicts q_low — same call.
    prio = {"q_low": 1, "cool": 10, "a": 8, "b": 5, "c": 0}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q_low"], s1=["cool", "b", "c"], lo=["a"], prio=prio,
                                    lengths=lengths)
    cache.put_cooldown("cool", "evicted")
    res = await run(client, cache, enr, cmp, make_synth(lengths), target=2)
    assert res["added"] == ["a"]
    assert res["preempted"] == ["b"] and res["evicted"] == ["q_low"]
    assert set(client.ids(Q)) == {"a", "b"} and "cool" in client.ids(S1)


async def test_topup_synth_failure_not_retried_by_preempt():
    prio = {"q1": 9, "bad": 8, "ok": 7}
    lengths = {k: H for k in prio}
    client, cache, enr, cmp = setup(["q1"], s1=["bad", "ok"], prio=prio, lengths=lengths)
    synth = make_synth(lengths, fail={"bad"})
    await run(client, cache, enr, cmp, synth, target=2)
    assert synth.calls.count("bad") == 1
    assert "bad" in cache.cooldown_ids()


async def test_make_reinsert_excludes_leaving_cards_from_rank():
    from counterfactual_podcast.routing import make_reinsert

    class C(Client):
        def set_rank_marker(self, card, rank, est, digest=""):
            self.calls.append(("rank", card.id, rank))

    prio = {"x": 5, "leaving": 9, "s_low": 1}
    client = C({Q: ["x"], S1: ["leaving", "s_low"]})
    cache = Cache(":memory:")
    cache.put_origin("x", S1)
    await make_reinsert(client, cache, Enricher(prio), None, Comparator())(
        client.get_cards(Q), exclude={"leaving"})
    assert ("rank", "x", 1) in client.calls
