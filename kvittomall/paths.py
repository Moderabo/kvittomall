"""Central definition of every path the pipeline reads or writes."""

import os
import shutil

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

# Web-UI-edited user preferences (the PDF cover-page layout, the sheet column mapping),
# not pipeline-generated state - deliberately outside DATA_DIR/FINAL_DIR so clean_all()
# below never touches it. In its own directory rather than directly in ROOT (like
# .env/the logo) because atomic_write()'s rename-into-place needs its target file to
# sit inside an ordinary directory: a container runtime bind-mounting the *file itself*
# (to persist it, e.g. under Docker) makes that rename fail outright (verified: raises
# "Device or resource busy") - bind-mounting the containing directory instead works
# correctly, which only helps if these files actually live in a directory of their own.
CONFIG_DIR = os.path.join(ROOT, "config")
PDF_LAYOUT_PATH = os.path.join(CONFIG_DIR, "pdf_layout.json")


def ensure_dirs() -> None:
    for path in (DATA_DIR, DOWNLOADS_DIR, PROCESSED_DIR, FINAL_DIR, LOGS_DIR, CONFIG_DIR):
        os.makedirs(path, exist_ok=True)


def clean_all() -> None:
    """Deletes everything under DATA_DIR and FINAL_DIR - the database, downloads,
    processed files, responses.csv, the lock file, and every generated/handled PDF -
    then recreates the empty directory layout via ensure_dirs(), leaving the same state
    as a machine that has never run the pipeline. logs/ is untouched (it's the audit
    trail, not pipeline state, and deliberately kept easy to check independently); .env
    and the service-account key live outside both directories and are untouched too.
    """
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    shutil.rmtree(FINAL_DIR, ignore_errors=True)
    ensure_dirs()
