"""Stage 3: normalize each downloaded attachment into processed/.

Images are re-encoded as JPEG using adaptive compression - starting at high quality and
only stepping down as far as needed to hit a target size, with a floor that protects
legibility, and dimensions are only reduced as a last resort. PDFs pass through as-is;
final page scaling/merging happens in pdf_gen.py.
"""

import io
import os
import shutil

import magic
from PIL import Image, ImageOps
from PyPDF2 import PdfReader

from kvittomall import db
from kvittomall.atomic import atomic_write
from kvittomall.logging_setup import run_timer, setup_logging
from kvittomall.paths import PROCESSED_DIR
from kvittomall.progress import ProgressBar
from kvittomall.rowkey import sync_row
from kvittomall.sheet import read_rows

logger = setup_logging("process")

MAX_DIMENSION = 2000
QUALITY_STEPS = [90, 80, 70, 60, 50]  # 50 is the legibility floor - never go lower
TARGET_BYTES = 500_000  # per-image target, keeps multi-receipt PDFs small
HARD_CEILING = 1_500_000  # still this big at floor quality -> shrink dimensions and retry
MAX_DIMENSION_ROUNDS = 2


def _downscale(img: Image.Image, max_dim: float) -> Image.Image:
    width, height = img.size
    if max(width, height) <= max_dim:
        return img
    scale = max_dim / max(width, height)
    return img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def encode_adaptive(img: Image.Image, max_dim: float = MAX_DIMENSION) -> bytes:
    data = b""
    for _round in range(MAX_DIMENSION_ROUNDS):
        img = _downscale(img, max_dim)
        for quality in QUALITY_STEPS:
            data = _encode_jpeg(img, quality)
            if len(data) <= TARGET_BYTES:
                return data
        if len(data) <= HARD_CEILING:
            return data
        max_dim *= 0.75
    return data


def _open_image(path: str, mime: str) -> Image.Image:
    if mime in ("image/heic", "image/heif"):
        import pillow_heif
        pillow_heif.register_heif_opener()
    return Image.open(path)


def _process_one(conn, row_key: str, link_index: int, src_path: str) -> None:
    base_name = os.path.splitext(os.path.basename(src_path))[0]
    try:
        mime = magic.from_file(src_path, mime=True)

        if mime == "application/pdf":
            dst_path = os.path.join(PROCESSED_DIR, base_name + ".pdf")
            atomic_write(
                dst_path,
                writer=lambda tmp: shutil.copy2(src_path, tmp),
                validate=lambda tmp: _require(len(PdfReader(tmp).pages) >= 1, "PDF has no pages"),
            )
        elif mime.startswith("image/"):
            dst_path = os.path.join(PROCESSED_DIR, base_name + ".jpg")

            def writer(tmp_path: str) -> None:
                img = _open_image(src_path, mime)
                img = ImageOps.exif_transpose(img).convert("RGB")
                with open(tmp_path, "wb") as f:
                    f.write(encode_adaptive(img))

            def validate(tmp_path: str) -> None:
                with Image.open(tmp_path) as im:
                    im.verify()

            atomic_write(dst_path, writer, validate)
        else:
            raise ValueError(f"unsupported file type: {mime}")

        size = os.path.getsize(dst_path)
        db.mark_process_ok(conn, row_key, link_index, dst_path, size)
        logger.info(f"Processed {src_path} -> {dst_path}")
    except Exception as e:
        db.mark_process_failed(conn, row_key, link_index, str(e))
        logger.error(f"Failed to process row {row_key} attachment {link_index} ({src_path}): {e}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _find_existing_processed(base_name: str) -> str | None:
    """Looks for a file already sitting in processed/ for this attachment (e.g. from
    before this database existed) so it can be adopted instead of redone from scratch.
    """
    for ext in (".jpg", ".pdf"):
        candidate = os.path.join(PROCESSED_DIR, base_name + ext)
        if os.path.exists(candidate):
            return candidate
    return None


def _adopt_existing(conn, row_key: str, link_index: int, path: str) -> bool:
    """Validates a pre-existing processed file and records it as done if it checks out."""
    try:
        if path.endswith(".pdf"):
            _require(len(PdfReader(path).pages) >= 1, "PDF has no pages")
        else:
            with Image.open(path) as im:
                im.verify()
        size = os.path.getsize(path)
    except Exception:
        return False
    db.mark_process_ok(conn, row_key, link_index, path, size)
    logger.info(f"Adopted existing processed file {path}")
    return True


def run() -> None:
    rows = read_rows()
    with db.connect() as conn, run_timer(logger, "process"), ProgressBar(len(rows), "Processing") as bar:
        for i, row in enumerate(rows):
            row_key = sync_row(conn, row)
            if row_key is None:
                bar.update()
                continue

            for att in db.list_attachments(conn, row_key):
                downloaded, _ = db.is_download_valid(att)
                if not downloaded:
                    continue
                processed, reason = db.is_process_valid(att)
                if processed:
                    continue

                base_name = os.path.splitext(os.path.basename(att["download_path"]))[0]
                existing_path = _find_existing_processed(base_name)
                if existing_path and _adopt_existing(conn, row_key, att["link_index"], existing_path):
                    continue

                logger.info(f"Row {i} attachment {att['link_index']}: processing ({reason})")
                _process_one(conn, row_key, att["link_index"], att["download_path"])

            bar.update()
