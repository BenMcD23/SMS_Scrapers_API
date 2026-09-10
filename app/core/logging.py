"""Process-wide logging setup.

Every module logs through ``logging.getLogger(__name__)``; this configures the
root logger once so those records reach stdout in one consistent shape. Output
is plain text (timestamp, level, logger, message) because the container
platform captures stdout and nothing downstream parses it as JSON yet — when
that changes, swap the formatter here and nothing else moves.

``LOG_LEVEL`` (default ``INFO``) controls verbosity. Uvicorn's own loggers are
left alone so its access log keeps its familiar format.
"""

import logging
import os

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATE_FORMAT, force=True)
    # Third-party chatter that drowns out our own records at INFO.
    for noisy in ("googleapiclient.discovery_cache", "httpx", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
