"""Tests de RcspSession.read_small_file (cmd 0x28 op=1).

Port del readSmallFileE87 del TS upstream. Body request:
  [op=01][type 1B][id 2B BE][offset 2B BE][len 2B BE][flag=01]

Response: variantes manejadas
  Variant A (típica): [ret][crc_hi][crc_lo][data...]
  Variant B (algunos stacks): [op][ret=0][crc_hi][crc_lo][data...]
"""
import pytest
from tests.conftest import MockTransport
from qix_ble.rcsp_frame import RcspFrame
from qix_ble.rcsp_session import (
    RcspSession, FileEntry,
    CMD_SMALL_FILE_OP, FLAG_DEVICE_RESP, FLAG_PHONE_REQ,
)
from qix_ble.errors import BadgeRejected


def _auth_done_mock() -> MockTransport:
    m = MockTransport()
    m._auth_done = True
    return m


def test_read_small_file_request_byte_exact():
    """Body request: op=01 type=02 id=000a offset=0000 len=0100 flag=01.

    maxLen = min(0xffff, max(size, 256)) — del TS. size=10 → max(10,256)=256=0x0100.
    """
    t = _auth_done_mock()
    entry = FileEntry(type=0x02, type_name="file", id=0x000a, size=10, cluster=0, name="x")
    # Response: ret=0, crc=0x1234, data=10 bytes
    payload = bytes([0x00, 0x12, 0x34]) + b"A" * 10
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_SMALL_FILE_OP, payload=payload))

    sess = RcspSession(t)
    data, crc = sess.read_small_file(entry)
    assert data == b"A" * 10
    assert crc == 0x1234

    # Verify request body byte-exact
    # head=fedcba flag=c0 cmd=28 len_be=0009 body=[01][02][000a][0000][0100][01] tail=ef
    expected = bytes.fromhex("fedcba" "c0" "28" "0009"
                              "01"     # op=1
                              "02"     # type
                              "000a"   # id BE
                              "0000"   # offset BE
                              "0100"   # len BE = 256
                              "01"     # flag=1
                              "ef")
    assert t.ae01_sent[0] == expected


def test_read_small_file_variant_b_op_echoed():
    """Variant B response: device echoes op antes del ret."""
    t = _auth_done_mock()
    entry = FileEntry(type=0x02, type_name="file", id=0x0001, size=5, cluster=0, name="x")
    # [op=01][ret=00][crc=0xab12][data]
    payload = bytes([0x01, 0x00, 0xab, 0x12]) + b"hello"
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_SMALL_FILE_OP, payload=payload))

    sess = RcspSession(t)
    data, crc = sess.read_small_file(entry)
    assert data == b"hello"
    assert crc == 0xab12


def test_read_small_file_status_nonzero_raises():
    """ret != 0 → BadgeRejected."""
    t = _auth_done_mock()
    entry = FileEntry(type=0x02, type_name="file", id=0x0001, size=5, cluster=0, name="x")
    payload = bytes([0xff, 0x00, 0x00])  # ret=0xff
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_SMALL_FILE_OP, payload=payload))

    sess = RcspSession(t)
    with pytest.raises(BadgeRejected, match="status"):
        sess.read_small_file(entry)


def test_read_small_file_size_too_large_raises_value_error():
    """File > 64KB → ValueError sin contactar el badge."""
    t = _auth_done_mock()
    entry = FileEntry(type=0x02, type_name="file", id=0x0001, size=70000, cluster=0, name="big")
    sess = RcspSession(t)
    with pytest.raises(ValueError, match="too large"):
        sess.read_small_file(entry)
    # No frame sent
    assert t.ae01_sent == []


def test_read_small_file_request_len_uses_size_when_above_256():
    """size=50000 → req len=50000 (max(50000, 256)=50000)."""
    t = _auth_done_mock()
    entry = FileEntry(type=0x02, type_name="file", id=0x0001, size=50000, cluster=0, name="x")
    payload = bytes([0x00, 0x00, 0x00]) + b"X" * 50000
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_SMALL_FILE_OP, payload=payload))

    sess = RcspSession(t)
    data, _ = sess.read_small_file(entry)
    assert len(data) == 50000

    # Verify request len field (BE16 en offset 11-12 del wire frame:
    # head(3) + flag(1) + cmd(1) + len_be(2) + op(1) + type(1) + id(2) + offset(2) + len(2)…
    req_len_be = int.from_bytes(t.ae01_sent[0][13:15], "big")
    assert req_len_be == 50000
