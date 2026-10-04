"""Webhook trigger server for the Trello Butler buttons.

Two Butler board buttons issue an HTTP POST (one per phase); each runs the corresponding
pipeline with --apply. Exposed to Trello via Cloudflare (Worker -> Container) or a tunnel.
Requests must carry the shared secret in the ``X-Trigger-Token`` header.

IMPORTANT — the pipeline runs in a DEDICATED THREAD with its own asyncio event loop, not
on the FastAPI request loop. The pipeline does plenty of synchronous/blocking work (Trello
HTTP with rate-limit sleeps, SQLite cache, sort orchestration) that would otherwise starve
the event loop and make ``/health`` and ``/logs`` stop responding. On Cloudflare Containers
an unresponsive container fails its health probe and gets reaped mid-run — which is exactly
what killed early runs. Keeping the pipeline off the request loop fixes both the reaping and
the loss of ``/logs`` visibility during a run.

Run:  uv run uvicorn counterfactual_podcast.server:app --host 127.0.0.1 --port 8787
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

from fastapi import FastAPI, Header, HTTPException

from . import config
from .logging_setup import enable_ring_capture, recent_logs

log = logging.getLogger(__name__)

app = FastAPI(title="Counterfactual Podcast Triggers")

# Capture module-level loggers (per-card synth/enrich progress) into the ring buffer so
# /logs reflects a full run even though Cloudflare doesn't surface container stdout.
enable_ring_capture()

# Run state. The pipeline runs off the event loop in a worker thread, so a threading.Lock
# (not asyncio.Lock) guards the in-flight flags.
_state_lock = threading.Lock()
_running = {"phase1": False, "phase2": False}
# A phase pressed while the OTHER phase is in flight waits here and starts the moment the
# current run finishes. Butler never shows the HTTP response to Jay, so a 409 was invisible:
# pressing "Extract" then "Sort" right after silently dropped the Sort (2026-10-04).
_queued: str | None = None


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
    """Pull the cache from R2, run the phase, push the cache back.

    Wrapped so a scale-to-zero container preserves the expensive digests/comparisons/audio
    across runs (no-op when R2 is unconfigured). Runs inside the worker thread's loop; the
    in-flight flag is owned by :func:`start_run`/the worker, not here."""
    from .cache import pull_cache_from_r2, push_cache_to_r2
    await asyncio.to_thread(pull_cache_from_r2)
    try:
        await RUNNERS[name]()
    finally:
        await asyncio.to_thread(push_cache_to_r2)


# Friendly button labels for the warning message Jay sees.
_PHASE_LABELS = {"phase1": "Extract readables", "phase2": "Sort readables"}


def start_run(name: str) -> tuple[bool, str]:
    """Start ``run_named(name)`` in a daemon thread with its own event loop so the FastAPI
    request loop stays responsive — or QUEUE it if the other phase is in flight.

    GLOBAL mutex (not per-phase): two phases never run at once. Both pull the SAME R2 cache
    file (``state/cache.sqlite3``) into the same local path at start and push it back at
    finish — running both at once races on that file and can corrupt the cache. So a press
    for the other phase is queued (one slot) and runs right after, in the same worker thread,
    once the first run has pushed its cache. Pressing a phase that's already running or
    already queued is a harmless no-op.

    Returns ``(accepted, message)`` — False only for those no-op re-presses."""
    global _queued
    with _state_lock:
        busy = next((p for p, on in _running.items() if on), None)
        this_label = _PHASE_LABELS.get(name, name)
        if busy == name:
            return False, (f"'{this_label}' is already running — no need to press again.")
        if _queued == name:
            return False, (f"'{this_label}' is already queued — it starts when the current "
                           f"run finishes.")
        if busy is not None:
            _queued = name
            busy_label = _PHASE_LABELS.get(busy, busy)
            log.info("%s queued: waiting for '%s' to finish", name, busy_label)
            return True, (f"{name} queued — '{this_label}' starts automatically when "
                          f"'{busy_label}' finishes.")
        _running[name] = True

    def worker(current: str) -> None:
        global _queued
        while current is not None:
            try:
                asyncio.run(run_named(current))
            except Exception:  # noqa: BLE001 — a crashed pipeline must not wedge the flag
                log.exception("pipeline %s crashed", current)
            finally:
                # Hand off under the lock so no press can slip a parallel run into the gap.
                with _state_lock:
                    _running[current] = False
                    current, _queued = _queued, None
                    if current is not None:
                        _running[current] = True
            if current is not None:
                log.info("starting queued %s", current)

    threading.Thread(target=worker, args=(name,), name=f"pipeline-{name}",
                     daemon=True).start()
    return True, f"{name} started"


def _running_snapshot() -> dict:
    with _state_lock:
        return dict(_running)


def _queued_snapshot() -> str | None:
    with _state_lock:
        return _queued


@app.get("/health")
async def health():
    return {"ok": True, "running": _running_snapshot(), "queued": _queued_snapshot()}


@app.get("/logs")
async def logs(n: int = 200, x_trigger_token: str | None = Header(default=None)):
    """Recent pipeline log lines (token-protected) — the cloud's window into a run,
    since Cloudflare doesn't pipe container stdout into `wrangler tail`."""
    _check_token(x_trigger_token)
    n = max(1, min(int(n), 1000))
    return {"running": _running_snapshot(), "queued": _queued_snapshot(),
            "lines": recent_logs(n)}


@app.post("/phase1")
async def phase1(x_trigger_token: str | None = Header(default=None)):
    _check_token(x_trigger_token)
    started, message = start_run("phase1")
    if not started:
        # 409 only for a no-op re-press (same phase already running/queued).
        raise HTTPException(status_code=409, detail=message)
    return {"status": message}


@app.post("/phase2")
async def phase2(x_trigger_token: str | None = Header(default=None)):
    _check_token(x_trigger_token)
    started, message = start_run("phase2")
    if not started:
        raise HTTPException(status_code=409, detail=message)
    return {"status": message}
