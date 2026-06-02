"""Tests for the RSS feed / R2 publish module.

No real network: :func:`r2_client` is monkeypatched to a fake S3 that records
``put_object`` calls. Audio "files" are a few tmp bytes.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from counterfactual_podcast import config, rss
from counterfactual_podcast.rss import QueueEpisode, build_feed, publish

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _episodes(tmp_path, n=3):
    eps = []
    for i in range(n):
        p = tmp_path / f"card{i}.mp3"
        p.write_bytes(b"\x00\x01\x02\x03" * (i + 1))
        eps.append(
            QueueEpisode(
                card_id=f"card{i}",
                title=f"Episode {i}",
                audio_path=str(p),
                seconds=600 + i,
                url=f"https://example.com/{i}",
            )
        )
    return eps


# --- build_feed -----------------------------------------------------------

def test_build_feed_valid_rss_three_items_in_order(tmp_path):
    eps = _episodes(tmp_path, 3)
    xml = build_feed(eps, public_base="https://pub.example.com", prefix="abc123")

    root = ET.fromstring(xml)
    channel = root.find("channel")
    items = channel.findall("item")
    assert len(items) == 3

    # In order, with enclosure urls carrying card_id + public_base/prefix.
    for i, item in enumerate(items):
        title = item.find("title").text
        assert title == f"Episode {i}"
        enc = item.find("enclosure")
        url = enc.get("url")
        assert f"card{i}" in url
        assert "https://pub.example.com/abc123/" in url
        assert enc.get("type") == "audio/mpeg"
        # guid carries the card_id.
        assert item.find("guid").text == f"card{i}"
        # itunes:duration present.
        dur = item.find(f"{{{ITUNES_NS}}}duration")
        assert dur is not None
        assert dur.text


def test_build_feed_enclosure_length_is_file_size(tmp_path):
    eps = _episodes(tmp_path, 1)
    xml = build_feed(eps, public_base="https://pub.example.com", prefix="p")
    root = ET.fromstring(xml)
    enc = root.find("channel").find("item").find("enclosure")
    # card0 has 4 bytes.
    assert enc.get("length") == "4"


# --- duration formatting --------------------------------------------------

def test_duration_formatting():
    assert rss._fmt_duration(3661) == "1:01:01"
    assert rss._fmt_duration(0) == "0:00:00"
    assert rss._fmt_duration(59) == "0:00:59"
    assert rss._fmt_duration(600) == "0:10:00"


# --- publish: no upload ---------------------------------------------------

def test_publish_no_upload_writes_local_no_boto(tmp_path, monkeypatch):
    eps = _episodes(tmp_path, 2)

    out_dir = tmp_path / "outputs"
    monkeypatch.setattr(config, "OUTPUTS", out_dir)
    monkeypatch.setattr(config, "R2_PUBLIC_BASE", "https://pub.example.com")

    # If r2_client is ever called, blow up.
    def _boom():
        raise AssertionError("r2_client should not be called when upload=False")

    monkeypatch.setattr(rss, "r2_client", _boom)

    result = publish(eps, upload=False)

    assert result["uploaded"] == 0
    assert result["prefix"]
    assert result["feed_url"].startswith("https://pub.example.com/")
    assert result["feed_url"].endswith("/rss.xml")
    # Local file written.
    assert (out_dir / "rss.xml").exists()
    assert "<rss" in (out_dir / "rss.xml").read_text()


def test_publish_pinned_prefix(tmp_path, monkeypatch):
    eps = _episodes(tmp_path, 1)
    monkeypatch.setattr(config, "OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(config, "R2_PUBLIC_BASE", "https://pub.example.com")
    result = publish(eps, prefix="pinned", upload=False)
    assert result["prefix"] == "pinned"
    assert result["feed_url"] == "https://pub.example.com/pinned/rss.xml"


# --- publish: upload (fake S3) --------------------------------------------

class _FakeS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        return {}


def test_publish_upload_uploads_audio_and_feed(tmp_path, monkeypatch):
    eps = _episodes(tmp_path, 3)

    monkeypatch.setattr(config, "OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(config, "R2_PUBLIC_BASE", "https://pub.example.com")
    monkeypatch.setattr(config, "R2_BUCKET", "mybucket")
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "secret")

    fake = _FakeS3()
    monkeypatch.setattr(rss, "r2_client", lambda: fake)

    result = publish(eps, prefix="pfx", upload=True)

    # 3 audio + 1 rss.xml.
    assert result["uploaded"] == 3
    assert len(fake.calls) == 4

    keys = [c["Key"] for c in fake.calls]
    for i in range(3):
        assert f"pfx/card{i}.mp3" in keys
    assert "pfx/rss.xml" in keys

    # All keys under the prefix.
    assert all(k.startswith("pfx/") for k in keys)

    # Content types correct.
    by_key = {c["Key"]: c for c in fake.calls}
    assert by_key["pfx/card0.mp3"]["ContentType"] == "audio/mpeg"
    assert by_key["pfx/rss.xml"]["ContentType"] == "application/rss+xml"
    assert by_key["pfx/rss.xml"]["Bucket"] == "mybucket"

    # Local copy still written.
    assert (tmp_path / "outputs" / "rss.xml").exists()


def test_publish_upload_missing_config_does_not_crash(tmp_path, monkeypatch):
    eps = _episodes(tmp_path, 2)

    monkeypatch.setattr(config, "OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(config, "R2_PUBLIC_BASE", "")
    # Missing creds.
    monkeypatch.setattr(config, "R2_BUCKET", None)
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", None)
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", None)
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", None)

    def _boom():
        raise AssertionError("r2_client must not be called when config missing")

    monkeypatch.setattr(rss, "r2_client", _boom)

    result = publish(eps, prefix="pfx", upload=True)
    assert result["uploaded"] == 0
    assert (tmp_path / "outputs" / "rss.xml").exists()
