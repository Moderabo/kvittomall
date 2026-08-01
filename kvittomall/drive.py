"""Stage 2: download each row's receipt attachments from Google Drive into downloads/.

Every attachment's status lives in the attachments table (see db.py) rather than being
inferred from "does a file with a guessed name exist" - so a zero-byte or half-written
download from a previous crashed run is correctly retried, not mistaken for done.
"""

import glob
import os
import re
from typing import Optional

import magic
import requests

from kvittomall import db
from kvittomall.config import RECEIPT_LINKS_COLUMN
from kvittomall.logging_setup import run_timer, setup_logging
from kvittomall.paths import DOWNLOADS_DIR
from kvittomall.progress import ProgressBar
from kvittomall.rowkey import sync_row
from kvittomall.sheet import read_rows

logger = setup_logging("download")

MIME_TO_EXTENSION = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
}

CHUNK_SIZE = 32768


def get_drive_file_id(url: str) -> Optional[str]:
    match = re.search(r"id=([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/d/([a-zA-Z0-9_-]+)/", url)
    if match:
        return match.group(1)
    return None


def _get_confirm_token(response: requests.Response) -> Optional[str]:
    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            return value
    if "text/html" in response.headers.get("Content-Type", ""):
        match = re.search(r'confirm=([a-zA-Z0-9\-_]+)"', response.text)
        if match:
            return match.group(1)
    return None


def _download_raw(file_id: str, dst_tmp_path: str) -> None:
    url = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(url, params={"id": file_id}, stream=True, timeout=30)
    token = _get_confirm_token(response)
    if token:
        response = session.get(url, params={"id": file_id, "confirm": token}, stream=True, timeout=30)
    response.raise_for_status()
    with open(dst_tmp_path, "wb") as f:
        for chunk in response.iter_content(CHUNK_SIZE):
            if chunk:
                f.write(chunk)


def _find_existing_download(base_filename: str, link_index: int) -> Optional[str]:
    """Looks for a file already sitting in downloads/ for this attachment (e.g. from
    before this database existed) so it can be adopted instead of re-fetched from Drive.
    """
    matches = [
        p for p in glob.glob(os.path.join(DOWNLOADS_DIR, f"{base_filename}_file{link_index}.*"))
        if not p.endswith(".part")
    ]
    return matches[0] if matches else None


def _adopt_existing(conn, row_key: str, link_index: int, path: str) -> bool:
    """Validates a pre-existing downloaded file and records it as done if it checks out."""
    try:
        size = os.path.getsize(path)
        if size == 0 or magic.from_file(path, mime=True) not in MIME_TO_EXTENSION:
            return False
    except OSError:
        return False
    db.mark_download_ok(conn, row_key, link_index, path, size)
    logger.info(f"Adopted existing download {path}")
    return True


def _download_one(conn, row_key: str, link_index: int, file_id: str, base_filename: str) -> None:
    tmp_path = os.path.join(DOWNLOADS_DIR, f".{base_filename}_file{link_index}.part")
    try:
        _download_raw(file_id, tmp_path)
        size = os.path.getsize(tmp_path)
        if size == 0:
            raise ValueError("downloaded file is empty")
        mime = magic.from_file(tmp_path, mime=True)
        ext = MIME_TO_EXTENSION.get(mime)
        if ext is None:
            raise ValueError(f"unsupported file type: {mime}")
        final_path = os.path.join(DOWNLOADS_DIR, f"{base_filename}_file{link_index}{ext}")
        os.replace(tmp_path, final_path)
        db.mark_download_ok(conn, row_key, link_index, final_path, size)
        logger.info(f"Downloaded {final_path}")
    except Exception as e:
        db.mark_download_failed(conn, row_key, link_index, str(e))
        logger.error(f"Failed to download row {row_key} attachment {link_index}: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def run() -> None:
    rows = read_rows()
    with db.connect() as conn, run_timer(logger, "download"), ProgressBar(len(rows), "Downloading") as bar:
        for i, row in enumerate(rows):
            row_key = sync_row(conn, row)
            if row_key is None:
                logger.warning(f"Row {i} is missing timestamp/name, skipping downloads")
                bar.update()
                continue

            links = [link.strip() for link in row.get(RECEIPT_LINKS_COLUMN, "").split(",") if link.strip()]
            base = db.get_row(conn, row_key)["base_filename"]

            for j, link in enumerate(links):
                file_id = get_drive_file_id(link)
                if file_id is None:
                    logger.warning(f"Row {i}: could not parse Drive link: {link}")
                    continue

                db.upsert_attachment(conn, row_key, j, file_id)
                attachment = db.get_attachment(conn, row_key, j)
                valid, reason = db.is_download_valid(attachment)
                if valid:
                    continue

                existing_path = _find_existing_download(base, j)
                if existing_path and _adopt_existing(conn, row_key, j, existing_path):
                    continue

                logger.info(f"Row {i} attachment {j}: downloading ({reason})")
                _download_one(conn, row_key, j, file_id, base)

            bar.update()
