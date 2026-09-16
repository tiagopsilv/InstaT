"""Logging configuration helpers — public API for switching InstaT's
internal Loguru sink between local-dev (stderr + rotating file) and
production-pod (forward to stdlib logging so Cloud Logging / journald
ingest automatically).

Why this exists:
  Several modules in InstaT call `logger.add(...)` at import time,
  including a file sink at `instat/logs/insta_extractor.log`. Inside a
  Kubernetes pod (Cloud Composer's KubernetesPodOperator) that file
  is ephemeral and never seen — the right destination is stderr,
  which the platform captures.

Use:

    # Default (local dev): stderr + file. No call needed.
    extractor = InstaExtractor(...)

    # Pod / serverless: forward to stdlib logging
    from instat import configure_logging
    configure_logging(use_stdlib=True)
    extractor = InstaExtractor(...)

    # Or via the shortcut on the extractor:
    extractor = InstaExtractor(..., use_stdlib_logging=True)

Idempotent: calling configure_logging multiple times resets cleanly.
"""
import logging
import sys
from typing import Optional

from loguru import logger


_DEFAULT_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

_STDLIB_FORMAT = "{message}"


def _stdlib_sink(message) -> None:
    """Loguru sink that forwards to the stdlib logging module.

    Each loguru record is replayed on a stdlib logger named after the
    loguru record's `name` field — preserves module attribution so
    consumers can configure handlers per-namespace (e.g. silence
    `instat.engines.engine_manager` while keeping `instat.login`
    verbose).
    """
    record = message.record
    target = logging.getLogger(record["name"] or "instat")
    # Loguru levels expose `.no` (the numeric level), aligned with
    # stdlib's level numbering for the standard names.
    target.log(record["level"].no, record["message"])


def configure_logging(
    *,
    use_stdlib: bool = False,
    file_log: bool = True,
    level: str = "DEBUG",
    file_path: Optional[str] = None,
) -> None:
    """Reconfigure InstaT's logging.

    Args:
        use_stdlib: when True, route all loguru records to the stdlib
            `logging` module via a sink. Stderr / file sinks are NOT
            added in this mode — the consumer's stdlib handlers
            (e.g. Cloud Logging integration) own destination.
        file_log: when True (default) and use_stdlib=False, add a
            rotating file sink. Pass False to skip the disk sink
            inside ephemeral environments (containers).
        level: minimum level for stderr / stdlib sinks.
        file_path: override default file path
            ("instat/logs/insta_extractor.log").

    Idempotent — repeated calls remove prior handlers first.
    """
    logger.remove()

    if use_stdlib:
        logger.add(_stdlib_sink, level=level, format=_STDLIB_FORMAT)
        return

    logger.add(
        sys.stderr, level=level, colorize=True,
        backtrace=True, diagnose=False, format=_DEFAULT_FORMAT,
    )
    if file_log:
        logger.add(
            file_path or "instat/logs/insta_extractor.log",
            rotation="10 MB", retention="10 days",
            level=level, backtrace=True, diagnose=False,
        )


__all__ = ["configure_logging"]
