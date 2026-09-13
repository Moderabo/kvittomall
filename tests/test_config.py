from kvittomall import config


def test_env_helper_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_TEST_VAR_NOT_SET", raising=False)
    assert config._env("SOME_TEST_VAR_NOT_SET", "fallback") == "fallback"


def test_env_helper_uses_override_when_set(monkeypatch):
    monkeypatch.setenv("SOME_TEST_VAR", "overridden")
    assert config._env("SOME_TEST_VAR", "fallback") == "overridden"


def test_column_uses_default_when_nothing_set(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(tmp_path / "column_mapping.json"))
    monkeypatch.delenv("SHEET_COLUMN_TEST", raising=False)
    assert config._column("SHEET_COLUMN_TEST", "fallback") == "fallback"


def test_column_prefers_env_over_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(tmp_path / "column_mapping.json"))
    monkeypatch.setenv("SHEET_COLUMN_TEST", "FromEnv")
    assert config._column("SHEET_COLUMN_TEST", "fallback") == "FromEnv"


def test_column_prefers_override_file_over_env_and_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(tmp_path / "column_mapping.json"))
    monkeypatch.setenv("SHEET_COLUMN_TEST", "FromEnv")
    config.save_column_overrides({"SHEET_COLUMN_TEST": "FromOverride"})

    assert config._column("SHEET_COLUMN_TEST", "fallback") == "FromOverride"


def test_column_overrides_falls_back_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(tmp_path / "column_mapping.json"))
    assert config._column_overrides() == {}


def test_column_overrides_falls_back_when_file_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "column_mapping.json"
    path.write_text("not valid json{{{")
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(path))
    assert config._column_overrides() == {}


def test_save_column_overrides_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(tmp_path / "column_mapping.json"))
    config.save_column_overrides({"SHEET_COLUMN_TIMESTAMP": "Tidsstämpel"})
    assert config._column_overrides() == {"SHEET_COLUMN_TIMESTAMP": "Tidsstämpel"}


def test_env_based_settings_are_unaffected_by_column_overrides(tmp_path, monkeypatch):
    # IMAGE_QUALITY/WEBUI_HOST etc. go through _env(), never _column() - an override
    # file full of SHEET_COLUMN_* entries must never leak into unrelated settings.
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(tmp_path / "column_mapping.json"))
    config.save_column_overrides({"IMAGE_QUALITY": "potato"})
    monkeypatch.delenv("IMAGE_QUALITY", raising=False)

    assert config._env("IMAGE_QUALITY", "normal") == "normal"


def test_column_settings_list_matches_resolved_constants():
    resolved = {env_var: value for env_var, _, value, _ in config.column_settings()}
    assert resolved["SHEET_COLUMN_TIMESTAMP"] == config.TIMESTAMP_COLUMN
    assert resolved["SHEET_COLUMN_SUM"] == config.SUM_COLUMN
    structural = {env_var for env_var, _, _, is_structural in config.column_settings() if is_structural}
    assert structural == {
        "SHEET_COLUMN_TIMESTAMP", "SHEET_COLUMN_NAME", "SHEET_COLUMN_RECEIPT_LINKS", "SHEET_COLUMN_TRANSACTION_TYPE",
    }


def test_column_mapping_rows_marks_overridden_and_exposes_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(tmp_path / "column_mapping.json"))
    monkeypatch.delenv("SHEET_COLUMN_SUM", raising=False)
    config.save_column_overrides({"SHEET_COLUMN_SUM": "Belopp"})

    rows = {row["env_var"]: row for row in config.column_mapping_rows()}

    sum_row = rows["SHEET_COLUMN_SUM"]
    assert sum_row["overridden"] is True
    assert sum_row["current"] == "Belopp"
    assert sum_row["default_value"] == "Summa"

    timestamp_row = rows["SHEET_COLUMN_TIMESTAMP"]
    assert timestamp_row["overridden"] is False
    assert timestamp_row["current"] == timestamp_row["default_value"] == "Tidstämpel"


def test_column_mapping_rows_treats_empty_override_as_not_overridden(tmp_path, monkeypatch):
    # save_column_mapping() (web/app.py) already drops empty values before writing the
    # file, but column_mapping_rows() must degrade the same way if one ever sneaks in
    # (e.g. a hand-edited column_mapping.json), rather than reporting a falsy override
    # as if it were a real one.
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(tmp_path / "column_mapping.json"))
    config.save_column_overrides({"SHEET_COLUMN_SUM": ""})

    rows = {row["env_var"]: row for row in config.column_mapping_rows()}

    assert rows["SHEET_COLUMN_SUM"]["overridden"] is False


def test_column_overrides_migrates_a_file_from_the_legacy_repo_root_location(tmp_path, monkeypatch):
    # Older versions stored column_mapping.json directly at _ROOT - moving it under a
    # "config" subdirectory (see the comment above _COLUMN_MAPPING_PATH) must not
    # silently forget an override someone already saved there.
    legacy_path = tmp_path / "legacy" / "column_mapping.json"
    legacy_path.parent.mkdir()
    legacy_path.write_text('{"SHEET_COLUMN_TIMESTAMP": "Tidsstämpel"}')
    new_path = tmp_path / "config" / "column_mapping.json"
    monkeypatch.setattr(config, "_LEGACY_COLUMN_MAPPING_PATH", str(legacy_path))
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(new_path))

    overrides = config._column_overrides()

    assert overrides == {"SHEET_COLUMN_TIMESTAMP": "Tidsstämpel"}
    assert new_path.exists()
    assert not legacy_path.exists()


def test_migrate_legacy_override_file_never_overwrites_an_existing_new_file(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy" / "column_mapping.json"
    legacy_path.parent.mkdir()
    legacy_path.write_text('{"SHEET_COLUMN_TIMESTAMP": "OldValue"}')
    new_path = tmp_path / "config" / "column_mapping.json"
    new_path.parent.mkdir()
    new_path.write_text('{"SHEET_COLUMN_TIMESTAMP": "NewValue"}')
    monkeypatch.setattr(config, "_LEGACY_COLUMN_MAPPING_PATH", str(legacy_path))
    monkeypatch.setattr(config, "_COLUMN_MAPPING_PATH", str(new_path))

    overrides = config._column_overrides()

    assert overrides == {"SHEET_COLUMN_TIMESTAMP": "NewValue"}
    assert legacy_path.exists()  # untouched, not deleted just because it wasn't used


def test_category_for_known_value():
    assert config.category_for({"Transaktionstyp": "Privat utlägg"}) == "privat"
    assert config.category_for({"Transaktionstyp": "Sektionskort"}) == "sektionskort"
    assert config.category_for({"Transaktionstyp": "Milersättning"}) == "milersättning"


def test_category_for_unmapped_value_falls_back_to_default():
    assert config.category_for({"Transaktionstyp": "Något helt annat"}) == config.DEFAULT_CATEGORY


def test_category_for_blank_or_missing_value():
    assert config.category_for({"Transaktionstyp": ""}) == config.DEFAULT_CATEGORY
    assert config.category_for({}) == config.DEFAULT_CATEGORY


def test_currency_converts_decimal_point_to_comma():
    assert config.currency("123.45") == "123,45 kr"


def test_currency_leaves_comma_input_untouched():
    assert config.currency("123,45") == "123,45 kr"


def test_mil_converts_decimal_point_to_comma():
    assert config.mil("4.5") == "4,5 mil"


def test_field_default_formatter_is_identity():
    f = config.Field("Label:", "Column")
    assert f.formatter("raw value") == "raw value"
