import logging

from counterfactual_podcast import config
from counterfactual_podcast.logging_setup import setup_logging


def test_setup_logging_writes_timestamped_file():
    logger = setup_logging("testrun")
    try:
        log_path = logger.log_path
        assert log_path.parent == config.LOGS
        assert log_path.name.startswith("testrun-")
        assert log_path.name.endswith(".log")

        msg = "hello-counterfactual-log-marker"
        logger.info(msg)
        for h in logger.handlers:
            h.flush()

        assert log_path.exists()
        contents = log_path.read_text(encoding="utf-8")
        assert msg in contents
        # Format includes level and logger name.
        assert "INFO" in contents
        assert "testrun" in contents
    finally:
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
        if getattr(logger, "log_path", None) is not None and logger.log_path.exists():
            logger.log_path.unlink()
        # Reset so re-running in the same process starts clean.
        if hasattr(logger, "log_path"):
            delattr(logger, "log_path")


def test_setup_logging_does_not_duplicate_handlers():
    name = "testrun_dup"
    logger1 = setup_logging(name)
    n_after_first = len(logger1.handlers)
    logger2 = setup_logging(name)
    n_after_second = len(logger2.handlers)
    try:
        assert logger1 is logger2
        # Exactly one console + one file handler, and no growth on re-call.
        assert n_after_first == 2
        assert n_after_second == n_after_first

        console_handlers = [
            h
            for h in logger2.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(console_handlers) == 1
    finally:
        for h in list(logger2.handlers):
            h.close()
            logger2.removeHandler(h)
        if getattr(logger2, "log_path", None) is not None and logger2.log_path.exists():
            logger2.log_path.unlink()
        if hasattr(logger2, "log_path"):
            delattr(logger2, "log_path")
