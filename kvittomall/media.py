"""Stage 3: normalize each downloaded attachment into processed/.

Images are re-encoded as JPEG using adaptive compression - starting at a preset's
highest quality step and only stepping down as far as needed to hit a target size,
with dimensions only reduced as a last resort. PDFs pass through as-is; final page
scaling/merging happens in pdf_gen.py.

How hard to compress is a legibility-vs-size tradeoff, not a looks-good-vs-size one -
a receipt only needs to be readable, so IMAGE_QUALITY in .env picks one of five presets
(QUALITY_PRESETS below), from "extreme" (full quality, no resize) down to "potato"
(480px, compressed hard). Defaults to "normal" - the tradeoff this project shipped with
originally - if unset.
"""

import io
import os
import shutil
from dataclasses import dataclass
from typing import Optional

import magic
from PIL import Image, ImageOps
from pypdf import PdfReader

from kvittomall import db
from kvittomall.atomic import atomic_write
from kvittomall.config import IMAGE_QUALITY
from kvittomall.logging_setup import run_timer, setup_logging
from kvittomall.paths import PROCESSED_DIR
from kvittomall.progress import ProgressBar
from kvittomall.rowkey import sync_row
from kvittomall.sheet import read_rows

logger = setup_logging("process")


@dataclass(frozen=True)
class QualityPreset:
    max_dimension: Optional[int]  # longest side in px; None = never resize
    quality_steps: list[int]      # tried highest-first; stops at the first that fits target_bytes
    target_bytes: Optional[int]   # None = accept the first (only) quality step's result outright
    hard_ceiling: Optional[int]   # still over this after every quality step -> shrink dims and retry
    dimension_rounds: int = 2


QUALITY_PRESETS: dict[str, QualityPreset] = {
    "extreme": QualityPreset(None, [95], None, None, dimension_rounds=1),
    "high": QualityPreset(2600, [90, 82, 75], 150_000, 300_000),
    "normal": QualityPreset(2000, [90, 80, 70, 60, 50], 60_000, 150_000),
    "low": QualityPreset(1200, [70, 55, 40], 30_000, 75_000),
    "potato": QualityPreset(480, [40, 25], 15_000, 40_000),
}
def get_quality_preset() -> QualityPreset:
    name = IMAGE_QUALITY.lower()
    if name not in QUALITY_PRESETS:
        raise SystemExit(f"IMAGE_QUALITY must be one of {sorted(QUALITY_PRESETS)}, got '{name}'")
    return QUALITY_PRESETS[name]


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


def encode_adaptive(img: Image.Image, preset: QualityPreset) -> bytes:
    data = b""
    max_dim = preset.max_dimension
    for _round in range(preset.dimension_rounds):
        if max_dim is not None:
            img = _downscale(img, max_dim)
        for quality in preset.quality_steps:
            data = _encode_jpeg(img, quality)
            if preset.target_bytes is None or len(data) <= preset.target_bytes:
                return data
        if max_dim is None or preset.hard_ceiling is None or len(data) <= preset.hard_ceiling:
            return data
        max_dim *= 0.75
    return data


def _open_image(path: str, mime: str) -> Image.Image:
    if mime in ("image/heic", "image/heif"):
        import pillow_heif
        pillow_heif.register_heif_opener()
    return Image.open(path)


def _process_one(conn, row_key: str, link_index: int, src_path: str, preset: QualityPreset) -> None:
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
                    f.write(encode_adaptive(img, preset))

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


def run(only: str | None = None) -> None:
    with run_timer(logger, "process" if only is None else f"process (row {only})"):
        try:
            preset = get_quality_preset()
        except SystemExit as e:
            logger.error(f"Process failed before it could start: {e}")
            raise SystemExit(1)
        _process_all(preset, only)


def remove_processed(row_key: str) -> int:
    """Deletes every processed file recorded for this row - the same disk-only cleanup
    as drive.remove_downloads(): is_process_valid() re-checks disk on every run, so a
    later `process` run just redoes it from the (still-present) download.
    """
    removed = 0
    with db.connect() as conn:
        for att in db.list_attachments(conn, row_key):
            path = att["processed_path"]
            if path and os.path.exists(path):
                os.remove(path)
                removed += 1
                logger.info(f"Removed processed file for {row_key} attachment {att['link_index']}: {path}")
    return removed


def _process_all(preset: QualityPreset, only: str | None = None) -> None:
    rows = read_rows()
    with db.connect() as conn, ProgressBar(len(rows), "Processing") as bar:
        for i, row in enumerate(rows):
            row_key = sync_row(conn, row)
            if row_key is None:
                bar.update()
                continue
            if only is not None and row_key != only:
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
                _process_one(conn, row_key, att["link_index"], att["download_path"], preset)

            bar.update()
