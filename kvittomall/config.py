"""Everything about *what* goes in the sheet and *how* the PDF is laid out lives here,
so the pipeline code itself never has to special-case a column name or field.
"""

from dataclasses import dataclass, field
from typing import Callable

# --- CSV column names, exactly as they appear in the Google Sheet ---
TIMESTAMP_COLUMN = "Tidstämpel"
NAME_COLUMN = "Namn"
RECEIPT_LINKS_COLUMN = "Ladda upp kvittot"
TRANSACTION_TYPE_COLUMN = "Transaktionstyp"
SUM_COLUMN = "Summa"

# --- PDF text content ---
PDF_TITLE_PREFIX = "Kvittomall - "
PDF_ATTACHMENT_PAGE_TITLE = f"{PDF_TITLE_PREFIX}Bild på kvittot"
LOGO = "logo.png"


def currency(value: str) -> str:
    return f"{value} kr"


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
        Field("Datum:", "Datum på kvittot"),
        Field("Namn:", NAME_COLUMN),
        Field("Summa:", SUM_COLUMN, formatter=currency),
        Field("Kontonummer:", "Kontonummer"),
    ]),
    Section([
        Field("Utskott:", "Utskott"),
        Field("Arrangemang:", "Arrangemang"),
        Field("Specificering:", "Specificering"),
    ]),
    Section([
        Field("Övrigt:", "Övrigt"),
        Field("Extra info:", "Extra info"),
    ]),
]

# --- Final-folder categorization, based on Transaktionstyp ---
# Keys must match the sheet's "Transaktionstyp" values exactly; values are the
# (lowercase) folder names used under final/.
CATEGORY_MAP = {
    "Privat utlägg": "privat",
    "Sektionskort": "sektionskort",
}
DEFAULT_CATEGORY = "övrigt"


def category_for(row: dict) -> str:
    value = (row.get(TRANSACTION_TYPE_COLUMN) or "").strip()
    return CATEGORY_MAP.get(value, DEFAULT_CATEGORY)


# final/<category>/ always holds only the most recently generated/updated batch, so
# it's obvious at a glance what's new. Anything superseded by a newer batch is archived
# under final/<PREVIOUS_DIRNAME>/<category>/ instead of being deleted.
PREVIOUS_DIRNAME = "previous"
