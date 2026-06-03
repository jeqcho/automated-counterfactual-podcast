"""Phase-1 Inbox triage: is this card READING MATERIAL or a TODO/action?

The Inbox mixes links worth reading with todos, things-to-apply, and quick notes
(which sometimes also contain links). Phase 1 moves only the reading material to
'To Be Processed'; todos stay in the Inbox. This judges from the card title + URL
only (cheap Haiku call, no extraction) — Jay's review catches any edge cases.
"""
from __future__ import annotations

import asyncio

from . import config
from .models import Card

TRIAGE_INSTRUCTIONS = (
    "Decide whether a Trello inbox card is READING MATERIAL (an article, paper, post, "
    "video, or link Jay saved to read/consume later) or a TODO (a task, reminder, "
    "thing-to-apply, idea, or personal note — even if it contains a link). Reading "
    "material gets routed into Jay's reading lists; todos stay in his inbox. When a "
    "card is an action Jay must DO rather than something to READ, choose 'do'. "
    "Call the `triage` tool with kind = 'read' or 'do' and a one-line reason."
)

TRIAGE_TOOL = {
    "name": "triage",
    "description": "Classify an inbox card as reading material or a todo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["read", "do"]},
            "why": {"type": "string"},
        },
        "required": ["kind", "why"],
    },
}


class InboxTriager:
    def __init__(self, client=None, model: str | None = None, concurrency: int | None = None):
        self.model = model or config.CLAUDE_MODEL_DIGEST  # cheap (Haiku) is plenty
        self._sem = asyncio.Semaphore(concurrency or config.MAX_LLM_CONCURRENCY)
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self.client = client

    async def atriage(self, card: Card) -> dict:
        user = (f"Card title: {card.name}\n"
                f"Has link: {'yes' if (card.url or 'http' in (card.desc or '')) else 'no'}"
                f"{(' — ' + card.url) if card.url else ''}\n"
                f"Notes: {(card.desc or '')[:300]}\n\nIs this READ or DO?")
        async with self._sem:
            resp = await self.client.messages.create(
                model=self.model, max_tokens=150,
                messages=[{"role": "user", "content": user}],
                tools=[TRIAGE_TOOL],
                tool_choice={"type": "tool", "name": "triage"},
            )
        for block in getattr(resp, "content", []) or []:
            inp = getattr(block, "input", None)
            if inp is not None and inp.get("kind") in ("read", "do"):
                return {"kind": inp["kind"], "why": str(inp.get("why", ""))}
        # Fallback: if it has a link, treat as reading material; else a todo.
        return {"kind": "read" if card.url else "do", "why": "fallback"}

    def triage(self, card: Card) -> dict:
        return asyncio.run(self.atriage(card))
