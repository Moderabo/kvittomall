import os

from kvittomall import paths


def test_clean_all_removes_data_and_final_but_recreates_empty_layout(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    downloads_dir = data_dir / "downloads"
    processed_dir = data_dir / "processed"
    final_dir = tmp_path / "final"
    logs_dir = tmp_path / "logs"

    monkeypatch.setattr(paths, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(paths, "DOWNLOADS_DIR", str(downloads_dir))
    monkeypatch.setattr(paths, "PROCESSED_DIR", str(processed_dir))
    monkeypatch.setattr(paths, "FINAL_DIR", str(final_dir))
    monkeypatch.setattr(paths, "LOGS_DIR", str(logs_dir))

    paths.ensure_dirs()
    (downloads_dir / "receipt.jpg").write_bytes(b"x")
    (processed_dir / "receipt.jpg").write_bytes(b"x")
    (data_dir / "kvittomall_state.db").write_bytes(b"sqlite")
    (data_dir / "responses.csv").write_text("a,b\n")
    (final_dir / "privat").mkdir(parents=True)
    (final_dir / "privat" / "out.pdf").write_bytes(b"pdf")
    (logs_dir / "download-2026-01.log").write_text("log line\n")

    paths.clean_all()

    # data/ and final/ are back to an empty layout ...
    assert os.path.isdir(data_dir)
    assert os.path.isdir(downloads_dir) and not os.listdir(downloads_dir)
    assert os.path.isdir(processed_dir) and not os.listdir(processed_dir)
    assert not (data_dir / "kvittomall_state.db").exists()
    assert not (data_dir / "responses.csv").exists()
    assert os.path.isdir(final_dir) and not os.listdir(final_dir)
    # ... but logs/ - not in scope - is left completely alone.
    assert (logs_dir / "download-2026-01.log").exists()


def test_clean_all_is_safe_when_nothing_exists_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "DOWNLOADS_DIR", str(tmp_path / "data" / "downloads"))
    monkeypatch.setattr(paths, "PROCESSED_DIR", str(tmp_path / "data" / "processed"))
    monkeypatch.setattr(paths, "FINAL_DIR", str(tmp_path / "final"))
    monkeypatch.setattr(paths, "LOGS_DIR", str(tmp_path / "logs"))

    paths.clean_all()  # nothing exists yet - must not raise

    assert os.path.isdir(tmp_path / "data")
    assert os.path.isdir(tmp_path / "final")
