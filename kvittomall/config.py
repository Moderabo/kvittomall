"""Everything about *what* goes in the sheet and *how* the PDF is laid out lives here,
so the pipeline code itself never has to special-case a column name or field.
"""

import os
from dataclasses import dataclass, field
from typing import Callable

from dotenv import load_dotenv

# Every module that needs an env var (this one included) imports from config.py sooner
# or later, so loading .env here - once, at import time - guarantees it's in place no
# matter which single stage gets invoked, rather than depending on fetch having run
# first in the same process.
load_dotenv()


def _env(env_var: str, default: str) -> str:
    """A setting's default value, optionally overridden by an env var - used for both
    sheet column names (the Google Form's wording can differ between deployments) and
    plain settings like IMAGE_QUALITY below, so every one of them can be changed via
    .env without requiring a code change.
    """
    return os.getenv(env_var, default)


# --- CSV column names, exactly as they appear in the Google Sheet - each overridable
# via the matching SHEET_COLUMN_* variable in .env. ---
TIMESTAMP_COLUMN = _env("SHEET_COLUMN_TIMESTAMP", "Tidstämpel")
NAME_COLUMN = _env("SHEET_COLUMN_NAME", "Namn")
RECEIPT_LINKS_COLUMN = _env("SHEET_COLUMN_RECEIPT_LINKS", "Ladda upp bild")
TRANSACTION_TYPE_COLUMN = _env("SHEET_COLUMN_TRANSACTION_TYPE", "Transaktionstyp")
SUM_COLUMN = _env("SHEET_COLUMN_SUM", "Summa")
DATE_COLUMN = _env("SHEET_COLUMN_DATE", "Datum för händelsen")
MIL_COLUMN = _env("SHEET_COLUMN_MIL", "Körda mil")
ROUTE_COLUMN = _env("SHEET_COLUMN_ROUTE", "Sträcka")
ACCOUNT_COLUMN = _env("SHEET_COLUMN_ACCOUNT", "Kontonummer")
COMMITTEE_COLUMN = _env("SHEET_COLUMN_COMMITTEE", "Utskott")
EVENT_COLUMN = _env("SHEET_COLUMN_EVENT", "Arrangemang")
SPECIFICATION_COLUMN = _env("SHEET_COLUMN_SPECIFICATION", "Specificering")
OTHER_COLUMN = _env("SHEET_COLUMN_OTHER", "Övrigt")

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
# line within it. Blank values are omitted automatically. To customize the PDF's text
# page, edit this structure - nothing else in the codebase needs to change.
PDF_SECTIONS: list[Section] = [
    Section([
        Field("Datum:", DATE_COLUMN),
        Field("Namn:", NAME_COLUMN),
        Field("Summa:", SUM_COLUMN, formatter=currency),
        Field("Körda mil:", MIL_COLUMN, formatter=mil),
        Field("Kontonummer:", ACCOUNT_COLUMN),
    ]),
    Section([
        Field("Utskott:", COMMITTEE_COLUMN),
        Field("Arrangemang:", EVENT_COLUMN),
        Field("Specificering:", SPECIFICATION_COLUMN),
        Field("Sträcka:", ROUTE_COLUMN),
    ]),
    Section([
        Field("Övrigt:", OTHER_COLUMN),
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
    value = (row.get(TRANSACTION_TYPE_COLUMN) or "").strip()
    return CATEGORY_MAP.get(value, DEFAULT_CATEGORY)


# final/<category>/ holds every generated PDF not yet reviewed - it's cumulative, not
# a "latest batch" folder. An entry only leaves it when a human deliberately marks it
# handled (`kvittomall handled [row_key]`), moving it to final/<HANDLED_DIRNAME>/<category>/
# instead of being deleted. A later content change moves it back (see db.classify_generated).
HANDLED_DIRNAME = "handled"
