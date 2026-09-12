"""SQLite-backed processing state.

The rule this module exists to enforce: a row or attachment is only ever considered
"done" if the database says so *and* the file it points to actually still exists on
disk, is the size we expect, and was produced from the CSV data as it currently reads.
Any mismatch is treated as "not done" and logged with a reason, never trusted blindly.
"""

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from kvittomall.paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS rows (
    row_key             TEXT PRIMARY KEY,
    base_filename       TEXT NOT NULL,
    transaktionstyp     TEXT,
    content_hash        TEXT NOT NULL,
    links_hash          TEXT NOT NULL,
    final_pdf_path       TEXT,
    final_pdf_size        INTEGER,
    final_pdf_content_hash TEXT,
    generated_at            TEXT,
    status                  TEXT NOT NULL DEFAULT 'new',
    handled                 INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    updated_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    row_key          TEXT NOT NULL,
    link_index       INTEGER NOT NULL,
    drive_file_id    TEXT NOT NULL,
    download_status  TEXT NOT NULL DEFAULT 'pending',
    download_path    TEXT,
    download_size    INTEGER,
    process_status   TEXT NOT NULL DEFAULT 'pending',
    processed_path   TEXT,
    processed_size   INTEGER,
    error_message    TEXT,
    PRIMARY KEY (row_key, link_index)
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """One-off patches for databases created before a column existed - CREATE TABLE IF
    NOT EXISTS above doesn't retroactively add columns to a table that already exists.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(rows)")}
    if "handled" not in columns:
        conn.execute("ALTER TABLE rows ADD COLUMN handled INTEGER NOT NULL DEFAULT 0")
    # Rows created while "handled" was briefly a status value (rather than this column)
    # get migrated forward - a no-op update on any DB that never had that value.
    conn.execute("UPDATE rows SET status = 'generated', handled = 1 WHERE status = 'handled'")
    conn.commit()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --- rows ---

def upsert_row(conn: sqlite3.Connection, row_key: str, base_filename: str,
                transaktionstyp: str, content_hash: str, links_hash: str) -> None:
    conn.execute(
        """
        INSERT INTO rows (row_key, base_filename, transaktionstyp, content_hash, links_hash, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(row_key) DO UPDATE SET
            base_filename = excluded.base_filename,
            transaktionstyp = excluded.transaktionstyp,
            content_hash = excluded.content_hash,
            links_hash = excluded.links_hash,
            updated_at = excluded.updated_at
        """,
        (row_key, base_filename, transaktionstyp, content_hash, links_hash, _now()),
    )


def get_row(conn: sqlite3.Connection, row_key: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM rows WHERE row_key = ?", (row_key,)).fetchone()


def list_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM rows ORDER BY row_key").fetchall()


def set_row_error(conn: sqlite3.Connection, row_key: str, message: str) -> None:
    conn.execute(
        "UPDATE rows SET status = 'error', last_error = ?, updated_at = ? WHERE row_key = ?",
        (message, _now(), row_key),
    )


def rename_base_filename(conn: sqlite3.Connection, row_key: str, old_base: str, new_base: str) -> None:
    """Called after a submitter's name was corrected in the sheet and the on-disk files
    were renamed to match: keeps every stored path in sync so nothing looks "missing"
    and gets needlessly redownloaded or regenerated.
    """
    conn.execute(
        """
        UPDATE rows SET base_filename = ?, final_pdf_path = REPLACE(final_pdf_path, ?, ?), updated_at = ?
        WHERE row_key = ?
        """,
        (new_base, old_base, new_base, _now(), row_key),
    )
    conn.execute(
        """
        UPDATE attachments SET download_path = REPLACE(download_path, ?, ?),
            processed_path = REPLACE(processed_path, ?, ?)
        WHERE row_key = ?
        """,
        (old_base, new_base, old_base, new_base, row_key),
    )


def mark_generated(conn: sqlite3.Connection, row_key: str, final_pdf_path: str,
                    final_pdf_size: int, content_hash: str, handled: bool = False) -> None:
    """`handled` is set explicitly (never left as whatever it was before) so a
    regeneration always leaves the row in a state that matches where its file actually
    ended up - pdf_gen.run() passes the row's *current* handled value through for a
    repair (nothing conceptually changed) and False for anything else (new or
    content-changed output needs fresh review, and belongs in the active folder).
    """
    conn.execute(
        """
        UPDATE rows SET status = 'generated', final_pdf_path = ?, final_pdf_size = ?,
            final_pdf_content_hash = ?, handled = ?, generated_at = ?, last_error = NULL, updated_at = ?
        WHERE row_key = ?
        """,
        (final_pdf_path, final_pdf_size, content_hash, 1 if handled else 0, _now(), _now(), row_key),
    )


def set_final_pdf_path(conn: sqlite3.Connection, row_key: str, final_pdf_path: str) -> None:
    """Updates just the recorded location of an already-generated PDF, without touching
    its status - used for repairs/relocations that aren't a review-state change.
    """
    conn.execute(
        "UPDATE rows SET final_pdf_path = ?, updated_at = ? WHERE row_key = ?",
        (final_pdf_path, _now(), row_key),
    )


def set_handled(conn: sqlite3.Connection, row_key: str, handled: bool, final_pdf_path: str) -> None:
    """Flips whether a human has reviewed this row's PDF, independently of `status`
    (which stays "generated" throughout) - `handled` is a separate axis, not a
    generation outcome. Always paired with relocating the file (final_pdf_path is
    required, not optional), since toggling this always means moving it between
    final/<category>/ and final/<HANDLED_DIRNAME>/<category>/.
    """
    conn.execute(
        "UPDATE rows SET handled = ?, final_pdf_path = ?, updated_at = ? WHERE row_key = ?",
        (1 if handled else 0, final_pdf_path, _now(), row_key),
    )


def classify_generated(row: Optional[sqlite3.Row], current_content_hash: str, final_dir: str) -> tuple[str, str]:
    """Fail-safe classification of a row's generated-PDF status, never trusting the
    database alone:
      - "valid": nothing to do, matches disk and current row content (whether it's still
        sitting in its category folder or has since been marked handled).
      - "stale": never generated, or the row's content changed since it was - this is
        genuinely new/updated output and belongs in the (active) category folder. A row
        that was handled but whose content has since changed also lands here, which is
        what moves it back out of final/<HANDLED_DIRNAME>/ for re-review (see
        pdf_gen.run(), which resets `handled` to False for this case).
      - "repair": content is unchanged but the recorded file is missing or corrupt on
        disk - this should be restored to wherever it already was, not treated as new.
    """
    if row is None or row["status"] != "generated" or not row["final_pdf_path"]:
        return "stale", "not yet generated"
    if row["final_pdf_content_hash"] != current_content_hash:
        return "stale", "source row data changed since this PDF was generated"

    full_path = os.path.join(final_dir, row["final_pdf_path"])
    if not os.path.exists(full_path):
        return "repair", f"recorded final PDF is missing on disk: {full_path}"
    actual_size = os.path.getsize(full_path)
    if actual_size == 0:
        return "repair", "recorded final PDF is zero bytes"
    if actual_size != row["final_pdf_size"]:
        return "repair", "recorded final PDF size does not match what's on disk"
    return "valid", "ok"


# --- attachments ---

def upsert_attachment(conn: sqlite3.Connection, row_key: str, link_index: int, drive_file_id: str) -> None:
    existing = conn.execute(
        "SELECT drive_file_id FROM attachments WHERE row_key = ? AND link_index = ?",
        (row_key, link_index),
    ).fetchone()
    if existing is not None and existing["drive_file_id"] != drive_file_id:
        # The link at this position changed - the old download/process state no longer applies.
        conn.execute(
            """
            UPDATE attachments SET drive_file_id = ?, download_status = 'pending', download_path = NULL,
                download_size = NULL, process_status = 'pending', processed_path = NULL,
                processed_size = NULL, error_message = NULL
            WHERE row_key = ? AND link_index = ?
            """,
            (drive_file_id, row_key, link_index),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO attachments (row_key, link_index, drive_file_id) VALUES (?, ?, ?)",
            (row_key, link_index, drive_file_id),
        )


def get_attachment(conn: sqlite3.Connection, row_key: str, link_index: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM attachments WHERE row_key = ? AND link_index = ?", (row_key, link_index)
    ).fetchone()


def list_attachments(conn: sqlite3.Connection, row_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM attachments WHERE row_key = ? ORDER BY link_index", (row_key,)
    ).fetchall()


def mark_download_ok(conn: sqlite3.Connection, row_key: str, link_index: int, path: str, size: int) -> None:
    conn.execute(
        """
        UPDATE attachments SET download_status = 'ok', download_path = ?, download_size = ?,
            error_message = NULL WHERE row_key = ? AND link_index = ?
        """,
        (path, size, row_key, link_index),
    )


def mark_download_failed(conn: sqlite3.Connection, row_key: str, link_index: int, error: str) -> None:
    conn.execute(
        """
        UPDATE attachments SET download_status = 'failed', error_message = ?
        WHERE row_key = ? AND link_index = ?
        """,
        (error, row_key, link_index),
    )


def mark_process_ok(conn: sqlite3.Connection, row_key: str, link_index: int, path: str, size: int) -> None:
    conn.execute(
        """
        UPDATE attachments SET process_status = 'ok', processed_path = ?, processed_size = ?,
            error_message = NULL WHERE row_key = ? AND link_index = ?
        """,
        (path, size, row_key, link_index),
    )


def mark_process_failed(conn: sqlite3.Connection, row_key: str, link_index: int, error: str) -> None:
    conn.execute(
        """
        UPDATE attachments SET process_status = 'failed', error_message = ?
        WHERE row_key = ? AND link_index = ?
        """,
        (error, row_key, link_index),
    )


def is_download_valid(att: Optional[sqlite3.Row]) -> tuple[bool, str]:
    if att is None or att["download_status"] != "ok" or not att["download_path"]:
        return False, "not yet downloaded"
    if not os.path.exists(att["download_path"]):
        return False, f"recorded download is missing on disk: {att['download_path']}"
    actual_size = os.path.getsize(att["download_path"])
    if actual_size == 0 or actual_size != att["download_size"]:
        return False, "recorded download is zero-byte or size mismatch on disk"
    return True, "ok"


def is_process_valid(att: Optional[sqlite3.Row]) -> tuple[bool, str]:
    if att is None or att["process_status"] != "ok" or not att["processed_path"]:
        return False, "not yet processed"
    if not os.path.exists(att["processed_path"]):
        return False, f"recorded processed file is missing on disk: {att['processed_path']}"
    actual_size = os.path.getsize(att["processed_path"])
    if actual_size == 0 or actual_size != att["processed_size"]:
        return False, "recorded processed file is zero-byte or size mismatch on disk"
    return True, "ok"
