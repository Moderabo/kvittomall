"""Stage 2: download each row's receipt attachments from Google Drive into downloads/.

Every attachment's status lives in the attachments table (see db.py) rather than being
inferred from "does a file with a guessed name exist" - so a zero-byte or half-written
download from a previous crashed run is correctly retried, not mistaken for done.

Two ways to reach Drive are supported, chosen via ACCESS_MODE (see google_api.py): a
service account through the Drive API, or the original anonymous download link that
only works while each file is shared as "anyone with the link". Which one is usable is
decided once at the start of run() (a single API call), not per file - a broken
key/share affects every file identically, so there's no point re-discovering that on
every attachment.
"""

import glob
import os
import re
from typing import Optional

import magic
import requests
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from kvittomall import db, google_api
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
    "image/webp": ".webp",
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


def _download_public(file_id: str, dst_tmp_path: str) -> None:
    """The original method: works only while the file is shared as "anyone with the link"."""
    url = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(url, params={"id": file_id}, stream=True, timeout=30)
    token = _get_confirm_token(response)
    if token:
        response = session.get(url, params={"id": file_id, "confirm": token}, stream=True, timeout=30)
    response.raise_for_status()
    if "text/html" in response.headers.get("Content-Type", ""):
        # A file that isn't accessible gets a 200 OK here with a Google sign-in/permission
        # page instead of a real 4xx status, so raise_for_status() alone can't catch it -
        # this is the only way to tell "denied" apart from a real download before writing
        # that HTML page to disk as if it were the receipt.
        raise ValueError(
            "access was denied - Google returned a sign-in/permission page instead of the "
            'file, which usually means it is not shared as "anyone with the link"'
        )
    with open(dst_tmp_path, "wb") as f:
        for chunk in response.iter_content(CHUNK_SIZE):
            if chunk:
                f.write(chunk)


def _download_api(file_id: str, dst_tmp_path: str, service) -> None:
    """Uses MediaIoBaseDownload's default chunk size (100 MB) rather than CHUNK_SIZE -
    each chunk is a separate API request, and a receipt is always far under 100 MB, so
    this downloads it in a single request instead of dozens of tiny ones.
    """
    request = service.files().get_media(fileId=file_id)
    with open(dst_tmp_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


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


def _download_one(
    conn, row_key: str, link_index: int, file_id: str, base_filename: str, drive_service
) -> None:
    tmp_path = os.path.join(DOWNLOADS_DIR, f".{base_filename}_file{link_index}.part")
    try:
        try:
            if drive_service is not None:
                _download_api(file_id, tmp_path, drive_service)
            else:
                _download_public(file_id, tmp_path)
        # Deliberately narrower than google_api.API_ERRORS: that tuple's bare OSError
        # exists for credential-file problems when *building* a service, which already
        # succeeded before we got here. An OSError raised during the download itself
        # (e.g. disk full writing tmp_path) is a local infrastructure failure, not a
        # Google-side one, and must not be relabeled as "could not download it" and
        # retried per-attachment forever - it should propagate and stop the run.
        except (HttpError, GoogleAuthError, *google_api.PUBLIC_ERRORS) as e:
            via = "api" if drive_service is not None else "public"
            raise ValueError(f"could not download it: {google_api.describe(e, via=via)}") from e
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
    except OSError:
        raise
    except Exception as e:
        db.mark_download_failed(conn, row_key, link_index, str(e))
        logger.error(f"Failed to download row {row_key} attachment {link_index}: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _get_drive_service():
    """Decides, once per run, whether the Drive API is usable. Returns a service object
    to use it, or None to fall back to the public download method for this whole run -
    logging a WARNING (visible on the console, not just in the log file) when that
    fallback happens, so it's clear *why* subsequent public-sharing failures are even
    being attempted via the public method instead of the API.
    Raises SystemExit only when ACCESS_MODE=api and the API genuinely can't be used -
    the caller is responsible for logging that before it propagates, same as every
    other stage-ending failure.
    """
    mode = google_api.get_access_mode()
    if mode == "public":
        return None
    try:
        service = google_api.build_drive_service()
        service.about().get(fields="user").execute()  # cheap call that proves auth works
        logger.info("Using the Drive API.")
        return service
    except google_api.API_ERRORS as e:
        reason = google_api.describe(e, via="api")
        if mode == "api":
            raise SystemExit(f"Could not connect to the Drive API: {reason}")
        logger.warning(f"Could not connect to the Drive API, falling back to public downloads: {reason}")
        return None


def run() -> None:
    with run_timer(logger, "download"):
        try:
            drive_service = _get_drive_service()
        except SystemExit as e:
            logger.error(f"Download failed before it could start: {e}")
            raise SystemExit(1)
        _download_all(drive_service)


def _download_all(drive_service) -> None:
    rows = read_rows()
    with db.connect() as conn, ProgressBar(len(rows), "Downloading") as bar:
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
                _download_one(conn, row_key, j, file_id, base, drive_service)

            bar.update()
