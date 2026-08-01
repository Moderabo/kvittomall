"""Row identity: what filename a CSV row maps to on disk, and hashes used to detect
when a row's source data has changed since it was last processed.
"""

import hashlib
import json
import os
import re
import sqlite3

from kvittomall import db
from kvittomall.config import NAME_COLUMN, RECEIPT_LINKS_COLUMN, TIMESTAMP_COLUMN, TRANSACTION_TYPE_COLUMN
from kvittomall.paths import DOWNLOADS_DIR, FINAL_DIR, PROCESSED_DIR


def sanitize_filename(s: str) -> str:
    """Replace anything unsafe for a filename with an underscore."""
    s = s.strip()
    return re.sub(r"[^\w\-_.]", "_", s)


def row_key(row: dict) -> str | None:
    """The stable identity of a submission: its raw timestamp. Stable even if the
    submitter's name is corrected later.
    """
    timestamp = row.get(TIMESTAMP_COLUMN, "").strip()
    return timestamp or None


def base_filename(row: dict) -> str | None:
    """The human-readable filename stem used for this row's files on disk, e.g.
    "2023-10-27_12.30.00_John-Doe". Returns None if required fields are missing.
    """
    timestamp_raw = row.get(TIMESTAMP_COLUMN, "")
    name_raw = row.get(NAME_COLUMN, "")
    if not timestamp_raw or not name_raw:
        return None

    timestamp = timestamp_raw.replace(" ", "_")
    name = name_raw.rstrip(" ").replace(" ", "-")
    return f"{sanitize_filename(timestamp)}_{sanitize_filename(name)}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def links_hash(row: dict) -> str:
    """Changes only when the receipt links change -> invalidates download/process state."""
    return _hash(row.get(RECEIPT_LINKS_COLUMN, ""))


def content_hash(row: dict) -> str:
    """Changes whenever any field changes -> invalidates the generated PDF.
    Always changes when links_hash does too, since links are part of the row.
    """
    return _hash(json.dumps(sorted(row.items()), ensure_ascii=False))


def _rename_prefixed(directory: str, old_base: str, new_base: str) -> None:
    if not os.path.isdir(directory):
        return
    for dirpath, _dirnames, filenames in os.walk(directory):
        for name in filenames:
            if name.startswith(old_base):
                new_name = new_base + name[len(old_base):]
                os.replace(os.path.join(dirpath, name), os.path.join(dirpath, new_name))


def sync_row(conn: sqlite3.Connection, row: dict) -> str | None:
    """Upserts this row's identity/hashes into the database and returns its row_key,
    or None if the row is missing the fields needed to identify it. If the submitter's
    name was corrected since the last sync, renames the row's existing files in place
    instead of treating it as a brand-new, unprocessed row.
    """
    key = row_key(row)
    new_base = base_filename(row)
    if key is None or new_base is None:
        return None

    existing = db.get_row(conn, key)
    if existing is not None and existing["base_filename"] != new_base:
        old_base = existing["base_filename"]
        _rename_prefixed(DOWNLOADS_DIR, old_base, new_base)
        _rename_prefixed(PROCESSED_DIR, old_base, new_base)
        _rename_prefixed(FINAL_DIR, old_base, new_base)
        db.rename_base_filename(conn, key, old_base, new_base)

    db.upsert_row(
        conn, key, new_base,
        row.get(TRANSACTION_TYPE_COLUMN, ""),
        content_hash(row),
        links_hash(row),
    )
    return key
