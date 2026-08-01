"""Stage 1: fetch the Google Sheet, validate it, and save it as responses.csv.
Also provides read_rows(), used by every later stage to read that CSV back.

Two ways to reach the sheet are supported, chosen via ACCESS_MODE (see google_api.py):
a service account through the Sheets API, or the original anonymous CSV export that
only works while the sheet is shared as "anyone with the link".
"""

import csv
import io
import os
from datetime import datetime

import requests

from kvittomall import google_api
from kvittomall.atomic import atomic_write
from kvittomall.config import TIMESTAMP_COLUMN
from kvittomall.logging_setup import run_timer, setup_logging
from kvittomall.paths import RESPONSES_CSV

TIMESTAMP_FORMAT = "%Y-%m-%d %H.%M.%S"

logger = setup_logging("fetch")


class SheetFetchError(Exception):
    """Raised when a sheet fetch (API or public) fails outright, e.g. network/auth errors."""


def read_rows(csv_path: str = RESPONSES_CSV) -> list[dict]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _validate_chronological(rows: list[dict]) -> None:
    """Warns about unparseable or out-of-order timestamps, but doesn't block the fetch -
    no row's identity or state depends on sheet order or on this format parsing cleanly
    (each row is keyed by its own raw timestamp value, not by position), so this is just
    an early heads-up that the sheet may have been reordered or has a malformed row.
    """
    previous_ts = None
    previous_raw = None
    for i, row in enumerate(rows):
        raw = row.get(TIMESTAMP_COLUMN, "")
        try:
            ts = datetime.strptime(raw, TIMESTAMP_FORMAT)
        except ValueError:
            logger.warning(f"Row {i}: cannot parse '{TIMESTAMP_COLUMN}' value '{raw}'")
            continue
        if previous_ts is not None and ts < previous_ts:
            logger.warning(f"Row {i}: timestamps out of order ('{previous_raw}' then '{raw}')")
        previous_ts, previous_raw = ts, raw


def _fetch_public(sheet_id: str, sheet_gid: str) -> list[dict]:
    """The original method: works only while the sheet is shared as "anyone with the link"."""
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={sheet_gid}"
    logger.info(f"Downloading sheet from {url}")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except google_api.PUBLIC_ERRORS as e:
        raise SheetFetchError(f"Could not download the sheet: {google_api.describe(e, via='public')}")
    response.encoding = "utf-8"
    return list(csv.DictReader(io.StringIO(response.text)))


def _sheet_title_for_gid(service, sheet_id: str, sheet_gid: str) -> str:
    """The Sheets API addresses tabs by title, not gid, so this looks up the title of
    the tab SHEET_GID refers to (the same one the public CSV export's gid= picks)."""
    metadata = service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets(properties(sheetId,title))"
    ).execute()
    for sheet in metadata["sheets"]:
        if str(sheet["properties"]["sheetId"]) == str(sheet_gid):
            return sheet["properties"]["title"]
    raise SheetFetchError(f"No sheet tab with gid {sheet_gid} found in spreadsheet {sheet_id}")


def _rows_from_values(values: list[list[str]]) -> list[dict]:
    """The Sheets API omits trailing empty cells per row, unlike CSV export - pad each
    row back out to the header's width so every row has every column, like DictReader."""
    if not values:
        return []
    header, *data_rows = values
    rows = []
    for raw_row in data_rows:
        padded = raw_row + [""] * (len(header) - len(raw_row))
        rows.append(dict(zip(header, padded)))
    return rows


def _fetch_api(sheet_id: str, sheet_gid: str) -> list[dict]:
    logger.info(f"Fetching sheet {sheet_id} (gid {sheet_gid}) via the Sheets API")
    try:
        service = google_api.build_sheets_service()
        title = _sheet_title_for_gid(service, sheet_id, sheet_gid)
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=f"'{title}'", valueRenderOption="FORMATTED_VALUE")
            .execute()
        )
    except google_api.API_ERRORS as e:
        raise SheetFetchError(f"Could not fetch the sheet via the API: {google_api.describe(e, via='api')}")
    return _rows_from_values(result.get("values", []))


def fetch_and_save(sheet_id: str, sheet_gid: str, csv_path: str = RESPONSES_CSV) -> int:
    """Downloads the sheet and atomically replaces csv_path. Returns the number of rows
    saved. Only a failed download (SheetFetchError) leaves csv_path untouched - rows
    with unparseable/out-of-order timestamps are still saved, just logged as warnings.
    """
    mode = google_api.get_access_mode()
    if mode == "public":
        rows = _fetch_public(sheet_id, sheet_gid)
    elif mode == "api":
        rows = _fetch_api(sheet_id, sheet_gid)
    else:
        try:
            rows = _fetch_api(sheet_id, sheet_gid)
        except SheetFetchError as e:
            logger.warning(f"API fetch failed, falling back to public CSV export: {e}")
            rows = _fetch_public(sheet_id, sheet_gid)

    if rows:
        _validate_chronological(rows)
    logger.info(f"Downloaded {len(rows)} rows.")

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

    with run_timer(logger, "fetch"):
        try:
            if not sheet_id or not sheet_gid:
                raise SheetFetchError("SHEET_ID and SHEET_GID must be set in .env")
            fetch_and_save(sheet_id, sheet_gid)
        except (SheetFetchError, SystemExit) as e:
            logger.error(f"Fetch failed, existing responses.csv left untouched: {e}")
            raise SystemExit(1)
