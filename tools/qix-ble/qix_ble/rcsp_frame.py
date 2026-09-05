"""Encoder/decoder del frame RCSP JieLi (service AE00 post-auth).

Wire-format:
  [0xFE 0xDC 0xBA head][flag 1B][cmd 1B][len BE16][payload][0xEF tail]

Diferencias con QixFrame:
- Magic = 3 bytes (head) + 1 byte (tail), no 1 byte como Qix.
- Length es BIG-endian (Qix es LE).
- No hay checksum byte (a diferencia de Qix).
- Frame es delimited: tail 0xEF marca fin de frame.

Nota: el handshake auth NO usa este framing — va raw sobre AE01/AE02
(ver qix_ble/auth.py). RcspFrame es para comandos post-auth: GetTargetInfo,
filesystem browse (StartFileBrowse/Upload/etc.), OTA, etc.

Verificado byte-a-byte contra btsnoop del Day 1 (packet 576 GetTargetInfo
request: fe dc ba c0 03 00 06 74 ff ff ff ff 00 ef).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from qix_ble.errors import InvalidFrameError

RCSP_HEAD: bytes = b"\xfe\xdc\xba"
RCSP_TAIL: int = 0xef
HEAD_LEN: int = 3
HEADER_LEN: int = HEAD_LEN + 1 + 1 + 2  # head + flag + cmd + len_be16 = 7
FOOTER_LEN: int = 1                      # tail
MIN_FRAME_LEN: int = HEADER_LEN + FOOTER_LEN  # 8


@dataclass
class RcspFrame:
    flag: int
    cmd: int
    payload: bytes

    def encode(self) -> bytes:
        if len(self.payload) > 0xFFFF:
            raise InvalidFrameError(
                f"RCSP payload demasiado grande: {len(self.payload)} > 65535"
            )
        out = bytearray(RCSP_HEAD)
        out.append(self.flag & 0xFF)
        out.append(self.cmd & 0xFF)
        out += len(self.payload).to_bytes(2, "big")
        out += self.payload
        out.append(RCSP_TAIL)
        return bytes(out)

    @classmethod
    def decode(cls, raw: bytes) -> "RcspFrame":
        if len(raw) < MIN_FRAME_LEN:
            raise InvalidFrameError(f"RCSP frame demasiado corto: {len(raw)} < {MIN_FRAME_LEN}")
        if raw[0:HEAD_LEN] != RCSP_HEAD:
            raise InvalidFrameError(f"RCSP head inválido: {raw[0:HEAD_LEN].hex()}")
        flag = raw[3]
        cmd = raw[4]
        payload_len = int.from_bytes(raw[5:7], "big")
        total = HEADER_LEN + payload_len + FOOTER_LEN
        if len(raw) < total:
            raise InvalidFrameError(
                f"RCSP payload truncado: declared {payload_len}, "
                f"have {len(raw) - HEADER_LEN - FOOTER_LEN}"
            )
        if raw[HEADER_LEN + payload_len] != RCSP_TAIL:
            raise InvalidFrameError(
                f"RCSP tail inválido: 0x{raw[HEADER_LEN + payload_len]:02x} != 0x{RCSP_TAIL:02x}"
            )
        payload = bytes(raw[HEADER_LEN : HEADER_LEN + payload_len])
        return cls(flag=flag, cmd=cmd, payload=payload)

    @classmethod
    def from_bytes_stream(cls, buffer: bytearray) -> tuple[Optional["RcspFrame"], bytearray]:
        """Consume frame de un streaming buffer (BLE notifications pueden fragmentar)."""
        head_idx = buffer.find(RCSP_HEAD)
        if head_idx < 0:
            return None, bytearray()
        if head_idx > 0:
            buffer = buffer[head_idx:]
        if len(buffer) < HEADER_LEN:
            return None, buffer
        payload_len = int.from_bytes(buffer[5:7], "big")
        total = HEADER_LEN + payload_len + FOOTER_LEN
        if len(buffer) < total:
            return None, buffer
        try:
            frame = cls.decode(bytes(buffer[:total]))
        except InvalidFrameError:
            # Skip past spurious head (1 byte) and retry
            return cls.from_bytes_stream(buffer[1:])
        return frame, buffer[total:]
