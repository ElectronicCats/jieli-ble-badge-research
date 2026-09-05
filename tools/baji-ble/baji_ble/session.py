"""High-level send + wait orchestration sobre BajiTransport."""
from __future__ import annotations

import logging
import time
from typing import Protocol

from baji_ble.frame import MAGIC_ACK, MAGIC_DATA
from baji_ble.errors import BajiError
from qix_ble.errors import TimeoutError

log = logging.getLogger("baji_ble.session")


class _Transport(Protocol):
    def send_raw_to_nus(self, data: bytes, *, response: bool = False) -> None: ...
    def recv_frame(self, timeout: float = 5.0) -> bytes | None: ...


def _extract_cmd_sub(frame: bytes) -> tuple[int, int] | None:
    """Extract (cmd, sub) handling both CD (sub at [5]) and DC (sub at [4]) layouts."""
    if len(frame) < 6:
        return None
    magic = frame[0]
    cmd = frame[3]
    if magic == MAGIC_ACK:
        sub = frame[4]
    elif magic == MAGIC_DATA:
        sub = frame[5]
    else:
        return None
    return cmd, sub


class BajiSession:
    def __init__(self, transport: _Transport):
        self._t = transport

    def send_and_wait(
        self, tx: bytes, *, expect_cmd: int, expect_sub: int, timeout: float = 5.0,
    ) -> bytes:
        """Escribe tx + descarta frames hasta encontrar uno con cmd+sub matching.

        Handles both CD (sub at byte 5) and DC short (sub at byte 4) layouts.
        """
        self._t.send_raw_to_nus(tx)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"no se recibió cmd={expect_cmd} sub={expect_sub} en {timeout}s"
                )
            frame = self._t.recv_frame(timeout=remaining)
            if frame is None:
                continue
            cs = _extract_cmd_sub(frame)
            if cs is None:
                continue
            cmd, sub = cs
            if cmd == expect_cmd and sub == expect_sub:
                return frame
            log.debug("skip cmd=%d sub=%d (waiting for %d/%d)",
                      cmd, sub, expect_cmd, expect_sub)
