"""Tests del RCSP frame layer (JieLi AE00 framing).

Wire-format: fe dc ba [flag 1B][cmd 1B][len BE16][payload] ef

Real-world KAT extraído del btsnoop Day 1: packet 576 (Phone → AE01)
es un GetTargetInfo request RCSP-framed:
  fe dc ba c0 03 00 06 74 ff ff ff ff 00 ef
           ^^ flag=0xc0
              ^^ cmd=0x03 (GetTargetInfo)
                 ^^ ^^ len BE16 = 6
                       └─── 6B payload ───┘
                                            ^^ tail
"""
import pytest
from qix_ble.rcsp_frame import RcspFrame, RCSP_HEAD, RCSP_TAIL
from qix_ble.errors import InvalidFrameError


def test_encode_empty_payload():
    """flag=0 cmd=0x03 (GetTargetInfo) sin payload."""
    f = RcspFrame(flag=0x00, cmd=0x03, payload=b"")
    encoded = f.encode()
    # head(3) + flag(1) + cmd(1) + len_be16(2) + payload(0) + tail(1) = 8B
    assert len(encoded) == 8
    assert encoded[0:3] == b"\xfe\xdc\xba"
    assert encoded[3] == 0x00       # flag
    assert encoded[4] == 0x03       # cmd
    assert encoded[5:7] == b"\x00\x00"   # len BE16 = 0
    assert encoded[7] == 0xef


def test_encode_with_payload_bigendian_length():
    """Length se codifica BIG-endian (opuesto a Qix que es LE)."""
    payload = b"hello world!"  # 12 bytes
    f = RcspFrame(flag=0x01, cmd=0x42, payload=payload)
    encoded = f.encode()
    assert encoded[0:3] == b"\xfe\xdc\xba"
    assert encoded[3] == 0x01
    assert encoded[4] == 0x42
    assert encoded[5:7] == b"\x00\x0c"   # 12 BE16
    assert encoded[7:19] == payload
    assert encoded[19] == 0xef


def test_decode_round_trip():
    f = RcspFrame(flag=0x00, cmd=0x03, payload=b"\xaa\xbb\xcc")
    decoded = RcspFrame.decode(f.encode())
    assert decoded.flag == 0x00
    assert decoded.cmd == 0x03
    assert decoded.payload == b"\xaa\xbb\xcc"


def test_decode_bad_head_raises():
    bad = b"\xff\xff\xff\x00\x03\x00\x00\xef"
    with pytest.raises(InvalidFrameError, match="head"):
        RcspFrame.decode(bad)


def test_decode_bad_tail_raises():
    """Construido manual: head OK, length OK, pero último byte != 0xEF."""
    bad = b"\xfe\xdc\xba\x00\x03\x00\x00\xff"
    with pytest.raises(InvalidFrameError, match="tail"):
        RcspFrame.decode(bad)


def test_decode_truncated_raises():
    """Header dice len=10 pero solo hay 2 bytes de payload + tail."""
    bad = b"\xfe\xdc\xba\x00\x03\x00\x0a\x01\x02\xef"  # length=10 declarado, 2 reales + 1 tail
    with pytest.raises(InvalidFrameError):
        RcspFrame.decode(bad)


def test_stream_full_frame_in_one_chunk():
    f = RcspFrame(flag=0x00, cmd=0x03, payload=b"\x42")
    buf = bytearray(f.encode())
    extracted, remainder = RcspFrame.from_bytes_stream(buf)
    assert extracted is not None
    assert extracted.cmd == 0x03
    assert extracted.payload == b"\x42"
    assert remainder == bytearray()


def test_stream_partial_frame_returns_none():
    """Buffer con head pero length payload incompleto."""
    partial = bytearray(b"\xfe\xdc\xba\x00\x03\x00\x10\x01\x02\x03")  # len=16, solo 3 payload
    extracted, remainder = RcspFrame.from_bytes_stream(partial)
    assert extracted is None
    assert remainder == partial


def test_stream_skips_garbage_before_head():
    f = RcspFrame(flag=0, cmd=0x03, payload=b"\x42")
    buf = bytearray(b"\x00\x11\x22") + bytearray(f.encode())
    extracted, _ = RcspFrame.from_bytes_stream(buf)
    assert extracted is not None
    assert extracted.cmd == 0x03


def test_decode_get_target_info_real_from_btsnoop():
    """KAT real: packet 576 del btsnoop Day 1 = GetTargetInfo request.

    Bytes RAW del paquete (post-ATT header):
    fedcba c0 03 0006 74ffffffff00 ef
    """
    raw = bytes.fromhex("fedcbac003000674ffffffff00ef")
    f = RcspFrame.decode(raw)
    assert f.flag == 0xc0
    assert f.cmd == 0x03
    assert f.payload == bytes.fromhex("74ffffffff00")
    assert len(f.payload) == 6
