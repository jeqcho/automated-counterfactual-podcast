from counterfactual_podcast.cache import Cache
from counterfactual_podcast.models import (
    AudioAsset, CardFeatures, ExtractedContent, PairwiseResult,
)


def test_extracted_roundtrip():
    c = Cache()
    ec = ExtractedContent("c1", "T", "body words here", 3, 1, "text", True, "")
    c.put_extracted(ec)
    got = c.get_extracted("c1")
    assert got.title == "T" and got.ok is True and got.word_count == 3
    assert c.get_extracted("missing") is None


def test_digest_roundtrip():
    c = Cache()
    f = CardFeatures("c1", "T", 5, "a digest", "html", True)
    c.put_digest(f, model="haiku")
    got = c.get_digest("c1")
    assert got.digest == "a digest" and got.est_minutes == 5


def test_pairwise_is_symmetric():
    c = Cache()
    c.put_pairwise("b", "a", PairwiseResult(winner_id="a", step=2, why="x", model="m"))
    # lookup in both orders returns the same stored winner
    assert c.get_pairwise("a", "b").winner_id == "a"
    assert c.get_pairwise("b", "a").winner_id == "a"


def test_audio_roundtrip():
    c = Cache()
    c.put_audio(AudioAsset("c1", "/tmp/a.mp3", 12.5, "kokoro"))
    assert c.get_audio("c1").seconds == 12.5
