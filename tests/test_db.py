import sqlite3

from kvittomall import db

# conn fixture lives in conftest.py - shared with test_pdf_gen.py's orchestration tests.


def test_migrate_adds_handled_column_and_upgrades_old_status(tmp_path, monkeypatch):
    """A database created before the `handled` column existed - CREATE TABLE IF NOT
    EXISTS doesn't add it retroactively, so connect() must patch it in. A row still
    carrying the old status="handled" value (from before that was its own column) must
    also come out the other side as status="generated", handled=1.
    """
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute("""
        CREATE TABLE rows (
            row_key TEXT PRIMARY KEY, base_filename TEXT NOT NULL, transaktionstyp TEXT,
            content_hash TEXT NOT NULL, links_hash TEXT NOT NULL, final_pdf_path TEXT,
            final_pdf_size INTEGER, final_pdf_content_hash TEXT, generated_at TEXT,
            status TEXT NOT NULL DEFAULT 'new', last_error TEXT, updated_at TEXT NOT NULL
        )
    """)
    raw.execute(
        "INSERT INTO rows (row_key, base_filename, status, content_hash, links_hash, updated_at) "
        "VALUES ('k1', 'base1', 'handled', 'h', 'l', 'now')"
    )
    raw.commit()
    raw.close()

    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    with db.connect() as conn:
        row = db.get_row(conn, "k1")
        assert row["status"] == "generated"
        assert row["handled"] == 1


def test_upsert_row_insert_then_update(conn):
    db.upsert_row(conn, "k1", "base1", "Privat utlägg", "hash1", "links1")
    row = db.get_row(conn, "k1")
    assert row["base_filename"] == "base1"
    assert row["status"] == "new"

    db.upsert_row(conn, "k1", "base1-renamed", "Privat utlägg", "hash2", "links1")
    row = db.get_row(conn, "k1")
    assert row["base_filename"] == "base1-renamed"
    assert row["content_hash"] == "hash2"


def test_get_row_missing_returns_none(conn):
    assert db.get_row(conn, "nope") is None


def test_list_rows_ordered_by_key(conn):
    db.upsert_row(conn, "b", "base-b", "x", "h", "l")
    db.upsert_row(conn, "a", "base-a", "x", "h", "l")
    keys = [r["row_key"] for r in db.list_rows(conn)]
    assert keys == ["a", "b"]


def test_classify_generated_stale_when_never_generated(conn):
    kind, reason = db.classify_generated(None, "hash", "/tmp")
    assert kind == "stale"
    assert "not yet generated" in reason


def test_classify_generated_stale_when_content_changed(conn, tmp_path):
    db.upsert_row(conn, "k1", "base1", "Privat utlägg", "old-hash", "links1")
    db.mark_generated(conn, "k1", "privat/base1.pdf", 10, "old-hash")
    row = db.get_row(conn, "k1")
    kind, reason = db.classify_generated(row, "new-hash", str(tmp_path))
    assert kind == "stale"
    assert "changed" in reason


def test_classify_generated_repair_when_file_missing(conn, tmp_path):
    db.upsert_row(conn, "k1", "base1", "Privat utlägg", "hash", "links1")
    db.mark_generated(conn, "k1", "privat/base1.pdf", 10, "hash")
    row = db.get_row(conn, "k1")
    kind, reason = db.classify_generated(row, "hash", str(tmp_path))
    assert kind == "repair"
    assert "missing" in reason


def test_classify_generated_repair_when_zero_bytes(conn, tmp_path):
    (tmp_path / "privat").mkdir()
    pdf_path = tmp_path / "privat" / "base1.pdf"
    pdf_path.write_bytes(b"")

    db.upsert_row(conn, "k1", "base1", "Privat utlägg", "hash", "links1")
    db.mark_generated(conn, "k1", "privat/base1.pdf", 10, "hash")
    row = db.get_row(conn, "k1")
    kind, reason = db.classify_generated(row, "hash", str(tmp_path))
    assert kind == "repair"
    assert "zero bytes" in reason


def test_classify_generated_repair_when_size_mismatch(conn, tmp_path):
    (tmp_path / "privat").mkdir()
    pdf_path = tmp_path / "privat" / "base1.pdf"
    pdf_path.write_bytes(b"1234567890")  # 10 bytes

    db.upsert_row(conn, "k1", "base1", "Privat utlägg", "hash", "links1")
    db.mark_generated(conn, "k1", "privat/base1.pdf", 999, "hash")
    row = db.get_row(conn, "k1")
    kind, reason = db.classify_generated(row, "hash", str(tmp_path))
    assert kind == "repair"
    assert "size" in reason


def test_classify_generated_valid_when_matches(conn, tmp_path):
    (tmp_path / "privat").mkdir()
    pdf_path = tmp_path / "privat" / "base1.pdf"
    pdf_path.write_bytes(b"1234567890")

    db.upsert_row(conn, "k1", "base1", "Privat utlägg", "hash", "links1")
    db.mark_generated(conn, "k1", "privat/base1.pdf", 10, "hash")
    row = db.get_row(conn, "k1")
    kind, reason = db.classify_generated(row, "hash", str(tmp_path))
    assert (kind, reason) == ("valid", "ok")


def test_classify_generated_valid_when_handled_and_unchanged(conn, tmp_path):
    # status stays "generated" regardless of the separate `handled` column - being
    # handled must never make classify_generated think this needs regenerating.
    (tmp_path / "handled" / "privat").mkdir(parents=True)
    pdf_path = tmp_path / "handled" / "privat" / "base1.pdf"
    pdf_path.write_bytes(b"1234567890")

    db.upsert_row(conn, "k1", "base1", "Privat utlägg", "hash", "links1")
    # mirrors the real flow: generate first (records the content hash it was built
    # from), then set_handled relocates it without touching that hash.
    db.mark_generated(conn, "k1", "privat/base1.pdf", 10, "hash")
    db.set_handled(conn, "k1", True, "handled/privat/base1.pdf")
    row = db.get_row(conn, "k1")
    assert row["status"] == "generated"
    assert row["handled"] == 1
    kind, reason = db.classify_generated(row, "hash", str(tmp_path))
    assert (kind, reason) == ("valid", "ok")


def test_classify_generated_stale_when_handled_row_content_changed(conn, tmp_path):
    db.upsert_row(conn, "k1", "base1", "Privat utlägg", "old-hash", "links1")
    db.mark_generated(conn, "k1", "privat/base1.pdf", 10, "old-hash")
    db.set_handled(conn, "k1", True, "handled/privat/base1.pdf")
    row = db.get_row(conn, "k1")
    kind, reason = db.classify_generated(row, "new-hash", str(tmp_path))
    assert kind == "stale"
    assert "changed" in reason


def test_upsert_attachment_new_then_unchanged(conn):
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    db.mark_download_ok(conn, "k1", 0, "/some/path.jpg", 100)

    # Same drive_file_id again - should not reset the already-recorded download.
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    att = db.get_attachment(conn, "k1", 0)
    assert att["download_status"] == "ok"


def test_upsert_attachment_resets_state_when_link_changes(conn):
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    db.mark_download_ok(conn, "k1", 0, "/some/path.jpg", 100)

    db.upsert_attachment(conn, "k1", 0, "file-id-2")
    att = db.get_attachment(conn, "k1", 0)
    assert att["drive_file_id"] == "file-id-2"
    assert att["download_status"] == "pending"
    assert att["download_path"] is None


def test_mark_download_failed_records_error_and_keeps_pending_path(conn):
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    db.mark_download_failed(conn, "k1", 0, "network error")
    att = db.get_attachment(conn, "k1", 0)
    assert att["download_status"] == "failed"
    assert att["error_message"] == "network error"


def test_is_download_valid_not_yet_downloaded():
    assert db.is_download_valid(None) == (False, "not yet downloaded")


def test_is_download_valid_missing_on_disk(conn, tmp_path):
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    db.mark_download_ok(conn, "k1", 0, str(tmp_path / "gone.jpg"), 10)
    att = db.get_attachment(conn, "k1", 0)
    valid, reason = db.is_download_valid(att)
    assert valid is False
    assert "missing" in reason


def test_is_download_valid_size_mismatch(conn, tmp_path):
    path = tmp_path / "file.jpg"
    path.write_bytes(b"12345")
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    db.mark_download_ok(conn, "k1", 0, str(path), 999)
    att = db.get_attachment(conn, "k1", 0)
    valid, _ = db.is_download_valid(att)
    assert valid is False


def test_is_download_valid_true_when_matches(conn, tmp_path):
    path = tmp_path / "file.jpg"
    path.write_bytes(b"12345")
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    db.mark_download_ok(conn, "k1", 0, str(path), 5)
    att = db.get_attachment(conn, "k1", 0)
    assert db.is_download_valid(att) == (True, "ok")


def test_is_process_valid_true_when_matches(conn, tmp_path):
    path = tmp_path / "file.jpg"
    path.write_bytes(b"12345")
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    db.mark_process_ok(conn, "k1", 0, str(path), 5)
    att = db.get_attachment(conn, "k1", 0)
    assert db.is_process_valid(att) == (True, "ok")


def test_rename_base_filename_updates_row_and_attachment_paths(conn):
    db.upsert_row(conn, "k1", "old-base", "Privat utlägg", "hash", "links")
    db.mark_generated(conn, "k1", "privat/old-base.pdf", 10, "hash")
    db.upsert_attachment(conn, "k1", 0, "file-id-1")
    db.mark_download_ok(conn, "k1", 0, "/data/downloads/old-base_file0.jpg", 10)
    db.mark_process_ok(conn, "k1", 0, "/data/processed/old-base_file0.jpg", 10)

    db.rename_base_filename(conn, "k1", "old-base", "new-base")

    row = db.get_row(conn, "k1")
    assert row["base_filename"] == "new-base"
    assert row["final_pdf_path"] == "privat/new-base.pdf"

    att = db.get_attachment(conn, "k1", 0)
    assert att["download_path"] == "/data/downloads/new-base_file0.jpg"
    assert att["processed_path"] == "/data/processed/new-base_file0.jpg"
