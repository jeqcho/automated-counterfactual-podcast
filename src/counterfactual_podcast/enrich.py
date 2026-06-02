"""Enrichment round (preprocessing): card -> CardFeatures, run once, cached.

Each card appears in ~9-18 comparisons; instead of shipping the full article every
time, we summarize each article ONCE into a compact, impact-relevant digest (Haiku,
written through the profile lens), then comparisons ship tiny digests. This is what
makes pairwise ranking ~5x cheaper (Scenario C).

Pipeline per card:
  extract (text + est_minutes, code/free) -> Haiku digest (cached) -> CardFeatures
Unreadable cards (paywall/X/YouTube/dead link) get a title-based digest with NO LLM
call, so they can still be ranked in-list (just excluded from the listen queue later).
"""
from __future__ import annotations

import asyncio

from . import config
from .cache import Cache
from .extract import extract as default_extract
from .models import Card, CardFeatures

DIGEST_INSTRUCTIONS = (
    "Summarize the article into a terse digest (<= 120 words) optimized for ranking "
    "Jay's reading by counterfactual impact. Capture: the core topic; which pillar it "
    "fits (robotics / society & tech under AI / forecasting) or supporting interest; "
    "novelty/neglectedness; the key claims, data, or methods; and insight density. "
    "No preamble, no markdown headers — just the digest."
)

_TEXT_CAP_CHARS = 24000  # ~6k tokens, keeps long PDFs from blowing cost


class Enricher:
    def __init__(self, client=None, cache: Cache | None = None,
                 profile_doc: str | None = None, model: str | None = None,
                 concurrency: int | None = None, extract_fn=None):
        self.cache = cache
        self.model = model or config.CLAUDE_MODEL_DIGEST
        self.extract_fn = extract_fn or default_extract
        self._sem = asyncio.Semaphore(concurrency or config.MAX_LLM_CONCURRENCY)
        if profile_doc is not None:
            self.profile_doc = profile_doc
        else:
            self.profile_doc = config.PROFILE_DOC.read_text(encoding="utf-8")
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self.client = client

    async def _ask_digest(self, title: str, text: str) -> str:
        async with self._sem:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=256,
                system=[
                    {"type": "text", "text": DIGEST_INSTRUCTIONS},
                    {"type": "text", "text": self.profile_doc,
                     "cache_control": {"type": "ephemeral"}},
                ],
                messages=[{"role": "user",
                           "content": f"Title: {title}\n\n{text[:_TEXT_CAP_CHARS]}"}],
            )
        for block in getattr(resp, "content", []) or []:
            t = getattr(block, "text", None)
            if t:
                return t.strip()
        return title

    async def aenrich(self, card: Card) -> CardFeatures:
        if self.cache is not None:
            cached = self.cache.get_digest(card.id)
            if cached is not None:
                return cached

        ec = None
        if self.cache is not None:
            ec = self.cache.get_extracted(card.id)
        if ec is None:
            ec = self.extract_fn(card)
            if self.cache is not None:
                self.cache.put_extracted(ec)

        if not ec.ok:
            digest = f"[unreadable: {ec.note or ec.kind}] {ec.title}"
        else:
            digest = await self._ask_digest(ec.title, ec.text)

        feats = CardFeatures(card.id, ec.title, ec.est_minutes, digest, ec.kind, ec.ok)
        if self.cache is not None:
            self.cache.put_digest(feats, model=self.model)
        return feats

    async def aenrich_many(self, cards) -> list[CardFeatures]:
        return await asyncio.gather(*[self.aenrich(c) for c in cards])

    def enrich(self, card: Card) -> CardFeatures:
        return asyncio.run(self.aenrich(card))
