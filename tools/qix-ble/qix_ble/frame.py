"""Encoder/decoder del frame Qix.

Wire-format:
  [0x9E magic][checksum 1B][flags 1B][cmd 1B][len LE16][payload]
  checksum = sum(bytes[2:]) & 0xFF — confirmado en UpdateManager.getCheck() decompiled.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from qix_ble.errors import InvalidFrameError, ChecksumError

QIX_MAGIC: int = 0x9E
HEADER_LEN: int = 6  # magic + checksum + flags + cmd + len LE16


@dataclass
class QixFrame:
    flags: int
    cmd: int
    payload: bytes

    def encode(self) -> bytes:
        """Encode a wire format. Computa checksum y length automáticamente."""
        if len(self.payload) > 0xFFFF:
            raise InvalidFrameError(f"payload demasiado grande: {len(self.payload)} bytes (max 65535)")

        body = bytes([self.flags & 0xFF, self.cmd & 0xFF])
        body += len(self.payload).to_bytes(2, "little")
        body += self.payload
        checksum = sum(body) & 0xFF
        return bytes([QIX_MAGIC, checksum]) + body

    @classmethod
    def decode(cls, raw: bytes) -> "QixFrame":
        """Parse + valida magic + checksum + length. Raises InvalidFrameError | ChecksumError."""
        if len(raw) < HEADER_LEN:
            raise InvalidFrameError(f"frame demasiado corto: {len(raw)} bytes < {HEADER_LEN}")
        if raw[0] != QIX_MAGIC:
            raise InvalidFrameError(f"magic inválido: 0x{raw[0]:02x} != 0x{QIX_MAGIC:02x}")

        checksum_recv = raw[1]
        flags = raw[2]
        cmd = raw[3]
        payload_len = int.from_bytes(raw[4:6], "little")

        if len(raw) < HEADER_LEN + payload_len:
            raise InvalidFrameError(
                f"payload truncado: declared {payload_len}, available {len(raw) - HEADER_LEN}"
            )

        payload = raw[HEADER_LEN : HEADER_LEN + payload_len]

        # Recompute checksum: bytes [2:HEADER_LEN+payload_len] = flags + cmd + len_lo + len_hi + payload
        checksum_calc = sum(raw[2 : HEADER_LEN + payload_len]) & 0xFF
        if checksum_calc != checksum_recv:
            raise ChecksumError(
                f"checksum mismatch: recv 0x{checksum_recv:02x} != calc 0x{checksum_calc:02x}"
            )

        return cls(flags=flags, cmd=cmd, payload=bytes(payload))

    @classmethod
    def from_bytes_stream(cls, buffer: bytearray) -> tuple[Optional["QixFrame"], bytearray]:
        """Consume from streaming buffer (BLE notifications fragmentan).

        Returns (frame, new_buffer). Si frame es None, no había suficiente data;
        new_buffer es el original (posiblemente skipping garbage pre-magic).
        Si frame se extrae, new_buffer es lo que quedó después del frame.
        """
        # Skip cualquier garbage hasta encontrar magic
        try:
            magic_idx = buffer.index(QIX_MAGIC)
        except ValueError:
            return None, bytearray()  # no magic en absoluto, descartar todo

        if magic_idx > 0:
            buffer = buffer[magic_idx:]

        if len(buffer) < HEADER_LEN:
            return None, buffer  # header incompleto

        payload_len = int.from_bytes(buffer[4:6], "little")
        total_needed = HEADER_LEN + payload_len

        if len(buffer) < total_needed:
            return None, buffer  # payload incompleto

        try:
            frame = cls.decode(bytes(buffer[:total_needed]))
        except (InvalidFrameError, ChecksumError):
            # Frame malo: skip 1 byte y reintentar (probablemente magic spurious en datos)
            return cls.from_bytes_stream(buffer[1:])

        return frame, buffer[total_needed:]
