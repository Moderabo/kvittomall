"""Everything about *what* goes in the sheet and *how* the PDF is laid out lives here,
so the pipeline code itself never has to special-case a column name or field.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Callable

from dotenv import load_dotenv

from kvittomall.atomic import atomic_write, no_validation

# Every module that needs an env var (this one included) imports from config.py sooner
# or later, so loading .env here - once, at import time - guarantees it's in place no
# matter which single stage gets invoked, rather than depending on fetch having run
# first in the same process.
load_dotenv()

# Deliberately duplicated from paths.py's own ROOT computation, not imported from there:
# paths.py does `from kvittomall.config import LOGO` at module level, so config.py
# importing anything from paths.py - even lazily, inside a function called during
# config.py's own top-level execution below - would hit a partially-initialized module.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A user preference (like pdf_layout.json), not pipeline-generated state - lives at the
# repo root, outside paths.DATA_DIR/FINAL_DIR, so paths.clean_all() never touches it.
_COLUMN_MAPPING_PATH = os.path.join(_ROOT, "column_mapping.json")


def _env(env_var: str, default: str) -> str:
    """A setting's default value, optionally overridden by an env var - used for plain
    settings like IMAGE_QUALITY/WEBUI_HOST below, so they can be changed via .env
    without requiring a code change.
    """
    return os.getenv(env_var, default)


def _column_overrides() -> dict:
    """Falls back to no overrides both when nothing's been configured via the web UI's
    /column-mapping page yet, and when the file's been hand-edited into something
    unreadable - a bad edit should degrade to ".env/the compiled-in default," never
    crash every stage that reads a column constant.
    """
    if not os.path.exists(_COLUMN_MAPPING_PATH):
        return {}
    try:
        with open(_COLUMN_MAPPING_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, TypeError):
        return {}


def save_column_overrides(overrides: dict) -> None:
    def writer(tmp_path: str) -> None:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)

    atomic_write(_COLUMN_MAPPING_PATH, writer, no_validation)


def _column(env_var: str, default: str) -> str:
    """A sheet column name's current value, in order: the web UI's /column-mapping
    override (column_mapping.json), then the matching SHEET_COLUMN_* variable in .env,
    then the compiled-in default (the current Google Form's exact wording). Resolved
    fresh on every call - see _COLUMN_DEFAULTS/__getattr__ below for why that matters.
    """
    overrides = _column_overrides()
    return overrides.get(env_var) or _env(env_var, default)


# --- CSV column names, exactly as they appear in the Google Sheet - each overridable
# via the matching SHEET_COLUMN_* variable in .env, or via the web UI's /column-mapping
# page (which takes precedence over .env - see _column() above). Keyed by the constant
# name every consumer accesses (TIMESTAMP_COLUMN, NAME_COLUMN, ...): (env_var, label,
# compiled-in default, is_structural). "Structural" columns determine row identity,
# attachment discovery, or the category folder - not just what's displayed - see
# rowkey.py/drive.py/category_for(). This dict is also /column-mapping's source of
# truth for which settings exist at all (see column_settings() below).
_COLUMN_DEFAULTS: dict[str, tuple[str, str, str, bool]] = {
    "TIMESTAMP_COLUMN": ("SHEET_COLUMN_TIMESTAMP", "Timestamp", "Tidstämpel", True),
    "NAME_COLUMN": ("SHEET_COLUMN_NAME", "Name", "Namn", True),
    "RECEIPT_LINKS_COLUMN": ("SHEET_COLUMN_RECEIPT_LINKS", "Receipt links", "Ladda upp bild", True),
    "TRANSACTION_TYPE_COLUMN": ("SHEET_COLUMN_TRANSACTION_TYPE", "Transaction type", "Transaktionstyp", True),
    "SUM_COLUMN": ("SHEET_COLUMN_SUM", "Sum", "Summa", False),
    "DATE_COLUMN": ("SHEET_COLUMN_DATE", "Date", "Datum för händelsen", False),
    "MIL_COLUMN": ("SHEET_COLUMN_MIL", "Mileage", "Körda mil", False),
    "ROUTE_COLUMN": ("SHEET_COLUMN_ROUTE", "Route", "Sträcka", False),
    "ACCOUNT_COLUMN": ("SHEET_COLUMN_ACCOUNT", "Account", "Kontonummer", False),
    "COMMITTEE_COLUMN": ("SHEET_COLUMN_COMMITTEE", "Committee", "Utskott", False),
    "EVENT_COLUMN": ("SHEET_COLUMN_EVENT", "Event", "Arrangemang", False),
    "SPECIFICATION_COLUMN": ("SHEET_COLUMN_SPECIFICATION", "Specification", "Specificering", False),
    "OTHER_COLUMN": ("SHEET_COLUMN_OTHER", "Other", "Övrigt", False),
}


def _resolve(attr_name: str) -> str:
    env_var, _, default, _ = _COLUMN_DEFAULTS[attr_name]
    return _column(env_var, default)


def __getattr__(name: str):
    """PEP 562 module-level dynamic attributes: TIMESTAMP_COLUMN, NAME_COLUMN, etc. are
    resolved fresh on *every* access instead of once at import time, so a
    column_mapping.json change (saved via the web UI's /column-mapping page) takes
    effect on the very next `download`/`process`/`generate`/etc. - no restart of
    `kvittomall webui` needed, matching pdf_layout.py's existing live-reload.

    This only works if every consumer accesses these as `config.X_COLUMN` (attribute
    access, which this hook intercepts) rather than
    `from kvittomall.config import X_COLUMN` (which evaluates once at that import
    statement and caches the snapshot in the *importing* module's own namespace,
    silently defeating this entirely) - hence every consumer in this codebase does
    `from kvittomall import config` and writes `config.X_COLUMN` at the point of use.
    """
    if name in _COLUMN_DEFAULTS:
        return _resolve(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def column_settings() -> list[tuple[str, str, str, bool]]:
    """(env_var, label, current resolved value, is_structural) for every SHEET_COLUMN_*
    setting - what /column-mapping is built from. A function, not a cached list, for
    the same live-reload reason as __getattr__ above.
    """
    return [
        (env_var, label, _column(env_var, default), is_structural)
        for env_var, label, default, is_structural in _COLUMN_DEFAULTS.values()
    ]


def column_mapping_rows() -> list[dict]:
    """Everything /column-mapping needs per setting, beyond the plain (env_var, label,
    current, is_structural) shape column_settings() returns: whether the current value
    comes from an explicit override (`overridden`) and what it would resolve to without
    one (`default_value`, i.e. .env or the compiled-in default). This is what lets the
    web UI offer an explicit "use default" choice per field instead of forcing every
    setting to be pinned to one specific column - important for a column that's
    genuinely absent from a given deployment's sheet (e.g. MIL_COLUMN for a committee
    that never reimburses mileage), which has no sensible discovered-column choice.
    """
    overrides = _column_overrides()
    return [
        {
            "env_var": env_var,
            "label": label,
            "current": _column(env_var, default),
            "default_value": _env(env_var, default),
            "is_structural": is_structural,
            "overridden": bool(overrides.get(env_var)),
        }
        for env_var, label, default, is_structural in _COLUMN_DEFAULTS.values()
    ]

# --- Receipt image compression, used by media.py's process stage. One of "extreme",
# "high", "normal", "low", "potato" (see media.py's QUALITY_PRESETS for what each
# means) - overridable via IMAGE_QUALITY in .env. media.py validates the value, since
# it's the one that knows which names are actually valid.
IMAGE_QUALITY = _env("IMAGE_QUALITY", "normal")

# --- Local web UI bind address/port, overridable via .env. Defaults to every
# interface, not just localhost - the VM/server this typically runs on is rarely the
# machine you browse from, so binding to 127.0.0.1 alone would make it unreachable
# from anywhere else on the network.
WEBUI_HOST = _env("WEBUI_HOST", "0.0.0.0")
WEBUI_PORT = int(_env("WEBUI_PORT", "5000"))

# --- PDF text content ---
PDF_TITLE_PREFIX = "Kvittomall - "
LOGO = "logo.svg"


def _swedish_decimal(value: str) -> str:
    return value.replace(".", ",")


def currency(value: str) -> str:
    return f"{_swedish_decimal(value)} kr"


def mil(value: str) -> str:
    return f"{_swedish_decimal(value)} mil"


@dataclass(frozen=True)
class Field:
    label: str
    column: str
    formatter: Callable[[str], str] = str


@dataclass(frozen=True)
class Section:
    fields: list[Field] = field(default_factory=list)


# Each Section is one bordered block on the cover page; each Field is one label/value
# line within it. Blank values are omitted automatically. This is the compiled-in
# *default* layout only - pdf_layout.py's default_sections_data() calls this to seed a
# fresh install; pdf_layout.get_pdf_sections() is what pdf_gen.py actually renders (the
# web UI's /pdf-layout page, once used, takes over from this entirely). A function, not
# a cached list, so it always reflects the *current* column mapping (e.g. DATE_COLUMN)
# even before any /pdf-layout customization exists yet - see __getattr__ above for why
# these can't be plain module-level constants anymore.
def default_pdf_sections() -> list[Section]:
    return [
        Section([
            Field("Datum:", _resolve("DATE_COLUMN")),
            Field("Namn:", _resolve("NAME_COLUMN")),
            Field("Summa:", _resolve("SUM_COLUMN"), formatter=currency),
            Field("Körda mil:", _resolve("MIL_COLUMN"), formatter=mil),
            Field("Kontonummer:", _resolve("ACCOUNT_COLUMN")),
        ]),
        Section([
            Field("Utskott:", _resolve("COMMITTEE_COLUMN")),
            Field("Arrangemang:", _resolve("EVENT_COLUMN")),
            Field("Specificering:", _resolve("SPECIFICATION_COLUMN")),
            Field("Sträcka:", _resolve("ROUTE_COLUMN")),
        ]),
        Section([
            Field("Övrigt:", _resolve("OTHER_COLUMN")),
        ]),
    ]

# --- Final-folder categorization, based on Transaktionstyp ---
# Keys must match the sheet's Transaktionstyp *values* exactly (not its column header,
# which is TRANSACTION_TYPE_COLUMN above); values are the (lowercase) folder names used
# under final/.
CATEGORY_MAP = {
    "Privat utlägg": "privat",
    "Sektionskort": "sektionskort",
    "Milersättning": "milersättning",
}
DEFAULT_CATEGORY = "övrigt"


def category_for(row: dict) -> str:
    value = (row.get(_resolve("TRANSACTION_TYPE_COLUMN")) or "").strip()
    return CATEGORY_MAP.get(value, DEFAULT_CATEGORY)


# final/<category>/ holds every generated PDF not yet reviewed - it's cumulative, not
# a "latest batch" folder. An entry only leaves it when a human deliberately marks it
# handled (`kvittomall handled [row_key]`), moving it to final/<HANDLED_DIRNAME>/<category>/
# instead of being deleted. A later content change moves it back (see db.classify_generated).
HANDLED_DIRNAME = "handled"
