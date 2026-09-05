"""Tests para qix_ble.sysinfo — RCSP cmd 0x03 (target_info) + 0x07 (sys_info)."""
from __future__ import annotations

import pytest

from qix_ble.errors import BadgeRejected
from qix_ble.rcsp_frame import RcspFrame
from qix_ble.rcsp_session import RcspSession
from qix_ble.sysinfo import (
    CMD_GET_SYS_INFO,
    CMD_GET_TARGET_INFO,
    FLAG_PHONE_REQ,
    Attr,
    _decode_target_info_attr,
    _parse_tlv,
    PUBLIC_SYSINFO_ATTR_NAMES,
    TARGET_INFO_ATTR_NAMES,
    get_sys_info,
    get_target_info,
)
from tests.conftest import MockTransport


def _make_session(authed: bool = True) -> tuple[RcspSession, MockTransport]:
    t = MockTransport()
    t._auth_done = authed
    return RcspSession(t), t


# ── TLV parser tests ─────────────────────────────────────────────────────────

def test_parse_tlv_empty():
    assert _parse_tlv(b"", TARGET_INFO_ATTR_NAMES) == []


def test_parse_tlv_single_attr_ascii():
    """attrLen=5: 1B type + 4B "test" data."""
    payload = bytes([5, 16]) + b"test"  # type 16 = name
    attrs = _parse_tlv(payload, TARGET_INFO_ATTR_NAMES, _decode_target_info_attr)
    assert len(attrs) == 1
    assert attrs[0].type == 16
    assert attrs[0].name == "name"
    assert attrs[0].data == b"test"
    assert attrs[0].decoded == "test"


def test_parse_tlv_mac_address():
    """type 2 = edr_addr, data 6B → mac decoded."""
    payload = bytes([7, 2, 0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc])
    attrs = _parse_tlv(payload, TARGET_INFO_ATTR_NAMES, _decode_target_info_attr)
    assert attrs[0].decoded == "12:34:56:78:9a:bc"


def test_parse_tlv_vid_pid():
    """type 10 = vid_pid, data 4B BE → vid/pid decoded."""
    payload = bytes([5, 10, 0x05, 0xB9, 0xab, 0xcd])
    attrs = _parse_tlv(payload, TARGET_INFO_ATTR_NAMES, _decode_target_info_attr)
    assert attrs[0].decoded == "vid=0x05b9, pid=0xabcd"


def test_parse_tlv_fw_version_or_mtu():
    """type 13 = fw_version (per community PROTOCOL.md) o protocol_mtu (per TS).

    Ambiguo entre community projects — nuestra impl reporta ambos.
    """
    payload = bytes([5, 13, 0x01, 0x10, 0x02, 0x1c])  # community example bytes
    attrs = _parse_tlv(payload, TARGET_INFO_ATTR_NAMES, _decode_target_info_attr)
    assert attrs[0].name == "fw_version"
    # Verifica que output contiene ambas interpretaciones
    assert "fw=1.16.2.28" in attrs[0].decoded
    assert "mtu_alt=272" in attrs[0].decoded


def test_parse_tlv_multiple_attrs():
    """3 attrs back-to-back: name + mac + vid_pid."""
    payload = (
        bytes([3, 16]) + b"E0"  # name = "E0" (short)
        + bytes([7, 2, 0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc])  # edr_addr
        + bytes([5, 10, 0x05, 0xB9, 0xab, 0xcd])  # vid_pid
    )
    attrs = _parse_tlv(payload, TARGET_INFO_ATTR_NAMES, _decode_target_info_attr)
    assert len(attrs) == 3
    assert [a.name for a in attrs] == ["name", "edr_addr", "vid_pid"]


def test_parse_tlv_truncated_data_stops():
    """attrLen claims 10B but only 5 left → stop parsing."""
    payload = bytes([10, 16]) + b"shrt"
    attrs = _parse_tlv(payload, TARGET_INFO_ATTR_NAMES, _decode_target_info_attr)
    assert attrs == []


def test_parse_tlv_unknown_type():
    """attr_type 99 → name=attr_99."""
    payload = bytes([2, 99, 0xAA])
    attrs = _parse_tlv(payload, TARGET_INFO_ATTR_NAMES, _decode_target_info_attr)
    assert attrs[0].name == "attr_99"


def test_parse_tlv_battery_percent():
    """type 0 + 1B = battery percent (sysinfo decoder)."""
    payload = bytes([2, 0, 0x55])  # 85%
    attrs = _parse_tlv(payload, PUBLIC_SYSINFO_ATTR_NAMES)
    assert attrs[0].decoded == "85%"


# ── get_target_info tests ────────────────────────────────────────────────────

def test_get_target_info_happy_path():
    sess, t = _make_session()
    payload = bytes([7, 2, 0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc])  # edr_addr
    resp_body = bytes([0x00, 0x00]) + payload  # [status][seq][TLV]
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_TARGET_INFO, payload=resp_body))

    result = get_target_info(sess)

    assert len(result.attrs) == 1
    assert result.attrs[0].name == "edr_addr"
    assert result.attrs[0].decoded == "12:34:56:78:9a:bc"
    # Verify request frame format
    sent_raw = t.ae01_sent[0]
    sent_frame = RcspFrame.decode(sent_raw)
    assert sent_frame.cmd == CMD_GET_TARGET_INFO
    assert sent_frame.flag == FLAG_PHONE_REQ
    # body = [seq, mask_BE32, platform]
    assert sent_frame.payload[1:5] == b"\xff\xff\xff\xff"  # default mask
    assert sent_frame.payload[5] == 0  # default platform


def test_get_target_info_custom_mask_platform():
    sess, t = _make_session()
    resp_body = bytes([0x00, 0x00])  # status=0 seq=0 sin TLV
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_TARGET_INFO, payload=resp_body))

    get_target_info(sess, mask=0x0000_0010, platform=0x02)

    sent_frame = RcspFrame.decode(t.ae01_sent[0])
    assert sent_frame.payload[1:5] == bytes([0x00, 0x00, 0x00, 0x10])
    assert sent_frame.payload[5] == 0x02


def test_get_target_info_requires_auth():
    sess, t = _make_session(authed=False)
    with pytest.raises(BadgeRejected, match="requires prior auth"):
        get_target_info(sess)


def test_get_target_info_status_rejection():
    sess, t = _make_session()
    t.queue_rcsp_response(
        RcspFrame(flag=0x00, cmd=CMD_GET_TARGET_INFO, payload=bytes([0x02, 0x00]))
    )
    with pytest.raises(BadgeRejected, match="status=0x02"):
        get_target_info(sess)


def test_get_target_info_unexpected_cmd():
    sess, t = _make_session()
    t.queue_rcsp_response(
        RcspFrame(flag=0x00, cmd=0x99, payload=bytes([0x00, 0x00]))
    )
    with pytest.raises(BadgeRejected, match="unexpected cmd"):
        get_target_info(sess)


# ── get_sys_info tests ───────────────────────────────────────────────────────

def test_get_sys_info_happy_path():
    sess, t = _make_session()
    tlv = bytes([2, 0, 0x55])  # battery=85%
    resp_body = bytes([0x00, 0x00, 0xFF]) + tlv  # status, seq, function_id=0xFF, TLV
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO, payload=resp_body))

    result = get_sys_info(sess)

    assert result.function == 0xFF
    assert len(result.attrs) == 1
    assert result.attrs[0].name == "battery"
    assert result.attrs[0].decoded == "85%"


def test_get_sys_info_request_format():
    sess, t = _make_session()
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO,
                                     payload=bytes([0x00, 0x00, 0xFF])))

    get_sys_info(sess, function=0x01, mask=0x000000FF)

    sent_frame = RcspFrame.decode(t.ae01_sent[0])
    # body = [seq, function, mask_BE32]
    assert sent_frame.payload[1] == 0x01
    assert sent_frame.payload[2:6] == bytes([0x00, 0x00, 0x00, 0xFF])


def test_get_sys_info_empty_body():
    """Response con body vacío después de status/seq — return result vacío."""
    sess, t = _make_session()
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO,
                                     payload=bytes([0x00, 0x00])))

    result = get_sys_info(sess, function=0x42)

    assert result.function == 0x42  # fallback al requested
    assert result.attrs == []


def test_get_sys_info_requires_auth():
    sess, t = _make_session(authed=False)
    with pytest.raises(BadgeRejected, match="requires prior auth"):
        get_sys_info(sess)


def test_get_sys_info_status_rejection():
    sess, t = _make_session()
    t.queue_rcsp_response(
        RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO, payload=bytes([0x05, 0x00]))
    )
    with pytest.raises(BadgeRejected, match="status=0x05"):
        get_sys_info(sess)
