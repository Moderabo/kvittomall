"""Prevents two mutating pipeline runs from stepping on each other."""

import contextlib
import fcntl

from kvittomall.paths import LOCK_PATH


class AlreadyRunningError(Exception):
    pass


@contextlib.contextmanager
def run_lock():
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise AlreadyRunningError(
            "Another kvittomall command is already running (lock file: " + LOCK_PATH + ")"
        )
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
