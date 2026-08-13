"""Stage 4: build each row's final PDF - a cover page from PDF_SECTIONS followed by
its receipt attachments.

final/<category>/ always holds only the latest generated/updated batch, so it's obvious
at a glance what's new. Before writing anything new into a category folder, whatever is
currently sitting there (the previous batch) is archived into
final/<PREVIOUS_DIRNAME>/<category>/ - but only for categories that are actually about
to receive something new this run; an otherwise-idle run touches nothing.

A row whose content hasn't changed but whose recorded PDF went missing from disk (e.g.
accidentally deleted) is a repair, not new output: it's restored to wherever it already
was - category folder or previous/ - rather than being surfaced as "new" again.
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
from kvittomall.config import (
    PDF_SECTIONS, PDF_TITLE_PREFIX, PREVIOUS_DIRNAME,
    RECEIPT_LINKS_COLUMN, TRANSACTION_TYPE_COLUMN, category_for,
)
from kvittomall.logging_setup import run_timer, setup_logging
from kvittomall.paths import FINAL_DIR, LOGO_PATH
from kvittomall.progress import ProgressBar
from kvittomall.rowkey import content_hash, sync_row
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
        output_page = PdfReader(header_buffer).pages[0]

        page.add_transformation(Transformation().scale(scale).translate(tx, ty))
        output_page.merge_page(page)
        writer.add_page(output_page)

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
    links = [link.strip() for link in row.get(RECEIPT_LINKS_COLUMN, "").split(",") if link.strip()]
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


def _generate_one(conn, row_key: str, row: dict, dest_relpath: str, attachment_paths: list[str]) -> None:
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

    db.mark_generated(conn, row_key, dest_relpath, os.path.getsize(dst_path), content_hash(row))
    logger.info(f"Generated {dst_path}")


def _sweep_category(conn, category: str) -> int:
    """Archives everything currently in final/<category>/ into final/previous/<category>/,
    ahead of a new batch taking its place. Only moves rows whose file actually exists on
    disk right now - a row mid-repair (its file is missing, being restored elsewhere this
    same run) is left alone so its restore isn't clobbered by this sweep.
    """
    prefix = category + "/"
    moved = 0
    for db_row in db.list_rows(conn):
        path = db_row["final_pdf_path"]
        if not path or not path.startswith(prefix):
            continue
        src = os.path.join(FINAL_DIR, path)
        if not os.path.exists(src):
            continue

        dst_dir = os.path.join(FINAL_DIR, PREVIOUS_DIRNAME, category)
        os.makedirs(dst_dir, exist_ok=True)
        filename = os.path.basename(path)
        dst = os.path.join(dst_dir, filename)

        os.replace(src, dst)
        db.set_final_pdf_path(conn, db_row["row_key"], os.path.join(PREVIOUS_DIRNAME, category, filename))
        moved += 1
    return moved


def run() -> None:
    rows = read_rows()
    with db.connect() as conn, run_timer(logger, "generate"):
        # --- Pass 1: work out what needs (re)generating, without writing anything yet ---
        plan: list[tuple[str, dict, str, list[str], str]] = []  # row_key, row, dest, attachments, reason
        categories_needing_sweep: set[str] = set()

        for i, row in enumerate(rows):
            row_key = sync_row(conn, row)
            if row_key is None:
                logger.warning(f"Row {i} is missing timestamp/name, skipping PDF generation")
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

            if kind == "repair":
                dest = db_row["final_pdf_path"]  # restore in place - not new, don't touch categories
            else:
                category = category_for(row)
                dest = os.path.join(category, f"{db_row['base_filename']}.pdf")
                categories_needing_sweep.add(category)

            plan.append((row_key, row, dest, attachment_paths, reason))

        # --- Archive each affected category's current batch before writing the new one ---
        for category in categories_needing_sweep:
            moved = _sweep_category(conn, category)
            if moved:
                logger.info(f"Archived {moved} previous PDF(s) from '{category}' into {PREVIOUS_DIRNAME}/{category}/")

        # --- Pass 2: actually generate ---
        with ProgressBar(len(plan), "Generating") as bar:
            for row_key, row, dest, attachment_paths, reason in plan:
                logger.info(f"Generating {dest} ({reason})")
                try:
                    _generate_one(conn, row_key, row, dest, attachment_paths)
                except Exception as e:
                    db.set_row_error(conn, row_key, str(e))
                    logger.error(f"Failed to generate PDF for row {row_key}: {e}", exc_info=True)
                bar.update()
