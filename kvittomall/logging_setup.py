"""Shared logging setup, used by every stage instead of hand-rolled per-script logging.

Every INFO-and-up message goes to a log file under logs/, named "{stage}-{year}-{month}.log"
so files stop growing forever and older activity naturally lands in its own file - no
manual rotation/cleanup needed. The console only ever shows WARNING and above, so a normal
run stays quiet; anything printed to the terminal is coordinated with progress.py so it
doesn't get mangled by the progress bar's live redraws.

Timestamps are always in Swedish local time (with daylight saving applied), regardless
of the host machine's own timezone - this machine runs in UTC, which made log times
confusing to read against.
"""

import contextlib
import logging
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from kvittomall.paths import LOGS_DIR

STOCKHOLM = ZoneInfo("Europe/Stockholm")

# PyPDF2 logs benign warnings (e.g. malformed-but-recoverable PDF structures) straight
# to the console via Python's root logger, bypassing our own error-only console policy.
# Silencing it here keeps "only errors on screen" true for third-party noise too.
logging.getLogger("PyPDF2").setLevel(logging.ERROR)


class _StockholmFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=STOCKHOLM)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S %Z")


class _ConsoleHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        from kvittomall.progress import active_bar

        bar = active_bar()
        if bar is not None:
            bar.clear_line()
        super().emit(record)
        if bar is not None:
            bar.redraw()


def setup_logging(name: str) -> logging.Logger:
    os.makedirs(LOGS_DIR, exist_ok=True)
    month = datetime.now(STOCKHOLM).strftime("%Y-%m")
    log_file = os.path.join(LOGS_DIR, f"{name}-{month}.log")

    logger = logging.getLogger(f"kvittomall.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(_StockholmFormatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(file_handler)

        console_handler = _ConsoleHandler(sys.stderr)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(_StockholmFormatter("[%(levelname)s] %(message)s"))
        logger.addHandler(console_handler)

    return logger


@contextlib.contextmanager
def run_timer(logger: logging.Logger, label: str):
    """Logs a clear start/end marker (file only) for a stage run, so it's easy to see
    in the log when each run began and how long it took - the actual clock time is
    whatever `asctime` prints on those two lines.
    """
    started = time.monotonic()
    logger.info(f"=== {label} started ===")
    try:
        yield
    finally:
        logger.info(f"=== {label} finished in {time.monotonic() - started:.1f}s ===")
