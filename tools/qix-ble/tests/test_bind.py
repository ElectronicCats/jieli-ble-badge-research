"""Tests para qix_ble.bind — cmd 0x60 (bind) + 0x62 (unbind)."""
from __future__ import annotations

import pytest

from qix_ble.bind import (
    CMD_BIND_REQ,
    CMD_BIND_RESP,
    CMD_UNBIND,
    BindResponse,
    _java_string_hash,
    _long_to_6_bytes_le,
    _next_flag,
    _reset_flag_counter,
    bind,
    build_bind_payload,
    parse_bind_response,
    unbind,
)
from qix_ble.errors import BadgeRejected
from qix_ble.errors import TimeoutError as QixTimeoutError
from qix_ble.frame import QixFrame
from tests.conftest import MockTransport


# ── Helpers tests ────────────────────────────────────────────────────────────

def test_java_string_hash_known_values():
    """Verificar contra valores que Java produce."""
    assert _java_string_hash("") == 0
    assert _java_string_hash("a") == 97  # ord('a')
    # Empirical: jdk → "test".hashCode() = 3556498
    assert _java_string_hash("test") == 3556498


def test_long_to_6_bytes_le_zero():
    assert _long_to_6_bytes_le(0) == b"\x00\x00\x00\x00\x00\x00"


def test_long_to_6_bytes_le_basic():
    # LSB-first
    assert _long_to_6_bytes_le(0x010203) == b"\x03\x02\x01\x00\x00\x00"


def test_long_to_6_bytes_le_negative():
    # Negative wraps via 2^64
    result = _long_to_6_bytes_le(-1)
    assert result == b"\xff\xff\xff\xff\xff\xff"


def test_next_flag_short_payload():
    """payload_len 13 → 13+6=19 ≤ 20 → is_long=0. Serial 0 → flag = (0<<3) | (0<<2) | (1<<1) = 0x02."""
    _reset_flag_counter()
    assert _next_flag(13) == 0x02
    assert _next_flag(13) == 0x0A  # serial=1: (1<<3) | 0 | 2 = 8+2 = 0x0A


def test_next_flag_long_payload():
    """payload_len 15 → 15+6=21 > 20 → is_long=1."""
    _reset_flag_counter()
    assert _next_flag(15) == 0x06  # (0<<3) | (1<<2) | (1<<1) = 0+4+2 = 0x06


# ── Payload tests ────────────────────────────────────────────────────────────

def test_build_bind_payload_default_lang_en():
    """lang=en → lang_flag=1. hour24 default → hour_flag=0. header = (0<<2)|(1<<1) = 0x02."""
    payload = build_bind_payload(lang="en", hour12=False, device_id=0)
    assert len(payload) == 13
    assert payload[0] == 0x02
    assert payload[1:7] == b"\x00" * 6  # device_id 0
    assert payload[7:13] == b"\x00" * 6  # repeat


def test_build_bind_payload_zh_hour12():
    """lang=zh → lang_flag=0. hour12=True → hour_flag=1. header = (1<<2)|(0<<1) = 0x04."""
    payload = build_bind_payload(lang="zh", hour12=True, device_id=0)
    assert payload[0] == 0x04


def test_build_bind_payload_repeat_device_id():
    """device_id encoded twice."""
    payload = build_bind_payload(lang="en", device_id=0xDEADBEEF)
    expected_6 = b"\xef\xbe\xad\xde\x00\x00"
    assert payload[1:7] == expected_6
    assert payload[7:13] == expected_6


def test_build_bind_payload_invalid_lang_raises():
    with pytest.raises(ValueError, match="lang debe ser"):
        build_bind_payload(lang="fr")


# ── Response parser tests ────────────────────────────────────────────────────

def test_parse_bind_response_too_short():
    """Tolerant parser: <14B (no firmwa_version) → None. ≥14B parses partial."""
    assert parse_bind_response(b"\x00" * 13) is None
    # 14+ should parse (post-fix tolerant para E87 partial 30B response)
    parsed = parse_bind_response(b"\x00" * 14)
    assert parsed is not None and parsed.firmwa_version == ""


def test_parse_bind_response_partial_30b_e87():
    """Real E87 partial response: 30B con firmwa='11.1.0.4' serial=0x80104001."""
    payload = bytes.fromhex("00322e3731312e312e302e3400000016060080104001000020312e302e30")
    parsed = parse_bind_response(payload)
    assert parsed is not None
    assert parsed.firmwa_version == "11.1.0.4"
    assert parsed.serial_number == 0x80104001
    assert parsed.platform == 0x00160600
    # función_config2/ui_version/function_bytes vacíos (no hay esos bytes)
    assert parsed.function_config2 == 0
    assert parsed.ui_version == ""
    assert parsed.function_bytes == b""


def test_parse_bind_response_canonical_43b():
    """39-byte payload byte-exact contra parser."""
    payload = (
        bytes([0x01])  # state
        + b"V01"  # pact_version 3B
        + b"v10.2.3.6\x00"  # firmwa_version 10B (with NUL trim)
        + bytes([0x00, 0x00, 0x00, 0x07])  # platform BE 7
        + bytes([0x00, 0x12, 0x34, 0x56])  # serial_number 0x123456
        + bytes([0x00, 0x00, 0x00, 0x0F])  # function_config 15
        + bytes([0x00, 0x00, 0x00, 0x00])  # function_config1
        + bytes([0, 0, 0, 0, 0, 0, 0, 0xFF])  # function_config2 (8B BE = 255)
        + b"E"  # ui_version 5B (1 char + NUL trim)
    )
    parsed = parse_bind_response(payload)
    assert parsed is not None
    assert parsed.state == 1
    assert parsed.pact_version == "V01"
    assert parsed.firmwa_version == "v10.2.3.6"
    assert parsed.platform == 7
    assert parsed.serial_number == 0x123456
    assert parsed.function_config == 15
    assert parsed.function_config2 == 0xFF
    assert parsed.ui_version == "E"


def test_parse_bind_response_with_function_bytes():
    """43+ payload → function_bytes capture el tail."""
    payload = b"\x00" * 43 + b"\xAA\xBB\xCC"
    parsed = parse_bind_response(payload)
    assert parsed.function_bytes == b"\xAA\xBB\xCC"


# ── bind() / unbind() integration tests ──────────────────────────────────────

def test_bind_happy_path():
    _reset_flag_counter()
    transport = MockTransport()
    resp_payload = (
        bytes([0x01]) + b"V01" + b"v1.0\x00\x00\x00\x00\x00\x00"
        + bytes(20) + b"FOO\x00\x00"
    )
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BIND_RESP, payload=resp_payload))

    result = bind(transport, lang="en", device_id=0xABCD)

    assert isinstance(result, BindResponse)
    assert result.state == 1
    assert result.pact_version == "V01"

    # Verificar request frame
    cmd_sent, payload_sent, flags_sent = transport.sent[0]
    assert cmd_sent == CMD_BIND_REQ
    assert len(payload_sent) == 13
    # header EN/24h = 0x02
    assert payload_sent[0] == 0x02
    # device_id 0xABCD LE
    assert payload_sent[1:7] == b"\xcd\xab\x00\x00\x00\x00"
    # flag = 0x02 (serial 0, short payload, bit1 set)
    assert flags_sent == 0x02


def test_bind_38b_parses_with_warning():
    """38B payload (< 39 canonical) → parse partial + log warning, NO raise."""
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BIND_RESP, payload=b"\x00" * 38))

    result = bind(transport)
    # parse partial worked
    assert result.state == 0
    assert result.firmwa_version == ""  # all zeros


def test_bind_1byte_response_captures_status():
    """1B payload (gated FW) → BadgeRejected con status code 0x02 visible."""
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BIND_RESP, payload=b"\x02"))

    with pytest.raises(BadgeRejected, match="status=0x02.*NOT_SUPPORTED"):
        bind(transport)


def test_bind_empty_response_distinct_error():
    """0B payload → distinct 'silently dropped' message."""
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=CMD_BIND_RESP, payload=b""))

    with pytest.raises(BadgeRejected, match="silently dropped"):
        bind(transport)


def test_bind_timeout_propagates():
    transport = MockTransport()
    with pytest.raises(QixTimeoutError):
        bind(transport)


def test_unbind_sends_correct_frame():
    transport = MockTransport()
    unbind(transport)

    assert len(transport.sent) == 1
    cmd, payload, flags = transport.sent[0]
    assert cmd == CMD_UNBIND
    assert payload == b"\x01"
    assert flags == 0x00


def test_unbind_no_response_expected():
    """unbind() retorna None, no espera nada del transport."""
    transport = MockTransport()
    # No scripted response → si esperara, fallaría con timeout
    result = unbind(transport)
    assert result is None
