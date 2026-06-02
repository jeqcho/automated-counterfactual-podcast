from types import SimpleNamespace

import pytest

from counterfactual_podcast.cache import Cache
from counterfactual_podcast.enrich import Enricher
from counterfactual_podcast.models import Card, ExtractedContent


class FakeClient:
    def __init__(self, digest_text="DIGEST"):
        self.digest_text = digest_text
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text=self.digest_text)])


def ok_extract(card):
    return ExtractedContent(card.id, card.name, "lots of words here " * 50, 250, 1,
                            "html", True, "")


def hard_extract(card):
    return ExtractedContent(card.id, card.name, card.name, 3, 0, "hard", False,
                            "hard source: x.com")


async def test_enrich_produces_features():
    fake = FakeClient("a sharp robotics digest")
    e = Enricher(client=fake, cache=Cache(), profile_doc="P", extract_fn=ok_extract,
                 concurrency=2)
    f = await e.aenrich(Card("c1", "Some article"))
    assert f.digest == "a sharp robotics digest"
    assert f.est_minutes == 1 and f.ok is True and f.card_id == "c1"
    assert fake.calls == 1


async def test_enrich_is_cached():
    fake = FakeClient()
    cache = Cache()
    e = Enricher(client=fake, cache=cache, profile_doc="P", extract_fn=ok_extract,
                 concurrency=2)
    card = Card("c1", "A")
    await e.aenrich(card)
    await e.aenrich(card)  # second call should hit the digest cache
    assert fake.calls == 1


async def test_unreadable_card_skips_llm():
    fake = FakeClient()
    e = Enricher(client=fake, cache=Cache(), profile_doc="P", extract_fn=hard_extract,
                 concurrency=2)
    f = await e.aenrich(Card("c1", "Tweet thread", url="https://x.com/foo"))
    assert f.ok is False
    assert "unreadable" in f.digest
    assert fake.calls == 0  # no LLM call for unreadable cards
