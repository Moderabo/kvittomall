import fcntl

import pytest

from kvittomall import lock


def test_run_lock_acquires_and_releases(monkeypatch, tmp_path):
    lock_path = tmp_path / "kvittomall.lock"
    monkeypatch.setattr(lock, "LOCK_PATH", str(lock_path))

    with lock.run_lock():
        assert lock_path.exists()

    # Released afterward - a fresh acquire from this same process must succeed too.
    with lock.run_lock():
        pass


def test_run_lock_raises_when_already_held(monkeypatch, tmp_path):
    lock_path = tmp_path / "kvittomall.lock"
    monkeypatch.setattr(lock, "LOCK_PATH", str(lock_path))

    # flock is per open-file-description, so a second independent open()+flock() on
    # the same path - even from this same process - genuinely contends for the lock,
    # which is exactly what simulates "another kvittomall command is already running".
    held_fh = open(lock_path, "w")
    fcntl.flock(held_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(lock.AlreadyRunningError):
            with lock.run_lock():
                pass
    finally:
        fcntl.flock(held_fh, fcntl.LOCK_UN)
        held_fh.close()
