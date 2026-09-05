"""Framing CD/DC del path Baji NUS (per PROTOCOL.md + APK SendData.getProtocol)."""
from __future__ import annotations

from dataclasses import dataclass

from baji_ble.errors import BajiFrameError

MAGIC_DATA = 0xCD
MAGIC_ACK = 0xDC
HEADER_LEN_CD = 8   # magic(1) + len(2) + cmd(1) + 0x01(1) + sub(1) + plen(2)
HEADER_LEN_DC = 8   # mismo layout para DC shorts


@dataclass(frozen=True)
class BajiFrame:
    magic: int     # 0xCD or 0xDC
    cmd: int
    sub: int
    payload: bytes

    @staticmethod
    def decode(raw: bytes) -> "BajiFrame":
        """Parse un frame Baji completo (CD o DC). Raises BajiFrameError si malformado."""
        if len(raw) < 8:
            raise BajiFrameError(f"frame truncated: len={len(raw)} < 8")
        magic = raw[0]
        if magic not in (MAGIC_DATA, MAGIC_ACK):
            raise BajiFrameError(f"unknown magic 0x{magic:02X} (expected CD or DC)")
        total_minus_3 = int.from_bytes(raw[1:3], "big")
        expected_total = total_minus_3 + 3
        if len(raw) < expected_total:
            raise BajiFrameError(
                f"frame truncated: declared total={expected_total} but got {len(raw)}"
            )
        cmd = raw[3]
        if magic == MAGIC_DATA:
            sub = raw[5]
            plen = int.from_bytes(raw[6:8], "big")
            if expected_total - 8 != plen:
                raise BajiFrameError(
                    f"payload length mismatch: header plen={plen} but body has "
                    f"{expected_total - 8} bytes"
                )
            payload = bytes(raw[8:expected_total])
        else:
            # DC short: [4] is sub, [5:8] son el tail (3 bytes); guardarlo como "payload" para uniformidad.
            sub = raw[4]
            payload = bytes(raw[5:8])
        return BajiFrame(magic=magic, cmd=cmd, sub=sub, payload=payload)


def encode_cd(*, cmd: int, sub: int, payload: bytes) -> bytes:
    """Construye un frame 0xCD con payload arbitrario.

    Layout (PROTOCOL.md):
      [0] 0xCD
      [1:3] big-endian u16 = (total_len - 3) = 5 + len(payload)
      [3] cmd
      [4] 0x01 (fixed key-length field)
      [5] sub
      [6:8] big-endian u16 = len(payload)
      [8:] payload
    """
    plen = len(payload)
    total_minus_3 = 5 + plen
    out = bytearray(8 + plen)
    out[0] = MAGIC_DATA
    out[1:3] = total_minus_3.to_bytes(2, "big")
    out[3] = cmd & 0xFF
    out[4] = 0x01
    out[5] = sub & 0xFF
    out[6:8] = plen.to_bytes(2, "big")
    out[8:] = payload
    return bytes(out)


def encode_no_value(*, cmd: int, sub: int) -> bytes:
    """getNoValueProtocol — frame 0xCD de 8 bytes sin payload."""
    return encode_cd(cmd=cmd, sub=sub, payload=b"")


def encode_switch_protocol(*, cmd: int, sub: int, value: int) -> bytes:
    """SwitchProtocol — fixed 9-byte frame con un solo byte de payload.
    Layout APK: [CD, 00, 06, cmd, 01, sub, 00, 01, value]"""
    return encode_cd(cmd=cmd, sub=sub, payload=bytes([value & 0xFF]))


class CdNotifyAssembler:
    """Reassembly de frames 0xCD partidos entre BLE notifies.

    Port byte-exact del Rust `dial_upload.rs:CdNotifyAssembler`. Mantiene buffer
    interno; `push(chunk)` retorna lista de frames CD completos (cada uno bytes).
    """

    def __init__(self):
        self._buf = bytearray()

    def push(self, chunk: bytes) -> list[bytes]:
        self._buf.extend(chunk)
        out: list[bytes] = []
        while self._buf:
            if self._buf[0] != MAGIC_DATA:
                idx = self._buf.find(bytes([MAGIC_DATA]))
                if idx < 0:
                    self._buf.clear()
                    break
                del self._buf[:idx]
                continue
            if len(self._buf) < 3:
                break
            need = int.from_bytes(self._buf[1:3], "big") + 3
            if len(self._buf) < need:
                break
            out.append(bytes(self._buf[:need]))
            del self._buf[:need]
        return out


@dataclass(frozen=True)
class DialClockInfo:
    screen_type: int
    grade: int
    width: int
    height: int
    config: int | None


def parse_dc_short(packet: bytes) -> tuple[int, int] | None:
    """Retorna (cmd, sub) de un DC short de 8B. None si no es DC válido."""
    if len(packet) < 8 or packet[0] != MAGIC_ACK:
        return None
    return packet[3], packet[4]


def parse_cd_status(packet: bytes) -> int | None:
    """Para frames CD que llevan un i32 BE como primeros 4B del payload (dial ACKs).
    Retorna el code (>=1000 = chunk ACK, <1000 = error fatal). None si no parseable.
    """
    if len(packet) < 12 or packet[0] != MAGIC_DATA:
        return None
    total = int.from_bytes(packet[1:3], "big") + 3
    if len(packet) < total or total < 12:
        return None
    return int.from_bytes(packet[8:12], "big", signed=True)


def parse_dial_clock_info(packet: bytes) -> DialClockInfo | None:
    """Port del Rust parse_dial_clock_info_full (BaseReceiveData.parseDialInfo).
    Aplica solo a cmd 32 sub 2 (getDialClockInfo response)."""
    if len(packet) < 14 or packet[0] != MAGIC_DATA:
        return None
    if packet[3] != 32 or packet[5] != 2:
        return None
    plen = int.from_bytes(packet[6:8], "big")
    if plen < 6 or len(packet) < 8 + plen:
        return None
    p = packet[8:8 + plen]
    screen_type = p[0]
    grade = p[1]
    width = int.from_bytes(p[2:4], "big")
    height = int.from_bytes(p[4:6], "big")
    config: int | None = None
    if len(p) > 6:
        lm = p[6]
        if len(p) > 7 + lm:
            main_len = p[7 + lm]
            if len(p) >= 8 + lm + main_len:
                i5 = 8 + lm + main_len
                if len(p) > i5:
                    config = p[i5]
    return DialClockInfo(
        screen_type=screen_type, grade=grade, width=width, height=height, config=config,
    )
