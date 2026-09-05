"""Client for the OEM-app BINARY PATCH raw-flash service (option A).

Drives the raw-flash handler injected into the OEM app.bin via the Qix command
dispatch hook (see the stager-app-target memory / oem_patch/). Protocol differs
from the stager firmware: it's FIRE-AND-FORGET (the injected handler sends no
reply) and the write carries its own length.

Wire (Qix frame, cmd + payload over FD02):
  0xD0 RAW_ERASE  [addr LE32]                  -> (no reply) erase ONE 4KB sector @addr
  0xD1 RAW_WRITE  [addr LE32][len LE16][data]  -> (no reply) tzflash_write(data,len,addr)

Erase is ONE sector per command (a 64KB-loop blocks the BLE task >1s → watchdog
reset). The handler protects nothing (calls sfc_erase/tzflash_write directly), so
the host must avoid the running CODE region (<0x17E000). Erase before write.
"""
from __future__ import annotations

import struct
import time

from qix_ble.frame import QixFrame
from qix_ble.transport import QixTransport

CMD_RAW_ERASE = 0xD0
CMD_RAW_WRITE = 0xD1


class PatchClient:
    """Fire-and-forget driver for the injected raw-flash handler."""

    def __init__(self, t: QixTransport, settle: float = 0.04):
        self.t = t
        self.settle = settle      # delay after each op (handler has no ACK → pace by time)

    def _send(self, cmd: int, payload: bytes) -> None:
        # write-WITHOUT-response (like the OEM OTA SEND_DATA): the handler blocks
        # the BLE task doing the flash op, so we must NOT make the badge ACK each
        # write at the link layer (that delayed ACK reset it). Pace by time instead.
        self.t.send_raw(QixFrame(flags=0, cmd=cmd, payload=payload), response=False)

    def erase_sector(self, addr: int) -> None:
        self._send(CMD_RAW_ERASE, struct.pack("<I", addr))   # ONE 4KB sector @addr
        time.sleep(self.settle + 0.15)                       # sector erase can take 50-300ms

    def write(self, addr: int, data: bytes) -> None:
        self._send(CMD_RAW_WRITE, struct.pack("<IH", addr, len(data)) + data)
        time.sleep(self.settle)

    def flash_region(self, addr: int, data: bytes, *, chunk: int = 160,
                     erase: bool = True, on_progress=None) -> None:
        if erase:
            # erase one 4KB sector at a time (long erases trip the watchdog)
            a = addr & ~0xFFF
            end = addr + len(data)
            while a < end:
                self.erase_sector(a)
                a += 0x1000
        off, cur, total = 0, addr, len(data)
        while off < total:
            piece = data[off:off + chunk]
            self.write(cur, piece)
            off += len(piece)
            cur += len(piece)
            if on_progress:
                on_progress(off / total)
