"""Tests del frame layer Qix.

Wire-format:
  [0x9E magic][checksum 1B][flags 1B][cmd 1B][len LE16][payload]
  checksum = sum(bytes[2:]) & 0xFF (mod 256)
"""
import pytest
from qix_ble.frame import QixFrame, QIX_MAGIC
from qix_ble.errors import InvalidFrameError, ChecksumError


def test_encode_simple_payload():
    """Frame con payload corto: cmd=0xC0, payload de 3 bytes."""
    frame = QixFrame(flags=0x00, cmd=0xC0, payload=b"\x01\x02\x03")
    encoded = frame.encode()
    # Header: 0x9E magic + checksum + flags + cmd + len LE16 = 6 bytes; payload = 3 bytes => 9 bytes total
    assert len(encoded) == 9
    assert encoded[0] == QIX_MAGIC
    # checksum = (flags + cmd + len_lo + len_hi + payload bytes) & 0xFF
    # = 0x00 + 0xC0 + 0x03 + 0x00 + 0x01 + 0x02 + 0x03 = 0xC9
    assert encoded[1] == 0xC9
    assert encoded[2] == 0x00       # flags
    assert encoded[3] == 0xC0       # cmd
    assert encoded[4:6] == b"\x03\x00"  # length LE16
    assert encoded[6:] == b"\x01\x02\x03"


def test_encode_empty_payload():
    """Frame sin payload: válido (e.g. ACK)."""
    frame = QixFrame(flags=0x00, cmd=0xA6, payload=b"")  # TEST_CLOSE
    encoded = frame.encode()
    assert len(encoded) == 6
    # checksum = (0x00 + 0xA6 + 0x00 + 0x00) & 0xFF = 0xA6
    assert encoded[1] == 0xA6
    assert encoded[4:6] == b"\x00\x00"  # length 0


def test_decode_round_trip():
    """encode → decode preserva flags/cmd/payload."""
    original = QixFrame(flags=0x40, cmd=0xC1, payload=b"\x01\xAB\xCD\xEF")
    decoded = QixFrame.decode(original.encode())
    assert decoded.flags == 0x40
    assert decoded.cmd == 0xC1
    assert decoded.payload == b"\x01\xAB\xCD\xEF"


def test_decode_bad_magic_raises():
    bad = b"\x00\x00\x00\xC0\x00\x00"  # magic byte wrong
    with pytest.raises(InvalidFrameError):
        QixFrame.decode(bad)


def test_decode_bad_checksum_raises():
    """Construir un frame manualmente con checksum incorrecto."""
    bad = bytes([QIX_MAGIC, 0xFF, 0x00, 0xC0, 0x00, 0x00])  # checksum 0xFF, real es 0xC0
    with pytest.raises(ChecksumError):
        QixFrame.decode(bad)


def test_decode_truncated_header_raises():
    bad = b"\x9E\x00\x00"  # solo 3 bytes, < 6 header
    with pytest.raises(InvalidFrameError):
        QixFrame.decode(bad)


def test_decode_truncated_payload_raises():
    """Header dice len=10 pero solo hay 2 bytes de payload."""
    bad = bytes([QIX_MAGIC, 0xCA, 0x00, 0xC0, 0x0A, 0x00, 0x01, 0x02])
    with pytest.raises(InvalidFrameError):
        QixFrame.decode(bad)


def test_stream_full_frame_in_one_chunk():
    """Buffer con 1 frame completo → emite frame, buffer queda vacío."""
    frame = QixFrame(flags=0, cmd=0xC1, payload=b"\xAB\xCD")
    buffer = bytearray(frame.encode())
    extracted, remainder = QixFrame.from_bytes_stream(buffer)
    assert extracted is not None
    assert extracted.cmd == 0xC1
    assert extracted.payload == b"\xAB\xCD"
    assert remainder == bytearray()


def test_stream_partial_frame_returns_none():
    """Buffer con header incompleto → None, buffer preservado."""
    partial = bytearray(b"\x9E\x00\x00")  # solo 3 bytes
    extracted, remainder = QixFrame.from_bytes_stream(partial)
    assert extracted is None
    assert remainder == partial


def test_stream_two_frames_in_one_chunk():
    """Buffer con 2 frames concatenados → primer call emite frame 1,
    segundo call (con el remainder) emite frame 2."""
    f1 = QixFrame(flags=0, cmd=0xC1, payload=b"\x01")
    f2 = QixFrame(flags=0, cmd=0xC3, payload=b"\x02\x03")
    buffer = bytearray(f1.encode() + f2.encode())

    extracted_1, remainder_1 = QixFrame.from_bytes_stream(buffer)
    assert extracted_1.cmd == 0xC1
    assert len(remainder_1) > 0

    extracted_2, remainder_2 = QixFrame.from_bytes_stream(remainder_1)
    assert extracted_2.cmd == 0xC3
    assert remainder_2 == bytearray()


def test_stream_skips_garbage_before_magic():
    """Si el buffer empieza con bytes que NO son magic, descartar hasta encontrar magic."""
    f = QixFrame(flags=0, cmd=0xC1, payload=b"\x42")
    buffer = bytearray(b"\xDE\xAD\xBE\xEF") + bytearray(f.encode())
    extracted, _ = QixFrame.from_bytes_stream(buffer)
    assert extracted is not None
    assert extracted.cmd == 0xC1
