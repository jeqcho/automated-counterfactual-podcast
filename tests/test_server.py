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


def test_start_run_skips_when_same_phase_already_running():
    server._running["phase1"] = True
    try:
        started, msg = server.start_run("phase1")
        assert started is False
        assert "already running" in msg
    finally:
        server._running["phase1"] = False


def test_health_reports_queue(client):
    assert client.get("/health").json()["queued"] is None


def test_start_run_queues_other_phase_while_one_runs():
    """Cross-phase mutex: phase2 pressed during phase1 is QUEUED, not run in parallel
    (shared cache) and not dropped (Butler never shows a 409 to Jay)."""
    server._running["phase1"] = True
    try:
        started, msg = server.start_run("phase2")
        assert started is True and "queued" in msg
        assert "Sort readables" in msg and "Extract readables" in msg
        assert server._running["phase2"] is False   # must NOT run alongside phase1
        assert server._queued == "phase2"
        again, msg2 = server.start_run("phase2")    # re-press while queued = no-op
        assert again is False and "already queued" in msg2
    finally:
        server._running["phase1"] = False
        server._queued = None


def test_phase2_endpoint_queues_when_phase1_running(client):
    server._running["phase1"] = True
    try:
        r = client.post("/phase2", headers={"X-Trigger-Token": "secret123"})
        assert r.status_code == 200 and "queued" in r.json()["status"]
        assert client.get("/health").json()["queued"] == "phase2"
    finally:
        server._running["phase1"] = False
        server._queued = None


def test_queued_phase_runs_after_current_finishes(client, monkeypatch, no_r2_sync):
    """Press Extract, then Sort while Extract runs: Sort starts right after, never overlapping."""
    import threading
    order, release = [], threading.Event()

    async def fake1():
        order.append("phase1-start")
        release.wait(3)
        order.append("phase1-end")

    async def fake2():
        order.append("phase2")
    monkeypatch.setitem(server.RUNNERS, "phase1", fake1)
    monkeypatch.setitem(server.RUNNERS, "phase2", fake2)
    h = {"X-Trigger-Token": "secret123"}
    assert client.post("/phase1", headers=h).status_code == 200
    r = client.post("/phase2", headers=h)
    assert r.status_code == 200 and "queued" in r.json()["status"]
    release.set()
    deadline = time.time() + 3
    while (server._running["phase1"] or server._running["phase2"] or server._queued
           or "phase2" not in order) and time.time() < deadline:
        time.sleep(0.02)
    assert order == ["phase1-start", "phase1-end", "phase2"]
    assert server._queued is None and not any(server._running.values())
