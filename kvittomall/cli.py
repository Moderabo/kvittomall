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
}

PIPELINE_ORDER = ["fetch", "download", "process", "generate"]

logger = setup_logging("run")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kvittomall", description="Expense receipt PDF pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("fetch", help="download and validate the Google Sheet -> responses.csv")
    subparsers.add_parser("download", help="download receipt attachments from Drive -> downloads/")
    subparsers.add_parser("process", help="normalize/compress attachments -> processed/")
    subparsers.add_parser("generate", help="build final PDFs -> final/<Nya kvitton | category>/")
    subparsers.add_parser("run", help="fetch -> download -> process -> generate")
    subparsers.add_parser("status", help="read-only status report (no lock)")

    args = parser.parse_args(argv)
    ensure_dirs()

    if args.command == "status":
        status.run()
        return 0

    try:
        with run_lock():
            if args.command == "run":
                with run_timer(logger, "full pipeline run"):
                    for name in PIPELINE_ORDER:
                        STAGES[name]()
            else:
                STAGES[args.command]()
    except AlreadyRunningError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
