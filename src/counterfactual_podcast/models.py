"""Shared data types passed between pipeline stages.

Keeping these in one place lets every module agree on the same shapes:
    Card            -> raw Trello card
    ExtractedContent-> extract.py output (text + reading time)
    CardFeatures    -> enrich.py output (digest, what the comparator consumes)
    PairwiseResult  -> llm_compare.py output
    AudioAsset      -> audio.py output
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Card:
    id: str
    name: str
    desc: str = ""
    url: str = ""
    list_id: str = ""
    pos: float = 0.0


@dataclass
class ExtractedContent:
    card_id: str
    title: str
    text: str
    word_count: int
    est_minutes: int
    kind: str          # "html" | "pdf" | "text" | "hard"
    ok: bool           # False => paywalled / X / YouTube / dead link (skip TTS)
    note: str = ""


@dataclass
class CardFeatures:
    """What the pairwise comparator consumes — small, cached, digest-based."""
    card_id: str
    title: str
    est_minutes: int
    digest: str
    kind: str
    ok: bool


@dataclass
class PairwiseResult:
    winner_id: str
    step: int          # which of the 7 comparator steps decided it
    why: str
    model: str = ""


@dataclass
class AudioAsset:
    card_id: str
    path: str
    seconds: float
    engine: str = ""
