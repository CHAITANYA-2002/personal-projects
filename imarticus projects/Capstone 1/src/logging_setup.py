"""Logging configuration shared by every script."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from config import settings

_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-22s  %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def configure(name: str, level: int = logging.INFO) -> logging.Logger:
    """Set up console and file logging. Returns the logger for the caller."""
    settings.ensure_dirs()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_path = settings.LOGS_DIR / f"{name}_{stamp}.log"

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    root.addHandler(console)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    root.addHandler(file_handler)

    # SQLAlchemy is chatty at INFO and drowns everything else out.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    return logging.getLogger(name)
