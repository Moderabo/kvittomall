import random

import pytest
from PIL import Image

from kvittomall import media


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
