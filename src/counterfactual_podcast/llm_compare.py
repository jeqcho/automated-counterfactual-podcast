"""Pairwise comparator: 'which article should Jay read first, A or B?'.

Uses Claude with the scoped profile doc as a prompt-CACHED system block (charged
once per ~5-min window, read cheaply on every one of ~6,700 calls). Consumes
`CardFeatures` (digests), not raw text. Deterministic + total: the model is forced
to pick a winner via a tool; any parse failure falls back to a deterministic rule
(shorter reading time, then card id) so the sort never sees a tie.

Close calls (the model reports it decided at step >= 6) are re-asked on the
escalation model (Opus). Results are cached so re-runs and resumes are ~free.
"""
from __future__ import annotations

import asyncio

from . import config
from .cache import Cache
from .models import CardFeatures, PairwiseResult

PAIRWISE_INSTRUCTIONS = (
    "You rank Jay's reading list by counterfactual impact via PAIRWISE comparison. "
    "Given article A and B, decide which Jay should read FIRST by applying the "
    "7-step deterministic comparator in the profile document (relevance gate -> "
    "impact magnitude -> impact per minute -> pillar priority -> 2028 boost -> "
    "novelty -> deterministic fallback). Stop at the first step that separates them. "
    "Never tie. Call the `decide` tool with the winner (A or B), the step number "
    "(1-7) that decided it, and a one-line reason."
)

DECIDE_TOOL = {
    "name": "decide",
    "description": "Report which article Jay should read first.",
    "input_schema": {
        "type": "object",
        "properties": {
            "winner": {"type": "string", "enum": ["A", "B"]},
            "step": {"type": "integer", "description": "which comparator step (1-7) decided it"},
            "why": {"type": "string", "description": "one-line reason naming the deciding factor"},
        },
        "required": ["winner", "step", "why"],
    },
}


def _fmt(label: str, f: CardFeatures) -> str:
    return (
        f"--- Article {label} ---\n"
        f"Title: {f.title}\n"
        f"Kind: {f.kind}  | Est. reading minutes: {f.est_minutes}\n"
        f"Digest: {f.digest}\n"
    )


class Comparator:
    def __init__(
        self,
        client=None,
        cache: Cache | None = None,
        profile_doc: str | None = None,
        model: str | None = None,
        escalate_model: str | None = None,
        concurrency: int | None = None,
    ):
        self.cache = cache
        self.model = model or config.CLAUDE_MODEL
        self.escalate_model = escalate_model or config.CLAUDE_MODEL_ESCALATE
        self._sem = asyncio.Semaphore(concurrency or config.MAX_LLM_CONCURRENCY)
        if profile_doc is not None:
            self.profile_doc = profile_doc
        else:
            self.profile_doc = config.PROFILE_DOC.read_text(encoding="utf-8")
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY,
                                    timeout=config.ANTHROPIC_TIMEOUT_SECONDS,
                                    max_retries=config.ANTHROPIC_MAX_RETRIES)
        self.client = client

    # --- deterministic fallback (total order, never a tie) ---------------
    @staticmethod
    def _fallback(a: CardFeatures, b: CardFeatures) -> CardFeatures:
        if a.est_minutes != b.est_minutes:
            return a if a.est_minutes < b.est_minutes else b
        return a if a.card_id <= b.card_id else b

    def _system(self):
        return [
            {"type": "text", "text": PAIRWISE_INSTRUCTIONS},
            {"type": "text", "text": self.profile_doc,
             "cache_control": {"type": "ephemeral"}},
        ]

    async def _ask(self, model: str, a: CardFeatures, b: CardFeatures) -> dict | None:
        user = _fmt("A", a) + "\n" + _fmt("B", b) + "\nWhich should Jay read first?"
        async with self._sem:
            resp = await self.client.messages.create(
                model=model,
                max_tokens=300,
                system=self._system(),
                messages=[{"role": "user", "content": user}],
                tools=[DECIDE_TOOL],
                tool_choice={"type": "tool", "name": "decide"},
            )
        for block in getattr(resp, "content", []) or []:
            inp = getattr(block, "input", None)
            if inp is not None:
                return dict(inp)
        return None

    async def acompare(self, a: CardFeatures, b: CardFeatures) -> CardFeatures:
        # cache hit?
        if self.cache is not None:
            cached = self.cache.get_pairwise(a.card_id, b.card_id)
            if cached is not None:
                return a if cached.winner_id == a.card_id else b

        out = await self._ask(self.model, a, b)
        model_used = self.model
        if out and int(out.get("step", 0)) >= 6 and self.escalate_model != self.model:
            esc = await self._ask(self.escalate_model, a, b)
            if esc:
                out, model_used = esc, self.escalate_model

        if not out or out.get("winner") not in ("A", "B"):
            winner = self._fallback(a, b)
            res = PairwiseResult(winner.card_id, step=7, why="deterministic fallback",
                                 model=model_used)
        else:
            winner = a if out["winner"] == "A" else b
            res = PairwiseResult(winner.card_id, int(out.get("step", 0)),
                                 str(out.get("why", "")), model_used)

        if self.cache is not None:
            self.cache.put_pairwise(a.card_id, b.card_id, res)
        return winner

    def compare(self, a: CardFeatures, b: CardFeatures) -> CardFeatures:
        return asyncio.run(self.acompare(a, b))
