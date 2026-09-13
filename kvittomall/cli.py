"""Single entry point: `python -m kvittomall <command>`."""

import argparse
import sys

from kvittomall import drive, media, pdf_gen, sheet, status
from kvittomall.lock import AlreadyRunningError, run_lock
from kvittomall.logging_setup import run_timer, setup_logging
from kvittomall.paths import ensure_dirs

STAGES = {
    "fetch": sheet.run,
    "download": drive.run,
    "process": media.run,
    "generate": pdf_gen.run,
    "handled": pdf_gen.run_handled,
}

# Each removes one kind of on-disk file for a single row - never the CSV/database/entry
# list, and never bulk (no-row-key) - see cli.py's `remove` subcommand.
REMOVE_TARGETS = {
    "downloads": drive.remove_downloads,
    "processed": media.remove_processed,
    "final": pdf_gen.remove_final,
}


def remove_all_files(row_key: str) -> dict[str, int | bool]:
    return {name: fn(row_key) for name, fn in REMOVE_TARGETS.items()}

# "handled" is deliberately excluded: it's a separate, explicit review action, never
# part of the automatic fetch -> download -> process -> generate pipeline or "run all".
PIPELINE_ORDER = ["fetch", "download", "process", "generate"]

# fetch always pulls the whole sheet - there's no cheaper single-row fetch - so it's the
# only pipeline stage that never accepts a row_key filter. "handled" also accepts one
# (scoping to a single row, or bulk when omitted) even though it isn't a pipeline stage.
SCOPABLE_STAGES = {"download", "process", "generate", "handled"}

logger = setup_logging("run")


def _run_pipeline_stages(only: str | None = None) -> list[str]:
    """Runs every stage in order even if an earlier one fails, since each stage works
    from whatever local state (responses.csv, downloads/, the database) already exists
    - e.g. a failed fetch shouldn't stop generate from rebuilding a PDF that was deleted
    but whose data was already downloaded and processed in an earlier run. Every stage
    logs its own detailed failure reason before raising SystemExit, so nothing is lost
    by continuing past it here.

    When `only` is given, fetch is skipped entirely (it can't be scoped to one row, and
    a row can only have been picked in the first place from an already-fetched sheet) -
    just download -> process -> generate run for that one row.
    """
    failed = []
    for name in PIPELINE_ORDER:
        if only is not None and name not in SCOPABLE_STAGES:
            continue
        try:
            STAGES[name](only) if name in SCOPABLE_STAGES else STAGES[name]()
        except SystemExit:
            failed.append(name)
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kvittomall", description="Expense receipt PDF pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("fetch", help="download and validate the Google Sheet -> responses.csv")

    row_key_help = (
        "optional row key (a row's raw timestamp, as shown in `status` or the web UI's "
        "entries list) to scope this to a single entry instead of every row"
    )
    download_parser = subparsers.add_parser("download", help="download receipt attachments from Drive -> downloads/")
    download_parser.add_argument("row_key", nargs="?", default=None, help=row_key_help)
    process_parser = subparsers.add_parser("process", help="normalize/compress attachments -> processed/")
    process_parser.add_argument("row_key", nargs="?", default=None, help=row_key_help)
    generate_parser = subparsers.add_parser("generate", help="build final PDFs -> final/<category>/ (use `handled` to move a reviewed one out)")
    generate_parser.add_argument("row_key", nargs="?", default=None, help=row_key_help)
    run_parser = subparsers.add_parser("run", help="fetch -> download -> process -> generate")
    run_parser.add_argument(
        "row_key", nargs="?", default=None,
        help=row_key_help + " (skips fetch when given - download/process/generate only)",
    )
    handled_parser = subparsers.add_parser(
        "handled",
        help="toggle a row's handled state (final/<category>/ <-> final/handled/<category>/); "
             "with no row key, marks every currently-unhandled row as handled",
    )
    handled_parser.add_argument(
        "row_key", nargs="?", default=None,
        help="row key to toggle handled/unhandled; omit to mark every currently-unhandled row",
    )
    remove_parser = subparsers.add_parser(
        "remove",
        help="delete on-disk file(s) for one entry - never the CSV/database/entry list "
             "(a later run just redownloads/reprocesses/regenerates what's missing)",
    )
    remove_parser.add_argument("row_key", help="row key of the entry to remove files for")
    remove_parser.add_argument(
        "--only", choices=sorted(("downloads", "processed", "final")), default=None,
        help="remove only this kind of file; omit to remove all three",
    )
    subparsers.add_parser("status", help="read-only status report (no lock)")
    subparsers.add_parser("webui", help="start the local web UI (http://127.0.0.1:5000)")

    args = parser.parse_args(argv)
    ensure_dirs()

    if args.command == "status":
        status.run()
        return 0

    if args.command == "webui":
        # Imported here, not at module level, since kvittomall.web.app imports STAGES/
        # PIPELINE_ORDER back from this module - a top-level import would be circular.
        from kvittomall.web.app import main as run_webui

        run_webui()
        return 0

    try:
        with run_lock():
            if args.command == "run":
                label = "full pipeline run" if args.row_key is None else f"pipeline run for row {args.row_key}"
                with run_timer(logger, label):
                    failed_stages = _run_pipeline_stages(args.row_key)
                if failed_stages:
                    print(
                        f"These stages failed (see logs for details): {', '.join(failed_stages)}. "
                        "Later stages still ran against whatever data was already available.",
                        file=sys.stderr,
                    )
                    return 1
            elif args.command == "remove":
                if args.only:
                    removed = REMOVE_TARGETS[args.only](args.row_key)
                    print(f"Removed {args.only} for {args.row_key}: {removed}")
                else:
                    print(f"Removed for {args.row_key}: {remove_all_files(args.row_key)}")
            elif args.command in SCOPABLE_STAGES:
                STAGES[args.command](args.row_key)
            else:
                STAGES[args.command]()
    except AlreadyRunningError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
