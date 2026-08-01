"""Central definition of every path the pipeline reads or writes."""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESPONSES_CSV = os.path.join(ROOT, "responses.csv")
DOWNLOADS_DIR = os.path.join(ROOT, "downloads")
PROCESSED_DIR = os.path.join(ROOT, "processed")
FINAL_DIR = os.path.join(ROOT, "final")
LOGS_DIR = os.path.join(ROOT, "logs")
DB_PATH = os.path.join(ROOT, "kvittomall_state.db")
LOCK_PATH = os.path.join(ROOT, "kvittomall.lock")
LOGO_PATH = os.path.join(ROOT, "logo.png")


def ensure_dirs() -> None:
    for path in (DOWNLOADS_DIR, PROCESSED_DIR, FINAL_DIR, LOGS_DIR):
        os.makedirs(path, exist_ok=True)
