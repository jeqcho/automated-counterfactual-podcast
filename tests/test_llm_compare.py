from types import SimpleNamespace

import pytest

from counterfactual_podcast.cache import Cache
from counterfactual_podcast.llm_compare import Comparator
from counterfactual_podcast.models import CardFeatures, PairwiseResult


def cf(cid, digest, est=10, title=None):
    return CardFeatures(cid, title or cid, est, digest, "html", True)


class FakeClient:
    """Mimics anthropic AsyncAnthropic: client.messages.create(**kwargs)."""
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.last_kwargs = None
        self.messages = self

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        self.calls.append(kwargs)
        out = self.handler(kwargs)
        content = [] if out is None else [SimpleNamespace(input=out, type="tool_use")]
        return SimpleNamespace(content=content)


def _sections(kwargs):
    user = kwargs["messages"][0]["content"]
    a_part, _, b_part = user.partition("--- Article B ---")
    return a_part, b_part


def prefer_keyword(keyword, step=2):
    def handler(kwargs):
        a_part, b_part = _sections(kwargs)
        winner = "A" if keyword in a_part else ("B" if keyword in b_part else "A")
        return {"winner": winner, "step": step, "why": f"has {keyword}"}
    return handler


async def test_comparator_prefers_on_pillar():
    robo = cf("robo", "new robotics evaluation benchmark, novel and on-pillar")
    celeb = cf("celeb", "celebrity gossip, pure entertainment")
    cmp = Comparator(client=FakeClient(prefer_keyword("robotics")),
                     cache=Cache(), profile_doc="PROFILE", concurrency=2)
    winner = await cmp.acompare(robo, celeb)
    assert winner is robo


async def test_profile_doc_is_cache_controlled():
    fake = FakeClient(prefer_keyword("x"))
    cmp = Comparator(client=fake, cache=Cache(), profile_doc="PROFILE DOC TEXT", concurrency=2)
    await cmp.acompare(cf("a", "x"), cf("b", "y"))
    system = fake.last_kwargs["system"]
    assert any(blk.get("cache_control") for blk in system)
    assert any("PROFILE DOC TEXT" in blk.get("text", "") for blk in system)


async def test_cache_hit_skips_llm_call():
    cache = Cache()
    a, b = cf("a", "x"), cf("b", "y")
    cache.put_pairwise("a", "b", PairwiseResult(winner_id="b", step=2, why="pre", model="m"))

    def boom(kwargs):
        raise AssertionError("LLM should not be called on a cache hit")

    cmp = Comparator(client=FakeClient(boom), cache=cache, profile_doc="P", concurrency=2)
    winner = await cmp.acompare(a, b)
    assert winner is b


async def test_fallback_on_empty_output_picks_shorter_read():
    cmp = Comparator(client=FakeClient(lambda k: None), cache=Cache(),
                     profile_doc="P", concurrency=2)
    a = cf("a", "x", est=30)
    b = cf("b", "y", est=5)
    winner = await cmp.acompare(a, b)
    assert winner is b  # shorter est_minutes wins the deterministic fallback


async def test_escalation_on_step6_uses_opus():
    def handler(kwargs):
        # sonnet says A at step 6 (close); opus overrides to B
        if "opus" in kwargs["model"]:
            return {"winner": "B", "step": 2, "why": "opus call"}
        return {"winner": "A", "step": 6, "why": "close on sonnet"}

    fake = FakeClient(handler)
    cmp = Comparator(client=fake, cache=Cache(), profile_doc="P",
                     model="claude-sonnet-4-6", escalate_model="claude-opus-4-8",
                     concurrency=2)
    a, b = cf("a", "x"), cf("b", "y")
    winner = await cmp.acompare(a, b)
    assert winner is b
    models = [c["model"] for c in fake.calls]
    assert "claude-sonnet-4-6" in models and "claude-opus-4-8" in models
