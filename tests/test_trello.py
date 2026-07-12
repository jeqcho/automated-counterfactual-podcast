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


# --- session-cookie native-Inbox path ----------------------------------------------------
_COOKIE = "cloud.session.token=abc; dsc=DSC123; other=x"


def _session_client():
    return TrelloClient("KEY", "TOKEN", sleep=lambda *_: None, session_cookie=_COOKIE)


def test_parse_dsc_and_has_session():
    c = _session_client()
    assert c.has_session is True
    assert c._dsc == "DSC123"
    # No cookie / no dsc -> not usable
    assert TrelloClient("K", "T", session_cookie="").has_session is False
    assert TrelloClient("K", "T", session_cookie="foo=bar").has_session is False


@responses.activate
def test_get_inbox_cards_uses_session_cookie():
    from counterfactual_podcast.trello import API_BASE as _AB  # token resolves inbox list id
    responses.add(responses.GET, f"{_AB}/1/members/me",
                  json={"inbox": {"idList": "INBOX", "idBoard": "IB"}}, status=200)
    # cards come from trello.com/1 (web base) with the cookie
    responses.add(responses.GET, "https://trello.com/1/lists/INBOX/cards",
                  json=[{"id": "c1", "name": "https://x.org/a", "pos": 1}], status=200)
    c = _session_client()
    cards = c.get_inbox_cards()
    assert [x.id for x in cards] == ["c1"]
    # the web request carried the cookie header
    web_call = [call for call in responses.calls if "trello.com/1/lists" in call.request.url][0]
    assert "dsc=DSC123" in web_call.request.headers["Cookie"]


@responses.activate
def test_move_inbox_card_posts_dsc_in_body():
    captured = {}

    def cb(request):
        captured["body"] = request.body
        captured["ct"] = request.headers.get("Content-Type", "")
        return (200, {}, "{}")

    responses.add_callback(responses.PUT, "https://trello.com/1/cards/c1", callback=cb)
    c = _session_client()
    c.move_inbox_card("c1", "TBP", "HOME")
    assert "idList=TBP" in captured["body"] and "idBoard=HOME" in captured["body"]
    assert "dsc=DSC123" in captured["body"]        # CSRF token submitted in the body
    assert "urlencoded" in captured["ct"]


@responses.activate
def test_web_request_raises_inbox_auth_error_on_401():
    import pytest
    from counterfactual_podcast.trello import InboxAuthError, API_BASE as _AB
    responses.add(responses.GET, f"{_AB}/1/members/me",
                  json={"inbox": {"idList": "INBOX", "idBoard": "IB"}}, status=200)
    responses.add(responses.GET, "https://trello.com/1/lists/INBOX/cards", status=401)
    c = _session_client()
    with pytest.raises(InboxAuthError):
        c.get_inbox_cards()


def test_inbox_methods_require_session_cookie():
    import pytest
    from counterfactual_podcast.trello import InboxAuthError
    c = TrelloClient("K", "T", sleep=lambda *_: None, session_cookie="")  # no cookie
    with pytest.raises(InboxAuthError):
        c.move_inbox_card("c1", "TBP", "HOME")
