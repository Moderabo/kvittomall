from kvittomall import config, pdf_layout


def test_default_sections_data_matches_config_pdf_sections():
    data = pdf_layout.default_sections_data()
    default_sections = config.default_pdf_sections()
    assert len(data) == len(default_sections)
    for section_data, section in zip(data, default_sections):
        assert len(section_data["fields"]) == len(section.fields)
        for field_data, field in zip(section_data["fields"], section.fields):
            assert field_data["label"] == field.label
            assert field_data["column"] == field.column
            assert pdf_layout.FORMATTERS[field_data["formatter"]] is field.formatter


def test_load_sections_data_falls_back_to_default_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    assert pdf_layout.load_sections_data() == pdf_layout.default_sections_data()


def test_load_sections_data_falls_back_to_default_when_file_is_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "pdf_layout.json"
    path.write_text("not valid json{{{")
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(path))
    assert pdf_layout.load_sections_data() == pdf_layout.default_sections_data()


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    custom = [{"fields": [{"label": "Belopp:", "column": "Summa", "formatter": "currency"}]}]

    pdf_layout.save_sections_data(custom)

    assert pdf_layout.load_sections_data() == custom


def test_get_pdf_sections_rebuilds_real_field_and_section_objects(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    pdf_layout.save_sections_data([
        {"fields": [{"label": "Belopp:", "column": "Summa", "formatter": "currency"}]},
    ])

    sections = pdf_layout.get_pdf_sections()

    assert len(sections) == 1
    field = sections[0].fields[0]
    assert isinstance(field, config.Field)
    assert field.label == "Belopp:"
    assert field.column == "Summa"
    assert field.formatter is config.currency
    assert field.formatter("10.50") == "10,50 kr"


def test_get_pdf_sections_defaults_unknown_formatter_to_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    pdf_layout.save_sections_data([
        {"fields": [{"label": "X:", "column": "Y", "formatter": "not-a-real-formatter"}]},
    ])

    field = pdf_layout.get_pdf_sections()[0].fields[0]
    assert field.formatter is str


def test_known_columns_includes_structural_and_mapped_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    pdf_layout.save_sections_data([{"fields": [{"label": "Belopp:", "column": "Summa", "formatter": "plain"}]}])

    known = pdf_layout.known_columns()

    assert "Summa" in known
    assert pdf_layout.structural_columns() <= known


def test_unmapped_columns_with_data_flags_genuinely_unmapped_column(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    pdf_layout.save_sections_data([{"fields": [{"label": "Belopp:", "column": "Summa", "formatter": "plain"}]}])
    row = {"Summa": "100", "Godkännande": "Ja"}

    assert pdf_layout.unmapped_columns_with_data(row) == ["Godkännande"]


def test_unmapped_columns_with_data_ignores_mapped_but_empty_column(tmp_path, monkeypatch):
    # The MIL_COLUMN scenario: mapped, just empty on a non-mileage expense - not a
    # mapping problem, so it must never be flagged.
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    pdf_layout.save_sections_data([{"fields": [{"label": "Mil:", "column": "Körda mil", "formatter": "mil"}]}])
    row = {"Körda mil": "", "Summa": "100"}

    assert pdf_layout.unmapped_columns_with_data(row) == ["Summa"]


def test_unmapped_columns_with_data_never_flags_structural_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    pdf_layout.save_sections_data([])  # nothing mapped at all
    row = {col: "some value" for col in pdf_layout.structural_columns()}

    assert pdf_layout.unmapped_columns_with_data(row) == []


def test_migrate_columns_is_a_noop_when_no_layout_saved_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    changed = pdf_layout.migrate_columns({"Datum för händelsen": "Datum för transaktionen"})
    assert changed is False
    assert not (tmp_path / "pdf_layout.json").exists()


def test_migrate_columns_rewrites_matching_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    pdf_layout.save_sections_data([
        {"fields": [{"label": "Datum:", "column": "Datum för händelsen", "formatter": "plain"}]},
    ])

    changed = pdf_layout.migrate_columns({"Datum för händelsen": "Datum för transaktionen"})

    assert changed is True
    assert pdf_layout.load_sections_data()[0]["fields"][0]["column"] == "Datum för transaktionen"


def test_migrate_columns_leaves_freeform_fields_untouched(tmp_path, monkeypatch):
    # A field the user deliberately pointed at some column unrelated to any
    # SHEET_COLUMN_* setting was never "tracking" that setting - a mapping change
    # elsewhere must never accidentally repoint it.
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(tmp_path / "pdf_layout.json"))
    pdf_layout.save_sections_data([
        {"fields": [{"label": "Övrigt:", "column": "Godkännande", "formatter": "plain"}]},
    ])

    changed = pdf_layout.migrate_columns({"Datum för händelsen": "Datum för transaktionen"})

    assert changed is False
    assert pdf_layout.load_sections_data()[0]["fields"][0]["column"] == "Godkännande"


def test_load_sections_data_migrates_a_file_from_the_legacy_repo_root_location(tmp_path, monkeypatch):
    # Older versions stored pdf_layout.json directly at the repo root - moving it under
    # paths.CONFIG_DIR (see paths.py) must not silently forget anyone's existing layout.
    legacy_path = tmp_path / "legacy" / "pdf_layout.json"
    legacy_path.parent.mkdir()
    legacy_path.write_text('{"sections": [{"fields": [{"label": "X:", "column": "Y", "formatter": "plain"}]}]}')
    new_path = tmp_path / "config" / "pdf_layout.json"
    monkeypatch.setattr(pdf_layout, "_LEGACY_PDF_LAYOUT_PATH", str(legacy_path))
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(new_path))

    data = pdf_layout.load_sections_data()

    assert data[0]["fields"][0]["column"] == "Y"
    assert new_path.exists()
    assert not legacy_path.exists()


def test_migrate_legacy_file_never_overwrites_an_existing_new_file(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy" / "pdf_layout.json"
    legacy_path.parent.mkdir()
    legacy_path.write_text('{"sections": [{"fields": [{"label": "Old:", "column": "Old", "formatter": "plain"}]}]}')
    new_path = tmp_path / "config" / "pdf_layout.json"
    new_path.parent.mkdir()
    new_path.write_text('{"sections": [{"fields": [{"label": "New:", "column": "New", "formatter": "plain"}]}]}')
    monkeypatch.setattr(pdf_layout, "_LEGACY_PDF_LAYOUT_PATH", str(legacy_path))
    monkeypatch.setattr(pdf_layout, "PDF_LAYOUT_PATH", str(new_path))

    data = pdf_layout.load_sections_data()

    assert data[0]["fields"][0]["column"] == "New"
    assert legacy_path.exists()  # untouched, not deleted just because it wasn't used
