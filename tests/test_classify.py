from types import SimpleNamespace

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.classify import Classifier, target_list_id
from counterfactual_podcast.models import CardFeatures


def cf(cid, digest, est=10, title=None, kind="html"):
    return CardFeatures(cid, title or cid, est, digest, kind, True)


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


def keyword_handler(kwargs):
    """Pick a label from keywords in the user message text."""
    user = kwargs["messages"][0]["content"]
    text = user.lower()
    if "paper" in text or "benchmark" in text:
        label = "system2"
    elif "habit" in text or "productivity" in text:
        label = "life_optim"
    elif "newsletter" in text or "quick" in text:
        label = "system1"
    else:
        label = "system1"
    return {"label": label, "why": f"matched on text"}


async def test_dense_paper_is_system2():
    clf = Classifier(client=FakeClient(keyword_handler),
                     cache=Cache(), profile_doc="PROFILE", concurrency=2)
    out = await clf.aclassify(
        cf("paper", "a dense research paper introducing a new benchmark with proofs")
    )
    assert out["label"] == "system2"


async def test_quick_newsletter_is_system1():
    clf = Classifier(client=FakeClient(keyword_handler),
                     cache=Cache(), profile_doc="PROFILE", concurrency=2)
    out = await clf.aclassify(
        cf("news", "a quick weekly newsletter with light takes you skim in two minutes")
    )
    assert out["label"] == "system1"


async def test_habits_digest_is_life_optim():
    clf = Classifier(client=FakeClient(keyword_handler),
                     cache=Cache(), profile_doc="PROFILE", concurrency=2)
    out = await clf.aclassify(
        cf("habit", "how to build better habits and boost productivity at work")
    )
    assert out["label"] == "life_optim"


async def test_profile_doc_is_cache_controlled():
    fake = FakeClient(keyword_handler)
    clf = Classifier(client=fake, cache=Cache(),
                     profile_doc="PROFILE DOC TEXT", concurrency=2)
    await clf.aclassify(cf("x", "a quick newsletter"))
    system = fake.last_kwargs["system"]
    assert any(blk.get("cache_control") for blk in system)
    assert any("PROFILE DOC TEXT" in blk.get("text", "") for blk in system)


async def test_fallback_on_empty_output():
    clf = Classifier(client=FakeClient(lambda k: None), cache=Cache(),
                     profile_doc="P", concurrency=2)
    out = await clf.aclassify(cf("a", "anything"))
    assert out == {"label": "system1", "why": "fallback"}


def test_target_list_id_maps_three_labels():
    assert target_list_id("system1") == config.SYSTEM1_LIST_ID
    assert target_list_id("system2") == config.SYSTEM2_LIST_ID
    assert target_list_id("life_optim") == config.LIFE_OPTIM_LIST_ID
