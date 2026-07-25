"""Regression tests for the 2026-07-25 R2 cache corruption.

The live failure: R2's cache.sqlite3 was truncated by exactly one 4096-byte page (header
claimed 6727 pages, file held 6726). Every Phase 2 press then pulled it, died on open with
`database disk image is malformed`, and the `finally: push_cache_to_r2()` re-uploaded the
corrupt file — a loop that re-pressing could never escape.
"""
import sqlite3
import struct

import pytest

from counterfactual_podcast import cache as cache_mod
from counterfactual_podcast.cache import Cache, pull_cache_from_r2, push_cache_to_r2


def _make_db(path, rows=200):
    c = Cache(path)
    for i in range(rows):  # enough rows to span several pages
        c.conn.execute("INSERT INTO pairwise VALUES (?,?,?,?,?,?,?)",
                       (f"a{i}", f"b{i}", f"a{i}", 2, "why " * 40, "m", 0.0))
    c.conn.commit()
    c.close()
    return path


def _truncate_one_page(path):
    """Reproduce the exact production corruption: drop the final page."""
    with open(path, "rb") as f:
        head = f.read(100)
    page = struct.unpack(">H", head[16:18])[0]
    page = 65536 if page == 1 else page
    size = path.stat().st_size if hasattr(path, "stat") else __import__("os").path.getsize(path)
    with open(path, "r+b") as f:
        f.truncate(size - page)


class FakeR2:
    """Stands in for the R2 client; `store` is the bucket."""

    def __init__(self, store):
        self.store = store

    def download_file(self, bucket, key, dest):
        if key not in self.store:
            raise FileNotFoundError(key)
        with open(dest, "wb") as out, open(self.store[key], "rb") as src:
            out.write(src.read())

    def upload_file(self, src, bucket, key):
        dest = str(src) + f".uploaded-{key.replace('/', '_')}"
        with open(dest, "wb") as out, open(src, "rb") as f:
            out.write(f.read())
        self.store[key] = dest


@pytest.fixture
def r2(tmp_path, monkeypatch):
    store = {}
    fake = FakeR2(store)
    monkeypatch.setattr("counterfactual_podcast.r2.r2_configured", lambda: True)
    monkeypatch.setattr("counterfactual_podcast.r2.r2_client", lambda: fake)
    from counterfactual_podcast import config
    monkeypatch.setattr(config, "R2_BUCKET", "bucket")
    return store


def test_truncated_db_is_detected(tmp_path):
    p = tmp_path / "c.sqlite3"
    _make_db(str(p))
    assert cache_mod._sqlite_ok(str(p))
    _truncate_one_page(p)
    assert not cache_mod._sqlite_ok(str(p)), "one-page truncation must be caught"


def test_push_refuses_to_publish_a_corrupt_db(tmp_path, r2):
    """THE loop-breaker: a crashed run must not overwrite good R2 state with corruption."""
    good = tmp_path / "good.sqlite3"
    _make_db(str(good))
    assert push_cache_to_r2(path=str(good), key="state/cache.sqlite3") is True
    published = r2["state/cache.sqlite3"]

    bad = tmp_path / "bad.sqlite3"
    _make_db(str(bad))
    _truncate_one_page(bad)
    assert push_cache_to_r2(path=str(bad), key="state/cache.sqlite3") is False
    assert r2["state/cache.sqlite3"] == published, "good R2 copy must be left untouched"


def test_pull_quarantines_a_corrupt_download(tmp_path, r2):
    """A corrupt remote must not crash the run — quarantine and start empty."""
    src = tmp_path / "remote.sqlite3"
    _make_db(str(src))
    _truncate_one_page(src)
    r2["state/cache.sqlite3"] = str(src)

    dest = tmp_path / "local.sqlite3"
    assert pull_cache_from_r2(path=str(dest), key="state/cache.sqlite3") is False
    assert not dest.exists(), "corrupt file must be moved aside, not left in place"
    assert list(tmp_path.glob("local.sqlite3.corrupt-*")), "should be quarantined"
    # and the pipeline can now open a fresh cache at that path instead of crashing
    Cache(str(dest)).close()


def test_push_snapshot_is_consistent_with_an_open_writer(tmp_path, r2):
    """The root cause: push used to copy the live file while a Cache was still open."""
    p = tmp_path / "live.sqlite3"
    live = Cache(str(p))
    live.conn.execute("INSERT INTO pairwise VALUES (?,?,?,?,?,?,?)",
                      ("a", "b", "a", 1, "w", "m", 0.0))
    live.conn.commit()

    assert push_cache_to_r2(path=str(p), key="state/cache.sqlite3") is True
    uploaded = r2["state/cache.sqlite3"]
    assert cache_mod._sqlite_ok(uploaded)
    n = sqlite3.connect(uploaded).execute("SELECT COUNT(*) FROM pairwise").fetchone()[0]
    assert n == 1
    live.close()
    assert not (tmp_path / "live.sqlite3.snapshot").exists(), "snapshot must be cleaned up"
