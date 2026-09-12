"""Stage 4: build each row's final PDF - a cover page from PDF_SECTIONS followed by
its receipt attachments.

final/<category>/ holds every generated PDF that hasn't been reviewed yet - it's
cumulative, not a "latest batch" folder. A row only leaves it when a human explicitly
marks it handled (toggle_handled()/mark_all_handled(), driven by `kvittomall handled`),
moving it to final/<HANDLED_DIRNAME>/<category>/ - running `handled` again on an already-
handled row moves it back (see toggle_handled()). If a handled row's content later
changes, Pass 1 below naturally regenerates it back into the active category folder and
clears its handled flag (see db.classify_generated and the `handled` column in db.py).

A row whose content hasn't changed but whose recorded PDF went missing from disk (e.g.
accidentally deleted) is a repair, not new output: it's restored to wherever it already
was - category folder or handled/ - rather than being surfaced as "new" again.
"""

import os
from functools import lru_cache
from io import BytesIO

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from svglib.svglib import svg2rlg

from kvittomall import db
from kvittomall.atomic import atomic_write
from kvittomall.config import HANDLED_DIRNAME, PDF_SECTIONS, PDF_TITLE_PREFIX, TRANSACTION_TYPE_COLUMN, category_for
from kvittomall.logging_setup import run_timer, setup_logging
from kvittomall.paths import FINAL_DIR, LOGO_PATH
from kvittomall.progress import ProgressBar
from kvittomall.rowkey import content_hash, receipt_links, sync_row
from kvittomall.sheet import read_rows

logger = setup_logging("generate")

FONT_BOLD = "Helvetica-Bold"
FONT_NORMAL = "Helvetica"
HEADER_FONT_SIZE = 28
BODY_FONT_SIZE = 14
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_TOP = 4 * cm
MARGIN_SIDE = 2 * cm
LOGO_MARGIN = 1.5 * cm
LOGO_SIZE = 3 * cm
PAGE_BREAK_THRESHOLD = 4 * cm
IMAGE_AREA_BOTTOM_MARGIN = 2 * cm
LABEL_WIDTH = 4 * cm
LINE_HEIGHT = 0.7 * cm
PADDING = 0.2 * cm
HEADER_HEIGHT = 3 * cm


def _wrap_text(c: canvas.Canvas, text: str, max_width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test_line = f"{current} {word}".strip()
        if c.stringWidth(test_line, c._fontname, c._fontsize) <= max_width:
            current = test_line
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@lru_cache(maxsize=1)
def _load_svg_logo(path: str):
    """Cached since it's re-drawn on every page - the parsed drawing is never mutated
    by _draw_svg_logo (scaling happens via the canvas's transform stack instead), so
    it's safe to reuse across calls.
    """
    return svg2rlg(path)


def _draw_svg_logo(c: canvas.Canvas, path: str, x: float, y: float, size: float) -> None:
    drawing = _load_svg_logo(path)
    scale = size / max(drawing.width, drawing.height)
    c.saveState()
    c.translate(x, y)
    c.scale(scale, scale)
    renderPDF.draw(drawing, c, 0, 0)
    c.restoreState()


def _draw_page_header(c: canvas.Canvas, title: str) -> None:
    if os.path.exists(LOGO_PATH):
        x = PAGE_WIDTH - LOGO_SIZE - LOGO_MARGIN
        y = PAGE_HEIGHT - LOGO_SIZE - LOGO_MARGIN
        if LOGO_PATH.lower().endswith(".svg"):
            _draw_svg_logo(c, LOGO_PATH, x, y, LOGO_SIZE)
        else:
            c.drawImage(
                ImageReader(LOGO_PATH),
                x, y,
                width=LOGO_SIZE, height=LOGO_SIZE,
                preserveAspectRatio=True, mask="auto",
            )
    c.setFont(FONT_BOLD, HEADER_FONT_SIZE)
    c.drawString(MARGIN_SIDE, PAGE_HEIGHT - MARGIN_TOP, title)


def _page_title(row: dict) -> str:
    return f"{PDF_TITLE_PREFIX}{row.get(TRANSACTION_TYPE_COLUMN, '')}"


def _build_text_page(row: dict) -> PdfReader:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _draw_page_header(c, _page_title(row))

    y = PAGE_HEIGHT - 5 * cm
    max_text_width = PAGE_WIDTH - (2 * MARGIN_SIDE)

    for section in PDF_SECTIONS:
        non_empty = []
        for f in section.fields:
            raw = str(row.get(f.column, "")).strip()
            if raw:
                non_empty.append((f.label, f.formatter(raw)))
        if not non_empty:
            continue

        c.line(MARGIN_SIDE, y, MARGIN_SIDE + max_text_width, y)
        for label, value in non_empty:
            value_lines = _wrap_text(c, value, max_text_width - LABEL_WIDTH - PADDING)
            c.setFont(FONT_BOLD, BODY_FONT_SIZE)
            c.drawString(MARGIN_SIDE, y - LINE_HEIGHT, label)
            c.setFont(FONT_NORMAL, BODY_FONT_SIZE)
            for i, line in enumerate(value_lines):
                c.drawString(MARGIN_SIDE + LABEL_WIDTH, y - LINE_HEIGHT * (i + 1), line)
            y -= LINE_HEIGHT * max(len(value_lines), 1)
        y -= PADDING
        c.line(MARGIN_SIDE, y, MARGIN_SIDE + max_text_width, y)
        y -= 0.5 * cm
        if y < PAGE_BREAK_THRESHOLD:
            c.showPage()
            y = PAGE_HEIGHT - 2 * cm

    c.showPage()
    c.save()
    buffer.seek(0)
    return PdfReader(buffer)


def _scale_pdf_to_a4(input_pdf_path: str, title: str) -> PdfReader:
    """Scales an attachment PDF's pages to fit below the header, preserving vector quality."""
    input_pdf = PdfReader(input_pdf_path)
    writer = PdfWriter()
    content_width = PAGE_WIDTH - (2 * MARGIN_SIDE)
    content_height = PAGE_HEIGHT - MARGIN_TOP - IMAGE_AREA_BOTTOM_MARGIN - HEADER_HEIGHT

    for page in input_pdf.pages:
        original_width = float(page.mediabox.width)
        original_height = float(page.mediabox.height)
        scale = min(content_width / original_width, content_height / original_height)
        scaled_width, scaled_height = original_width * scale, original_height * scale
        tx = MARGIN_SIDE + (content_width - scaled_width) / 2
        ty = PAGE_HEIGHT - MARGIN_TOP - HEADER_HEIGHT - scaled_height

        header_buffer = BytesIO()
        c = canvas.Canvas(header_buffer, pagesize=A4)
        _draw_page_header(c, title)
        c.save()
        header_page = PdfReader(header_buffer).pages[0]

        # pypdf wants a page attached to its writer *before* its content is transformed
        # or merged - doing either on an orphan PageObject is deprecated (pypdf >=6) as
        # unreliable. merge_transformed_page() applies the transform as part of the
        # merge itself, so the source `page` never needs add_transformation() called on
        # it directly - only the already-attached `added_page` (A4-sized, from the
        # header canvas) gets mutated, which is why it keeps the A4 mediabox regardless
        # of the attachment's original page size.
        added_page = writer.add_page(header_page)
        added_page.merge_transformed_page(page, Transformation().scale(scale).translate(tx, ty))

    buffer = BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return PdfReader(buffer)


def _image_page(image_path: str, title: str) -> PdfReader:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _draw_page_header(c, title)

    img = ImageReader(image_path)
    iw, ih = img.getSize()
    max_img_width = PAGE_WIDTH - (2 * MARGIN_SIDE)
    max_img_height = PAGE_HEIGHT - MARGIN_TOP - IMAGE_AREA_BOTTOM_MARGIN - HEADER_HEIGHT
    scale = min(max_img_width / iw, max_img_height / ih, 1.0)
    width, height = iw * scale, ih * scale
    x = MARGIN_SIDE
    y = PAGE_HEIGHT - MARGIN_TOP - HEADER_HEIGHT - height
    c.drawImage(img, x, y, width=width, height=height)
    c.showPage()
    c.save()
    buffer.seek(0)
    return PdfReader(buffer)


def _build_final_pdf(row: dict, attachment_paths: list[str]) -> bytes:
    writer = PdfWriter()
    for page in _build_text_page(row).pages:
        writer.add_page(page)

    title = _page_title(row)
    for path in attachment_paths:
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in (".jpg", ".jpeg"):
                reader = _image_page(path, title)
            elif ext == ".pdf":
                reader = _scale_pdf_to_a4(path, title)
            else:
                logger.warning(f"Skipping attachment with unsupported extension: {path}")
                continue
            for page in reader.pages:
                writer.add_page(page)
        except Exception as e:
            logger.error(f"Failed to add attachment {path}: {e}", exc_info=True)

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _attachments_ready(conn, row_key: str, row: dict) -> tuple[bool, list[str]]:
    """A row is only ready to generate once every one of its current receipt links has
    a validated processed file. If links exist but processing hasn't caught up yet
    (e.g. `generate` was run before `download`/`process`), this returns not-ready
    rather than letting the PDF be built missing attachments it should have.
    """
    links = receipt_links(row)
    if not links:
        return True, []

    attachments = db.list_attachments(conn, row_key)
    if len(attachments) < len(links):
        return False, []

    paths = []
    for attachment in attachments:
        valid, _ = db.is_process_valid(attachment)
        if not valid:
            return False, []
        paths.append(attachment["processed_path"])
    return True, paths


def _generate_one(
    conn, row_key: str, row: dict, dest_relpath: str, attachment_paths: list[str], handled: bool = False,
) -> None:
    dst_path = os.path.join(FINAL_DIR, dest_relpath)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    min_expected_pages = 1 + len(attachment_paths)  # lower bound: cover page + 1/attachment

    def writer(tmp_path: str) -> None:
        with open(tmp_path, "wb") as f:
            f.write(_build_final_pdf(row, attachment_paths))

    def validate(tmp_path: str) -> None:
        page_count = len(PdfReader(tmp_path).pages)
        if page_count < min_expected_pages:
            raise ValueError(f"expected at least {min_expected_pages} pages, got {page_count}")

    atomic_write(dst_path, writer, validate)

    db.mark_generated(conn, row_key, dest_relpath, os.path.getsize(dst_path), content_hash(row), handled)
    logger.info(f"Generated {dst_path}")


def run(only: str | None = None) -> None:
    rows = read_rows()
    with db.connect() as conn, run_timer(logger, "generate" if only is None else f"generate (row {only})"):
        # --- Pass 1: work out what needs (re)generating, without writing anything yet ---
        # row_key, row, dest, attachments, reason, stale_path (the old location to remove
        # once the new one is written, e.g. a handled/ copy superseded by a content
        # change), handled (what `handled` should be set to once (re)generated)
        plan: list[tuple[str, dict, str, list[str], str, str | None, bool]] = []

        for i, row in enumerate(rows):
            row_key = sync_row(conn, row)
            if row_key is None:
                logger.warning(f"Row {i} is missing timestamp/name, skipping PDF generation")
                continue
            if only is not None and row_key != only:
                continue

            db_row = db.get_row(conn, row_key)
            current_hash = content_hash(row)
            kind, reason = db.classify_generated(db_row, current_hash, FINAL_DIR)
            if kind == "valid":
                continue

            ready, attachment_paths = _attachments_ready(conn, row_key, row)
            if not ready:
                logger.warning(
                    f"Row {i} ({db_row['base_filename']}): attachments not fully downloaded/processed "
                    f"yet, skipping PDF generation. Run 'download' and 'process' first."
                )
                continue

            stale_path = None
            if kind == "repair":
                dest = db_row["final_pdf_path"]  # restore in place, wherever it already was
                handled_value = bool(db_row["handled"])  # nothing conceptually changed
            else:
                # Covers both a brand-new row and one whose content changed after being
                # marked handled - either way it belongs back in the active category
                # folder, not wherever it used to be (see db.classify_generated), and
                # needs fresh review. If it used to live elsewhere (e.g. handled/), that
                # old copy is now superseded.
                category = category_for(row)
                dest = os.path.join(category, f"{db_row['base_filename']}.pdf")
                if db_row["final_pdf_path"] and db_row["final_pdf_path"] != dest:
                    stale_path = db_row["final_pdf_path"]
                handled_value = False

            plan.append((row_key, row, dest, attachment_paths, reason, stale_path, handled_value))

        # --- Pass 2: actually generate ---
        with ProgressBar(len(plan), "Generating") as bar:
            for row_key, row, dest, attachment_paths, reason, stale_path, handled_value in plan:
                logger.info(f"Generating {dest} ({reason})")
                try:
                    _generate_one(conn, row_key, row, dest, attachment_paths, handled_value)
                    if stale_path:
                        stale_full_path = os.path.join(FINAL_DIR, stale_path)
                        if os.path.exists(stale_full_path):
                            os.remove(stale_full_path)
                            logger.info(f"Removed superseded copy at {stale_path}")
                except Exception as e:
                    db.set_row_error(conn, row_key, str(e))
                    logger.error(f"Failed to generate PDF for row {row_key}: {e}", exc_info=True)
                bar.update()


def _move_between(conn, db_row, to_handled: bool) -> str:
    """Moves one row's final PDF between final/<category>/ and
    final/<HANDLED_DIRNAME>/<category>/ to match `to_handled`, and records both the new
    path and the flip. `category` is always the last path segment before the filename,
    whether the current path is "category/x.pdf" or "handled/category/x.pdf" - so this
    works the same regardless of which direction we're moving. The caller is responsible
    for having already confirmed the move should happen (file exists on disk).
    """
    old_relpath = db_row["final_pdf_path"]
    category = os.path.basename(os.path.dirname(old_relpath))
    filename = os.path.basename(old_relpath)
    new_relpath = os.path.join(HANDLED_DIRNAME, category, filename) if to_handled else os.path.join(category, filename)

    dst_dir = os.path.join(FINAL_DIR, os.path.dirname(new_relpath))
    os.makedirs(dst_dir, exist_ok=True)
    os.replace(os.path.join(FINAL_DIR, old_relpath), os.path.join(FINAL_DIR, new_relpath))
    db.set_handled(conn, db_row["row_key"], to_handled, new_relpath)
    logger.info(f"Row {db_row['row_key']} is now {'handled' if to_handled else 'unhandled'}: {new_relpath}")
    return new_relpath


def toggle_handled(conn, row_key: str) -> bool:
    """Flips whether this row is marked handled, moving its PDF between
    final/<category>/ and final/<HANDLED_DIRNAME>/<category>/ to match. Returns the new
    handled state. Raises SystemExit (after logging the reason, same convention as
    drive._get_drive_service()/media.get_quality_preset()) if there's no generated PDF
    to toggle right now.
    """
    db_row = db.get_row(conn, row_key)
    if db_row is None or not db_row["final_pdf_path"]:
        logger.error(f"Cannot toggle handled for {row_key}: no generated PDF yet - run 'generate' first.")
        raise SystemExit(1)
    src = os.path.join(FINAL_DIR, db_row["final_pdf_path"])
    if not os.path.exists(src):
        logger.error(f"Cannot toggle handled for {row_key}: recorded final PDF is missing on disk: {src}")
        raise SystemExit(1)

    new_handled = not bool(db_row["handled"])
    _move_between(conn, db_row, new_handled)
    return new_handled


def mark_all_handled(conn) -> int:
    """Marks every currently-unhandled, successfully-generated row as handled - one
    directional only (never un-handles), unlike toggle_handled(). Rows that aren't ready
    yet (never generated) or are already handled are silently skipped - not failures,
    just not applicable. A row whose recorded PDF is missing from disk is logged as a
    warning and skipped rather than aborting the whole bulk operation, the same fail-safe
    "don't lose the rest of the run over one bad row" approach used elsewhere in this
    codebase (e.g. cli._run_pipeline_stages).
    """
    handled_count = 0
    for db_row in db.list_rows(conn):
        if not db_row["final_pdf_path"] or db_row["handled"]:
            continue
        if not os.path.exists(os.path.join(FINAL_DIR, db_row["final_pdf_path"])):
            logger.warning(
                f"Row {db_row['row_key']}'s recorded final PDF is missing on disk, skipping: {db_row['final_pdf_path']}"
            )
            continue
        _move_between(conn, db_row, True)
        handled_count += 1
    return handled_count


def remove_final(row_key: str) -> bool:
    """Deletes this row's final PDF from disk, wherever it currently sits - a
    disk-space/cleanup action only. Neither `status`, `handled`, nor `final_pdf_path` are
    touched: classify_generated() already re-verifies the file exists on disk before
    trusting "valid", so a missing file is picked up as a "repair" on the next `generate`
    and rebuilt in the exact same place (handled or not) - the same fail-safe path that
    already handles any other accidental deletion.
    """
    with db.connect() as conn:
        db_row = db.get_row(conn, row_key)
        if db_row is None or not db_row["final_pdf_path"]:
            return False
        path = os.path.join(FINAL_DIR, db_row["final_pdf_path"])
        if not os.path.exists(path):
            return False
        os.remove(path)
        logger.info(f"Removed final PDF for {row_key}: {path}")
        return True


def run_handled(only: str | None = None) -> int:
    """CLI/web entry point for `kvittomall handled [row_key]`: toggles one row's handled
    state (handled -> unhandled or vice versa), or - when `only` is None - marks every
    currently-unhandled generated row as handled (one directional; bulk never
    un-handles). Returns the number of rows affected (only meaningful for the bulk case -
    the single-row case either succeeds or raises SystemExit).
    """
    with db.connect() as conn, run_timer(logger, "handled" if only is None else f"handled (row {only})"):
        if only is not None:
            toggle_handled(conn, only)
            return 1
        return mark_all_handled(conn)
