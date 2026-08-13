import logging

from kvittomall import sheet


def test_rows_from_values_pads_missing_trailing_cells():
    values = [
        ["Tidstämpel", "Namn", "Summa"],
        ["2026-01-01 10.00.00", "Anna"],  # Sheets API drops the trailing empty "Summa" cell
    ]
    rows = sheet._rows_from_values(values)
    assert rows == [{"Tidstämpel": "2026-01-01 10.00.00", "Namn": "Anna", "Summa": ""}]


def test_rows_from_values_empty_input():
    assert sheet._rows_from_values([]) == []


def test_validate_chronological_warns_on_unparseable_timestamp(caplog):
    rows = [{"Tidstämpel": "not-a-date"}]
    with caplog.at_level(logging.WARNING, logger=sheet.logger.name):
        sheet._validate_chronological(rows)
    assert "cannot parse" in caplog.text


def test_validate_chronological_warns_on_out_of_order_rows(caplog):
    rows = [
        {"Tidstämpel": "2026-01-02 10.00.00"},
        {"Tidstämpel": "2026-01-01 10.00.00"},
    ]
    with caplog.at_level(logging.WARNING, logger=sheet.logger.name):
        sheet._validate_chronological(rows)
    assert "out of order" in caplog.text


def test_validate_chronological_no_warning_when_in_order(caplog):
    rows = [
        {"Tidstämpel": "2026-01-01 10.00.00"},
        {"Tidstämpel": "2026-01-02 10.00.00"},
    ]
    with caplog.at_level(logging.WARNING, logger=sheet.logger.name):
        sheet._validate_chronological(rows)
    assert caplog.text == ""


def test_read_rows_returns_empty_list_when_file_missing(tmp_path):
    assert sheet.read_rows(str(tmp_path / "does-not-exist.csv")) == []


def test_fetch_and_save_writes_csv_via_public_path(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_MODE", "public")
    fake_rows = [{"Tidstämpel": "2026-01-01 10.00.00", "Namn": "Anna"}]
    monkeypatch.setattr(sheet, "_fetch_public", lambda sheet_id, sheet_gid: fake_rows)

    csv_path = tmp_path / "responses.csv"
    count = sheet.fetch_and_save("sheet-id", "sheet-gid", csv_path=str(csv_path))

    assert count == 1
    assert sheet.read_rows(str(csv_path)) == fake_rows


def test_fetch_and_save_falls_back_to_public_when_api_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_MODE", "auto")

    def failing_api(sheet_id, sheet_gid):
        raise sheet.SheetFetchError("api broken")

    fake_rows = [{"Tidstämpel": "2026-01-01 10.00.00", "Namn": "Anna"}]
    monkeypatch.setattr(sheet, "_fetch_api", failing_api)
    monkeypatch.setattr(sheet, "_fetch_public", lambda sheet_id, sheet_gid: fake_rows)

    csv_path = tmp_path / "responses.csv"
    count = sheet.fetch_and_save("sheet-id", "sheet-gid", csv_path=str(csv_path))

    assert count == 1
    assert sheet.read_rows(str(csv_path)) == fake_rows
