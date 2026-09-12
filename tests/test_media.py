import random

import pytest
from PIL import Image

from kvittomall import db, media
from kvittomall.rowkey import base_filename


def test_get_quality_preset_default(monkeypatch):
    monkeypatch.setattr(media, "IMAGE_QUALITY", "normal")
    assert media.get_quality_preset() is media.QUALITY_PRESETS["normal"]


def test_get_quality_preset_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(media, "IMAGE_QUALITY", "POTATO")
    assert media.get_quality_preset() is media.QUALITY_PRESETS["potato"]


def test_get_quality_preset_invalid_raises_systemexit(monkeypatch):
    monkeypatch.setattr(media, "IMAGE_QUALITY", "ultra-hd")
    with pytest.raises(SystemExit):
        media.get_quality_preset()


def test_downscale_no_op_when_already_within_bounds():
    img = Image.new("RGB", (100, 200))
    result = media._downscale(img, 2000)
    assert result.size == (100, 200)


def test_downscale_preserves_aspect_ratio():
    img = Image.new("RGB", (4000, 2000))
    result = media._downscale(img, 2000)
    assert result.size == (2000, 1000)


def _noisy_image(size=(1600, 1600)) -> Image.Image:
    """A random-noise image compresses far worse than a flat one, so it actually
    exercises the quality-stepping/target_bytes logic instead of returning after the
    first (highest-quality) attempt.
    """
    random.seed(0)
    data = bytes(random.getrandbits(8) for _ in range(size[0] * size[1] * 3))
    return Image.frombytes("RGB", size, data)


def test_encode_adaptive_extreme_preserves_dimensions_and_skips_target():
    img = Image.new("RGB", (3000, 4000), (240, 240, 235))
    data = media.encode_adaptive(img, media.QUALITY_PRESETS["extreme"])
    out = Image.open(__import__("io").BytesIO(data))
    assert out.size == (3000, 4000)


def test_encode_adaptive_potato_shrinks_dimensions():
    img = Image.new("RGB", (3000, 4000), (240, 240, 235))
    data = media.encode_adaptive(img, media.QUALITY_PRESETS["potato"])
    out = Image.open(__import__("io").BytesIO(data))
    assert max(out.size) <= 480


def test_encode_adaptive_shrinks_hard_to_compress_image_far_below_naive_encode():
    # True random noise defeats JPEG's block-based compression almost entirely, so it's
    # an unrealistic worst case for target_bytes/hard_ceiling themselves (no real receipt
    # photo behaves like this) - but encode_adaptive should still be doing real work:
    # stepping down quality and shrinking dimensions, not just returning the first pass.
    preset = media.QUALITY_PRESETS["low"]
    img = _noisy_image()
    adaptive_size = len(media.encode_adaptive(img.copy(), preset))
    naive_top_quality_original_size = len(media._encode_jpeg(img.copy(), preset.quality_steps[0]))
    assert adaptive_size < naive_top_quality_original_size


def test_encode_adaptive_flat_image_is_tiny_regardless_of_preset():
    img = Image.new("RGB", (2000, 2000), (255, 255, 255))
    for preset in media.QUALITY_PRESETS.values():
        data = media.encode_adaptive(img, preset)
        assert len(data) > 0
        # A flat white image should never come close to any preset's ceiling.
        if preset.hard_ceiling is not None:
            assert len(data) < preset.hard_ceiling


def test_process_all_only_filters_to_one_row(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "PROCESSED_DIR", str(tmp_path / "processed"))
    (tmp_path / "processed").mkdir()

    rows = [
        {"Tidstämpel": "2026-01-01 10.00.00", "Namn": "Anna Andersson"},
        {"Tidstämpel": "2026-01-02 10.00.00", "Namn": "Bertil Bengtsson"},
    ]
    monkeypatch.setattr(media, "read_rows", lambda: rows)
    key1, key2 = rows[0]["Tidstämpel"], rows[1]["Tidstämpel"]
    base1, base2 = base_filename(rows[0]), base_filename(rows[1])

    src1, src2 = tmp_path / "src1.jpg", tmp_path / "src2.jpg"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(src1, "JPEG")
    Image.new("RGB", (100, 100), (0, 255, 0)).save(src2, "JPEG")

    db.upsert_row(conn, key1, base1, "Privat utlägg", "h1", "l1")
    db.upsert_attachment(conn, key1, 0, "fileid1")
    db.mark_download_ok(conn, key1, 0, str(src1), src1.stat().st_size)

    db.upsert_row(conn, key2, base2, "Privat utlägg", "h2", "l2")
    db.upsert_attachment(conn, key2, 0, "fileid2")
    db.mark_download_ok(conn, key2, 0, str(src2), src2.stat().st_size)
    conn.commit()  # _process_all opens its own connection - must not see an open write lock

    media._process_all(media.QUALITY_PRESETS["normal"], only=key1)

    assert db.is_process_valid(db.get_attachment(conn, key1, 0))[0] is True
    assert db.is_process_valid(db.get_attachment(conn, key2, 0))[0] is False


def test_remove_processed_deletes_files_and_leaves_db_untouched(conn, tmp_path):
    path = tmp_path / "row1_file0.jpg"
    path.write_bytes(b"fake-processed-bytes")
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")
    db.upsert_attachment(conn, "k1", 0, "fileid")
    db.mark_process_ok(conn, "k1", 0, str(path), path.stat().st_size)
    conn.commit()  # remove_processed() opens its own connection - mustn't see an open write lock

    removed = media.remove_processed("k1")

    assert removed == 1
    assert not path.exists()
    att = db.get_attachment(conn, "k1", 0)
    assert att["process_status"] == "ok"  # DB is untouched - only the file is gone
    assert db.is_process_valid(att) == (False, f"recorded processed file is missing on disk: {path}")


def test_remove_processed_is_a_no_op_when_nothing_to_remove(conn):
    assert media.remove_processed("unknown-row") == 0
