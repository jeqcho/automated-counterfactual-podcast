import pytest

from counterfactual_podcast.tts import get_engine
from counterfactual_podcast.tts.google_engine import GoogleEngine, byte_safe_chunks


def test_byte_safe_chunks_run_on_sentence_under_limit():
    # A "sentence" with no .!? terminator, far over Google's 5000-byte limit.
    text = "word " * 4000  # ~20000 bytes, single run-on chunk from chunk_text
    chunks = byte_safe_chunks(text)
    assert chunks and all(len(c.encode("utf-8")) <= 4800 for c in chunks)
    # all word content preserved (only boundary whitespace dropped)
    assert "".join(c.replace(" ", "") for c in chunks) == text.replace(" ", "")


def test_byte_safe_chunks_multibyte_under_limit():
    text = "あ" * 3000  # ~9000 bytes, no whitespace, no terminator
    chunks = byte_safe_chunks(text)
    assert chunks and all(len(c.encode("utf-8")) <= 4800 for c in chunks)
    assert "".join(chunks) == text


def test_get_engine_google():
    eng = get_engine("google")
    assert isinstance(eng, GoogleEngine)
    assert eng._client is None          # client built lazily, not at construction


def test_single_chunk_writes_mp3_bytes(tmp_path, monkeypatch):
    eng = GoogleEngine(client=object())  # client present so _get_client isn't needed
    monkeypatch.setattr(eng, "_synth_chunk", lambda text: b"ID3FAKEMP3BYTES")
    out = tmp_path / "x.mp3"
    p = eng.synthesize("a short sentence.", out)
    assert p.read_bytes() == b"ID3FAKEMP3BYTES"


def test_voice_defaults_to_neural2():
    assert GoogleEngine().voice == "en-US-Neural2-D"
