"""TTS package: pluggable text-to-speech engines + a factory.

Use :func:`get_engine` to obtain an engine by name (defaults to
``config.TTS_ENGINE``). Engine modules keep their heavy imports lazy, so
importing this package is cheap and dependency-free.
"""
from __future__ import annotations

from .. import config
from .base import TTSEngine, chunk_text

__all__ = ["TTSEngine", "chunk_text", "get_engine"]


def get_engine(name: str | None = None) -> TTSEngine:
    """Return a TTS engine instance for ``name`` (default ``config.TTS_ENGINE``).

    Engine classes are imported inside this function so that selecting one
    engine never pulls in the other's dependencies.
    """
    name = (name or config.TTS_ENGINE).lower()

    if name == "kokoro":
        from .kokoro_engine import KokoroEngine

        return KokoroEngine()
    if name == "openai":
        from .openai_engine import OpenAIEngine

        return OpenAIEngine()

    raise ValueError(f"Unknown TTS engine: {name!r}")
