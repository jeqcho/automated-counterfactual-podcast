"""Inbox classifier: route a card into one of three Trello lists by the kind of
COGNITIVE EFFORT it demands — not by impact (that's the comparator's job).

Uses Claude with the scoped profile doc as a prompt-CACHED system block (charged
once per ~5-min window, read cheaply on every call) plus a short instructions
block defining the three labels. Consumes `CardFeatures` (digests), not raw text.
The decision is forced via a `classify` tool; any parse failure falls back to a
deterministic default so routing never throws.

Labels:
    system1    light, doesn't require deliberate focus — newsletters, takes, quick reads
    system2    deep, effortful — papers, long technical analyses
    life_optim productivity / health / career / meta / tools
"""
from __future__ import annotations

import asyncio

from . import config
from .cache import Cache
from .models import CardFeatures

LABELS = ("system1", "system2", "life_optim")

CLASSIFY_INSTRUCTIONS = (
    "You route Jay's inbox cards into one of three reading lists by the kind of "
    "COGNITIVE EFFORT each demands — NOT by how impactful it is. Choose exactly one "
    "label by calling the `classify` tool:\n"
    "- \"system1\": light, doesn't require deliberate focus — newsletters, takes, "
    "quick reads, news.\n"
    "- \"system2\": deep, effortful — papers, benchmarks, long technical analyses "
    "that need focused attention.\n"
    "- \"life_optim\": productivity / health / career / meta / tools — things that "
    "improve how Jay works or lives.\n"
    "Give a one-line reason."
)

CLASSIFY_TOOL = {
    "name": "classify",
    "description": "Report which reading list a card belongs in, by cognitive-effort type.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": list(LABELS)},
            "why": {"type": "string", "description": "one-line reason for the label"},
        },
        "required": ["label", "why"],
    },
}

_LIST_IDS = {
    "system1": config.SYSTEM1_LIST_ID,
    "system2": config.SYSTEM2_LIST_ID,
    "life_optim": config.LIFE_OPTIM_LIST_ID,
}


def target_list_id(label: str) -> str:
    """Map a classifier label to its destination Trello list id."""
    return _LIST_IDS[label]


def _fmt(f: CardFeatures) -> str:
    return (
        f"--- Card ---\n"
        f"Title: {f.title}\n"
        f"Kind: {f.kind}  | Est. reading minutes: {f.est_minutes}\n"
        f"Digest: {f.digest}\n"
        "\nWhich reading list does this card belong in?"
    )


class Classifier:
    def __init__(
        self,
        client=None,
        cache: Cache | None = None,
        profile_doc: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ):
        self.cache = cache
        self.model = model or config.CLAUDE_MODEL
        self._sem = asyncio.Semaphore(concurrency or config.MAX_LLM_CONCURRENCY)
        if profile_doc is not None:
            self.profile_doc = profile_doc
        else:
            self.profile_doc = config.PROFILE_DOC.read_text(encoding="utf-8")
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self.client = client

    def _system(self):
        return [
            {"type": "text", "text": CLASSIFY_INSTRUCTIONS},
            {"type": "text", "text": self.profile_doc,
             "cache_control": {"type": "ephemeral"}},
        ]

    async def aclassify(self, feats: CardFeatures) -> dict:
        async with self._sem:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=self._system(),
                messages=[{"role": "user", "content": _fmt(feats)}],
                tools=[CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "classify"},
            )
        for block in getattr(resp, "content", []) or []:
            inp = getattr(block, "input", None)
            if inp is not None:
                label = dict(inp).get("label")
                if label in LABELS:
                    return {"label": label, "why": str(dict(inp).get("why", ""))}
        return {"label": "system1", "why": "fallback"}

    def classify(self, feats: CardFeatures) -> dict:
        return asyncio.run(self.aclassify(feats))
