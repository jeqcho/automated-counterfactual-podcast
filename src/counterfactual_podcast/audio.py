"""Audio stage: turn a card's extracted text into a cached MP3 AudioAsset.

This sits after extraction/enrichment in the pipeline. It is resumable: an
``AudioAsset`` is cached per card, and a card is never re-synthesized while its
file is still on disk. Cards that could not be read (paywalled / X / YouTube /
dead links — ``ok=False``) cannot be voiced, so they are skipped (return None).

The engine call is a thin boundary (``engine.synthesize(text, out_path)``) so
tests can inject a fake engine without touching a real TTS model.
"""
from __future__ import annotations

import logging
import wave
from pathlib import Path

from . import config
from .cache import Cache
from .models import AudioAsset
from .tts import get_engine

log = logging.getLogger(__name__)


def audio_duration_seconds(path: str | Path) -> float:
    """Return the duration of an audio file in seconds; never raise.

    Tries mutagen first (handles MP3/WAV/etc). If mutagen can't read the file
    (e.g. a tiny non-audio test stub), falls back to the stdlib ``wave`` module
    for WAV files. Returns ``0.0`` if neither can determine a duration.
    """
    p = Path(path)

    # Primary: mutagen.
    try:
        import mutagen

        mf = mutagen.File(str(p))
        if mf is not None and getattr(mf, "info", None) is not None:
            length = getattr(mf.info, "length", None)
            if length is not None:
                return float(length)
    except Exception:  # noqa: BLE001 - duration probing must never raise
        pass

    # Fallback: stdlib wave (WAV only).
    try:
        with wave.open(str(p), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate:
                return frames / float(rate)
    except Exception:  # noqa: BLE001
        pass

    return 0.0


def synthesize_card(
    card_id: str,
    text: str,
    *,
    engine=None,
    cache: Cache | None = None,
    out_dir: Path = config.OUTPUTS / "audio",
    ok: bool = True,
) -> AudioAsset | None:
    """Synthesize ``text`` for ``card_id`` into an MP3 and return an AudioAsset.

    - ``ok=False`` (unreadable / paywalled card): can't be voiced -> return None.
    - If the cache already has audio for ``card_id`` and the file still exists,
      return the cached asset without re-synthesizing.
    - Otherwise synth to ``out_dir/{card_id}.mp3`` via ``engine`` (defaulting to
      :func:`get_engine`), measure the real duration, build and cache an
      ``AudioAsset``, and return it.
    """
    if not ok:
        log.info("skip TTS for card %s (ok=False, unreadable/paywalled)", card_id)
        return None

    # Resume: reuse cached audio only if its file is still present.
    if cache is not None:
        cached = cache.get_audio(card_id)
        if cached is not None and cached.path and Path(cached.path).exists():
            log.debug("audio cache hit for card %s -> %s", card_id, cached.path)
            return cached

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{card_id}.mp3"

    if engine is None:
        engine = get_engine()

    engine.synthesize(text, out_path)

    seconds = audio_duration_seconds(out_path)
    asset = AudioAsset(
        card_id=card_id,
        path=str(out_path),
        seconds=seconds,
        engine=getattr(engine, "name", ""),
    )

    if cache is not None:
        cache.put_audio(asset)

    log.info("synthesized card %s -> %s (%.1fs)", card_id, out_path, seconds)
    return asset
