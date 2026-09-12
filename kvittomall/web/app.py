"""Local web UI: a thin control panel over the same stage functions cli.py calls -
no pipeline logic lives here. Each button triggers a stage in a background thread so
the HTTP request returns immediately; the page polls /api/status to show whether
something is running and what the last result was.

run_lock() is still acquired around every stage execution, exactly as cli.py does it,
so the web UI and the CLI can never run two mutating stages at once - only one of
_running (in-process) or the lock file (cross-process) needs to say "busy" to refuse
a click.
"""

import functools
import os
import re
import threading
from datetime import datetime
from typing import Callable

from flask import Flask, abort, jsonify, render_template, send_file

from kvittomall import db, status
from kvittomall.cli import (
    PIPELINE_ORDER, REMOVE_TARGETS, SCOPABLE_STAGES, STAGES, _run_pipeline_stages, remove_all_files,
)
from kvittomall.config import (
    COMMITTEE_COLUMN, DATE_COLUMN, EVENT_COLUMN, MIL_COLUMN, RECEIPT_LINKS_COLUMN,
    SPECIFICATION_COLUMN, SUM_COLUMN, TIMESTAMP_COLUMN, WEBUI_HOST, WEBUI_PORT,
)
from kvittomall.lock import AlreadyRunningError, run_lock
from kvittomall.paths import DOWNLOADS_DIR, ensure_dirs, FINAL_DIR, PROCESSED_DIR
from kvittomall.paths import clean_all as clean_all_data
from kvittomall.sheet import read_rows

PROCESSED_IMAGE_EXTENSIONS = (".jpg", ".jpeg")

# The raw download can be any of drive.MIME_TO_EXTENSION's formats, unlike the
# processed file (always .jpg, aside from PDF passthrough). HEIC/HEIF - a common
# raw phone-photo format before processing converts it to JPEG - isn't reliably
# renderable via a browser <img> tag, so it deliberately stays a clickable link
# rather than a thumbnail that would likely just show up broken.
DOWNLOAD_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")

# Curated columns shown in the entries list, alongside name/category/status - each a
# tuple of column(s) to try in order (pulled through config.py's constants, never a
# hardcoded sheet header, so a deployment that overrides SHEET_COLUMN_* in .env still
# shows the right data). "Sum" falls back to MIL_COLUMN since a mileage-reimbursement
# row (Milersättning) has no Summa of its own - only a distance - so showing whichever
# of the two is actually filled in is more useful than always showing "-" for those rows.
ENTRIES_LIST_COLUMNS = [
    ("Date", (DATE_COLUMN,)),
    ("Sum", (SUM_COLUMN, MIL_COLUMN)),
    ("Committee", (COMMITTEE_COLUMN,)),
    ("Event", (EVENT_COLUMN,)),
    ("Specification", (SPECIFICATION_COLUMN,)),
]


def _first_nonempty(row: dict, columns: tuple[str, ...]) -> str:
    for column in columns:
        value = row.get(column, "").strip()
        if value:
            return value
    return ""


_PLAIN_DECIMAL = re.compile(r"^-?\d+\.\d+$")


def _swedish_decimal(value: str) -> str:
    """Displays a plain dotted decimal number ("12.3") with the Swedish comma ("12,3")
    instead, matching config.py's PDF-side formatting - a no-op for anything that isn't
    unambiguously a decimal number, so it's safe to apply to arbitrary CSV field text
    without risking mangling something else (a URL, an account number, etc).
    """
    return value.replace(".", ",") if _PLAIN_DECIMAL.match(value) else value


_state_lock = threading.Lock()
_running: str | None = None
_results: dict[str, dict] = {}


def _record(name: str, ok: bool, message: str) -> None:
    global _running
    with _state_lock:
        _results[name] = {
            "ok": ok,
            "message": message,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _running = None


def _run_and_record(name: str, body: Callable[[], str | None]) -> None:
    """body() performs the run and returns a failure message, or None on success.
    Anything other than SystemExit/AlreadyRunningError is a real bug (same "only catch
    what's expected" rule cli.py follows) - it's still recorded so the UI doesn't stay
    stuck on "running" forever, but then re-raised so the traceback isn't swallowed.
    """
    try:
        message = body()
    except AlreadyRunningError as e:
        _record(name, False, str(e))
        return
    except BaseException:
        _record(name, False, "Crashed unexpectedly - see the terminal/logs for details.")
        raise
    _record(name, message is None, message or "Done.")


def _worker(name: str, fn: Callable[[], None]) -> None:
    def body():
        try:
            with run_lock():
                fn()
        except SystemExit:
            return "Failed - see logs for details."
        return None

    _run_and_record(name, body)


def _worker_all() -> None:
    def body():
        with run_lock():
            failed = _run_pipeline_stages()
        if failed:
            return f"Failed stages: {', '.join(failed)} - see logs for details."
        return None

    _run_and_record("run", body)


def _worker_row_all(row_key: str) -> None:
    """download -> process -> generate for one row, no fetch (same rationale as the
    CLI's scoped `run <row_key>`: a row can only appear here from an already-fetched
    sheet, so re-pulling the whole sheet again would be disproportionate).
    """
    def body():
        with run_lock():
            failed = _run_pipeline_stages(row_key)
        if failed:
            return f"Failed stages: {', '.join(failed)} - see logs for details."
        return None

    _run_and_record(f"run:{row_key}", body)


def _worker_remove(what: str, row_key: str) -> None:
    """Deletes on-disk file(s) for one row - never the CSV/database/entries list. `what`
    is "all" or one of REMOVE_TARGETS' keys; routed through the same run_lock() as every
    other mutating action so it can't race a concurrent download/process/generate.
    """
    def body():
        with run_lock():
            if what == "all":
                remove_all_files(row_key)
            else:
                REMOVE_TARGETS[what](row_key)
        return None

    _run_and_record(f"remove-{what}:{row_key}", body)


def _worker_clean_all() -> None:
    """Wipes the entire database and every downloaded/processed/generated file - a
    full pipeline reset, not a per-row action - through the same run_lock() as
    everything else so it can't race a concurrent stage run.
    """
    def body():
        with run_lock():
            clean_all_data()
        return None

    _run_and_record("clean-all", body)


def _find_csv_row(row_key: str) -> dict | None:
    for row in read_rows():
        if row.get(TIMESTAMP_COLUMN, "").strip() == row_key:
            return row
    return None


def _has_extension(path: str | None, extensions: tuple[str, ...]) -> bool:
    return bool(path) and os.path.splitext(path)[1].lower() in extensions


def _entry_view(row_key: str, conn) -> dict | None:
    """Builds everything the entry detail template needs for one row: the shared
    status.row_status() classification (already correctly listing one entry per receipt
    link the row currently has, and whether the final PDF actually exists on disk right
    now), plus - unlike that summary - the actual file paths/extensions needed to link
    to or preview each attachment, and every other non-empty CSV field for this row.
    """
    csv_row = _find_csv_row(row_key)
    if csv_row is None:
        return None
    info = status.row_status(conn, csv_row)
    by_index = {a["link_index"]: a for a in db.list_attachments(conn, row_key)}
    view_attachments = []
    for a in info["attachments"]:
        att = by_index.get(a["link_index"])
        view_attachments.append({
            **a,
            "download_ext": os.path.splitext(att["download_path"])[1].lstrip(".").upper() if a["downloaded_ok"] else "",
            "download_is_image": a["downloaded_ok"] and _has_extension(att["download_path"], DOWNLOAD_IMAGE_EXTENSIONS),
            "processed_is_image": a["processed_ok"] and _has_extension(att["processed_path"], PROCESSED_IMAGE_EXTENSIONS),
        })
    info["attachments"] = view_attachments
    info["fields"] = [
        (column, _swedish_decimal(str(value).strip())) for column, value in csv_row.items()
        if column != RECEIPT_LINKS_COLUMN and str(value).strip()
    ]
    return info


def _send_under(path: str | None, base_dir: str):
    """Serves a file the DB pointed us at, refusing anything that doesn't resolve inside
    the directory it's supposed to live in - defense in depth on top of the fact that
    `path` here always comes from a DB lookup, never directly from the request.
    """
    if not path:
        abort(404)
    real_base = os.path.realpath(base_dir)
    real_path = os.path.realpath(path)
    if os.path.commonpath([real_base, real_path]) != real_base or not os.path.isfile(real_path):
        abort(404)
    return send_file(real_path)


def _start(name: str, target: Callable, *args) -> bool:
    global _running
    with _state_lock:
        if _running is not None:
            return False
        _running = name
    threading.Thread(target=target, args=args, daemon=True).start()
    return True


def create_app() -> Flask:
    ensure_dirs()
    app = Flask(__name__)

    @app.get("/")
    def index():
        with _state_lock:
            running, results = _running, dict(_results)
        with db.connect() as conn:
            rows_info = []
            for row in read_rows():
                info = status.row_status(conn, row)
                info["extra"] = [
                    _swedish_decimal(_first_nonempty(row, columns)) for _, columns in ENTRIES_LIST_COLUMNS
                ]
                rows_info.append(info)
        extra_headers = [label for label, _ in ENTRIES_LIST_COLUMNS]
        return render_template(
            "index.html", stages=PIPELINE_ORDER, running=running, results=results,
            entries=rows_info, extra_headers=extra_headers,
        )

    @app.post("/run/<stage>")
    def run_stage(stage: str):
        if stage not in STAGES:
            return jsonify(error="unknown stage"), 404
        started = _start(stage, _worker, stage, STAGES[stage])
        return jsonify(started=started)

    @app.post("/run-all")
    def run_all():
        started = _start("run", _worker_all)
        return jsonify(started=started)

    @app.post("/clean-all")
    def clean_all_route():
        started = _start("clean-all", _worker_clean_all)
        return jsonify(started=started)

    @app.get("/api/status")
    def api_status():
        with _state_lock:
            return jsonify(running=_running, results=_results)

    @app.get("/entries/<path:row_key>")
    def entry_detail(row_key: str):
        with db.connect() as conn:
            info = _entry_view(row_key, conn)
        if info is None:
            abort(404)
        with _state_lock:
            running, results = _running, dict(_results)
        return render_template(
            "entry_detail.html", row_key=row_key, info=info, running=running, results=results,
        )

    @app.get("/entries/<path:row_key>/download/<int:link_index>")
    def entry_download_file(row_key: str, link_index: int):
        with db.connect() as conn:
            att = db.get_attachment(conn, row_key, link_index)
        return _send_under(att["download_path"] if att else None, DOWNLOADS_DIR)

    @app.get("/entries/<path:row_key>/processed/<int:link_index>")
    def entry_processed_file(row_key: str, link_index: int):
        with db.connect() as conn:
            att = db.get_attachment(conn, row_key, link_index)
        return _send_under(att["processed_path"] if att else None, PROCESSED_DIR)

    @app.get("/entries/<path:row_key>/final")
    def entry_final_file(row_key: str):
        with db.connect() as conn:
            db_row = db.get_row(conn, row_key)
        rel = db_row["final_pdf_path"] if db_row else None
        return _send_under(os.path.join(FINAL_DIR, rel) if rel else None, FINAL_DIR)

    @app.post("/entries/<path:row_key>/run/<stage>")
    def entry_run_stage(row_key: str, stage: str):
        if stage not in SCOPABLE_STAGES:
            return jsonify(error="unknown or unscopable stage"), 404
        name = f"{stage}:{row_key}"
        started = _start(name, _worker, name, functools.partial(STAGES[stage], row_key))
        return jsonify(started=started)

    @app.post("/entries/<path:row_key>/run-all")
    def entry_run_all(row_key: str):
        started = _start(f"run:{row_key}", _worker_row_all, row_key)
        return jsonify(started=started)

    @app.post("/entries/<path:row_key>/remove/<what>")
    def entry_remove(row_key: str, what: str):
        if what not in REMOVE_TARGETS and what != "all":
            return jsonify(error="unknown remove target"), 404
        started = _start(f"remove-{what}:{row_key}", _worker_remove, what, row_key)
        return jsonify(started=started)

    return app


def main() -> None:
    create_app().run(host=WEBUI_HOST, port=WEBUI_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
