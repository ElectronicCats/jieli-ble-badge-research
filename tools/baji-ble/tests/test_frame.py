"""Tests para baji_ble.frame — golden vectors del community Rust + PROTOCOL.md."""
import pytest
from baji_ble.frame import BajiFrame, encode_cd, encode_no_value, encode_switch_protocol
from baji_ble.errors import BajiFrameError


def test_encode_cd_find_me_golden_bytes():
    """SendData.getSetFindMeValue(true) → SwitchProtocol(18, 11, 1).
    Bytes golden de PROTOCOL.md: cd 00 06 12 01 0b 00 01 01"""
    out = encode_switch_protocol(cmd=18, sub=11, value=1)
    assert out == bytes.fromhex("cd00061201 0b00 0101".replace(" ", ""))


def test_encode_cd_dial_start_minimal_5b():
    """dial_start(0, 0, 255, 255, 255, None) → cmd 31 sub 2 payload [0,0,255,255,255].
    Golden del Rust test start_frame_shape."""
    out = encode_cd(cmd=31, sub=2, payload=bytes([0, 0, 255, 255, 255]))
    # header: cd 00 LL 1f 01 02 00 05 + payload
    # total_len = 8+5 = 13 → len_field = 13-3 = 10 = 0x000A
    assert out[0] == 0xCD
    assert out[1:3] == bytes.fromhex("000a")
    assert out[3] == 31
    assert out[4] == 1
    assert out[5] == 2
    assert out[6:8] == bytes.fromhex("0005")
    assert out[8:] == bytes([0, 0, 255, 255, 255])


def test_encode_no_value_get_clock_info():
    """getDialClockInfo → getNoValueProtocol(32, 2). 8-byte total."""
    out = encode_no_value(cmd=32, sub=2)
    assert out == bytes.fromhex("cd0005200102 0000".replace(" ", ""))
    assert len(out) == 8


def test_encode_no_value_get_enter_ota_mode():
    """getEnterOtaMode → getNoValueProtocol(18, 25)."""
    out = encode_no_value(cmd=18, sub=25)
    assert out == bytes.fromhex("cd0005120119 0000".replace(" ", ""))


def test_encode_cd_empty_payload_treated_as_zero_len():
    out = encode_cd(cmd=18, sub=10, payload=b"")
    assert out[1:3] == bytes.fromhex("0005")  # total_len 8 → field 5
    assert out[6:8] == bytes.fromhex("0000")


def test_decode_cd_valid_frame():
    raw = bytes.fromhex("cd00061201 0b00 0101".replace(" ", ""))
    f = BajiFrame.decode(raw)
    assert f.magic == 0xCD
    assert f.cmd == 18
    assert f.sub == 11
    assert f.payload == bytes([0x01])


def test_decode_dc_short_ack():
    """ACK típico post-SwitchProtocol: dc 00 05 12 0a 00 09 01"""
    raw = bytes.fromhex("dc00051 20a 000901".replace(" ", ""))
    f = BajiFrame.decode(raw)
    assert f.magic == 0xDC
    assert f.cmd == 18
    assert f.sub == 0x0A
    # Para DC shorts el "payload" son los 3 bytes tail
    assert f.payload == bytes.fromhex("000901")


def test_decode_rejects_wrong_magic():
    raw = bytes.fromhex("ab00061201 0b00 0101".replace(" ", ""))
    with pytest.raises(BajiFrameError, match="magic"):
        BajiFrame.decode(raw)


def test_decode_rejects_truncated_frame():
    raw = bytes.fromhex("cd0006")  # solo header parcial
    with pytest.raises(BajiFrameError, match="truncated|too short"):
        BajiFrame.decode(raw)


def test_decode_rejects_payload_length_mismatch():
    """plen field dice 5 pero solo hay 3 bytes de payload"""
    raw = bytes.fromhex("cd0006120102 0005 010203".replace(" ", ""))
    with pytest.raises(BajiFrameError, match="payload|length"):
        BajiFrame.decode(raw)


from baji_ble.frame import CdNotifyAssembler


def test_assembler_single_complete_cd_passes_through():
    asm = CdNotifyAssembler()
    full = bytes.fromhex("cd00061201 0b00 0101".replace(" ", ""))
    out = asm.push(full)
    assert out == [full]


def test_assembler_merges_two_chunks():
    """Frame de 30B split 20+10 — port del test Rust assembler_merges_split_cd."""
    full = bytes([0xCD, 0x00, 0x1b]) + b"\x00" * 27  # total 30B
    a = full[:20]
    b = full[20:]
    asm = CdNotifyAssembler()
    assert asm.push(a) == []
    out = asm.push(b)
    assert len(out) == 1
    assert out[0] == full


def test_assembler_handles_multiple_complete_frames_in_one_chunk():
    f1 = bytes.fromhex("cd00061201 0b00 0101".replace(" ", ""))
    f2 = bytes.fromhex("cd0005120119 0000".replace(" ", ""))
    asm = CdNotifyAssembler()
    out = asm.push(f1 + f2)
    assert out == [f1, f2]


def test_assembler_skips_garbage_until_next_cd():
    """Si llega basura al inicio, descartar hasta encontrar 0xCD."""
    f1 = bytes.fromhex("cd00061201 0b00 0101".replace(" ", ""))
    asm = CdNotifyAssembler()
    out = asm.push(b"\xff\xee" + f1)
    assert out == [f1]


def test_assembler_empty_chunk_returns_empty():
    asm = CdNotifyAssembler()
    assert asm.push(b"") == []


from baji_ble.frame import (
    parse_dc_short, parse_cd_status, parse_dial_clock_info, DialClockInfo,
)


def test_parse_dc_short_extracts_cmd_sub():
    raw = bytes.fromhex("dc0005 22 02 001000".replace(" ", ""))
    cmd, sub = parse_dc_short(raw)
    assert cmd == 34
    assert sub == 2


def test_parse_dc_short_returns_none_on_cd():
    assert parse_dc_short(bytes.fromhex("cd0006120102 000101".replace(" ", ""))) is None


def test_parse_cd_status_chunk_ack_1000():
    """Start ACK exitoso: cmd 31 sub 2 con payload BE u32 = 1000."""
    pkt = bytes([0xCD, 0x00, 0x09, 31, 1, 2, 0, 4]) + (1000).to_bytes(4, "big")
    assert parse_cd_status(pkt) == 1000


def test_parse_cd_status_fatal_battery_low():
    pkt = bytes([0xCD, 0x00, 0x09, 31, 1, 1, 0, 4]) + (3).to_bytes(4, "big")
    assert parse_cd_status(pkt) == 3


def test_parse_dial_clock_info_minimal_360x360():
    """cmd 32 sub 2 con payload 6B: screen=0 grade=0 w=360 h=360"""
    pkt = bytes([0xCD, 0x00, 0x0b, 32, 1, 2, 0, 6]) + bytes([0, 0, 0x01, 0x68, 0x01, 0x68])
    info = parse_dial_clock_info(pkt)
    assert info == DialClockInfo(screen_type=0, grade=0, width=360, height=360, config=None)


def test_parse_dial_clock_info_with_config_byte():
    """Real-style payload con config byte 0x07 al final (= chunk 120B per APK)."""
    payload = bytes([
        1, 0, 0x01, 0x68, 0x01, 0x68, 0x03, 0x4b, 0x36, 0x36, 0x05, 0x4c, 0x4a, 0x37,
        0x33, 0x33, 0x07, 0x03, 0xff, 0xfe, 0x01, 0x05, 0x4a, 0x51, 0x30, 0x30, 0x31,
        0x00, 0x00, 0x09, 0x61, 0xa8,
    ])
    plen = len(payload)
    pkt = bytes([0xCD]) + (5 + plen).to_bytes(2, "big") + bytes([32, 1, 2]) + plen.to_bytes(2, "big") + payload
    info = parse_dial_clock_info(pkt)
    assert info.width == 360
    assert info.height == 360
    assert info.config == 0x07
