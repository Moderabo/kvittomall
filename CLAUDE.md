# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Automates creating expense-receipt PDF reports ("kvittomallar") for a student committee. Pulls submissions from a Google Sheet (backed by a Google Form), downloads the linked receipt images/PDFs from Google Drive, and generates one combined PDF per submission (cover page + attachments).

## Commands

```sh
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt        # versions are pinned intentionally - keep them pinned

python -m kvittomall run               # fetch -> download -> process -> generate, in order
python -m kvittomall fetch             # Sheet -> data/responses.csv
python -m kvittomall download          # Drive links in data/responses.csv -> data/downloads/
python -m kvittomall process           # data/downloads/ -> data/processed/ (adaptive JPEG compression)
python -m kvittomall generate          # data/processed/ + data/responses.csv -> final/<category>/
python -m kvittomall status            # read-only report of per-row/attachment state, no lock taken
```

There is no test suite and no linter/formatter configured in this repo - don't invent commands for either. `python3 -m py_compile kvittomall/*.py` and `pyflakes kvittomall/*.py` are reasonable ad-hoc sanity checks after edits.

System dependency: `libmagic` (`sudo apt-get install libmagic-dev` on Debian/Ubuntu/WSL, `brew install libmagic` on macOS) - `python-magic` needs it to detect file types since Drive downloads don't preserve extensions.

Requires Python 3.10+ (the codebase uses `X | None` union-type syntax throughout).

## Architecture

Single package (`kvittomall/`), one module per pipeline stage plus shared infrastructure. `kvittomall/cli.py` is the only entry point (`python -m kvittomall <command>`); each stage is also a plain function (`sheet.run()`, `drive.run()`, `media.run()`, `pdf_gen.run()`) that can be called directly.

**The state store is the load-bearing piece.** `kvittomall/db.py` holds a SQLite database (`data/kvittomall_state.db`, gitignored) with two tables: `rows` (one row per sheet submission, keyed by its raw `Tidstämpel` timestamp - the one genuinely stable identifier, since a submitter's name can be corrected later) and `attachments` (one row per receipt link within a submission). Every stage treats "is this done?" as a **fail-safe check, never a trusted flag**: a row/attachment only counts as done when the DB says so *and* the recorded file still exists on disk with the expected size *and* a content hash confirms the source sheet row hasn't changed since. Any mismatch resets that item to pending and gets logged with the specific reason - see `db.classify_generated()` and `db.is_download_valid()`/`is_process_valid()`. This is why re-running any stage after an interruption, a deleted file, or an edited sheet row is always safe and never redoes more than necessary.

**Sheet/Drive access has two interchangeable backends.** `kvittomall/google_api.py` holds everything shared between them: `get_access_mode()` reads `ACCESS_MODE` from `.env` (`"api"`, `"public"`, or unset/`"auto"`), and `describe()` turns a raw Google/network exception into a plain-language reason (contextualized by which method hit it, since the same HTTP status means something different on each). `sheet.py` and `drive.py` each implement both a service-account API path and the original anonymous-link path, and pick between them the same way: `"public"`/`"api"` use only that method (the latter raising outright if it fails), `"auto"` tries the API and falls back to the public method with a logged `WARNING` if it fails. `drive.py` decides this once per `download` run (one cheap API call), not per file, since a broken key/share affects every file identically.

**`kvittomall run` keeps going after a stage fails.** `cli.py`'s `_run_pipeline_stages()` runs all four stages regardless of earlier failures - e.g. a `fetch` that can't reach Google shouldn't stop `generate` from rebuilding a PDF whose data was already downloaded and processed in an earlier run. This only catches `SystemExit`, the deliberate signal every stage uses to end itself (always logged via that stage's own logger first); an unexpected exception is a real bug and is left to propagate. Failed stages are reported together at the end, with a nonzero exit code.

**Writes are atomic.** `kvittomall/atomic.py`'s `atomic_write()` writes to a temp file in the same directory, validates it (page count, image integrity, MIME type depending on stage), then `os.replace()`s it into place - only after that does the DB get updated. A process killed mid-write never leaves a half-written file mistaken for a finished one.

**`final/` output layout is intentional, not incidental.** `final/<category>/` (`privat/`, `sektionskort/`, `övrigt/` - see `config.category_for()`, keyed off the sheet's `Transaktionstyp` column) always holds only the most recently generated/updated batch, so it's obvious what's new. Before writing anything new into a category, `pdf_gen._sweep_category()` archives whatever's currently there into `final/previous/<category>/` - but only for categories that are actually about to receive something new, and never for a fully idle run. A row whose content is unchanged but whose file went missing (accidental deletion) is treated as a repair and restored to wherever it already was, not surfaced as new - see the `"repair"` vs `"stale"` distinction in `db.classify_generated()` and how `pdf_gen.run()` branches on it.

**PDF content is config-driven.** `kvittomall/config.py`'s `PDF_SECTIONS` (a list of `Section`/`Field` dataclasses) defines the cover page's layout - each `Field` maps a label to a sheet column and an optional value formatter (e.g. the currency suffix on `Summa`). Changing what appears on the cover page means editing this structure, not `pdf_gen.py`'s rendering code.

**Logging**: `kvittomall/logging_setup.py` gives every stage a logger writing to `logs/{stage}-{year}-{month}.log` (Swedish/`Europe/Stockholm` timestamps regardless of host timezone, monthly files so they don't grow forever). Console output is WARNING-and-above by design - routine progress is a live progress bar (`kvittomall/progress.py`), coordinated with the logging handler so a warning/error print doesn't get mangled mid-bar.

**`data/` (downloads/, processed/, responses.csv, kvittomall_state.db, the lock file), plus `final/`, `logs/`, `.env`, and any `*.json` (service account key), are all gitignored runtime state - none of it is precious.** `data/` holds everything the pipeline owns and rebuilds itself (see `paths.py`); it's kept separate from `final/` (the actual deliverable output) and `logs/` (kept easy to check without digging in), both of which stay in the repo root. The Google Sheet is the source of truth; any of this can be deleted and rebuilt with `python -m kvittomall run` (existing valid downloads/processed files get adopted rather than redone, per the fail-safe checks above).

## Module map

| Module | Responsibility |
|---|---|
| `paths.py` | All path constants, `ensure_dirs()` |
| `config.py` | Sheet column names, `PDF_SECTIONS`, category mapping |
| `rowkey.py` | Row identity, filename sanitization, content/link hashing, DB sync (incl. renaming files in place if a submitter's name changes) |
| `db.py` | Schema + all state read/write, including the fail-safe validity checks |
| `atomic.py` | `atomic_write()` |
| `lock.py` | Single-instance file lock for mutating commands |
| `google_api.py` | `ACCESS_MODE` handling, service-account credential/service builders, error-message translation - shared by `sheet.py` and `drive.py` |
| `sheet.py` | Stage 1 (fetch/validate CSV, via API or public export) + `read_rows()`, used by every later stage |
| `drive.py` | Stage 2 (download, via API or public link), per-attachment state, adopts pre-existing downloads |
| `media.py` | Stage 3 (adaptive JPEG compression / PDF passthrough), adopts pre-existing processed files |
| `pdf_gen.py` | Stage 4 (cover page + attachment merge, category sweep/archive logic) |
| `status.py` | Read-only status report |
| `cli.py` / `__main__.py` | Argument parsing and dispatch |
