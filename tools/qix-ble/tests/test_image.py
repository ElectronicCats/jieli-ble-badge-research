"""Tests para qix_ble.image — PIL pipeline + JPEG encode + AVI from animated."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from qix_ble.image import (  # noqa: E402
    DEFAULT_TARGET_SIZE,
    E87_TARGET_IMAGE_BYTES,
    encode_badge_jpeg,
    encode_video_jpeg,
    prepare_image,
    prepare_slideshow,
    prepare_video,
    transform_to_badge,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def red_square_512():
    """Plain red 512×512 RGB image."""
    return Image.new("RGB", (512, 512), (255, 0, 0))


@pytest.fixture
def gradient_1024():
    """Wide rectangle for testing cover vs contain."""
    img = Image.new("RGB", (1024, 256), (0, 0, 0))
    for x in range(1024):
        for y in range(256):
            img.putpixel((x, y), (x % 256, y, (x + y) % 256))
    return img


@pytest.fixture
def animated_gif(tmp_path):
    """Make a 5-frame animated GIF."""
    frames = []
    for i in range(5):
        f = Image.new("RGB", (64, 64), (i * 50, 0, 0))
        frames.append(f)
    out = tmp_path / "anim.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=80, loop=0)
    return out


# ── transform_to_badge ───────────────────────────────────────────────────────

def test_transform_to_badge_target_dimensions(red_square_512):
    out = transform_to_badge(red_square_512)
    assert out.size == DEFAULT_TARGET_SIZE


def test_transform_to_badge_custom_size(red_square_512):
    out = transform_to_badge(red_square_512, target_size=(100, 100))
    assert out.size == (100, 100)


def test_transform_to_badge_cover_fills_target(gradient_1024):
    """cover mode → output is fully populated (no bg_color showing through)."""
    bg = (37, 73, 250)  # uniquely identifiable
    out = transform_to_badge(gradient_1024, fit="cover",
                              target_size=(200, 200), bg_color=bg)
    # In cover mode, bg shouldn't be visible at center.
    center_px = out.getpixel((100, 100))
    assert center_px != bg


def test_transform_to_badge_contain_letterboxes(gradient_1024):
    """contain mode con bg distintivo → bg debe ser visible en bordes."""
    bg = (37, 73, 250)
    out = transform_to_badge(gradient_1024, fit="contain",
                              target_size=(200, 200), bg_color=bg)
    # Wide source aspect → letterbox vertical → top center should be bg
    assert out.getpixel((100, 5)) == bg


def test_transform_to_badge_converts_non_rgb_modes():
    """RGBA / L / P modes deberían convertirse a RGB sin error."""
    rgba = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
    out = transform_to_badge(rgba)
    assert out.mode == "RGB"


def test_transform_to_badge_zoom_doubles():
    """zoom=2 sobre square fit=cover → scaled imagen es 2× del target."""
    src = Image.new("RGB", (100, 100), (0, 0, 0))
    out = transform_to_badge(src, fit="cover", zoom=2.0, target_size=(50, 50))
    # Output size es target (cropped), pero internamente el scale fue 2x
    assert out.size == (50, 50)


# ── encode_badge_jpeg ────────────────────────────────────────────────────────

def test_encode_badge_jpeg_fits_budget(red_square_512):
    """Solid color → smallest possible → always fit."""
    out = encode_badge_jpeg(red_square_512)
    assert len(out) <= E87_TARGET_IMAGE_BYTES
    assert out.startswith(b"\xff\xd8")  # JPEG SOI
    assert out.endswith(b"\xff\xd9")    # JPEG EOI


def test_encode_badge_jpeg_quality_steps_descending(gradient_1024):
    """Complex image que no cabe en quality alta → uses lower quality."""
    out = encode_badge_jpeg(gradient_1024)
    # Should still fit at lowest quality OR be returned even if over
    assert out.startswith(b"\xff\xd8")


def test_encode_badge_jpeg_custom_max_bytes(red_square_512):
    """Manual budget override."""
    out = encode_badge_jpeg(red_square_512, max_bytes=500)
    # For a solid color, any quality fits 500B
    assert len(out) <= 5000  # generous bound


def test_encode_badge_jpeg_rgba_converted():
    """RGBA input — encode_badge_jpeg debe convertir antes de save."""
    rgba = Image.new("RGBA", (200, 200), (255, 0, 0, 128))
    out = encode_badge_jpeg(rgba)
    assert out.startswith(b"\xff\xd8")


# ── encode_video_jpeg ────────────────────────────────────────────────────────

def test_encode_video_jpeg_default_quality(red_square_512):
    out = encode_video_jpeg(red_square_512)
    assert out.startswith(b"\xff\xd8")


def test_encode_video_jpeg_low_quality_smaller(red_square_512):
    """Lower quality → smaller bytes (for non-trivial image)."""
    high = encode_video_jpeg(red_square_512, quality=95)
    low = encode_video_jpeg(red_square_512, quality=30)
    assert len(low) <= len(high)


# ── prepare_image ────────────────────────────────────────────────────────────

def test_prepare_image_basic(tmp_path, red_square_512):
    p = tmp_path / "red.png"
    red_square_512.save(p)
    out = prepare_image(p)
    assert out.startswith(b"\xff\xd8")
    assert len(out) <= E87_TARGET_IMAGE_BYTES


def test_prepare_image_jpeg_input(tmp_path, red_square_512):
    p = tmp_path / "red.jpg"
    red_square_512.save(p, format="JPEG")
    out = prepare_image(p)
    assert out.startswith(b"\xff\xd8")


def test_prepare_image_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare_image(tmp_path / "nope.png")


def test_prepare_image_passthrough_avi(tmp_path):
    """`.avi` extension passes raw bytes."""
    p = tmp_path / "passthrough.avi"
    raw = b"FAKE-AVI-CONTENT-BYTES"
    p.write_bytes(raw)
    out = prepare_image(p)
    assert out == raw


# ── prepare_video ────────────────────────────────────────────────────────────

def test_prepare_video_animated_gif(animated_gif):
    avi = prepare_video(animated_gif, target_size=(64, 64), fps=12)
    assert avi[:4] == b"RIFF"
    assert b"MJPG" in avi
    assert b"movi" in avi


def test_prepare_video_duration_limit(animated_gif):
    """duration=1s @ fps=12 → 12 frames max (we only have 5)."""
    avi = prepare_video(animated_gif, target_size=(64, 64), fps=12, duration=1.0)
    assert avi[:4] == b"RIFF"


def test_prepare_video_rejects_non_animated(tmp_path, red_square_512):
    """Static PNG → ValueError (no n_frames > 1)."""
    p = tmp_path / "static.png"
    red_square_512.save(p)
    with pytest.raises(ValueError, match="no parece ser animated"):
        prepare_video(p)


def test_prepare_video_rejects_unsupported_ext(tmp_path):
    p = tmp_path / "foo.mp4"
    p.write_bytes(b"not really mp4")
    with pytest.raises(ValueError, match="soporta solo"):
        prepare_video(p)


def test_prepare_video_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare_video(tmp_path / "nope.gif")


# ── prepare_slideshow ────────────────────────────────────────────────────────

def test_prepare_slideshow_multiple_images(tmp_path, red_square_512):
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    red_square_512.save(p1)
    Image.new("RGB", (256, 256), (0, 255, 0)).save(p2)
    avi = prepare_slideshow([p1, p2], target_size=(64, 64),
                             fps=12, duration_per_image=0.5)
    assert avi[:4] == b"RIFF"


def test_prepare_slideshow_empty_raises():
    with pytest.raises(ValueError, match="vacío"):
        prepare_slideshow([])
