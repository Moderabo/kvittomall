"""Read-only status report: cross-checks the database against the filesystem for every
row, the same way each pipeline stage does, and prints a summary. Never acquires the
run lock and never writes anything.
"""

from kvittomall import db
from kvittomall.config import NAME_COLUMN, PREVIOUS_DIRNAME, TIMESTAMP_COLUMN, category_for
from kvittomall.paths import FINAL_DIR
from kvittomall.rowkey import content_hash
from kvittomall.sheet import read_rows


def _location(final_pdf_path: str | None) -> str:
    if not final_pdf_path:
        return "-"
    return "previous" if final_pdf_path.startswith(PREVIOUS_DIRNAME + "/") else "new"


def run() -> None:
    rows = read_rows()
    with db.connect() as conn:
        total = len(rows)
        generated = pending = errors = not_synced = 0

        print(f"{'Row':<22} {'Name':<20} {'Category':<16} {'Attachments':<14} {'Location':<9} {'Status'}")
        print("-" * 100)

        for csv_row in rows:
            key = csv_row.get(TIMESTAMP_COLUMN, "").strip()
            db_row = db.get_row(conn, key) if key else None
            if db_row is None:
                not_synced += 1
                print(f"{key or '(missing timestamp)':<22} {'':<20} {'':<16} {'':<14} {'':<9} not yet synced")
                continue

            attachments = db.list_attachments(conn, key)
            downloaded_ok = sum(1 for a in attachments if db.is_download_valid(a)[0])
            processed_ok = sum(1 for a in attachments if db.is_process_valid(a)[0])

            kind, reason = db.classify_generated(db_row, content_hash(csv_row), FINAL_DIR)
            if kind == "valid":
                generated += 1
                state = "generated"
            elif kind == "repair":
                pending += 1
                state = f"needs repair: {reason}"
            elif db_row["status"] == "error":
                errors += 1
                state = f"ERROR: {db_row['last_error']}"
            else:
                pending += 1
                state = reason

            name = csv_row.get(NAME_COLUMN, "")[:20]
            category = category_for(csv_row)
            att_summary = f"{downloaded_ok}/{len(attachments)} dl, {processed_ok}/{len(attachments)} proc"
            location = _location(db_row["final_pdf_path"])
            print(f"{key:<22} {name:<20} {category:<16} {att_summary:<14} {location:<9} {state}")

        print("-" * 100)
        print(f"Total: {total}  Generated: {generated}  Pending: {pending}  "
              f"Not yet synced: {not_synced}  Errors: {errors}")
