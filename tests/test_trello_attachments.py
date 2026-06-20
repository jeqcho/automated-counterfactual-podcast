"""Regression tests for the attachment-URL fix.

Trello 'link cards' store the article URL as an attachment, not in name/desc. The
overnight live smoke test revealed get_cards was returning no article URL, so every
card fell back to title-only extraction (est_minutes=0). These lock the fix.
"""
import responses

from counterfactual_podcast.trello import TrelloClient


def test_best_attachment_url_prefers_external():
    f = TrelloClient._best_attachment_url
    atts = [
        {"url": "https://trello.com/1/cards/x/attachments/y/download/file.pdf"},
        {"url": "https://lesswrong.com/posts/abc"},
    ]
    assert f(atts) == "https://lesswrong.com/posts/abc"


def test_best_attachment_url_falls_back_to_trello_then_empty():
    f = TrelloClient._best_attachment_url
    assert f([{"url": "https://trello.com/x/file.pdf"}]) == "https://trello.com/x/file.pdf"
    assert f([]) == ""
    assert f(None) == ""
    assert f([{"url": "not-a-url"}]) == ""


@responses.activate
def test_get_cards_pulls_url_from_attachment():
    responses.add(
        responses.GET, "https://api.trello.com/1/lists/L1/cards",
        json=[{"id": "c1", "name": "A title", "desc": "",
               "attachments": [{"url": "https://example.com/article"}]}],
    )
    cards = TrelloClient("k", "t").get_cards("L1")
    assert cards[0].url == "https://example.com/article"


@responses.activate
def test_add_attachment_posts_url():
    # Adding a URL as an attachment is what makes Trello render a link preview for
    # cards whose link was pasted into the title/desc (instead of attached).
    responses.add(
        responses.POST, "https://api.trello.com/1/cards/c1/attachments",
        json={"id": "att1", "url": "https://example.com/article"}, status=200,
    )
    out = TrelloClient("k", "t").add_attachment("c1", "https://example.com/article")
    assert out["id"] == "att1"
    req = responses.calls[0].request
    assert req.method == "POST"
    assert "url=https%3A%2F%2Fexample.com%2Farticle" in req.url
    assert "key=k" in req.url and "token=t" in req.url
