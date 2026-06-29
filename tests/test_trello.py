import re

import responses

from counterfactual_podcast.models import Card
from counterfactual_podcast.trello import API_BASE, TrelloClient


def _client(monkeypatch=None):
    # No-op sleep so rate-limiter / backoff never actually blocks.
    return TrelloClient("KEY", "TOKEN", sleep=lambda *_: None)


@responses.activate
def test_get_cards_parses_names_and_sets_list_id():
    list_id = "L1"
    responses.add(
        responses.GET,
        f"{API_BASE}/1/lists/{list_id}/cards",
        json=[
            {"id": "a", "name": "First", "desc": "d1", "url": "u1", "pos": 16.5},
            {"id": "b", "name": "Second", "desc": "", "url": "u2", "pos": 32},
        ],
        status=200,
    )
    c = _client()
    cards = c.get_cards(list_id)
    assert [x.name for x in cards] == ["First", "Second"]
    assert all(x.list_id == list_id for x in cards)
    assert cards[0].pos == 16.5
    # key/token injected
    qs = responses.calls[0].request.url
    assert "key=KEY" in qs and "token=TOKEN" in qs
    assert "fields=name" in qs


@responses.activate
def test_set_card_position_puts_pos():
    responses.add(
        responses.PUT, f"{API_BASE}/1/cards/c1", json={"id": "c1"}, status=200
    )
    c = _client()
    c.set_card_position("c1", "top")
    url = responses.calls[0].request.url
    assert responses.calls[0].request.method == "PUT"
    assert "pos=top" in url


@responses.activate
def test_set_rank_marker_idempotent():
    captured = {}

    def cb(request):
        # Capture the desc param sent on each PUT.
        from urllib.parse import urlparse, parse_qs

        q = parse_qs(urlparse(request.url).query)
        captured["desc"] = q["desc"][0]
        return (200, {}, "{}")

    responses.add_callback(
        responses.PUT,
        f"{API_BASE}/1/cards/c1",
        callback=cb,
    )

    c = _client()
    card = Card(id="c1", name="n", desc="original body")

    first = c.set_rank_marker(card, 3, 12, "high impact")
    assert first.count("<!--cf-->") == 1
    assert "original body" in first

    # Feed the marked desc back in and re-rank — must replace, not duplicate.
    card2 = Card(id="c1", name="n", desc=first)
    second = c.set_rank_marker(card2, 1, 9, "even better")
    assert second.count("<!--cf-->") == 1
    assert second.count("<!--/cf-->") == 1
    assert "#1" in second and "#3" not in second
    assert "original body" in second
    assert captured["desc"] == second


@responses.activate
def test_set_rank_marker_embeds_full_multiline_digest():
    captured = {}

    def cb(request):
        from urllib.parse import parse_qs, urlparse
        captured["desc"] = parse_qs(urlparse(request.url).query)["desc"][0]
        return (200, {}, "{}")

    responses.add_callback(responses.PUT, f"{API_BASE}/1/cards/c1", callback=cb)
    c = _client()
    digest = '# Digest: "The Void"\n\n**Core topic:** LLMs lack a coherent character.\n**Key claims:** (1) ...'
    out = c.set_rank_marker(Card(id="c1", name="n", desc="my note"), 39, 28, digest)

    # Rank tag on its own; full digest (all lines) embedded; note preserved.
    assert "[#39 · 28 min]" in out
    assert "**Core topic:**" in out and "**Key claims:**" in out
    assert "my note" in out
    # Re-marking with a new multiline digest replaces cleanly (no marker pile-up).
    second = c.set_rank_marker(Card(id="c1", name="n", desc=out), 5, 10, "short digest")
    assert second.count("<!--cf-->") == 1 and second.count("<!--/cf-->") == 1
    assert "Core topic" not in second and "[#5 · 10 min]" in second
    assert "my note" in second


@responses.activate
def test_429_retry_after_then_success():
    url = f"{API_BASE}/1/members/me"
    responses.add(
        responses.GET, url, status=429, headers={"Retry-After": "1"}, json={}
    )
    responses.add(
        responses.GET, url, status=200, json={"inbox": {"idList": "INBOX1"}}
    )
    c = _client()
    assert c.inbox_list_id() == "INBOX1"
    assert len(responses.calls) == 2


@responses.activate
def test_inbox_list_id():
    responses.add(
        responses.GET,
        f"{API_BASE}/1/members/me",
        json={"inbox": {"idList": "INBOX99"}},
        status=200,
    )
    c = _client()
    assert c.inbox_list_id() == "INBOX99"
    assert "fields=inbox" in responses.calls[0].request.url


@responses.activate
def test_ensure_list_finds_existing():
    board = "B1"
    responses.add(
        responses.GET,
        f"{API_BASE}/1/boards/{board}/lists",
        json=[{"id": "L9", "name": "Listen Queue"}],
        status=200,
    )
    c = _client()
    assert c.ensure_list("Listen Queue", board_id=board) == "L9"
    assert len(responses.calls) == 1  # no POST/create


@responses.activate
def test_ensure_list_creates_when_missing():
    board = "B1"
    responses.add(
        responses.GET,
        f"{API_BASE}/1/boards/{board}/lists",
        json=[{"id": "L9", "name": "Other"}],
        status=200,
    )
    responses.add(
        responses.POST, f"{API_BASE}/1/lists", json={"id": "NEW"}, status=200
    )
    c = _client()
    assert c.ensure_list("Listen Queue", board_id=board) == "NEW"
    assert responses.calls[1].request.method == "POST"


@responses.activate
def test_move_card_sends_idlist_and_pos():
    responses.add(
        responses.PUT, f"{API_BASE}/1/cards/c1", json={"id": "c1"}, status=200
    )
    c = _client()
    c.move_card("c1", "L2", pos="bottom")
    url = responses.calls[0].request.url
    assert "idList=L2" in url and "pos=bottom" in url


@responses.activate
def test_request_retries_on_timeout_then_succeeds():
    # A hung connection must surface as a retryable Timeout (not block forever and wedge the
    # async event loop). First attempt times out, second succeeds.
    import requests

    responses.add(responses.GET, f"{API_BASE}/1/x", body=requests.exceptions.Timeout())
    responses.add(responses.GET, f"{API_BASE}/1/x", json={"ok": True}, status=200)
    c = _client()
    assert c._request("GET", "/1/x") == {"ok": True}
    assert len(responses.calls) == 2


@responses.activate
def test_request_passes_timeout_to_requests():
    # Guard: every request MUST carry a finite timeout, else requests blocks indefinitely.
    from counterfactual_podcast.trello import _REQUEST_TIMEOUT

    captured = {}

    def cb(request):
        captured["timeout"] = getattr(request, "req_kwargs", {})
        return (200, {}, "{}")

    responses.add(responses.GET, f"{API_BASE}/1/x", json={"ok": True}, status=200)
    c = _client()
    # Patch the session.request to assert the timeout kwarg is forwarded.
    orig = c._session.request
    seen = {}

    def wrapped(method, url, **kw):
        seen["timeout"] = kw.get("timeout")
        return orig(method, url, **kw)

    c._session.request = wrapped
    c._request("GET", "/1/x")
    assert seen["timeout"] == _REQUEST_TIMEOUT


@responses.activate
def test_request_raises_after_timeout_retries_exhausted():
    import pytest
    import requests

    for _ in range(5):
        responses.add(responses.GET, f"{API_BASE}/1/x", body=requests.exceptions.ConnectionError())
    c = _client()
    with pytest.raises(requests.ConnectionError):
        c._request("GET", "/1/x")
