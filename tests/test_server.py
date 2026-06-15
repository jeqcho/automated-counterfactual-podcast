import time

import pytest
from fastapi.testclient import TestClient

import counterfactual_podcast.cache as cache_mod
from counterfactual_podcast import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TRIGGER_TOKEN", "secret123")
    return TestClient(server.app)


@pytest.fixture
def no_r2_sync(monkeypatch):
    # .env is loaded at import, so R2 creds are live in the test env. The threaded runner
    # calls pull/push cache for real — stub them so tests never touch R2.
    monkeypatch.setattr(cache_mod, "pull_cache_from_r2", lambda *a, **k: False)
    monkeypatch.setattr(cache_mod, "push_cache_to_r2", lambda *a, **k: False)


def _wait_idle(name, timeout=3.0):
    deadline = time.time() + timeout
    while server._running[name] and time.time() < deadline:
        time.sleep(0.02)


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["running"] == {"phase1": False, "phase2": False}


def test_phase1_rejects_missing_token(client):
    assert client.post("/phase1").status_code == 401


def test_phase1_rejects_wrong_token(client):
    assert client.post("/phase1", headers={"X-Trigger-Token": "nope"}).status_code == 401


def test_phase1_accepts_valid_token_and_runs(client, monkeypatch, no_r2_sync):
    ran = {"n": 0}

    async def fake():
        ran["n"] += 1
    monkeypatch.setitem(server.RUNNERS, "phase1", fake)
    r = client.post("/phase1", headers={"X-Trigger-Token": "secret123"})
    assert r.status_code == 200 and "started" in r.json()["status"]
    _wait_idle("phase1")               # background worker thread runs the (fake) pipeline
    assert ran["n"] == 1


def test_logs_requires_token(client):
    assert client.get("/logs").status_code == 401
    assert client.get("/logs", headers={"X-Trigger-Token": "nope"}).status_code == 401


def test_logs_returns_recent_lines(client):
    import logging

    from counterfactual_podcast.logging_setup import enable_ring_capture
    enable_ring_capture()
    logging.getLogger("counterfactual_podcast.test").info("hello-from-test-run")
    r = client.get("/logs", headers={"X-Trigger-Token": "secret123"})
    assert r.status_code == 200
    body = r.json()
    assert "running" in body and isinstance(body["lines"], list)
    assert any("hello-from-test-run" in line for line in body["lines"])


async def test_run_named_invokes_runner(monkeypatch, no_r2_sync):
    called = {"n": 0}

    async def fake():
        called["n"] += 1
    monkeypatch.setitem(server.RUNNERS, "phase2", fake)
    await server.run_named("phase2")
    assert called["n"] == 1


def test_start_run_skips_when_already_running():
    server._running["phase1"] = True
    try:
        assert server.start_run("phase1") is False
    finally:
        server._running["phase1"] = False
