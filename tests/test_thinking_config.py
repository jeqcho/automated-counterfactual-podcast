"""Thinking/effort wiring for the 5-family model migration (2026-07-25).

The load-bearing rule: adaptive thinking goes to the models that accept it and NEVER to
Haiku 4.5 (pre-4.6 models 400 on `thinking={"type":"adaptive"}`), so the digest path must
stay thinking-free while comparisons/classification get it.
"""
from types import SimpleNamespace

import pytest

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.classify import Classifier
from counterfactual_podcast.llm_compare import Comparator
from counterfactual_podcast.models import CardFeatures


class FakeClient:
    """Mimics anthropic AsyncAnthropic; records kwargs, returns a forced tool call."""

    def __init__(self, out):
        self.out = out
        self.calls = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        # Real 5-family responses lead with a thinking block; the parsers must skip it.
        return SimpleNamespace(content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="tool_use", input=self.out),
        ])


def cf(cid="a", digest="x", est=10):
    return CardFeatures(cid, cid, est, digest, "html", True)


# --- supports_adaptive_thinking ------------------------------------------

@pytest.mark.parametrize("model", [
    "claude-opus-5", "claude-sonnet-5", "claude-fable-5",
    "claude-opus-4-8", "claude-sonnet-4-6",
])
def test_adaptive_supported(model):
    assert config.supports_adaptive_thinking(model)


@pytest.mark.parametrize("model", [
    "claude-haiku-4-5-20251001", "claude-haiku-4-5", "claude-opus-4-5-20251101", "",
])
def test_adaptive_unsupported(model):
    assert not config.supports_adaptive_thinking(model)


# --- thinking_kwargs ------------------------------------------------------

def test_default_is_adaptive(monkeypatch):
    monkeypatch.setattr(config, "CF_THINKING", "adaptive")
    monkeypatch.setattr(config, "CF_EFFORT", "")
    kw = config.thinking_kwargs("claude-sonnet-5")
    assert kw == {"thinking": {"type": "adaptive"}}  # no effort => API default (high)


def test_haiku_gets_no_thinking_kwargs(monkeypatch):
    monkeypatch.setattr(config, "CF_THINKING", "adaptive")
    assert config.thinking_kwargs(config.CLAUDE_MODEL_DIGEST) == {}


def test_effort_passed_through(monkeypatch):
    monkeypatch.setattr(config, "CF_THINKING", "adaptive")
    monkeypatch.setattr(config, "CF_EFFORT", "medium")
    kw = config.thinking_kwargs("claude-opus-5")
    assert kw["output_config"] == {"effort": "medium"}


def test_off_omits_the_field(monkeypatch):
    monkeypatch.setattr(config, "CF_THINKING", "off")
    monkeypatch.setattr(config, "CF_EFFORT", "")
    assert "thinking" not in config.thinking_kwargs("claude-sonnet-5")


def test_disabled_thinking_clamps_high_effort(monkeypatch):
    """Opus 5 400s on disabled thinking above `high` — clamp rather than fail the run."""
    monkeypatch.setattr(config, "CF_THINKING", "disabled")
    monkeypatch.setattr(config, "CF_EFFORT", "max")
    kw = config.thinking_kwargs("claude-opus-5")
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"] == {"effort": "high"}


# --- call sites -----------------------------------------------------------

async def test_comparator_sends_thinking_and_room_to_think(monkeypatch):
    monkeypatch.setattr(config, "CF_THINKING", "adaptive")
    monkeypatch.setattr(config, "CF_EFFORT", "")
    fake = FakeClient({"winner": "A", "step": 2, "why": "r"})
    cmp = Comparator(client=fake, cache=Cache(), profile_doc="P",
                     model="claude-sonnet-5", escalate_model="claude-opus-5",
                     concurrency=2)
    winner = await cmp.acompare(cf("a"), cf("b"))

    assert winner.card_id == "a"  # parsed past the leading thinking block
    kwargs = fake.calls[0]
    assert kwargs["thinking"] == {"type": "adaptive"}
    # A 300-token cap would be eaten by thinking before the tool call is emitted.
    assert kwargs["max_tokens"] >= 2048


async def test_classifier_sends_thinking(monkeypatch):
    monkeypatch.setattr(config, "CF_THINKING", "adaptive")
    monkeypatch.setattr(config, "CF_EFFORT", "")
    fake = FakeClient({"label": "system1", "why": "r"})
    clf = Classifier(client=fake, cache=Cache(), profile_doc="P",
                     model="claude-sonnet-5", concurrency=2)
    out = await clf.aclassify(cf())

    assert out["label"] == "system1"
    assert fake.calls[0]["thinking"] == {"type": "adaptive"}


async def test_defaults_are_the_5_family():
    assert config.CLAUDE_MODEL == "claude-sonnet-5"
    assert config.CLAUDE_MODEL_ESCALATE == "claude-opus-5"
    # No Haiku 5 exists — the digest tier stays on 4.5 deliberately.
    assert config.CLAUDE_MODEL_DIGEST.startswith("claude-haiku-4-5")
