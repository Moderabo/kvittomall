from kvittomall import db, rowkey


def test_sanitize_filename_replaces_unsafe_chars():
    assert rowkey.sanitize_filename("a/b:c?d") == "a_b_c_d"


def test_sanitize_filename_keeps_allowed_chars():
    assert rowkey.sanitize_filename("Anna-Andersson_2.0") == "Anna-Andersson_2.0"


def test_sanitize_filename_strips_surrounding_whitespace():
    assert rowkey.sanitize_filename("  spaced  ") == "spaced"


def test_row_key_returns_stripped_timestamp(sample_row):
    assert rowkey.row_key(sample_row) == "2026-03-05 12.30.00"


def test_row_key_none_when_blank():
    assert rowkey.row_key({"Tidstämpel": "  "}) is None
    assert rowkey.row_key({}) is None


def test_base_filename_builds_expected_stem(sample_row):
    assert rowkey.base_filename(sample_row) == "2026-03-05_12.30.00_Anna-Andersson"


def test_base_filename_none_when_missing_timestamp(sample_row):
    sample_row["Tidstämpel"] = ""
    assert rowkey.base_filename(sample_row) is None


def test_base_filename_none_when_missing_name(sample_row):
    sample_row["Namn"] = ""
    assert rowkey.base_filename(sample_row) is None


def test_content_hash_is_deterministic(sample_row):
    assert rowkey.content_hash(sample_row) == rowkey.content_hash(dict(sample_row))


def test_content_hash_ignores_key_order(sample_row):
    reordered = dict(reversed(list(sample_row.items())))
    assert rowkey.content_hash(sample_row) == rowkey.content_hash(reordered)


def test_content_hash_changes_when_a_value_changes(sample_row):
    before = rowkey.content_hash(sample_row)
    sample_row["Summa"] = "999.00"
    assert rowkey.content_hash(sample_row) != before


def test_links_hash_changes_only_with_links(sample_row):
    before = rowkey.links_hash(sample_row)
    sample_row["Övrigt"] = "changed, but not the links"
    assert rowkey.links_hash(sample_row) == before

    sample_row["Ladda upp bild"] = "https://drive.google.com/open?id=different"
    assert rowkey.links_hash(sample_row) != before


def test_sync_row_upserts_and_returns_key(monkeypatch, tmp_path, sample_row):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(rowkey, "DOWNLOADS_DIR", str(tmp_path / "downloads"))
    monkeypatch.setattr(rowkey, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(rowkey, "FINAL_DIR", str(tmp_path / "final"))

    with db.connect() as conn:
        key = rowkey.sync_row(conn, sample_row)
        assert key == "2026-03-05 12.30.00"
        db_row = db.get_row(conn, key)
        assert db_row["base_filename"] == "2026-03-05_12.30.00_Anna-Andersson"
        assert db_row["transaktionstyp"] == "Privat utlägg"


def test_sync_row_none_when_row_unidentifiable(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "state.db"))
    with db.connect() as conn:
        assert rowkey.sync_row(conn, {"Tidstämpel": "", "Namn": ""}) is None


def test_sync_row_renames_files_on_name_change(monkeypatch, tmp_path, sample_row):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(rowkey, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(rowkey, "PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(rowkey, "FINAL_DIR", str(tmp_path / "final"))

    with db.connect() as conn:
        key = rowkey.sync_row(conn, sample_row)
        old_base = db.get_row(conn, key)["base_filename"]
        (downloads / f"{old_base}_file0.jpg").write_bytes(b"fake-image")

        sample_row["Namn"] = "Anna Corrected-Name"
        rowkey.sync_row(conn, sample_row)
        new_base = db.get_row(conn, key)["base_filename"]

        assert not (downloads / f"{old_base}_file0.jpg").exists()
        assert (downloads / f"{new_base}_file0.jpg").exists()
