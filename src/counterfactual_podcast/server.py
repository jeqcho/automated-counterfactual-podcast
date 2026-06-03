"""Webhook trigger server for the Trello Butler buttons.

Two Butler board buttons (Premium) issue an HTTP POST to this server — one per phase —
and it runs the corresponding pipeline with --apply. Exposed to Trello via a Cloudflare
Tunnel (see reports/trigger-setup.md). Requests must carry the shared secret in the
`X-Trigger-Token` header (matched against env `TRIGGER_TOKEN`).

Each phase runs as a fire-and-forget background task and the endpoint returns
immediately (Butler's HTTP request has a short timeout). A per-phase lock prevents
overlapping runs.

Run:  uv run uvicorn counterfactual_podcast.server:app --host 127.0.0.1 --port 8787
"""
from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Header, HTTPException

from . import config

app = FastAPI(title="Counterfactual Podcast Triggers")

_LOCKS = {"phase1": asyncio.Lock(), "phase2": asyncio.Lock()}


async def _default_phase1():
    from .logging_setup import setup_logging
    from .pipelines.phase1 import _build_and_run
    return await _build_and_run(apply=True, log=setup_logging("phase1"))


async def _default_phase2():
    from .logging_setup import setup_logging
    from .pipelines.phase2 import _build_and_run
    return await _build_and_run(apply=True, log=setup_logging("phase2"))


# Indirection so tests can swap in fakes.
RUNNERS = {"phase1": _default_phase1, "phase2": _default_phase2}


def _check_token(token: str | None) -> None:
    expected = os.environ.get("TRIGGER_TOKEN")
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing trigger token")


async def run_named(name: str) -> None:
    """Run a phase under its lock; skip if one is already in flight.

    Wraps the run with R2 cache pull/push so a scale-to-zero container preserves the
    expensive digests/comparisons across runs (no-op when R2 is unconfigured)."""
    lock = _LOCKS[name]
    if lock.locked():
        return
    async with lock:
        from .cache import pull_cache_from_r2, push_cache_to_r2
        await asyncio.to_thread(pull_cache_from_r2)
        try:
            await RUNNERS[name]()
        finally:
            await asyncio.to_thread(push_cache_to_r2)


@app.get("/health")
async def health():
    return {"ok": True, "running": {k: v.locked() for k, v in _LOCKS.items()}}


@app.post("/phase1")
async def phase1(x_trigger_token: str | None = Header(default=None)):
    _check_token(x_trigger_token)
    if _LOCKS["phase1"].locked():
        return {"status": "phase1 already running"}
    asyncio.create_task(run_named("phase1"))
    return {"status": "phase1 started"}


@app.post("/phase2")
async def phase2(x_trigger_token: str | None = Header(default=None)):
    _check_token(x_trigger_token)
    if _LOCKS["phase2"].locked():
        return {"status": "phase2 already running"}
    asyncio.create_task(run_named("phase2"))
    return {"status": "phase2 started"}
