import glob
import os

import pytest

from kvittomall.atomic import atomic_write, no_validation


def test_atomic_write_success_creates_file_and_no_tmp_left(tmp_path):
    dst = tmp_path / "out.txt"
    atomic_write(str(dst), writer=lambda p: open(p, "w").write("hello"), validate=no_validation)

    assert dst.read_text() == "hello"
    assert glob.glob(str(tmp_path / "*.tmp.*")) == []


def test_atomic_write_writer_failure_leaves_dst_untouched_and_cleans_tmp(tmp_path):
    dst = tmp_path / "out.txt"
    dst.write_text("original")

    def bad_writer(p):
        open(p, "w").write("partial")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        atomic_write(str(dst), writer=bad_writer, validate=no_validation)

    assert dst.read_text() == "original"
    assert glob.glob(str(tmp_path / "*.tmp.*")) == []


def test_atomic_write_validation_failure_leaves_dst_untouched(tmp_path):
    dst = tmp_path / "out.txt"

    def failing_validate(p):
        raise ValueError("invalid content")

    with pytest.raises(ValueError):
        atomic_write(str(dst), writer=lambda p: open(p, "w").write("x"), validate=failing_validate)

    assert not dst.exists()
    assert glob.glob(str(tmp_path / "*.tmp.*")) == []


def test_atomic_write_overwrites_existing_dst(tmp_path):
    dst = tmp_path / "out.txt"
    dst.write_text("old")
    atomic_write(str(dst), writer=lambda p: open(p, "w").write("new"), validate=no_validation)
    assert dst.read_text() == "new"


def test_atomic_write_tmp_path_lives_next_to_dst(tmp_path):
    dst = tmp_path / "out.txt"
    seen_tmp_dir = {}

    def writer(p):
        seen_tmp_dir["dir"] = os.path.dirname(p)
        open(p, "w").write("x")

    atomic_write(str(dst), writer=writer, validate=no_validation)
    assert seen_tmp_dir["dir"] == str(tmp_path)
