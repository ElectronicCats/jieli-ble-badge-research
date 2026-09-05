"""Stager raw-flash client.

Drives the custom raw-flash commands (0xD0..0xD3) added to the e_badge firmware's
qix_ota_server.c. Once the "STAGER" app is loaded (via the OEM app-only Qix OTA),
these let a host erase/write/read ANY flash partition over BLE — the UI resources
(SDFILE 0x17E000), VIRFAT (0x33E000), etc. — i.e. the stuff the OEM app-only OTA
(0xC0..0xC5, dual_bank app code only) physically can NOT touch.

The running CODE region [0, 0x17E000) is write-protected by the firmware so a raw
write can't brick the live app/uboot; replace the app itself with the dual_bank
Qix OTA (`flash`) or over USB ISP. See full-4mb-ota-not-via-qix analysis.

Wire (Qix frame, cmd/payload):
  0xD0 RAW_ERASE  [addr LE32][len LE32]   -> 0xD8 [st][addr LE32]
  0xD1 RAW_WRITE  [addr LE32][data..]     -> 0xD9 [st][next_addr LE32]
  0xD2 RAW_READ   [addr LE32][len u8<=16] -> 0xDA [st][data..]
  0xD3 RAW_REBOOT []                      -> 0xDB [st]   (then the badge resets)
  st: 0=ok, 1=flash error, 2=protected / out of range
"""
from __future__ import annotations

import struct

from qix_ble.transport import QixTransport

CMD_RAW_ERASE  = 0xD0
CMD_RAW_WRITE  = 0xD1
CMD_RAW_READ   = 0xD2
CMD_RAW_REBOOT = 0xD3
CMD_RAW_APPLY  = 0xD4
RET_ERASE  = 0xD8
RET_WRITE  = 0xD9
RET_READ   = 0xDA
RET_REBOOT = 0xDB
RET_APPLY  = 0xDC

# Scratch region for a staged code .ufw: above the resources (which start at
# ui_res 0x17E000) and below FLASH_END, big enough for a ~1 MB code UFW. The
# apply consumes it, then the new firmware's resources overwrite it — pure scratch.
STAGING_ADDR = 0x2F0000

CODE_PROTECT_END = 0x17E000   # keep in sync with STAGER_CODE_PROTECT_END (firmware)
FLASH_END        = 0x400000
SECTOR           = 0x1000

# Named partition bases on the 4 MB layout (board_ac707n_demo_cfg.h, 4M branch).
PARTITIONS = {
    "ui_res": 0x17E000,   # SDFILE / inflash_uires (RU52), 1.75 MB
    "virfat": 0x33E000,   # watch.bin virtual FAT, 512 KB
    "data":   0x3BE000,   # FDB user data, 160 KB
}

_ST = {0: "ok", 1: "flash-error", 2: "protected/out-of-range"}


class StagerError(Exception):
    pass


class StagerClient:
    """Thin sync wrapper over QixTransport for the raw-flash commands."""

    def __init__(self, t: QixTransport, timeout: float = 6.0):
        self.t = t
        self.timeout = timeout

    # ── primitives ──────────────────────────────────────────────────────────
    def erase(self, addr: int, length: int) -> None:
        r = self.t.send_command(CMD_RAW_ERASE, struct.pack("<II", addr, length),
                                expect_cmd=RET_ERASE, timeout=self.timeout, response=True)
        st = r.payload[0] if r.payload else 0xFF
        if st != 0:
            raise StagerError(f"erase @0x{addr:x} len={length}: {_ST.get(st, st)}")

    def write(self, addr: int, data: bytes) -> int:
        r = self.t.send_command(CMD_RAW_WRITE, struct.pack("<I", addr) + data,
                                expect_cmd=RET_WRITE, timeout=self.timeout, response=True)
        if len(r.payload) < 5:
            raise StagerError(f"write @0x{addr:x}: short ack {r.payload.hex()}")
        st = r.payload[0]
        nxt = struct.unpack("<I", r.payload[1:5])[0]
        if st != 0:
            raise StagerError(f"write @0x{addr:x} len={len(data)}: {_ST.get(st, st)}")
        return nxt

    def read(self, addr: int, length: int) -> bytes:
        length = min(length, 16)
        r = self.t.send_command(CMD_RAW_READ, struct.pack("<I", addr) + bytes([length]),
                                expect_cmd=RET_READ, timeout=self.timeout, response=True)
        if not r.payload or r.payload[0] != 0:
            raise StagerError(f"read @0x{addr:x}: error")
        return r.payload[1:1 + length]

    def reboot(self) -> None:
        try:
            self.t.send_command(CMD_RAW_REBOOT, b"", expect_cmd=RET_REBOOT,
                                timeout=2.0, response=True)
        except Exception:
            pass  # the badge resets and drops the link — expected

    def apply(self, addr: int, length: int) -> int:
        """0xD4 RAW_APPLY: verify the staged code .ufw at `addr` (len bytes) and
        hand off to the resident uboot, which writes the CODE partition and reboots
        into the new firmware. Returns the status byte (0 = verify ok, apply started;
        nonzero = rejected). The device resets shortly after replying."""
        try:
            r = self.t.send_command(CMD_RAW_APPLY, struct.pack("<II", addr, length),
                                    expect_cmd=RET_APPLY, timeout=self.timeout, response=True)
            return r.payload[0] if r.payload else 0xFF
        except Exception:
            return 0  # device may reset before the ack lands — treat as started

    # ── high-level: flash a whole region ────────────────────────────────────
    def _max_chunk(self, requested: int) -> int:
        mtu = getattr(self.t, "mtu", None) or getattr(getattr(self.t, "_client", None), "mtu_size", 0) or 23
        # frame = 6 (qix hdr) + 4 (addr) + data ; stay under ATT MTU-3
        room = max(20, mtu - 3 - 10)
        return max(1, min(requested, room))

    def flash_region(self, addr: int, data: bytes, *, chunk: int = 224, erase: bool = True,
                     verify: bool = False, on_progress=None) -> None:
        if addr < CODE_PROTECT_END:
            raise StagerError(
                f"addr 0x{addr:x} is in the protected CODE region (< 0x{CODE_PROTECT_END:x}); "
                f"replace the app via the dual_bank Qix OTA (`flash`) or USB ISP, not rawflash")
        end = addr + len(data)
        if end > FLASH_END:
            raise StagerError(f"region 0x{addr:x}+{len(data)} exceeds flash end 0x{FLASH_END:x}")

        chunk = self._max_chunk(chunk)

        if erase:
            a = addr & ~(SECTOR - 1)
            while a < end:
                span = min(0x10000, end - a)          # erase in 64 KB spans (bounded BLE-task block)
                self.erase(a, span)
                a += span

        off, cur = 0, addr
        total = len(data)
        while off < total:
            piece = data[off:off + chunk]
            self.write(cur, piece)
            off += len(piece)
            cur += len(piece)
            if on_progress:
                on_progress(off / total)

        if verify:
            self._verify_sampled(addr, data)

    def _verify_sampled(self, addr: int, data: bytes, samples: int = 12) -> None:
        total = len(data)
        if total == 0:
            return
        # deterministic spread of probe offsets (no RNG — reproducible)
        points = sorted({(i * total // samples) for i in range(samples)} | {0, max(0, total - 16)})
        for p in points:
            n = min(16, total - p)
            got = self.read(addr + p, n)
            want = data[p:p + n]
            if got != want:
                raise StagerError(
                    f"verify mismatch @0x{addr + p:x}: flash={got.hex()} file={want.hex()}")
