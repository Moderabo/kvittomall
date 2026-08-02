"""Central definition of every path the pipeline reads or writes."""

import os

from kvittomall.config import LOGO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Working data the pipeline itself owns and rebuilds - kept out of the root so it
# doesn't clutter it. final/, logs/, and the logo file (config.LOGO) stay in the root:
# final/ is the actual deliverable output, logs/ is meant to be easy to check without
# digging in.
DATA_DIR = os.path.join(ROOT, "data")

RESPONSES_CSV = os.path.join(DATA_DIR, "responses.csv")
DOWNLOADS_DIR = os.path.join(DATA_DIR, "downloads")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
DB_PATH = os.path.join(DATA_DIR, "kvittomall_state.db")
LOCK_PATH = os.path.join(DATA_DIR, "kvittomall.lock")

FINAL_DIR = os.path.join(ROOT, "final")
LOGS_DIR = os.path.join(ROOT, "logs")
LOGO_PATH = os.path.join(ROOT, LOGO)


def ensure_dirs() -> None:
    for path in (DATA_DIR, DOWNLOADS_DIR, PROCESSED_DIR, FINAL_DIR, LOGS_DIR):
        os.makedirs(path, exist_ok=True)
