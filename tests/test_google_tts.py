import pytest

from counterfactual_podcast.tts import get_engine
from counterfactual_podcast.tts.google_engine import GoogleEngine


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
