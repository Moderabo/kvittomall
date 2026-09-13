"""Shared test fixtures.

Importing any of the stage modules (sheet, drive, media, pdf_gen, cli) sets up a
logger that writes into the real repo's logs/ directory as a side effect of module
import (see logging_setup.setup_logging()). Redirecting paths.LOGS_DIR here - before
any test file gets to import one of those modules - keeps the test suite from writing
into the real project's logs/ on every run. This only works because pytest always
loads conftest.py before collecting/importing test modules.

The same reasoning applies to paths.PDF_LAYOUT_PATH and config._COLUMN_MAPPING_PATH -
both point at real files in the repo root by default (pdf_layout.json,
column_mapping.json), read live by every call to pdf_layout.get_pdf_sections()/
config.TIMESTAMP_COLUMN-style attributes, not just by the tests that exercise those
files directly. A real file left there (e.g. from actually running `kvittomall webui`
and using /pdf-layout or /column-mapping) would otherwise silently change what most of
the suite resolves a column name or the cover-page layout to - not a hypothetical, this
is exactly what happened once those pages existed to create such a file. Tests that
specifically want to exercise the override-file behavior already monkeypatch their own
tmp_path on top of this default.
"""

import os
import tempfile

from kvittomall import config, paths

paths.LOGS_DIR = tempfile.mkdtemp(prefix="kvittomall-test-logs-")
paths.PDF_LAYOUT_PATH = os.path.join(tempfile.mkdtemp(prefix="kvittomall-test-pdflayout-"), "pdf_layout.json")
config._COLUMN_MAPPING_PATH = os.path.join(tempfile.mkdtemp(prefix="kvittomall-test-colmap-"), "column_mapping.json")

import pytest  # noqa: E402


# pdf_layout imported here, right after the PDF_LAYOUT_PATH patch above and before any
# test file gets a chance to import it - it does `from kvittomall.paths import
# PDF_LAYOUT_PATH` at module level, which binds its own copy of the path once, at
# import time. Importing it any later (e.g. only implicitly, from within a test module)
# would bind that copy to the real repo path instead of this test one.
from kvittomall import db, pdf_layout  # noqa: E402, F401


@pytest.fixture
def conn(monkeypatch, tmp_path):
    """A fresh, isolated SQLite connection - never the real data/kvittomall_state.db."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "state.db"))
    with db.connect() as c:
        yield c


@pytest.fixture
def sample_row() -> dict:
    """A CSV row shaped like a real fetched sheet row, using the current default
    column names - one attachment link, a mileage trip's worth of fields included.
    """
    return {
        "Tidstämpel": "2026-03-05 12.30.00",
        "Transaktionstyp": "Privat utlägg",
        "Datum för händelsen": "2026-03-01",
        "Utskott": "Styrelsen",
        "Arrangemang": "Årsmöte",
        "Specificering": "Fika till mötet",
        "Namn": "Anna Andersson",
        "Kontonummer": "1234-5, 123 456 789",
        "Summa": "245.50",
        "Körda mil": "",
        "Sträcka": "",
        "Ladda upp bild": "https://drive.google.com/open?id=abc123XYZ",
        "Övrigt": "",
        "Godkännande": "Ja",
    }
