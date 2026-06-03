"""Cloud durability: cache <-> R2 sync (mocked, no real network)."""
import pytest

from counterfactual_podcast import cache as cache_mod
from counterfactual_podcast import config
from counterfactual_podcast import r2 as r2_mod
from counterfactual_podcast import server


class FakeS3:
    def __init__(self):
        self.uploaded = None
        self.downloaded = None

    def upload_file(self, path, bucket, key):
        self.uploaded = (path, bucket, key)

    def download_file(self, bucket, key, path):
        self.downloaded = (bucket, key, path)
        with open(path, "w") as f:
            f.write("downloaded-db")


def test_sync_is_noop_without_r2(monkeypatch, tmp_path):
    monkeypatch.setattr(r2_mod, "r2_configured", lambda: False)
    p = tmp_path / "c.sqlite3"
    p.write_text("db")
    assert cache_mod.push_cache_to_r2(p) is False
    assert cache_mod.pull_cache_from_r2(p) is False


def test_push_uploads_when_configured(monkeypatch, tmp_path):
    fake = FakeS3()
    monkeypatch.setattr(r2_mod, "r2_configured", lambda: True)
    monkeypatch.setattr(r2_mod, "r2_client", lambda: fake)
    monkeypatch.setattr(config, "R2_BUCKET", "bkt")
    p = tmp_path / "c.sqlite3"
    p.write_text("db")
    assert cache_mod.push_cache_to_r2(p) is True
    assert fake.uploaded == (str(p), "bkt", cache_mod.R2_CACHE_KEY)


def test_pull_downloads_when_present(monkeypatch, tmp_path):
    fake = FakeS3()
    monkeypatch.setattr(r2_mod, "r2_configured", lambda: True)
    monkeypatch.setattr(r2_mod, "r2_client", lambda: fake)
    monkeypatch.setattr(config, "R2_BUCKET", "bkt")
    p = tmp_path / "c.sqlite3"
    assert cache_mod.pull_cache_from_r2(p) is True
    assert p.read_text() == "downloaded-db"


async def test_run_named_wraps_with_pull_push(monkeypatch):
    order = []
    monkeypatch.setattr(cache_mod, "pull_cache_from_r2", lambda *a, **k: order.append("pull"))
    monkeypatch.setattr(cache_mod, "push_cache_to_r2", lambda *a, **k: order.append("push"))

    async def fake_runner():
        order.append("run")
    monkeypatch.setitem(server.RUNNERS, "phase1", fake_runner)
    await server.run_named("phase1")
    assert order == ["pull", "run", "push"]
