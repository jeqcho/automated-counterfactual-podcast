"""Unit tests for extract.py — pure, no real network."""
from __future__ import annotations

import pytest

from counterfactual_podcast import config
from counterfactual_podcast.extract import (
    _extract_main_text,
    est_minutes,
    extract,
    extract_from_text,
    find_url,
    is_hard,
)
from counterfactual_podcast.models import Card


def _fake_extractor(precise_text, recall_text):
    def ex(html, *, include_comments, favor_precision=False, favor_recall=False):
        return recall_text if favor_recall else precise_text
    return ex


def test_extract_main_text_keeps_precise_when_substantial():
    ex = _fake_extractor("x" * 800, "y" * 5000)
    # precise pass is over the threshold -> used as-is, recall not preferred
    assert _extract_main_text("<html>", extractor=ex) == "x" * 800


def test_extract_main_text_falls_back_to_recall_when_precise_thin():
    ex = _fake_extractor("x" * 50, "y" * 4000)  # precise nearly empty
    assert _extract_main_text("<html>", extractor=ex) == "y" * 4000


def test_extract_main_text_keeps_precise_if_recall_not_longer():
    ex = _fake_extractor("x" * 50, "")  # both poor, recall no better
    assert _extract_main_text("<html>", extractor=ex) == "x" * 50


def test_est_minutes_rounds():
    assert config.WPM_READING == 230
    assert est_minutes(2300) == 10
    assert est_minutes(0) == 0
    # 345/230 = 1.5 -> banker's/round() = 2 in py3 round-half-to-even (1.5->2)
    assert est_minutes(345) == round(345 / 230)


def test_find_url_in_name():
    card = Card(id="1", name="Cool post https://example.com/a great read")
    assert find_url(card) == "https://example.com/a"


def test_find_url_in_desc():
    card = Card(id="2", name="No link here", desc="see http://blog.test/x for more")
    assert find_url(card) == "http://blog.test/x"


def test_find_url_absent():
    card = Card(id="3", name="just a plain thought", desc="no links at all")
    assert find_url(card) is None


def test_is_hard_true():
    assert is_hard("https://x.com/someone/status/123") is True
    assert is_hard("https://www.youtube.com/watch?v=abc") is True
    assert is_hard("https://mobile.twitter.com/foo") is True
    assert is_hard("https://www.nytimes.com/2026/01/01/x.html") is True


def test_is_hard_false():
    assert is_hard("https://example.com/post") is False
    assert is_hard("https://arxiv.org/abs/2401.00001") is False


def test_bare_text_card():
    text = "one two three four five six seven eight nine ten"
    card = Card(id="t1", name=text)
    out = extract(card)
    assert out.kind == "text"
    assert out.ok is True
    assert out.word_count >= 10
    assert out.title == text


def test_extract_from_text_helper():
    out = extract_from_text("alpha beta gamma", card_id="z", title="T")
    assert out.kind == "text"
    assert out.ok is True
    assert out.word_count == 3
    assert out.title == "T"


def test_hard_domain_card():
    card = Card(id="h1", name="A tweet", desc="https://x.com/a/status/9")
    out = extract(card)
    assert out.ok is False
    assert out.kind == "hard"
    assert "hard source" in out.note
    assert out.text == "A tweet"


def test_html_card_with_injected_fetch():
    # 460 words -> est_minutes = 460/230 = 2
    words = " ".join(["word"] * 460)

    def fake_fetch(url):
        assert url == "https://example.com/article"
        return {"kind": "html", "text": words, "title": "Injected Title"}

    card = Card(id="html1", name="Some Article https://example.com/article")
    out = extract(card, fetch=fake_fetch)
    assert out.ok is True
    assert out.kind == "html"
    assert out.word_count == 460
    assert out.est_minutes == 2
    # card.name preferred for title
    assert out.title == "Some Article https://example.com/article"


def test_pdf_card_with_injected_fetch():
    words = " ".join(["pdfword"] * 230)

    def fake_fetch(url):
        return {"kind": "pdf", "text": words, "title": "P"}

    card = Card(id="pdf1", name="Paper", desc="https://example.com/paper.pdf")
    out = extract(card, fetch=fake_fetch)
    assert out.ok is True
    assert out.kind == "pdf"
    assert out.word_count == 230
    assert out.est_minutes == 1


def test_graceful_failure_never_raises():
    def boom_fetch(url):
        raise ConnectionError("network down")

    card = Card(id="f1", name="Will fail https://example.com/dead")
    out = extract(card, fetch=boom_fetch)  # must not raise
    assert out.ok is False
    assert out.kind == "hard"
    assert "ConnectionError" in out.note
    assert out.text == "Will fail https://example.com/dead"


def test_empty_extraction_degrades():
    def empty_fetch(url):
        return {"kind": "html", "text": "   ", "title": ""}

    card = Card(id="e1", name="Empty https://example.com/empty")
    out = extract(card, fetch=empty_fetch)
    assert out.ok is False
    assert out.kind == "hard"
