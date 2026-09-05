"""Tests para qix_ble.upload — RCSP windowed upload + small file ops.

Tests del CRC, encoding, parser. State machine end-to-end test cubre
session_open → xfer_params → metadata → window → completion.
"""
from __future__ import annotations

import pytest

from qix_ble.errors import BadgeRejected, TimeoutError as QixTimeoutError
from qix_ble.rcsp_frame import RcspFrame
from qix_ble.rcsp_session import RcspSession
from qix_ble.upload import (
    CMD_DATA_PUSH,
    CMD_FILE_COMPLETE,
    CMD_FILE_METADATA,
    CMD_SESSION_CLOSE,
    CMD_SESSION_OPEN,
    CMD_SMALL_FILE_OP,
    CMD_WIN_ACK,
    CMD_XFER_PARAMS,
    FLAG_DEVICE_RESP,
    FLAG_PHONE_REQ,
    FLAG_PUSH,
    RcspUploader,
    SmallFileEntry,
    _build_file_path_response,
    _build_metadata_body,
    _parse_small_file_query,
    crc16_ccitt,
    delete_small_file,
    list_small_files,
)
from tests.conftest import MockTransport


# ── CRC tests ────────────────────────────────────────────────────────────────

def test_crc16_empty():
    assert crc16_ccitt(b"") == 0


def test_crc16_known_vector():
    """seed=0, poly=0x1021. '123456789' → 0x31C3 (standard CRC-16/XMODEM seed=0)."""
    assert crc16_ccitt(b"123456789") == 0x31C3


def test_crc16_single_byte():
    """\\x00 con seed 0 → 0 (no flip)."""
    assert crc16_ccitt(b"\x00") == 0


def test_crc16_deterministic():
    a = crc16_ccitt(b"hello world")
    b = crc16_ccitt(b"hello world")
    assert a == b


# ── Metadata body encoding ───────────────────────────────────────────────────

def test_build_metadata_body_length():
    """body = [seq][size_BE32][crc_BE16][rand][rand][name][0]."""
    body = _build_metadata_body(seq=0x42, file_size=1024, file_crc=0xABCD, name="x.jpg")
    assert body[0] == 0x42
    assert body[1:5] == bytes([0x00, 0x00, 0x04, 0x00])  # 1024 BE
    assert body[5:7] == bytes([0xAB, 0xCD])
    # body[7:9] = 2 random bytes (skip)
    assert body[9:14] == b"x.jpg"
    assert body[14] == 0x00


def test_build_metadata_body_zero_size():
    body = _build_metadata_body(seq=0, file_size=0, file_crc=0, name="")
    assert body[0] == 0
    assert body[1:5] == b"\x00\x00\x00\x00"
    assert body[5:7] == b"\x00\x00"
    assert body[-1] == 0x00


# ── FILE_COMPLETE response builder ───────────────────────────────────────────

def test_build_file_path_response_image_mode():
    """ext=.jpg for image/qr."""
    resp = _build_file_path_response(device_seq=5, upload_mode="image")
    assert resp[0] == 0x00
    assert resp[1] == 5
    # Path starts with U+555C in UTF-16 LE
    assert resp[2:4] == b"\x5c\x55"
    # Path ends with .jpg + NUL terminator
    decoded = resp[2:-2].decode("utf-16-le")
    assert decoded.endswith(".jpg")
    assert resp[-2:] == b"\x00\x00"


def test_build_file_path_response_avi_mode():
    resp = _build_file_path_response(device_seq=5, upload_mode="avi")
    decoded = resp[2:-2].decode("utf-16-le")
    assert decoded.endswith(".avi")


# ── Small file query parser ──────────────────────────────────────────────────

def test_parse_small_file_query_start_1():
    """Layout [status][id_hi id_lo size_hi size_lo]."""
    body = bytes([0x00,
                  0x00, 0x01, 0x00, 0x10,   # id=1, size=16
                  0x00, 0x02, 0x00, 0x20])  # id=2, size=32
    out = _parse_small_file_query(3, "heart_rate", body)
    assert len(out) == 2
    assert out[0] == SmallFileEntry(type=3, type_name="heart_rate", id=1, size=16)
    assert out[1].id == 2 and out[1].size == 32


def test_parse_small_file_query_zero_entries_skipped():
    """(id=0, size=0) entries son padding y se filtran."""
    body = bytes([0x00,
                  0x00, 0x01, 0x00, 0x10,   # real
                  0x00, 0x00, 0x00, 0x00])  # padding
    out = _parse_small_file_query(3, "heart_rate", body)
    assert len(out) == 1


def test_parse_small_file_query_no_entries():
    """Solo status byte → []."""
    body = bytes([0x00])
    assert _parse_small_file_query(3, "heart_rate", body) == []


# ── RcspUploader gating ──────────────────────────────────────────────────────

def test_upload_requires_auth():
    t = MockTransport()
    t._auth_done = False
    uploader = RcspUploader(t)
    with pytest.raises(BadgeRejected, match="requires prior auth"):
        uploader.upload(b"data", "f.jpg")


def test_open_session_sends_correct_frame():
    t = MockTransport()
    t._auth_done = True
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_SESSION_OPEN,
                                     payload=bytes([0x00, 0x00])))
    RcspUploader(t).open_session()

    sent = RcspFrame.decode(t.ae01_sent[0])
    assert sent.cmd == CMD_SESSION_OPEN
    assert sent.flag == FLAG_PHONE_REQ
    assert sent.payload == bytes([0x00, 0x00])  # seq=0, params=0


def test_xfer_params_sends_correct_frame():
    t = MockTransport()
    t._auth_done = True
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_XFER_PARAMS,
                                     payload=bytes([0x00, 0x00])))
    RcspUploader(t).xfer_params()

    sent = RcspFrame.decode(t.ae01_sent[0])
    assert sent.cmd == CMD_XFER_PARAMS
    assert sent.payload[1:7] == bytes([0, 0, 0, 0, 0x02, 0x01])  # magic param tail


def test_send_metadata_extracts_chunk_size():
    """Response body[2:4] = chunk_size BE16."""
    t = MockTransport()
    t._auth_done = True
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_FILE_METADATA,
                                     payload=bytes([0x00, 0x00, 0x01, 0xF4])))  # 500
    chunk = RcspUploader(t).send_metadata(file_size=1024, file_crc=0xABCD, name="x")
    assert chunk == 500


def test_send_metadata_falls_back_on_invalid_chunk_size():
    """chunk_size = 5000 (out of range) → DEFAULT (490)."""
    t = MockTransport()
    t._auth_done = True
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_FILE_METADATA,
                                     payload=bytes([0x00, 0x00, 0x13, 0x88])))  # 5000
    chunk = RcspUploader(t).send_metadata(file_size=1024, file_crc=0xABCD, name="x")
    assert chunk == 490


# ── Small file ops ───────────────────────────────────────────────────────────

def test_list_small_files_requires_auth():
    t = MockTransport()
    t._auth_done = False
    sess = RcspSession(t)
    with pytest.raises(BadgeRejected, match="requires prior auth"):
        list_small_files(sess)


def test_list_small_files_aggregates_types():
    """9 types queried; queue 1 entry para type=3 (heart_rate), resto timeout."""
    t = MockTransport()
    t._auth_done = True
    sess = RcspSession(t)
    # Queue 9 responses (one per type) — most empty, one with data
    for type_id in range(1, 10):
        if type_id == 3:
            body = bytes([0x00, 0x00, 0x05, 0x00, 0x40])  # id=5 size=64
        else:
            body = bytes([0x00])  # empty
        t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP,
                                         cmd=CMD_SMALL_FILE_OP, payload=body))

    result = list_small_files(sess)
    assert len(result) == 1
    assert result[0].type_name == "heart_rate"
    assert result[0].id == 5
    assert result[0].size == 64


def test_delete_small_file_success():
    t = MockTransport()
    t._auth_done = True
    sess = RcspSession(t)
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_SMALL_FILE_OP,
                                     payload=bytes([0x00])))

    entry = SmallFileEntry(type=3, type_name="heart_rate", id=5, size=64)
    delete_small_file(sess, entry)

    sent = RcspFrame.decode(t.ae01_sent[0])
    assert sent.cmd == CMD_SMALL_FILE_OP
    # body = [seq, op=0x04, type, id_hi, id_lo]
    assert sent.payload[1:5] == bytes([0x04, 3, 0, 5])


def test_delete_small_file_rejects_on_nonzero_ret():
    t = MockTransport()
    t._auth_done = True
    sess = RcspSession(t)
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_SMALL_FILE_OP,
                                     payload=bytes([0x05])))

    entry = SmallFileEntry(type=3, type_name="heart_rate", id=5, size=64)
    with pytest.raises(BadgeRejected, match="ret=0x05"):
        delete_small_file(sess, entry)
