"""Tests unitarios para qix_ble.health_dump."""
import struct

import pytest

from qix_ble.frame import QixFrame
from qix_ble.health_dump import (
    CMD_REQ_DATA,
    CMD_RET_BATTERY,
    CMD_RET_HR,
    CMD_RET_OXYGEN,
    CMD_RET_PRESSURE,
    CMD_RET_SLEEP,
    CMD_RET_STEP,
    CMD_RET_SYNCH_STATE,
    DATA_TYPE_MASK,
    dump_health,
    parse_battery,
    parse_hr,
    parse_oxygen,
    parse_pressure,
    parse_sleep,
    parse_steps,
    types_to_mask,
)
from tests.conftest import MockTransport


# ── parsers ─────────────────────────────────────────────────────────────────

def test_parse_hr_single_entry():
    """RET_HR_DATA empírico from btsnoop: 19020401ea0000 → 2025-02-04 1 entry."""
    payload = bytes.fromhex("19020401ea0000")
    # Y=0x19 (=25→2025), M=2, D=4, N=1, ts=0x00ea (LE)=234, bpm=0
    entries = parse_hr(payload)
    assert len(entries) == 1
    assert entries[0]["date"] == (2025, 2, 4)
    assert entries[0]["ts_min"] == 234
    assert entries[0]["bpm"] == 0


def test_parse_hr_multi_entry():
    payload = bytes([26, 5, 17, 2]) + \
              bytes([0x00, 0x05, 72]) + \
              bytes([0x10, 0x05, 80])
    entries = parse_hr(payload)
    assert len(entries) == 2
    assert entries[0] == {"date": (2026, 5, 17), "ts_min": 0x0500, "bpm": 72}
    assert entries[1] == {"date": (2026, 5, 17), "ts_min": 0x0510, "bpm": 80}


def test_parse_steps_single_entry():
    """RET_STEP_DATA: [Y][M][D][N] + 17B record."""
    payload = bytes([26, 5, 17, 1]) + \
              bytes([0x00, 0x01]) + \
              bytes([0x05]) + \
              bytes([0x10, 0x00]) + \
              (1500).to_bytes(4, "little") + \
              (1200).to_bytes(4, "little") + \
              (90).to_bytes(4, "little")
    entries = parse_steps(payload)
    assert len(entries) == 1
    e = entries[0]
    assert e["date"] == (2026, 5, 17)
    assert e["ts_min"] == 0x0100
    assert e["mode"] == 5
    assert e["time"] == 0x0010
    assert e["step"] == 1500
    assert e["dist"] == 1200
    assert e["cal"] == 90


def test_parse_sleep_period_markers():
    """sleep entries son period start markers, no aggregates."""
    payload = bytes([26, 5, 17, 2]) + \
              bytes([0x00, 0x05, 1]) + \
              bytes([0x80, 0x05, 2])
    entries = parse_sleep(payload)
    assert len(entries) == 2
    assert entries[0] == {"date": (2026, 5, 17), "ts_min": 0x0500, "mode": 1}
    assert entries[1] == {"date": (2026, 5, 17), "ts_min": 0x0580, "mode": 2}


def test_parse_pressure_swap_low_high():
    """SDK swap si low > high."""
    # Caso normal: low=80, high=120
    p = bytes([26, 5, 17, 1, 0x00, 0x06, 80, 120])
    e = parse_pressure(p)[0]
    assert e["systolic"] == 120 and e["diastolic"] == 80
    # Caso swap: low=120, high=80 → SDK swap
    p = bytes([26, 5, 17, 1, 0x00, 0x06, 120, 80])
    e = parse_pressure(p)[0]
    assert e["systolic"] == 120 and e["diastolic"] == 80


def test_parse_oxygen():
    payload = bytes([26, 5, 17, 1, 0x00, 0x07, 98])
    e = parse_oxygen(payload)[0]
    assert e["spo2"] == 98


def test_parse_battery():
    e = parse_battery(b"\x00\x35")
    assert e == {"charge_mode": 0, "percent": 0x35}


# ── mask conversion ─────────────────────────────────────────────────────────

def test_types_to_mask_known():
    assert types_to_mask(["steps"]) == 0x01
    assert types_to_mask(["hr"]) == 0x04
    assert types_to_mask(["steps", "hr"]) == 0x05
    # All types ≠ 0xBF (0xBF includes bit 7 which is ext, not a data type)
    all_types_mask = types_to_mask(list(DATA_TYPE_MASK))
    assert all_types_mask == 0x3F  # bits 0-5


def test_types_to_mask_unknown():
    with pytest.raises(KeyError):
        types_to_mask(["unknown_type"])


# ── state machine ──────────────────────────────────────────────────────────

def test_dump_health_collects_until_sync_end():
    """End-to-end: TX cmd 0x29 → RX SYNC_START → HR frame → SYNC_END."""
    transport = MockTransport()
    # Mock receive sequence: SYNC_START → HR data → SYNC_END
    transport.queue_response(QixFrame(flags=0, cmd=CMD_RET_SYNCH_STATE,
                                      payload=bytes([0x00, 0xbf])))
    # HR: Y=26→2026, M=5, D=17, N=1, ts_LE16=234 (0x00ea), bpm=0x50=80
    transport.queue_response(QixFrame(flags=0, cmd=CMD_RET_HR,
                                      payload=bytes([26, 5, 17, 1, 0xea, 0x00, 0x50])))
    transport.queue_response(QixFrame(flags=0, cmd=CMD_RET_SYNCH_STATE,
                                      payload=bytes([0x01, 0xbf])))

    result = dump_health(transport, types=["hr"], timeout_per_frame=1.0,
                         total_timeout=5.0)
    # raw_frames counts only frames in the inner loop (post SYNC_START): HR + SYNC_END
    assert result.raw_frames == 2
    assert len(result.hr) == 1
    assert result.hr[0]["bpm"] == 0x50
    # Verify cmd 0x29 was sent with mask=0x04 (hr bit)
    sent_cmds = [(c, p[0]) for c, p, *_ in transport.sent]
    assert (CMD_REQ_DATA, 0x04) in sent_cmds


def test_dump_health_all_types_default_mask():
    """types=None → mask 0xBF (all except bit 6)."""
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_RET_SYNCH_STATE,
                                      payload=bytes([0x00, 0xbf])))
    transport.queue_response(QixFrame(flags=0, cmd=CMD_RET_SYNCH_STATE,
                                      payload=bytes([0x01, 0xbf])))

    dump_health(transport, types=None, timeout_per_frame=1.0, total_timeout=5.0)
    sent_cmds = [(c, p[0]) for c, p, *_ in transport.sent]
    assert (CMD_REQ_DATA, 0xBF) in sent_cmds


def test_dump_health_auto_ack_on_flags_bit_1():
    """Frame con flags & 0x02 → debe enviar ack cmd 0xFF [cmd_echo, 0x00]."""
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_RET_SYNCH_STATE,
                                      payload=bytes([0x00, 0xbf])))
    # HR frame con flags=0x02 (request ack); N=1 record
    transport.queue_response(QixFrame(flags=0x02, cmd=CMD_RET_HR,
                                      payload=bytes([26, 5, 17, 1, 0xea, 0x00, 0x50])))
    transport.queue_response(QixFrame(flags=0, cmd=CMD_RET_SYNCH_STATE,
                                      payload=bytes([0x01, 0xbf])))

    dump_health(transport, types=["hr"], timeout_per_frame=1.0, total_timeout=5.0)

    # Verificar que se envió ack 0xFF [0x24, 0x00]
    ack_cmds = [(c, p) for c, p, *_ in transport.sent if c == 0xFF]
    assert (0xFF, bytes([CMD_RET_HR, 0x00])) in ack_cmds


def test_dump_health_raises_on_wrong_sync_state():
    """SYNC_START debe ser state=0; cualquier otro → RuntimeError."""
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_RET_SYNCH_STATE,
                                      payload=bytes([0x01, 0xbf])))  # state=1 = END no START
    with pytest.raises(RuntimeError, match="SYNC_START"):
        dump_health(transport, types=["hr"], timeout_per_frame=1.0, total_timeout=5.0)
