from kvittomall import db, status
from kvittomall.rowkey import content_hash


def _row(timestamp: str, name: str, transaction_type: str = "Privat utlägg") -> dict:
    return {
        "Tidstämpel": timestamp,
        "Namn": name,
        "Transaktionstyp": transaction_type,
        "Ladda upp bild": "",
    }


def test_row_status_not_synced_when_row_has_no_db_entry(conn):
    info = status.row_status(conn, _row("2026-01-01 10.00.00", "Anna Andersson"))
    assert info["row_key"] == "2026-01-01 10.00.00"
    assert info["kind"] == "not_synced"
    assert info["attachments"] == []


def test_row_status_missing_timestamp_has_no_row_key(conn):
    info = status.row_status(conn, _row("", ""))
    assert info["row_key"] is None
    assert info["kind"] == "not_synced"


def test_row_status_valid_when_generated_and_unchanged(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(status, "FINAL_DIR", str(tmp_path))
    key = "2026-01-01 10.00.00"
    row = _row(key, "Anna Andersson")
    (tmp_path / "privat").mkdir()
    pdf_path = tmp_path / "privat" / "receipt.pdf"
    pdf_path.write_bytes(b"pdf-bytes")

    db.upsert_row(conn, key, "receipt", "Privat utlägg", content_hash(row), "l")
    db.mark_generated(conn, key, "privat/receipt.pdf", pdf_path.stat().st_size, content_hash(row))

    info = status.row_status(conn, row)
    assert info["kind"] == "valid"
    assert info["state"] == "generated"
    assert info["location"] == "new"


def test_row_status_attachments_reflect_expected_links_even_before_download(conn):
    key = "2026-01-01 10.00.00"
    row = _row(key, "Anna Andersson")
    row["Ladda upp bild"] = "https://drive.google.com/open?id=a, https://drive.google.com/open?id=b"
    db.upsert_row(conn, key, "receipt", "Privat utlägg", content_hash(row), "l")
    # Nothing downloaded/processed yet - the attachments table has no rows for this key
    # at all, but the entry still has two links and must show as "not downloaded" for
    # both, not as if it had no attachments.

    info = status.row_status(conn, row)

    assert info["attachments"] == [
        {"link_index": 0, "downloaded_ok": False, "processed_ok": False},
        {"link_index": 1, "downloaded_ok": False, "processed_ok": False},
    ]


def test_row_status_state_is_pdf_removed_when_file_missing(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(status, "FINAL_DIR", str(tmp_path))
    key = "2026-01-01 10.00.00"
    row = _row(key, "Anna Andersson")
    db.upsert_row(conn, key, "receipt", "Privat utlägg", content_hash(row), "l")
    db.mark_generated(conn, key, "privat/receipt.pdf", 9, content_hash(row))  # no file actually written

    info = status.row_status(conn, row)

    assert info["kind"] == "repair"
    assert info["state"] == "PDF removed"
    assert info["final_pdf_exists"] is False


def test_row_status_state_keeps_detail_when_file_corrupt_not_missing(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(status, "FINAL_DIR", str(tmp_path))
    key = "2026-01-01 10.00.00"
    row = _row(key, "Anna Andersson")
    (tmp_path / "privat").mkdir()
    pdf_path = tmp_path / "privat" / "receipt.pdf"
    pdf_path.write_bytes(b"")  # exists, but zero bytes - a different problem than "removed"

    db.upsert_row(conn, key, "receipt", "Privat utlägg", content_hash(row), "l")
    db.mark_generated(conn, key, "privat/receipt.pdf", 10, content_hash(row))

    info = status.row_status(conn, row)

    assert info["kind"] == "repair"
    assert "PDF removed" not in info["state"]
    assert "zero bytes" in info["state"]
    assert info["final_pdf_exists"] is True


def test_row_status_pending_when_never_generated(conn):
    key = "2026-01-01 10.00.00"
    row = _row(key, "Anna Andersson")
    db.upsert_row(conn, key, "receipt", "Privat utlägg", content_hash(row), "l")

    info = status.row_status(conn, row)
    assert info["kind"] == "pending"
    assert info["row_key"] == key


def test_row_status_handled_when_marked_and_unchanged(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(status, "FINAL_DIR", str(tmp_path))
    key = "2026-01-01 10.00.00"
    row = _row(key, "Anna Andersson")
    (tmp_path / "handled" / "privat").mkdir(parents=True)
    pdf_path = tmp_path / "handled" / "privat" / "receipt.pdf"
    pdf_path.write_bytes(b"pdf-bytes")

    db.upsert_row(conn, key, "receipt", "Privat utlägg", content_hash(row), "l")
    db.mark_generated(conn, key, "privat/receipt.pdf", pdf_path.stat().st_size, content_hash(row))
    db.set_handled(conn, key, True, "handled/privat/receipt.pdf")

    info = status.row_status(conn, row)
    assert info["kind"] == "handled"
    assert info["state"] == "handled"
    assert info["location"] == "handled"
