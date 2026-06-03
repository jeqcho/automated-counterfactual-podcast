from types import SimpleNamespace

import pytest

from counterfactual_podcast.models import Card
from counterfactual_podcast.triage import InboxTriager


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.messages = self

    async def create(self, **kwargs):
        out = self.handler(kwargs)
        content = [] if out is None else [SimpleNamespace(input=out)]
        return SimpleNamespace(content=content)


def keyword_handler(kwargs):
    user = kwargs["messages"][0]["content"].lower()
    if "apply" in user or "todo" in user or "remember" in user:
        return {"kind": "do", "why": "actionable"}
    return {"kind": "read", "why": "article"}


async def test_reading_material_is_read():
    t = InboxTriager(client=FakeClient(keyword_handler), concurrency=2)
    v = await t.atriage(Card("c1", "A great essay on robotics", url="https://x.org/a"))
    assert v["kind"] == "read"


async def test_todo_is_do():
    t = InboxTriager(client=FakeClient(keyword_handler), concurrency=2)
    v = await t.atriage(Card("c2", "apply to the Oxford fellowship", url="https://apply.org"))
    assert v["kind"] == "do"


async def test_fallback_uses_url_presence():
    # handler returns nothing -> fallback: has url => read, else do
    t = InboxTriager(client=FakeClient(lambda k: None), concurrency=2)
    assert (await t.atriage(Card("c", "note", url="https://x.org")))["kind"] == "read"
    assert (await t.atriage(Card("c", "buy milk", url="")))["kind"] == "do"
