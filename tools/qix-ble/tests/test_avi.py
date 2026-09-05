"""Tests para qix_ble.avi — RIFF/AVI container builder con MJPG video.

Verificación byte-exact del layout RIFF + parse-back test.
"""
from __future__ import annotations

import struct

import pytest

from qix_ble.avi import _chunk, _fourcc, _pad_even, _u16le, _u32le, build_mjpg_avi

# ── Helpers ──────────────────────────────────────────────────────────────────


def _fake_jpeg(size: int = 32) -> bytes:
    """Synthetic JPEG-ish: SOI + filler + EOI. Solo para AVI container — el
    container no valida JPEG content."""
    body = bytes([0x00] * (size - 4))
    return b"\xff\xd8" + body + b"\xff\xd9"


# ── Helper unit tests ────────────────────────────────────────────────────────

def test_fourcc_pads_to_4():
    assert _fourcc("AB") == b"AB\x00\x00"


def test_fourcc_truncates_to_4():
    assert _fourcc("ABCDE") == b"ABCD"


def test_u32le_zero():
    assert _u32le(0) == b"\x00\x00\x00\x00"


def test_u32le_max():
    assert _u32le(0xFFFFFFFF) == b"\xff\xff\xff\xff"


def test_u16le_basic():
    assert _u16le(0x1234) == b"\x34\x12"


def test_pad_even_already_even():
    assert _pad_even(b"AB") == b"AB"


def test_pad_even_odd_adds_byte():
    assert _pad_even(b"ABC") == b"ABC\x00"


def test_chunk_padding_even():
    """data of length 5 → chunk has 1B padding but size declares 5."""
    c = _chunk("test", b"hello")
    assert c[:4] == b"test"
    assert c[4:8] == _u32le(5)
    assert c[8:13] == b"hello"
    assert c[13] == 0x00  # padding
    assert len(c) == 14


# ── AVI container tests ──────────────────────────────────────────────────────

def test_build_mjpg_avi_empty_raises():
    with pytest.raises(ValueError, match="At least one frame"):
        build_mjpg_avi([])


def test_build_mjpg_avi_single_frame_riff_header():
    avi = build_mjpg_avi([_fake_jpeg(40)])
    assert avi[:4] == b"RIFF"
    declared_size = struct.unpack("<I", avi[4:8])[0]
    assert declared_size == len(avi) - 8  # RIFF size excludes the 8B header
    assert avi[8:12] == b"AVI "


def test_build_mjpg_avi_contains_hdrl_movi_idx1():
    avi = build_mjpg_avi([_fake_jpeg() for _ in range(3)])
    # All 3 must appear as LIST/chunk fourccs
    assert b"hdrl" in avi
    assert b"movi" in avi
    assert b"idx1" in avi
    assert b"avih" in avi
    assert b"strl" in avi
    assert b"strh" in avi
    assert b"strf" in avi
    assert b"MJPG" in avi


def test_build_mjpg_avi_frame_count_in_avih():
    """avih.dwTotalFrames at offset 12+16 = 28 (4 RIFF + 4 size + 4 AVI + 4 LIST + 4 listsize + 4 hdrl + 4 avih + 4 avih_size + 16 inside avih)."""
    avi = build_mjpg_avi([_fake_jpeg() for _ in range(7)])
    # Locate "avih" then skip 8 (fourcc + size), then dwTotalFrames is at offset 16 within data
    avih_pos = avi.find(b"avih")
    total_frames = struct.unpack("<I", avi[avih_pos + 8 + 16:avih_pos + 8 + 20])[0]
    assert total_frames == 7


def test_build_mjpg_avi_dimensions_in_strf():
    """biWidth at offset strf_data+4, biHeight at +8."""
    avi = build_mjpg_avi([_fake_jpeg()], width=128, height=64)
    strf_pos = avi.find(b"strf")
    # Skip 8B (fourcc + size) + 4B biSize
    bi_width = struct.unpack("<i", avi[strf_pos + 8 + 4:strf_pos + 8 + 8])[0]
    bi_height = struct.unpack("<i", avi[strf_pos + 8 + 8:strf_pos + 8 + 12])[0]
    assert bi_width == 128
    assert bi_height == 64


def test_build_mjpg_avi_default_dimensions_360():
    avi = build_mjpg_avi([_fake_jpeg()])
    strf_pos = avi.find(b"strf")
    bi_width = struct.unpack("<i", avi[strf_pos + 8 + 4:strf_pos + 8 + 8])[0]
    assert bi_width == 360


def test_build_mjpg_avi_fps_to_microseconds():
    """usec_per_frame = 1_000_000 / fps en avih offset 0."""
    avi = build_mjpg_avi([_fake_jpeg()], fps=10)
    avih_pos = avi.find(b"avih")
    usec = struct.unpack("<I", avi[avih_pos + 8:avih_pos + 12])[0]
    assert usec == 100_000  # 1_000_000 / 10


def test_build_mjpg_avi_idx1_entry_count():
    """idx1 = 16B per frame."""
    frames = [_fake_jpeg() for _ in range(5)]
    avi = build_mjpg_avi(frames)
    idx1_pos = avi.find(b"idx1")
    idx1_size = struct.unpack("<I", avi[idx1_pos + 4:idx1_pos + 8])[0]
    assert idx1_size == 5 * 16


def test_build_mjpg_avi_frame_content_preserved():
    """Frame bytes deben aparecer literalmente en el container."""
    unique_pattern = b"\xff\xd8" + b"\xAA\xBB\xCC\xDD" * 8 + b"\xff\xd9"
    avi = build_mjpg_avi([unique_pattern], width=8, height=8)
    assert unique_pattern in avi


def test_build_mjpg_avi_handles_odd_frame_size():
    """JPEG of odd length must be padded but declared size = real."""
    odd_jpeg = _fake_jpeg(33)  # 33 bytes
    avi = build_mjpg_avi([odd_jpeg])
    # Locate 00dc chunk inside movi
    movi_pos = avi.find(b"movi")
    chunk_pos = avi.find(b"00dc", movi_pos)
    declared_size = struct.unpack("<I", avi[chunk_pos + 4:chunk_pos + 8])[0]
    assert declared_size == 33  # original size, not padded


def test_build_mjpg_avi_accepts_iterable():
    """Generator también funciona, no solo list."""
    def gen():
        for _ in range(2):
            yield _fake_jpeg()
    avi = build_mjpg_avi(gen())
    avih_pos = avi.find(b"avih")
    total_frames = struct.unpack("<I", avi[avih_pos + 8 + 16:avih_pos + 8 + 20])[0]
    assert total_frames == 2
