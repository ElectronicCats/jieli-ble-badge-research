"""Tests para commands/dial.py — golden bytes del Rust dial_upload.rs."""
from baji_ble.commands.dial import (
    build_dial_dims_query, build_dial_status_query,
    build_dial_start, build_dial_file_chunk, build_dial_finish,
)


def test_dial_dims_query_is_cmd32_sub2_no_payload():
    out = build_dial_dims_query()
    assert out == bytes.fromhex("cd00052001 0200 00".replace(" ", ""))


def test_dial_status_query_is_cmd32_sub1():
    out = build_dial_status_query()
    assert out[3] == 32
    assert out[5] == 1


def test_dial_start_minimal_5b_payload():
    """Port del Rust start_frame_shape test."""
    out = build_dial_start(font_position=0, custom=0, r=255, g=255, b=255)
    assert out[0] == 0xCD
    assert out[3] == 31
    assert out[5] == 2
    assert out[8:] == bytes([0, 0, 255, 255, 255])


def test_dial_start_with_replace_pic_pos_appends_byte():
    out = build_dial_start(font_position=0, custom=0, r=255, g=255, b=255, replace_pic_pos=2)
    assert out[8:] == bytes([0, 0, 255, 255, 255, 2])


def test_dial_file_chunk_layout_seq_data_checksum():
    """Layout: [seq_u16_be][chunk][u16_be checksum = sum(seq+chunk) mod 2^16]"""
    chunk = bytes([0x01, 0x02, 0x03])
    out = build_dial_file_chunk(seq=1, chunk=chunk)
    # payload section: seq (2B) + chunk (3B) + ck (2B) = 7B
    assert out[3] == 31
    assert out[5] == 1
    payload = out[8:]
    assert payload[:2] == bytes([0x00, 0x01])
    assert payload[2:5] == chunk
    expected_ck = (0 + 1 + 1 + 2 + 3) & 0xFFFF
    assert int.from_bytes(payload[5:7], "big") == expected_ck


def test_dial_finish_payload_is_be_u32_sum_only():
    """Port del Rust finish_payload_eight_bytes_pl.

    NOTE: el Rust test verifica 4B length + 4B sum, pero el comentario en línea 192
    dice 'sum-only payload' (descubrimiento iOS sysdiagnose). El plan se alinea con
    el comportamiento DEL CÓDIGO Rust (4+4), no del comentario stale.
    """
    file = bytes([1, 2, 3, 10])
    out = build_dial_finish(file)
    assert out[3] == 31
    assert out[5] == 3
    # Payload section
    assert out[8:12] == (4).to_bytes(4, "big")    # length
    assert out[12:16] == (16).to_bytes(4, "big")  # sum
