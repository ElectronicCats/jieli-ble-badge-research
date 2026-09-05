"""Tests para qix_ble.patterns — text + simple + numpy patterns + AVI helpers."""
from __future__ import annotations

import pytest

PIL = pytest.importorskip("PIL")
np = pytest.importorskip("numpy")

from PIL import Image  # noqa: E402

from qix_ble.patterns import (  # noqa: E402
    concentric_waves,
    frames_to_avi,
    plasma_waves,
    prepare_pattern,
    prepare_text,
    simple_pattern_frames,
    text_frames,
)


SMALL = (64, 64)  # tests rápidos con tamaño chico


# ── text_frames ──────────────────────────────────────────────────────────────

def test_text_frames_scroll_count():
    """duration=2s × fps=12 → 24 frames."""
    frames = text_frames("hello", mode="scroll", target_size=SMALL,
                         duration=2.0, fps=12, font_size=20)
    assert len(frames) == 24
    assert all(f.size == SMALL for f in frames)
    assert all(f.mode == "RGB" for f in frames)


def test_text_frames_static_all_identical():
    """static mode → todas las frames son la misma imagen."""
    frames = text_frames("X", mode="static", target_size=SMALL,
                         duration=0.5, fps=10, font_size=20)
    # 5 frames generated, all should be identical bytes
    ref = frames[0].tobytes()
    for f in frames[1:]:
        assert f.tobytes() == ref


def test_text_frames_pulse_varies():
    """pulse mode → frames varían (scale animation)."""
    frames = text_frames("P", mode="pulse", target_size=SMALL,
                         duration=1.0, fps=12, font_size=30)
    unique_hashes = {f.tobytes() for f in frames}
    # Pulse animation should produce variation
    assert len(unique_hashes) > 1


def test_text_frames_wave_varies():
    frames = text_frames("W", mode="wave", target_size=SMALL,
                         duration=1.0, fps=12, font_size=30)
    unique = {f.tobytes() for f in frames}
    assert len(unique) > 1


def test_text_frames_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown text mode"):
        text_frames("X", mode="lol", target_size=SMALL)  # type: ignore


def test_text_frames_min_one_frame():
    """duration*fps=0.05 → max(1, 0) = 1 frame."""
    frames = text_frames("X", mode="scroll", target_size=SMALL,
                         duration=0.05, fps=1, font_size=20)
    assert len(frames) >= 1


# ── simple_pattern_frames ────────────────────────────────────────────────────

def test_simple_pattern_gradient_count():
    frames = simple_pattern_frames("gradient", target_size=SMALL,
                                    frame_count=10)
    assert len(frames) == 10
    assert all(f.size == SMALL for f in frames)


def test_simple_pattern_pulse_solid_color_changes():
    """pulse mode → cada frame es un solid color que varía con t."""
    frames = simple_pattern_frames("pulse", target_size=SMALL,
                                    frame_count=20,
                                    color1=(255, 100, 50))
    # Sample center pixel of each frame — debe variar
    centers = {f.getpixel((SMALL[0] // 2, SMALL[1] // 2)) for f in frames}
    assert len(centers) > 1


def test_simple_pattern_checker_uses_both_colors():
    frames = simple_pattern_frames("checker", target_size=(64, 64),
                                    frame_count=3,
                                    color1=(255, 0, 0), color2=(0, 255, 0))
    # First frame: top-left should be color1 (cy=cx=0, sum=0 → color1)
    pixels = list(frames[0].getdata())
    has_red = any(p == (255, 0, 0) for p in pixels)
    has_green = any(p == (0, 255, 0) for p in pixels)
    assert has_red and has_green


def test_simple_pattern_rainbow_runs():
    """Rainbow uses colorsys.hsv_to_rgb — sanity check it executes."""
    frames = simple_pattern_frames("rainbow", target_size=SMALL, frame_count=5)
    assert len(frames) == 5


def test_simple_pattern_wave_runs():
    """Wave uses sin math on small grid + nearest resize."""
    frames = simple_pattern_frames("wave", target_size=SMALL, frame_count=3)
    assert len(frames) == 3


def test_simple_pattern_unknown_raises():
    with pytest.raises(ValueError, match="unknown simple pattern"):
        simple_pattern_frames("plasma_waves", target_size=SMALL)  # type: ignore


# ── numpy patterns ───────────────────────────────────────────────────────────

def test_plasma_waves_frame_count_size():
    frames = plasma_waves(target_size=SMALL, frame_count=5)
    assert len(frames) == 5
    assert all(f.size == SMALL for f in frames)
    assert all(f.mode == "RGB" for f in frames)


def test_plasma_waves_circular_mask_pixels_zero():
    """Con circular_mask=True, esquinas (lejos del centro) deben ser negras."""
    frames = plasma_waves(target_size=SMALL, frame_count=1, circular_mask=True)
    # Esquina (0,0) está fuera del círculo
    assert frames[0].getpixel((0, 0)) == (0, 0, 0)


def test_plasma_waves_no_mask_corners_colored():
    """Sin máscara, esquinas deberían tener algún color (no garantizado pero típico)."""
    frames = plasma_waves(target_size=SMALL, frame_count=1, circular_mask=False)
    # No assertions de color exacto (depende del phase), solo que está RGB
    assert frames[0].mode == "RGB"


def test_concentric_waves_frame_count():
    frames = concentric_waves(target_size=SMALL, frame_count=5)
    assert len(frames) == 5


def test_concentric_waves_circular_mask_corners_zero():
    frames = concentric_waves(target_size=SMALL, frame_count=1, circular_mask=True)
    assert frames[0].getpixel((0, 0)) == (0, 0, 0)


def test_concentric_waves_animation_varies():
    """Diferentes frames → diferentes pixeles en el centro."""
    frames = concentric_waves(target_size=SMALL, frame_count=10)
    centers = {f.getpixel((SMALL[0] // 2, SMALL[1] // 2)) for f in frames}
    assert len(centers) > 1


# ── frames_to_avi + prepare_text + prepare_pattern ───────────────────────────

def test_frames_to_avi_basic():
    frames = [Image.new("RGB", SMALL, (i * 30, 0, 0)) for i in range(3)]
    avi = frames_to_avi(frames, fps=12)
    assert avi[:4] == b"RIFF"
    assert b"MJPG" in avi


def test_frames_to_avi_empty_raises():
    with pytest.raises(ValueError, match="vacío"):
        frames_to_avi([])


def test_prepare_text_returns_avi():
    avi = prepare_text("hi", mode="scroll", target_size=SMALL,
                       duration=0.5, fps=12, font_size=20)
    assert avi[:4] == b"RIFF"
    assert b"movi" in avi


def test_prepare_pattern_simple():
    avi = prepare_pattern("gradient", target_size=SMALL, frame_count=5,
                          fps=12)
    assert avi[:4] == b"RIFF"


def test_prepare_pattern_numpy_plasma():
    avi = prepare_pattern("plasma_waves", target_size=SMALL, frame_count=3,
                          fps=12)
    assert avi[:4] == b"RIFF"


def test_prepare_pattern_numpy_concentric():
    avi = prepare_pattern("concentric_waves", target_size=SMALL,
                          frame_count=3, fps=12)
    assert avi[:4] == b"RIFF"


def test_prepare_pattern_unknown_raises():
    with pytest.raises(ValueError, match="unknown pattern"):
        prepare_pattern("nope")
