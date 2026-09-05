"""Tests para qix_ble.bootstrap — Phase 1-5 community dance.

Validan que las fases mandan los bytes correctos. Wait paths son ack-tolerant
(timeout no es error), pero tests verifican que se llaman.
"""
from __future__ import annotations

import pytest

from qix_ble.bootstrap import (
    COMMUNITY_PHASE1_TRIGGER,
    QIX_CMD_DC,
    QIX_CMD_DISPLAY,
    QIX_CMD_FF,
    QIX_CMD_GENERIC_20,
    QIX_CMD_INIT_29,
    QIX_CMD_SCREEN_INFO,
    QIX_CMD_SCREEN_INFO_RESP,
    QIX_CMD_SET_TIME,
    CMD_GET_SYS_INFO,
    CMD_GET_TARGET_INFO,
    CMD_RESET_AUTH,
    FLAG_PHONE_REQ,
    run_bootstrap,
)
from qix_ble.errors import BadgeRejected
from qix_ble.frame import QixFrame
from qix_ble.rcsp_frame import RcspFrame
from tests.conftest import MockTransport


def _no_sleep(_):
    pass


def _make_mock_authed() -> MockTransport:
    t = MockTransport()
    t._auth_done = True
    return t


# ── Auth gating ──────────────────────────────────────────────────────────────

def test_bootstrap_requires_auth():
    t = MockTransport()
    t._auth_done = False
    with pytest.raises(BadgeRejected, match="requires prior auth"):
        run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)


# ── Phase 1: cmd 0x06 + community trigger ────────────────────────────────────

def test_bootstrap_phase1_sends_reset_auth_rcsp():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)

    rcsp_sent = [RcspFrame.decode(raw) for raw in t.ae01_sent]
    phase1 = next(f for f in rcsp_sent if f.cmd == CMD_RESET_AUTH)
    assert phase1.flag == FLAG_PHONE_REQ
    assert phase1.payload == bytes([0x02, 0x00, 0x01])


def test_bootstrap_phase1_sends_community_trigger_to_fd02():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)

    assert hasattr(t, "fd02_raw_sent")
    assert COMMUNITY_PHASE1_TRIGGER in t.fd02_raw_sent


def test_bootstrap_phase1_returns_false_on_timeout():
    """MockTransport sin scripted responses → wait_for_cmd timeouts → phase=False."""
    t = _make_mock_authed()
    result = run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    assert result["phase_1"] is False
    assert result["phase_3"] is False
    assert result["phase_4"] is False


def test_bootstrap_phase1_returns_true_on_ack():
    """Queue acks: phase 1/3/4 son RCSP (AE02), phase_5_c7 es Qix (FD00)."""
    t = _make_mock_authed()
    # Phase 1/3/4 → RCSP responses (queue_rcsp_response)
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_RESET_AUTH, payload=b""))
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_TARGET_INFO, payload=b""))
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_GET_SYS_INFO, payload=b""))
    # Phase 5 c7 → Qix queue
    t.queue_response(QixFrame(flags=0, cmd=QIX_CMD_SCREEN_INFO_RESP, payload=b""))

    result = run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    assert result["phase_1"] is True
    assert result["phase_3"] is True
    assert result["phase_4"] is True
    assert result["phase_5_c7"] is True


# ── Phase 2: set time + display + init ───────────────────────────────────────

def test_bootstrap_phase2_sends_set_time():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)

    time_frames = [s for s in t.sent if s[0] == QIX_CMD_SET_TIME]
    assert len(time_frames) == 1
    cmd, payload, flags = time_frames[0]
    assert len(payload) == 7  # year_lo year_hi month day 00 hour minute
    assert flags == 0x08


def test_bootstrap_phase2_sends_display_cmd():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    display = [s for s in t.sent if s[0] == QIX_CMD_DISPLAY]
    assert len(display) == 1
    assert display[0][1] == b"\x01"


def test_bootstrap_phase2_sends_init_29():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    init_29 = [s for s in t.sent if s[0] == QIX_CMD_INIT_29]
    # Phase 2 sends 1, Phase 5 sends 1 → total 2
    assert len(init_29) == 2
    assert all(p == b"\x80" for _, p, _ in init_29)


# ── Phase 3: cmd 0x03 + 0xC6 + 0x20 ──────────────────────────────────────────

def test_bootstrap_phase3_sends_target_info_rcsp():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)

    rcsp_sent = [RcspFrame.decode(raw) for raw in t.ae01_sent]
    target_info = [f for f in rcsp_sent if f.cmd == CMD_GET_TARGET_INFO]
    assert len(target_info) == 1
    # body = [seq, mask_BE32=0xFFFFFFFF, platform=0x01]
    assert target_info[0].payload[1:5] == b"\xff\xff\xff\xff"
    assert target_info[0].payload[5] == 0x01


def test_bootstrap_phase3_sends_qix_20():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    cmd_20 = [s for s in t.sent if s[0] == QIX_CMD_GENERIC_20]
    assert len(cmd_20) == 1
    assert cmd_20[0][1] == b"\xff\x07"


# ── Phase 4: cmd 0x07 + 0xFF×2 ───────────────────────────────────────────────

def test_bootstrap_phase4_sends_sys_info_rcsp():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    rcsp_sent = [RcspFrame.decode(raw) for raw in t.ae01_sent]
    sys_info = [f for f in rcsp_sent if f.cmd == CMD_GET_SYS_INFO]
    assert len(sys_info) == 1
    # body = [seq, function=0xFF, mask_BE32=0xFFFFFFFF]
    assert sys_info[0].payload[1] == 0xFF
    assert sys_info[0].payload[2:6] == b"\xff\xff\xff\xff"


def test_bootstrap_phase4_sends_two_qix_ff_writes():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    cmd_ff = [s for s in t.sent if s[0] == QIX_CMD_FF]
    assert len(cmd_ff) == 2
    assert cmd_ff[0][1] == b"\x22\x00"
    assert cmd_ff[1][1] == b"\x24\x00"


# ── Phase 5: cmd 0x29 replay + 0xC6 + 0xDC ───────────────────────────────────

def test_bootstrap_phase5_sends_screen_info_request():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    screen_info = [s for s in t.sent if s[0] == QIX_CMD_SCREEN_INFO]
    # Phase 3 + Phase 5 → 2 writes
    assert len(screen_info) == 2


def test_bootstrap_phase5_sends_dc_command():
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    cmd_dc = [s for s in t.sent if s[0] == QIX_CMD_DC]
    assert len(cmd_dc) == 1
    assert cmd_dc[0][1] == b"\x0c"


# ── Overall ordering integrity ───────────────────────────────────────────────

def test_bootstrap_phase_order_p1_before_p3():
    """RCSP cmd 0x06 (Phase 1) debe mandarse antes que cmd 0x03 (Phase 3)."""
    t = _make_mock_authed()
    run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    rcsp_cmds = [RcspFrame.decode(raw).cmd for raw in t.ae01_sent]
    assert rcsp_cmds == [CMD_RESET_AUTH, CMD_GET_TARGET_INFO, CMD_GET_SYS_INFO]


def test_bootstrap_returns_all_phase_keys():
    t = _make_mock_authed()
    result = run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    assert set(result.keys()) == {
        "phase_1", "phase_3", "phase_4", "phase_5_c7", "phase_5_fd03",
    }


def test_bootstrap_partial_ack():
    """Solo phase_1 acked → resto False, no exception."""
    t = _make_mock_authed()
    t.queue_rcsp_response(RcspFrame(flag=0x00, cmd=CMD_RESET_AUTH, payload=b""))
    result = run_bootstrap(t, sleep=_no_sleep, phase_ack_timeout=0.05)
    assert result["phase_1"] is True
    assert result["phase_3"] is False
    assert result["phase_4"] is False
