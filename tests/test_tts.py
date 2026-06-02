"""Tests for the TTS module group.

No real model, no downloads, no network. The Kokoro model-call boundary
(``_synth_chunk``) and the OpenAI client are monkeypatched. WAV writing uses
``soundfile`` if available, otherwise a stdlib ``wave`` fallback so the test
needs no heavy deps.
"""
from __future__ import annotations

import importlib.util

import pytest

from counterfactual_podcast.tts import TTSEngine, chunk_text, get_engine
from counterfactual_podcast.tts.base import _SENTENCE_SPLIT
from counterfactual_podcast.tts.kokoro_engine import KokoroEngine
from counterfactual_podcast.tts.openai_engine import OpenAIEngine

_HAVE_SOUNDFILE = importlib.util.find_spec("soundfile") is not None


# --- chunk_text -----------------------------------------------------------

def test_chunk_text_long_string_multiple_chunks():
    # 60 sentences, each ~ "Sentence number NN is here." -> well over max_chars.
    sentences = [f"Sentence number {i} is here." for i in range(60)]
    text = " ".join(sentences)
    max_chars = 200

    chunks = chunk_text(text, max_chars=max_chars)

    assert len(chunks) > 1, "long multi-sentence text should split"
    for c in chunks:
        assert len(c) <= max_chars, f"chunk exceeds max_chars: {c!r}"

    # Every original sentence survives, in order, across the concatenation.
    recombined = " ".join(chunks)
    recovered = [s for s in _SENTENCE_SPLIT.split(recombined) if s]
    assert recovered == sentences


def test_chunk_text_short_string_single_chunk():
    text = "Just one sentence here. And a second short one."
    chunks = chunk_text(text, max_chars=1500)
    assert chunks == [text]


def test_chunk_text_empty_returns_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_oversized_single_sentence_not_dropped():
    # A single sentence longer than max_chars must still come back whole.
    big = "word " * 100  # no sentence terminator -> one "sentence"
    big = big.strip() + "."
    chunks = chunk_text(big, max_chars=50)
    assert len(chunks) == 1
    assert chunks[0] == big


# --- factory --------------------------------------------------------------

def test_get_engine_kokoro_constructs_lazily():
    # Constructing must NOT load the model (that happens lazily in synthesize),
    # so this is cheap whether or not kokoro_onnx is installed.
    engine = get_engine("kokoro")
    assert isinstance(engine, KokoroEngine)
    assert isinstance(engine, TTSEngine)  # satisfies the runtime Protocol
    assert engine._model is None          # model not loaded at construction


def test_get_engine_default_is_kokoro():
    # config.TTS_ENGINE defaults to "kokoro".
    engine = get_engine()
    assert isinstance(engine, KokoroEngine)


def test_get_engine_unknown_raises():
    with pytest.raises(ValueError):
        get_engine("does-not-exist")


# --- KokoroEngine.synthesize (model patched) ------------------------------

def _write_wav_fallback(path, audio, sample_rate):
    """Minimal stdlib WAV writer used only when soundfile is unavailable."""
    import struct
    import wave

    # float32 [-1, 1] -> int16 PCM
    int_samples = [
        max(-32768, min(32767, int(s * 32767))) for s in audio
    ]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{len(int_samples)}h", *int_samples))


def test_kokoro_synthesize_writes_wav(tmp_path, monkeypatch):
    import numpy as np

    engine = get_engine("kokoro")

    def fake_synth_chunk(text):
        # Tiny deterministic audio; sample_rate is arbitrary.
        return np.linspace(-0.5, 0.5, 16, dtype=np.float32), 24000

    monkeypatch.setattr(engine, "_synth_chunk", fake_synth_chunk)

    out = tmp_path / "out.wav"

    if not _HAVE_SOUNDFILE:
        # Avoid the soundfile dependency in the engine's WAV writer.
        monkeypatch.setattr(
            KokoroEngine,
            "_write_wav",
            staticmethod(
                lambda audio, sr, p: _write_wav_fallback(p, audio, sr)
            ),
        )

    result = engine.synthesize("Hello world. This is a test.", out)

    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0


# --- OpenAIEngine.synthesize (client patched) -----------------------------

def test_openai_synthesize_writes_bytes(tmp_path, monkeypatch):
    engine = OpenAIEngine(model="tts-1", voice="alloy")

    payload = b"FAKE-MP3-BYTES"

    class _FakeResponse:
        content = payload

    class _FakeSpeech:
        def create(self, model, voice, input):  # noqa: A002 - mirror SDK
            assert model == "tts-1"
            assert voice == "alloy"
            assert input  # non-empty text passed through
            return _FakeResponse()

    class _FakeAudio:
        speech = _FakeSpeech()

    class _FakeClient:
        audio = _FakeAudio()

    # Patch the lazy client factory so no real OpenAI client is built.
    monkeypatch.setattr(engine, "_client", lambda: _FakeClient())

    out = tmp_path / "speech.mp3"
    result = engine.synthesize("Say something.", out)

    assert result == out
    assert out.exists()
    assert out.read_bytes() == payload


def test_get_engine_openai_instance():
    engine = get_engine("openai")
    assert isinstance(engine, OpenAIEngine)
    assert isinstance(engine, TTSEngine)
