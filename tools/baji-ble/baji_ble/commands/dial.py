"""cmd 31 (dial transfer) + cmd 32 (dial read/status) per WatchThemeTools APK."""
from baji_ble.frame import encode_cd, encode_no_value
from baji_ble.opcodes import (
    CMD_DIAL_TRANSFER, CMD_DIAL_NOTIFY,
    SUB_DIAL_START, SUB_DIAL_FILE, SUB_DIAL_FINISH,
)


def build_dial_dims_query() -> bytes:
    """getDialClockInfo — cmd 32 sub 2, no payload."""
    return encode_no_value(cmd=CMD_DIAL_NOTIFY, sub=2)


def build_dial_status_query() -> bytes:
    """getReadDialValue(1) — status poll."""
    return encode_no_value(cmd=CMD_DIAL_NOTIFY, sub=1)


def build_dial_start(
    *, font_position: int, custom: int, r: int, g: int, b: int,
    replace_pic_pos: int | None = None,
) -> bytes:
    """getDialUpdateStartValue (cmd 31 sub 2) — 5B (o 6B con replace_pic_pos)."""
    pl = [font_position, custom, r, g, b]
    if replace_pic_pos is not None:
        pl.append(replace_pic_pos)
    return encode_cd(cmd=CMD_DIAL_TRANSFER, sub=SUB_DIAL_START, payload=bytes(pl))


def build_dial_file_chunk(*, seq: int, chunk: bytes) -> bytes:
    """getDialUpdateFileValue: [seq_u16_be][chunk][u16_be checksum]."""
    body = bytearray()
    body.extend(seq.to_bytes(2, "big"))
    body.extend(chunk)
    ck = sum(body) & 0xFFFF
    body.extend(ck.to_bytes(2, "big"))
    return encode_cd(cmd=CMD_DIAL_TRANSFER, sub=SUB_DIAL_FILE, payload=bytes(body))


def build_dial_finish(file: bytes) -> bytes:
    """calculateFinishCheckcode trailer: 4B BE length + 4B BE sum (per Rust impl)."""
    length = len(file).to_bytes(4, "big")
    file_sum = (sum(file) & 0xFFFFFFFF).to_bytes(4, "big")
    return encode_cd(cmd=CMD_DIAL_TRANSFER, sub=SUB_DIAL_FINISH, payload=length + file_sum)
