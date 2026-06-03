import asyncio

import pytest
from fastapi.testclient import TestClient

from counterfactual_podcast import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TRIGGER_TOKEN", "secret123")
    return TestClient(server.app)


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_phase1_rejects_missing_token(client):
    assert client.post("/phase1").status_code == 401


def test_phase1_rejects_wrong_token(client):
    assert client.post("/phase1", headers={"X-Trigger-Token": "nope"}).status_code == 401


def test_phase1_accepts_valid_token(client, monkeypatch):
    async def fake():
        return {"ok": True}
    monkeypatch.setitem(server.RUNNERS, "phase1", fake)
    r = client.post("/phase1", headers={"X-Trigger-Token": "secret123"})
    assert r.status_code == 200 and "started" in r.json()["status"]


async def test_run_named_invokes_runner(monkeypatch):
    called = {"n": 0}

    async def fake():
        called["n"] += 1
    monkeypatch.setitem(server.RUNNERS, "phase2", fake)
    await server.run_named("phase2")
    assert called["n"] == 1


async def test_run_named_skips_when_locked(monkeypatch):
    called = {"n": 0}

    async def fake():
        called["n"] += 1
    monkeypatch.setitem(server.RUNNERS, "phase1", fake)
    await server._LOCKS["phase1"].acquire()
    try:
        await server.run_named("phase1")   # locked -> should skip
        assert called["n"] == 0
    finally:
        server._LOCKS["phase1"].release()
