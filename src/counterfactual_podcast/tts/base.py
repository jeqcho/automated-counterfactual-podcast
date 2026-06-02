"""TTS engine protocol and shared, dependency-free helpers.

`TTSEngine` is a structural `typing.Protocol` — any object exposing a
`synthesize(text, out_path) -> Path` method satisfies it, so engines need not
inherit from anything. `chunk_text` is a pure function (no heavy deps) that
splits long text on sentence boundaries; it is the most-tested unit here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

# Split *after* a sentence terminator (. ! ?) that is followed by whitespace.
# The lookbehind keeps the terminator attached to the preceding sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@runtime_checkable
class TTSEngine(Protocol):
    """Anything that can turn text into an audio file at ``out_path``."""

    def synthesize(self, text: str, out_path: Path) -> Path:  # pragma: no cover
        ...


def chunk_text(text: str, max_chars: int = 1500) -> list[str]:
    """Split ``text`` into chunks of at most ``max_chars`` on sentence bounds.

    Sentences are detected by a ``.!?`` terminator followed by whitespace.
    Sentences are greedily accumulated into a chunk until adding the next one
    would exceed ``max_chars``; a single oversized sentence becomes its own
    chunk (it is never dropped or truncated). Concatenating the returned chunks
    (with a space between adjacent ones) preserves every sentence.
    """
    text = text.strip()
    if not text:
        return []

    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current = f"{current} {sentence}"
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks
