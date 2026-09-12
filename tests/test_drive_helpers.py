import requests

from kvittomall import db, drive
from kvittomall.drive import _get_confirm_token, get_drive_file_id


def test_get_drive_file_id_from_open_url():
    assert get_drive_file_id("https://drive.google.com/open?id=abc123XYZ") == "abc123XYZ"


def test_get_drive_file_id_from_share_url():
    url = "https://drive.google.com/file/d/abc123XYZ/view?usp=sharing"
    assert get_drive_file_id(url) == "abc123XYZ"


def test_get_drive_file_id_returns_none_for_unrecognized_url():
    assert get_drive_file_id("https://example.com/not-a-drive-link") is None


def test_get_confirm_token_from_cookie():
    response = requests.Response()
    response.cookies.set("download_warning_12345", "the-token")
    assert _get_confirm_token(response) == "the-token"


def test_get_confirm_token_from_html_body():
    response = requests.Response()
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response._content = b'<a href="?confirm=abc123-XYZ_9\">Download anyway</a>'
    response.encoding = "utf-8"
    assert _get_confirm_token(response) == "abc123-XYZ_9"


def test_get_confirm_token_none_when_neither_present():
    response = requests.Response()
    response.headers["Content-Type"] = "application/octet-stream"
    response._content = b""
    assert _get_confirm_token(response) is None


def test_download_all_only_filters_to_one_row(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(drive, "DOWNLOADS_DIR", str(tmp_path))
    rows = [
        {"Tidstämpel": "k1", "Namn": "A", "Ladda upp bild": "https://drive.google.com/open?id=fileA"},
        {"Tidstämpel": "k2", "Namn": "B", "Ladda upp bild": "https://drive.google.com/open?id=fileB"},
    ]
    monkeypatch.setattr(drive, "read_rows", lambda: rows)

    called_for = []
    monkeypatch.setattr(
        drive, "_download_one",
        lambda conn, row_key, j, file_id, base, svc: called_for.append(row_key),
    )

    drive._download_all(drive_service=None, only="k1")

    assert called_for == ["k1"]


def test_remove_downloads_deletes_files_and_leaves_db_untouched(conn, tmp_path):
    path = tmp_path / "row1_file0.jpg"
    path.write_bytes(b"fake-jpeg-bytes")
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")
    db.upsert_attachment(conn, "k1", 0, "fileid")
    db.mark_download_ok(conn, "k1", 0, str(path), path.stat().st_size)
    conn.commit()  # remove_downloads() opens its own connection - mustn't see an open write lock

    removed = drive.remove_downloads("k1")

    assert removed == 1
    assert not path.exists()
    att = db.get_attachment(conn, "k1", 0)
    assert att["download_status"] == "ok"  # DB is untouched - only the file is gone
    assert att["download_path"] == str(path)
    assert db.is_download_valid(att) == (False, f"recorded download is missing on disk: {path}")


def test_remove_downloads_is_a_no_op_when_nothing_to_remove(conn):
    assert drive.remove_downloads("unknown-row") == 0
