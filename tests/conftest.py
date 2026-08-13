"""Shared test fixtures.

Importing any of the stage modules (sheet, drive, media, pdf_gen, cli) sets up a
logger that writes into the real repo's logs/ directory as a side effect of module
import (see logging_setup.setup_logging()). Redirecting paths.LOGS_DIR here - before
any test file gets to import one of those modules - keeps the test suite from writing
into the real project's logs/ on every run. This only works because pytest always
loads conftest.py before collecting/importing test modules.
"""

import tempfile

from kvittomall import paths

paths.LOGS_DIR = tempfile.mkdtemp(prefix="kvittomall-test-logs-")

import pytest  # noqa: E402


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
