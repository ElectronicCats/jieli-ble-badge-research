"""Dial upload FSM (cmd 31 start → chunks → finish)."""
from __future__ import annotations

import logging
from typing import Protocol

from baji_ble.commands.dial import build_dial_start, build_dial_file_chunk, build_dial_finish
from baji_ble.errors import BajiError, BajiStatusError
from baji_ble.frame import parse_cd_status
from baji_ble.opcodes import (
    CMD_DIAL_TRANSFER, CMD_DIAL_NOTIFY,
    SUB_DIAL_START, SUB_DIAL_FILE, SUB_DIAL_FINISH,
    is_fatal_status, fatal_status_message,
)
from baji_ble.session import BajiSession

log = logging.getLogger("baji_ble.upload")


class UploadError(BajiError):
    """Upload FSM aborted por status inválido o secuencia inesperada."""


class _Transport(Protocol):
    def send_raw_to_nus(self, data: bytes, *, response: bool = False) -> None: ...
    def recv_frame(self, timeout: float = 5.0) -> bytes | None: ...


def upload_dial(
    transport: _Transport,
    *,
    file: bytes,
    chunk_size: int = 200,
    font_position: int = 0,
    custom: int = 0,
    r: int = 255,
    g: int = 255,
    b: int = 255,
    timeout: float = 10.0,
) -> None:
    """Sube un blob raw como dial. file ya debe estar en formato esperado por el FW
    (típicamente RGB565 little-endian del tamaño width*height*2)."""
    sess = BajiSession(transport)

    # Start frame
    start = build_dial_start(font_position=font_position, custom=custom, r=r, g=g, b=b)
    log.info("upload start: file_len=%d chunk_size=%d", len(file), chunk_size)
    start_ack = sess.send_and_wait(start, expect_cmd=CMD_DIAL_TRANSFER, expect_sub=SUB_DIAL_START, timeout=timeout)
    _check_status(start_ack, expected=1000, stage="start")

    # Chunks
    seq = 1
    for offset in range(0, len(file), chunk_size):
        chunk = file[offset:offset + chunk_size]
        tx = build_dial_file_chunk(seq=seq, chunk=chunk)
        ack = sess.send_and_wait(tx, expect_cmd=CMD_DIAL_NOTIFY, expect_sub=SUB_DIAL_FILE, timeout=timeout)
        _check_status(ack, expected=1000 + seq, stage=f"chunk {seq}")
        seq += 1

    # Finish
    finish = build_dial_finish(file)
    finish_ack = sess.send_and_wait(finish, expect_cmd=CMD_DIAL_NOTIFY, expect_sub=SUB_DIAL_FILE, timeout=timeout)
    status = parse_cd_status(finish_ack)
    if status is None or (status != 2 and status < 1000):
        if is_fatal_status(status or -1):
            raise BajiStatusError(fatal_status_message(status) or f"fatal status {status}", code=status)
        raise UploadError(f"finish unexpected status: {status}")
    log.info("upload complete (finish status %s)", status)


def _check_status(frame: bytes, *, expected: int, stage: str) -> None:
    status = parse_cd_status(frame)
    if status is None:
        raise UploadError(f"{stage}: unparseable status frame {frame.hex()}")
    if is_fatal_status(status):
        raise BajiStatusError(
            f"{stage}: {fatal_status_message(status)}", code=status,
        )
    if status != expected:
        log.warning("%s: got status %d (expected %d)", stage, status, expected)
