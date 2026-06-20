"""Tests for the audio stage.

No real TTS, no model downloads, no network. A FAKE engine writes a tiny valid
WAV (via the stdlib ``wave`` module) to the requested path, so the duration
probe and on-disk assertions exercise real files without heavy deps. The
``get_engine`` default is never triggered — every test passes an engine.
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path

from counterfactual_podcast.audio import audio_duration_seconds, synthesize_card
from counterfactual_podcast.cache import Cache


def _write_tiny_wav(path: Path, *, frames: int = 2400, rate: int = 24000) -> None:
    """Write a small but valid mono 16-bit WAV to ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = [0] * frames
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class FakeEngine:
    """Minimal engine: a ``name`` and a ``synthesize`` that writes a tiny WAV."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.calls = 0

    def synthesize(self, text: str, out_path: Path) -> Path:
        self.calls += 1
        _write_tiny_wav(out_path)
        return Path(out_path)


class RecordingEngine(FakeEngine):
    """Captures the exact text handed to the engine."""
    def synthesize(self, text: str, out_path: Path) -> Path:
        self.last_text = text
        return super().synthesize(text, out_path)


# --- synthesize_card ------------------------------------------------------

def test_title_is_spoken_as_intro_and_outro(tmp_path):
    engine = RecordingEngine()
    synthesize_card("c-intro", "The article body.", engine=engine,
                    cache=Cache(), out_dir=tmp_path / "audio",
                    title="A Clear Title")
    assert engine.last_text.startswith("Start of article. A Clear Title.")
    assert "The article body." in engine.last_text
    assert engine.last_text.rstrip().endswith("End of article. A Clear Title.")


def test_signpost_includes_author_source_and_date(tmp_path):
    engine = RecordingEngine()
    synthesize_card("c-meta", "Body.", engine=engine, cache=Cache(),
                    out_dir=tmp_path / "audio", title="The Title",
                    author="Jane Doe", source="example.com", date="November 2017")
    assert engine.last_text.startswith(
        "Start of article. The Title, by Jane Doe, from example.com, November 2017.")
    # outro keeps it minimal: title + source.
    assert engine.last_text.rstrip().endswith("End of article. The Title, from example.com.")


def test_no_title_means_no_signpost(tmp_path):
    engine = RecordingEngine()
    synthesize_card("c-nointro", "Just the body.", engine=engine,
                    cache=Cache(), out_dir=tmp_path / "audio")
    assert engine.last_text == "Just the body."


def test_ok_false_returns_none_and_skips_engine(tmp_path):
    engine = FakeEngine()
    result = synthesize_card(
        "card-paywalled",
        "some text",
        engine=engine,
        cache=Cache(),
        out_dir=tmp_path / "audio",
        ok=False,
    )
    assert result is None
    assert engine.calls == 0


def test_readable_card_synthesizes_and_caches(tmp_path):
    cache = Cache()
    engine = FakeEngine(name="fake-voice")

    asset = synthesize_card(
        "card-1",
        "Hello world. This is a test.",
        engine=engine,
        cache=cache,
        out_dir=tmp_path / "audio",
    )

    assert asset is not None
    assert asset.card_id == "card-1"
    assert asset.engine == "fake-voice"
    assert asset.seconds >= 0
    # A real file landed on disk.
    assert Path(asset.path).exists()
    assert Path(asset.path).stat().st_size > 0
    # The engine actually ran.
    assert engine.calls == 1
    # And it was cached (verified via get_audio).
    cached = cache.get_audio("card-1")
    assert cached is not None
    assert cached.path == asset.path
    assert cached.engine == "fake-voice"


def test_second_call_uses_cache_and_skips_engine(tmp_path):
    cache = Cache()
    out_dir = tmp_path / "audio"

    engine1 = FakeEngine()
    first = synthesize_card("card-2", "text", engine=engine1, cache=cache, out_dir=out_dir)
    assert first is not None
    assert engine1.calls == 1
    assert Path(first.path).exists()

    # Second call: populated cache + existing file -> engine NOT invoked again.
    engine2 = FakeEngine()
    second = synthesize_card("card-2", "text", engine=engine2, cache=cache, out_dir=out_dir)
    assert engine2.calls == 0
    assert second is not None
    assert second.path == first.path
    assert second.card_id == "card-2"


def test_cache_miss_when_file_deleted_resynthesizes(tmp_path):
    cache = Cache()
    out_dir = tmp_path / "audio"

    engine1 = FakeEngine()
    first = synthesize_card("card-3", "text", engine=engine1, cache=cache, out_dir=out_dir)
    assert engine1.calls == 1

    # The cached file vanished from disk -> must re-synthesize.
    Path(first.path).unlink()
    engine2 = FakeEngine()
    second = synthesize_card("card-3", "text", engine=engine2, cache=cache, out_dir=out_dir)
    assert engine2.calls == 1
    assert second is not None
    assert Path(second.path).exists()


# --- audio_duration_seconds -----------------------------------------------

def test_audio_duration_on_wav_positive(tmp_path):
    wav = tmp_path / "clip.wav"
    _write_tiny_wav(wav, frames=24000, rate=24000)  # ~1 second
    assert audio_duration_seconds(wav) > 0


def test_audio_duration_never_raises_on_garbage(tmp_path):
    junk = tmp_path / "not-audio.mp3"
    junk.write_bytes(b"not really audio")
    # Must not raise; returns a float (0.0 when undeterminable).
    assert audio_duration_seconds(junk) == 0.0
