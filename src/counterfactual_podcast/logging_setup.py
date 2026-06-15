"""Logging setup: console + timestamped file logger.

`setup_logging(name)` returns a `logging.Logger` that writes to both stdout and a
timestamped file `config.LOGS / f"{name}-YYYYMMDD-HHMMSS.log"`. The created log
file path is exposed as the `logger.log_path` attribute (a `pathlib.Path`) so
callers/tests can find the file. Calling it twice with the same name reuses the
existing handlers (no duplicates) and keeps the original `log_path`.
"""
from __future__ import annotations

import collections
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import config

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# --- In-process ring buffer of recent log lines ---------------------------
# Cloudflare Containers don't surface stdout into `wrangler tail` (Worker-only), so
# in the cloud the pipeline's progress is otherwise invisible. We tee every log record
# into a bounded deque that the FastAPI server exposes via GET /logs — making any run
# (local or cloud) observable over HTTP without depending on container-log plumbing.
_RING_MAX = 1000
_ring: collections.deque[str] = collections.deque(maxlen=_RING_MAX)
_ring_lock = threading.Lock()


class _RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001 — logging must never raise
            return
        with _ring_lock:
            _ring.append(msg)


_ring_handler = _RingBufferHandler()
_ring_handler.setLevel(logging.INFO)
_ring_handler.setFormatter(logging.Formatter(_FORMAT))


def recent_logs(n: int = 200) -> list[str]:
    """Return the last ``n`` captured log lines (most recent last)."""
    with _ring_lock:
        return list(_ring)[-n:]


# Chatty third-party loggers that would otherwise drown the pipeline's own progress
# lines in /logs (one INFO "HTTP Request" per Anthropic/Google/R2 call — thousands per run).
_NOISY_LOGGERS = ("httpx", "httpcore", "anthropic", "urllib3", "boto3", "botocore",
                  "s3transfer", "google", "openai")


def quiet_noisy_loggers() -> None:
    """Raise chatty HTTP-client loggers to WARNING so /logs shows pipeline progress."""
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def enable_ring_capture() -> None:
    """Attach the ring-buffer handler to the root logger so module-level loggers
    (e.g. audio/enrich per-card progress) are captured too. Idempotent."""
    quiet_noisy_loggers()
    root = logging.getLogger()
    if _ring_handler not in root.handlers:
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        root.addHandler(_ring_handler)


def setup_logging(name: str) -> logging.Logger:
    """Create (or reuse) a logger that tees to stdout and a timestamped file.

    The log file path is attached as `logger.log_path`.
    """
    config.ensure_dirs()
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # If already configured (called twice with the same name), reuse as-is to
    # avoid duplicate handlers and a second log file.
    if getattr(logger, "log_path", None) is not None and logger.handlers:
        return logger

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = config.LOGS / f"{name}-{ts}.log"

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.addHandler(_ring_handler)  # also tee into the HTTP-queryable ring buffer

    logger.log_path = log_path  # type: ignore[attr-defined]
    return logger
