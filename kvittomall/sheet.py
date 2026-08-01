"""Stage 1: fetch the Google Sheet, validate it, and save it as responses.csv.
Also provides read_rows(), used by every later stage to read that CSV back.
"""

import csv
import io
import os
from datetime import datetime

import requests

from kvittomall.atomic import atomic_write
from kvittomall.config import TIMESTAMP_COLUMN
from kvittomall.logging_setup import run_timer, setup_logging
from kvittomall.paths import RESPONSES_CSV

TIMESTAMP_FORMAT = "%Y-%m-%d %H.%M.%S"

logger = setup_logging("fetch")


class SheetValidationError(Exception):
    """Raised when the downloaded sheet fails validation; the CSV is left untouched."""


def read_rows(csv_path: str = RESPONSES_CSV) -> list[dict]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _validate_chronological(rows: list[dict]) -> None:
    """Every row's timestamp must parse and the column must be non-decreasing."""
    previous_ts = None
    previous_raw = None
    for i, row in enumerate(rows):
        raw = row.get(TIMESTAMP_COLUMN, "")
        try:
            ts = datetime.strptime(raw, TIMESTAMP_FORMAT)
        except ValueError:
            raise SheetValidationError(f"Row {i}: cannot parse '{TIMESTAMP_COLUMN}' value '{raw}'")
        if previous_ts is not None and ts < previous_ts:
            raise SheetValidationError(
                f"Row {i}: timestamps out of order ('{previous_raw}' then '{raw}')"
            )
        previous_ts, previous_raw = ts, raw


def fetch_and_save(sheet_id: str, sheet_gid: str, csv_path: str = RESPONSES_CSV) -> int:
    """Downloads the sheet, validates it, and atomically replaces csv_path.
    Returns the number of rows saved. Raises SheetValidationError without touching
    csv_path if validation fails, so a bad fetch never clobbers good existing data.
    """
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={sheet_gid}"
    logger.info(f"Downloading sheet from {url}")
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"

    rows = list(csv.DictReader(io.StringIO(response.text)))
    if rows:
        _validate_chronological(rows)
    logger.info(f"Downloaded {len(rows)} rows; chronological check passed.")

    def write(tmp_path: str) -> None:
        with open(tmp_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
            writer.writeheader()
            writer.writerows(rows)

    atomic_write(csv_path, write, lambda p: None)
    logger.info(f"Saved {csv_path}")
    return len(rows)


def run() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    sheet_id = os.getenv("SHEET_ID")
    sheet_gid = os.getenv("SHEET_GID")
    if not sheet_id or not sheet_gid:
        raise SystemExit("SHEET_ID and SHEET_GID must be set in .env")

    with run_timer(logger, "fetch"):
        try:
            fetch_and_save(sheet_id, sheet_gid)
        except (requests.exceptions.RequestException, SheetValidationError) as e:
            logger.error(f"Fetch failed, existing responses.csv left untouched: {e}")
            raise SystemExit(1)
