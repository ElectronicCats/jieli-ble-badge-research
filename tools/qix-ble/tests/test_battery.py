"""Tests para qix_ble.battery — DOS paths: legacy cmd 0x27 + RCSP sys_info attr 0."""
from __future__ import annotations

import pytest

from qix_ble.battery import query_battery_via_sysinfo
from qix_ble.battery import (
    BATTERY_REQ_PAYLOAD,
    CMD_BATTERY,
    BatteryStatus,
    query_battery,
    query_battery_legacy,
)
from qix_ble.errors import BadgeRejected, TimeoutError as QixTimeoutError
from qix_ble.frame import QixFrame
from qix_ble.rcsp_frame import RcspFrame
from qix_ble.rcsp_session import RcspSession
from qix_ble.sysinfo import CMD_GET_SYS_INFO
from tests.conftest import MockTransport


# ── Legacy tests (Qix cmd 0x27 path, port community CLI) ─────────────────────

# Legacy tests usan query_battery_legacy — el cmd 0x27 path roto en E87 prod.


def test_query_battery_happy_path():
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BATTERY, payload=b"\x01\x55"))

    result = query_battery_legacy(transport)

    assert result == BatteryStatus(mode=0x01, percent=0x55)
    cmd_sent, payload_sent, flags_sent = transport.sent[0]
    assert cmd_sent == CMD_BATTERY
    assert payload_sent == BATTERY_REQ_PAYLOAD
    assert flags_sent == 0


def test_query_battery_full():
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BATTERY, payload=b"\x02\x64"))

    result = query_battery_legacy(transport)

    assert result.mode == 0x02
    assert result.percent == 100


def test_query_battery_zero_percent():
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BATTERY, payload=b"\x01\x00"))

    result = query_battery_legacy(transport)

    assert result.percent == 0


def test_query_battery_short_payload_raises():
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BATTERY, payload=b"\x01"))

    with pytest.raises(BadgeRejected, match="payload corto"):
        query_battery_legacy(transport)


def test_query_battery_empty_payload_raises():
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BATTERY, payload=b""))

    with pytest.raises(BadgeRejected, match="payload corto"):
        query_battery_legacy(transport)


def test_query_battery_timeout_propagates():
    transport = MockTransport()
    with pytest.raises(QixTimeoutError):
        query_battery_legacy(transport)


def test_query_battery_extra_payload_bytes_ignored():
    """Forward compat: si firmware agrega bytes al final, ignorar (solo usamos primeros 2)."""
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BATTERY, payload=b"\x01\x4b\xaa\xbb"))

    result = query_battery_legacy(transport)

    assert result.mode == 0x01
    assert result.percent == 0x4b


# ── RCSP sys_info attr 0 tests (preferred path para FW E87 production) ──────

def _make_sess_authed() -> tuple[RcspSession, MockTransport]:
    t = MockTransport()
    t._auth_done = True
    return RcspSession(t), t


def test_query_battery_via_sysinfo_happy_path():
    """sys_info response con attr 0 (battery) = 73%."""
    sess, t = _make_sess_authed()
    # body = [status=0, seq_echo=0, function_id=0xFF, TLV: attrLen=2 type=0 data=0x49]
    payload = bytes([0x00, 0x00, 0xFF, 0x02, 0x00, 0x49])
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO, payload=payload))

    result = query_battery_via_sysinfo(sess)
    assert result.percent == 0x49
    assert result.mode == 0


def test_query_battery_via_sysinfo_zero_percent():
    sess, t = _make_sess_authed()
    payload = bytes([0x00, 0x00, 0xFF, 0x02, 0x00, 0x00])
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO, payload=payload))
    assert query_battery_via_sysinfo(sess).percent == 0


def test_query_battery_via_sysinfo_no_battery_attr_raises():
    """sys_info sin attr type=0 → BadgeRejected."""
    sess, t = _make_sess_authed()
    payload = bytes([0x00, 0x00, 0xFF, 0x02, 0x01, 0x32])  # solo type 1 (volume)
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO, payload=payload))

    with pytest.raises(BadgeRejected, match="sys_info no incluyó attr 0"):
        query_battery_via_sysinfo(sess)


def test_query_battery_via_sysinfo_picks_battery_among_many_attrs():
    sess, t = _make_sess_authed()
    tlv = bytes([2, 1, 0x40]) + bytes([2, 0, 0x55]) + bytes([2, 2, 0x01])
    payload = bytes([0x00, 0x00, 0xFF]) + tlv
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO, payload=payload))
    assert query_battery_via_sysinfo(sess).percent == 0x55
