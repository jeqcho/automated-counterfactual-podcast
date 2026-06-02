"""Logging setup: console + timestamped file logger.

`setup_logging(name)` returns a `logging.Logger` that writes to both stdout and a
timestamped file `config.LOGS / f"{name}-YYYYMMDD-HHMMSS.log"`. The created log
file path is exposed as the `logger.log_path` attribute (a `pathlib.Path`) so
callers/tests can find the file. Calling it twice with the same name reuses the
existing handlers (no duplicates) and keeps the original `log_path`.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from . import config

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


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

    logger.log_path = log_path  # type: ignore[attr-defined]
    return logger
