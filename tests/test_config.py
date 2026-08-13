from kvittomall import config


def test_env_helper_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_TEST_VAR_NOT_SET", raising=False)
    assert config._env("SOME_TEST_VAR_NOT_SET", "fallback") == "fallback"


def test_env_helper_uses_override_when_set(monkeypatch):
    monkeypatch.setenv("SOME_TEST_VAR", "overridden")
    assert config._env("SOME_TEST_VAR", "fallback") == "overridden"


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
