"""The PDF cover page's field layout - which CSV column feeds each labeled field, and
in what order - editable from the web UI, unlike the rest of this pipeline's behavior.

config.py's default_pdf_sections() is still the compiled-in default; once a user customizes
anything via the web UI, the layout actually used to generate a PDF lives in a small
JSON file (paths.PDF_LAYOUT_PATH, under paths.CONFIG_DIR) instead, since a web form
can't hand back Python code.
Every value the web UI sets - label, source column, formatter - has an unambiguous
plain (string) representation, so nothing here needs eval()/exec() or similar.

TIMESTAMP_COLUMN, RECEIPT_LINKS_COLUMN, and TRANSACTION_TYPE_COLUMN are deliberately not
offered as a *mapping target* here (non_display_columns() below) - they determine row
identity, attachment discovery, and the category folder respectively, and have no
sensible reason to also appear as cover-page text, so letting a web form repoint them
risks corrupting existing state (see rowkey.py/drive.py/config.category_for()).
NAME_COLUMN is structural too (it drives rowkey.base_filename()) but, unlike those
three, is also a perfectly ordinary thing to display - the default layout's "Namn:"
field maps straight to it - so it's deliberately excluded from non_display_columns()
even though it's still in the broader structural_columns(), which is what makes it
always "known" for unmapped_columns_with_data() below whether or not it currently
appears as a labeled field.
"""

import json
import os
import shutil

from kvittomall import config
from kvittomall.atomic import atomic_write, no_validation
from kvittomall.config import Field, Section
from kvittomall.paths import PDF_LAYOUT_PATH, ROOT

FORMATTERS = {"plain": str, "currency": config.currency, "mil": config.mil}
_FORMATTER_KEYS = {value: key for key, value in FORMATTERS.items()}

# Older versions stored this directly at the repo root - PDF_LAYOUT_PATH moved into
# paths.CONFIG_DIR (see that module for why) once a container-runtime bind mount on the
# bare file itself turned out to break atomic_write()'s rename-into-place outright. A
# module attribute (not computed inline in _migrate_legacy_file() below) so tests can
# monkeypatch it the same way PDF_LAYOUT_PATH itself already is - see tests/conftest.py.
_LEGACY_PDF_LAYOUT_PATH = os.path.join(ROOT, "pdf_layout.json")


def _migrate_legacy_file() -> None:
    """One-time, silent migration for anyone who already has a layout saved at the old
    location: moves it into place the first time it's needed, never overwriting a file
    that's already at the new path. A no-op once migrated (or if nothing was ever saved
    at the old location to begin with).
    """
    if not os.path.exists(PDF_LAYOUT_PATH) and os.path.exists(_LEGACY_PDF_LAYOUT_PATH):
        os.makedirs(os.path.dirname(PDF_LAYOUT_PATH), exist_ok=True)
        shutil.move(_LEGACY_PDF_LAYOUT_PATH, PDF_LAYOUT_PATH)


def structural_columns() -> set[str]:
    """Always "used" even though a layout doesn't have to include all of them as a
    labeled cover-page field - NAME_COLUMN in particular almost always does (the
    default layout's "Namn:" field), but nothing requires it. A function, not a cached
    set, so a /column-mapping change (e.g. renaming which column is NAME_COLUMN) is
    reflected immediately - see config.py's __getattr__.
    """
    return {config.TIMESTAMP_COLUMN, config.NAME_COLUMN, config.RECEIPT_LINKS_COLUMN, config.TRANSACTION_TYPE_COLUMN}


def non_display_columns() -> set[str]:
    """The subset of structural_columns() that must never be a PDF *display* field's
    mapping target - see module docstring for why NAME_COLUMN specifically is excluded
    from this narrower set despite being structural too. Enforced by web/app.py's
    _validate_pdf_layout() and by /pdf-layout's available_columns.
    """
    return {config.TIMESTAMP_COLUMN, config.RECEIPT_LINKS_COLUMN, config.TRANSACTION_TYPE_COLUMN}


def default_sections_data() -> list[dict]:
    """The compiled-in default layout (config.default_pdf_sections()), as the same
    plain-dict shape the web UI edits and pdf_layout.json stores - what a fresh install
    starts from, reflecting the *current* column mapping even before /pdf-layout has
    ever been customized.
    """
    return [
        {
            "fields": [
                {"label": f.label, "column": f.column, "formatter": _FORMATTER_KEYS[f.formatter]}
                for f in section.fields
            ]
        }
        for section in config.default_pdf_sections()
    ]


def load_sections_data() -> list[dict]:
    """Falls back to the compiled-in default both when nothing's been customized yet
    and when the file's been hand-edited into something unreadable - a bad edit should
    degrade to "the default layout," never crash `generate`.
    """
    _migrate_legacy_file()
    if not os.path.exists(PDF_LAYOUT_PATH):
        return default_sections_data()
    try:
        with open(PDF_LAYOUT_PATH, encoding="utf-8") as f:
            return json.load(f)["sections"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return default_sections_data()


def save_sections_data(sections_data: list[dict]) -> None:
    def writer(tmp_path: str) -> None:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"sections": sections_data}, f, ensure_ascii=False, indent=2)

    os.makedirs(os.path.dirname(PDF_LAYOUT_PATH), exist_ok=True)
    atomic_write(PDF_LAYOUT_PATH, writer, no_validation)


def migrate_columns(old_to_new: dict[str, str]) -> bool:
    """Rewrites any saved field currently pointing at one of old_to_new's old (resolved)
    column values to its new one - called after a /column-mapping save so a previously
    persisted layout doesn't silently keep using a column that's just been corrected.

    This exists because pdf_layout.json stores each field's column as a plain resolved
    string (e.g. "Datum för händelsen"), not a live reference back to config.DATE_COLUMN
    - unlike default_sections_data() (never persisted, so always current), once a layout
    is saved even once it's frozen to whatever every field resolved to *at that moment*.
    Without this, fixing a wrong SHEET_COLUMN_* mapping later would leave any
    already-saved field pointing at the old, now-wrong column forever, invisibly.

    Only rewrites a field whose column is an exact key in old_to_new - a field the user
    deliberately pointed at some unrelated, freeform column is never touched, since it
    was never tracking a SHEET_COLUMN_* setting in the first place. A no-op (and no
    write) if the layout hasn't been customized yet (nothing to migrate - the next
    default_sections_data() call already reflects the new mapping) or if nothing in it
    happens to match. Returns whether anything actually changed.
    """
    _migrate_legacy_file()
    if not os.path.exists(PDF_LAYOUT_PATH):
        return False
    sections = load_sections_data()
    changed = False
    for section in sections:
        for f in section.get("fields", []):
            new_column = old_to_new.get(f.get("column"))
            if new_column and new_column != f["column"]:
                f["column"] = new_column
                changed = True
    if changed:
        save_sections_data(sections)
    return changed


def get_pdf_sections() -> list[Section]:
    """What pdf_gen.py actually renders - the persisted layout (or the default, if
    nothing's been customized) rebuilt into real Field/Section objects. Read fresh on
    every call rather than cached, so a web-UI edit takes effect on the very next
    `generate` without needing to restart `kvittomall webui`.
    """
    sections = []
    for section in load_sections_data():
        fields = [
            Field(f.get("label", ""), f.get("column", ""), formatter=FORMATTERS.get(f.get("formatter"), str))
            for f in section.get("fields", [])
        ]
        sections.append(Section(fields))
    return sections


def known_columns() -> set[str]:
    """Every column the current layout actually reads from, plus structural_columns()."""
    columns = structural_columns()
    for section in load_sections_data():
        for f in section.get("fields", []):
            column = f.get("column")
            if column:
                columns.add(column)
    return columns


def unmapped_columns_with_data(row: dict) -> list[str]:
    """Columns in this CSV row that have a value but aren't mapped to anything - not a
    failure, just something the preparer might want to know isn't showing up anywhere
    on the generated PDF. A column that's mapped but simply empty this time (e.g.
    MIL_COLUMN on a normal, non-mileage expense) is never flagged - only genuinely
    unmapped, non-empty data is, and only for the row being generated right now.
    """
    known = known_columns()
    return [column for column, value in row.items() if column not in known and str(value).strip()]
