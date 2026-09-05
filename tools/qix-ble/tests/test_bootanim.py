"""Tests unitarios para qix_ble.bootanim (boot animation push helpers)."""
import struct

import pytest

from qix_ble.bootanim import (
    BOOT_ANI_FILE_TYPE,
    BootAnimationInfo,
    _crc16_xmodem,
    build_boot_ani_blob,
    encode_rgb565_le,
    query_boot_ani_info,
)
from qix_ble.frame import QixFrame
from tests.conftest import MockTransport


pytest.importorskip("PIL", reason="bootanim requires Pillow")
pytest.importorskip("numpy", reason="bootanim requires numpy for RGB565 encode")


def test_crc16_xmodem_seed_ffff():
    """jl_crc16 seed=0xFFFF per DialTool.smali:311."""
    # CRC16/XMODEM seed=0xFFFF over b"123456789" — sanity vs known seed=0 vector.
    # Different seed → different result. Just verify it computes deterministically.
    crc = _crc16_xmodem(b"123456789", seed=0xFFFF)
    assert isinstance(crc, int)
    assert 0 <= crc <= 0xFFFF
    # Idempotent
    assert _crc16_xmodem(b"123456789", seed=0xFFFF) == crc


def test_encode_rgb565_le_pure_colors():
    """Verificar RGB565 LE encoding contra valores conocidos."""
    from PIL import Image
    img = Image.new("RGB", (1, 3), color=(0, 0, 0))
    # px 0,0 = red, px 0,1 = green, px 0,2 = blue
    img.putpixel((0, 0), (0xFF, 0x00, 0x00))
    img.putpixel((0, 1), (0x00, 0xFF, 0x00))
    img.putpixel((0, 2), (0x00, 0x00, 0xFF))

    px = encode_rgb565_le(img)
    assert len(px) == 6  # 3 pixels × 2 bytes

    # Red (R=0xFF, G=0, B=0): ((0xF8 & 0xFF) << 8) = 0xF800 → LE: 00 F8
    # Green (G=0xFF): ((0xFC & 0xFF) << 3) = 0x07E0 → LE: E0 07
    # Blue (B=0xFF): (0xFF >> 3) = 0x001F → LE: 1F 00
    assert px[0:2] == b"\x00\xF8"
    assert px[2:4] == b"\xE0\x07"
    assert px[4:6] == b"\x1F\x00"


def test_build_boot_ani_blob_layout():
    """Verificar layout completo 27B file_hdr + 8B BM hdr + pixels."""
    from PIL import Image
    img = Image.new("RGB", (4, 4), color=(0xFF, 0x00, 0x00))  # 4x4 red
    blob = build_boot_ani_blob(img, mode=0)

    # Sizes
    expected_pixels = 4 * 4 * 2  # 32 bytes
    expected_total = 27 + 8 + expected_pixels
    assert len(blob) == expected_total

    # File header bytes
    assert blob[0:2] == b"\xBC\xAF"  # magic
    assert blob[2] == BOOT_ANI_FILE_TYPE  # 0x0B
    assert blob[3] == 0  # mode lo
    assert blob[4] == 0  # mode hi
    assert blob[5:13] == b"\x00" * 8  # reserved
    # total_len BE32 = 8 (BM hdr) + 32 (pixels) = 40 = 0x00000028
    assert blob[13:17] == b"\x00\x00\x00\x28"
    assert blob[17:25] == b"\x00" * 8  # reserved
    # CRC LE16 over blob[27:]
    img_block = blob[27:]
    expected_crc = _crc16_xmodem(img_block, seed=0xFFFF)
    actual_crc = blob[25] | (blob[26] << 8)
    assert actual_crc == expected_crc

    # BM header
    assert blob[27:29] == b"\x42\x4D"  # 'BM'
    assert blob[29:31] == b"\x04\x00"  # width LE16 = 4
    assert blob[31:33] == b"\x04\x00"  # height LE16 = 4
    assert blob[33] == 0x10  # bpp
    assert blob[34] == 0x80  # flag


def test_build_boot_ani_blob_mode_param():
    """Param mode debe quedar en bytes [3:5] LE16."""
    from PIL import Image
    img = Image.new("RGB", (2, 2), color=(0, 0, 0))
    blob = build_boot_ani_blob(img, mode=0x1234)
    assert blob[3] == 0x34
    assert blob[4] == 0x12


def test_query_boot_ani_info_parses_5b_response():
    """query_boot_ani_info debe parsear LE16 width/height + 1B type."""
    transport = MockTransport()
    # 5B response: width=360 LE16, height=360 LE16, type=0x01
    payload = struct.pack("<HHB", 360, 360, 0x01)
    transport.queue_response(QixFrame(flags=0, cmd=0x8A, payload=payload))

    info = query_boot_ani_info(transport)
    assert isinstance(info, BootAnimationInfo)
    assert info.width == 360
    assert info.height == 360
    assert info.fmt_type == 0x01


def test_query_boot_ani_info_raises_on_short_payload():
    """Reply <5B → ValueError (feature no soportada en FW)."""
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0x8A, payload=b"\x00\x00"))
    with pytest.raises(ValueError, match="boot_ani_info reply corto"):
        query_boot_ani_info(transport)
