"""Read-only status report: cross-checks the database against the filesystem for every
row, the same way each pipeline stage does, and prints a summary. Never acquires the
run lock and never writes anything.
"""

import os

from kvittomall import db
from kvittomall.config import NAME_COLUMN, TIMESTAMP_COLUMN, category_for
from kvittomall.paths import FINAL_DIR
from kvittomall.rowkey import content_hash, receipt_links
from kvittomall.sheet import read_rows


def _location(final_pdf_path: str | None, handled: bool) -> str:
    if not final_pdf_path:
        return "-"
    return "handled" if handled else "new"


def _attachment_details(conn, row_key: str, csv_row: dict) -> list[dict]:
    """One entry per receipt link the CSV row *currently* has (not just whatever's
    already in the attachments table) - a link that hasn't been downloaded yet must
    still show up as "not downloaded", not be silently absent from the list.
    """
    by_index = {a["link_index"]: a for a in db.list_attachments(conn, row_key)}
    details = []
    for i in range(len(receipt_links(csv_row))):
        att = by_index.get(i)
        details.append({
            "link_index": i,
            "downloaded_ok": db.is_download_valid(att)[0] if att else False,
            "processed_ok": db.is_process_valid(att)[0] if att else False,
        })
    return details


def row_status(conn, csv_row: dict) -> dict:
    """The same fail-safe per-row classification `run()` prints as a table row, as plain
    structured data - shared with the web UI's entries list/detail views so both surfaces
    agree on a row's state instead of each re-deriving it.

    Returns a dict with: row_key (the row's raw timestamp - the same stable identifier
    rowkey.row_key() computes straight from the CSV, so it's set even before a row has
    ever been synced to the DB; None only if the CSV row has no timestamp at all and truly
    can't be identified), name, category, attachments (list of {link_index, downloaded_ok,
    processed_ok} - one per receipt link the row currently has, whether or not it's been
    downloaded/processed yet), kind ("not_synced" | "valid" | "handled" | "repair" |
    "error" | "pending"), state (human-readable reason), location ("new" | "handled" |
    "-"), final_pdf_path, and final_pdf_exists (a direct, fail-safe disk check - unlike
    final_pdf_path, which is just the DB's recorded location and stays set even after the
    file is removed).
    """
    key = csv_row.get(TIMESTAMP_COLUMN, "").strip() or None
    db_row = db.get_row(conn, key) if key else None
    if db_row is None:
        return {
            "row_key": key,
            "name": csv_row.get(NAME_COLUMN, ""),
            "category": category_for(csv_row),
            "attachments": _attachment_details(conn, key, csv_row) if key else [],
            "kind": "not_synced",
            "state": "not yet synced",
            "location": "-",
            "final_pdf_path": None,
            "final_pdf_exists": False,
        }

    final_pdf_exists = bool(db_row["final_pdf_path"]) and os.path.exists(
        os.path.join(FINAL_DIR, db_row["final_pdf_path"])
    )

    kind, reason = db.classify_generated(db_row, content_hash(csv_row), FINAL_DIR)
    if kind == "valid":
        if db_row["handled"]:
            kind = "handled"
            state = "handled"
        else:
            state = "generated"
    elif kind == "repair":
        # "missing on disk" is the one repair cause that means someone removed the file
        # rather than it being corrupt/wrong-size (both of which still exist on disk) -
        # worth a short, friendly state instead of dumping classify_generated()'s
        # diagnostic reason (which includes a full filesystem path) into the UI.
        state = "PDF removed" if not final_pdf_exists else f"needs repair: {reason}"
    elif db_row["status"] == "error":
        kind = "error"
        state = f"ERROR: {db_row['last_error']}"
    else:
        kind = "pending"
        state = reason

    return {
        "row_key": key,
        "name": csv_row.get(NAME_COLUMN, ""),
        "category": category_for(csv_row),
        "attachments": _attachment_details(conn, key, csv_row),
        "kind": kind,
        "state": state,
        "location": _location(db_row["final_pdf_path"], bool(db_row["handled"])),
        "final_pdf_path": db_row["final_pdf_path"],
        "final_pdf_exists": final_pdf_exists,
    }


def run() -> None:
    rows = read_rows()
    with db.connect() as conn:
        total = len(rows)
        generated = handled = pending = errors = not_synced = 0

        print(f"{'Row':<22} {'Name':<20} {'Category':<16} {'Attachments':<14} {'Location':<9} {'Status'}")
        print("-" * 100)

        for csv_row in rows:
            info = row_status(conn, csv_row)
            key = info["row_key"]
            if info["kind"] == "not_synced":
                not_synced += 1
                print(f"{key or '(missing timestamp)':<22} {'':<20} {'':<16} {'':<14} {'':<9} not yet synced")
                continue

            if info["kind"] == "valid":
                generated += 1
            elif info["kind"] == "handled":
                handled += 1
            elif info["kind"] == "error":
                errors += 1
            else:
                pending += 1

            attachments = info["attachments"]
            downloaded_ok = sum(1 for a in attachments if a["downloaded_ok"])
            processed_ok = sum(1 for a in attachments if a["processed_ok"])
            name = info["name"][:20]
            att_summary = f"{downloaded_ok}/{len(attachments)} dl, {processed_ok}/{len(attachments)} proc"
            print(f"{key:<22} {name:<20} {info['category']:<16} {att_summary:<14} {info['location']:<9} {info['state']}")

        print("-" * 100)
        print(f"Total: {total}  Generated: {generated}  Handled: {handled}  Pending: {pending}  "
              f"Not yet synced: {not_synced}  Errors: {errors}")
