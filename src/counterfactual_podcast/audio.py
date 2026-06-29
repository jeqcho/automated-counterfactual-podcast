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


def _signpost_text(body: str, *, title: str = "", author: str = "",
                   source: str = "", date: str = "") -> str:
    """Wrap the article in spoken intro/outro signposts so it's clear where one
    episode ends and the next begins. Minimal, and gracefully omits any missing
    field. Blank lines give the TTS a short pause around the body.

    Intro: "Start of article. <title>, by <author>, from <domain>, <Month Year>."
    Outro: "End of article. <title>, from <domain>."
    """
    title = (title or "").strip()
    if not title or not config.SPEAK_TITLE_INTRO:
        return body
    intro_bits = [title]
    if author:
        intro_bits.append(f"by {author}")
    if source:
        intro_bits.append(f"from {source}")
    if date:
        intro_bits.append(date)
    intro = f"Start of article. {', '.join(intro_bits)}.\n\n\n"
    outro_tail = f"{title}, from {source}" if source else title
    outro = f"\n\n\nEnd of article. {outro_tail}."
    return f"{intro}{body}{outro}"


def synthesize_card(
    card_id: str,
    text: str,
    *,
    engine=None,
    cache: Cache | None = None,
    out_dir: Path = config.OUTPUTS / "audio",
    ok: bool = True,
    title: str = "",
    author: str = "",
    source: str = "",
    date: str = "",
    r2_check=None,
) -> AudioAsset | None:
    """Synthesize ``text`` for ``card_id`` into an MP3 and return an AudioAsset.

    - ``ok=False`` (unreadable / paywalled card): can't be voiced -> return None.
    - If the cache already has audio for ``card_id`` AND the MP3 still exists — locally
      OR (via ``r2_check(card_id)``) in R2 — return the cached asset without re-synth.
      The R2 check is what makes audio durable on fresh cloud containers (no local disk).
    - Otherwise synth to ``out_dir/{card_id}.mp3`` via ``engine`` (defaulting to
      :func:`get_engine`), measure the real duration, build and cache an
      ``AudioAsset``, and return it.
    """
    if not ok:
        log.info("skip TTS for card %s (ok=False, unreadable/paywalled)", card_id)
        return None

    # Resume: reuse cached audio if its MP3 is still present locally or in R2.
    hit = cached_audio(card_id, cache, r2_check=r2_check)
    if hit is not None:
        return hit

    asset = render_audio(card_id, text, engine=engine, out_dir=out_dir,
                         title=title, author=author, source=source, date=date)

    if cache is not None:
        cache.put_audio(asset)
    return asset


def cached_audio(card_id: str, cache: Cache | None, *, r2_check=None) -> AudioAsset | None:
    """Return a reusable cached AudioAsset (its MP3 still exists locally or in R2), else None.

    This is a CACHE READ — keep it on the main thread (the SQLite connection is bound to its
    creating thread). Split out of :func:`synthesize_card` so the parallel queue builder can do
    hit-checks on the main thread before farming the misses out to a render thread pool.
    """
    if cache is None:
        return None
    cached = cache.get_audio(card_id)
    if cached is None:
        return None
    if cached.path and Path(cached.path).exists():
        log.debug("audio cache hit (local) for card %s -> %s", card_id, cached.path)
        return cached
    if r2_check is not None and r2_check(card_id):
        log.debug("audio cache hit (R2) for card %s", card_id)
        return cached
    return None


def render_audio(card_id: str, text: str, *, engine=None,
                 out_dir: Path = config.OUTPUTS / "audio",
                 title: str = "", author: str = "", source: str = "",
                 date: str = "") -> AudioAsset:
    """PURE synthesis: text -> MP3 file -> AudioAsset. Does NO cache I/O, so it is safe to run
    in a thread pool for thread-safe engines (Google/OpenAI). Each card writes a distinct
    ``{card_id}.mp3``, so concurrent renders don't collide. (Kokoro must NOT be run concurrently
    — its espeak phonemizer has global state; gate parallelism on config.PARALLEL_SAFE_TTS.)"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{card_id}.mp3"

    if engine is None:
        engine = get_engine()

    # Synthesize the FULL article — no truncation. One article = one episode, however
    # long. (Speed comes from a fast TTS provider, not from cutting content.) The title
    # is spoken first so it's clear where one episode ends and the next begins.
    engine.synthesize(
        _signpost_text(text, title=title, author=author, source=source, date=date),
        out_path)

    seconds = audio_duration_seconds(out_path)
    asset = AudioAsset(
        card_id=card_id,
        path=str(out_path),
        seconds=seconds,
        engine=getattr(engine, "name", ""),
    )
    log.info("synthesized card %s -> %s (%.1fs)", card_id, out_path, seconds)
    return asset
