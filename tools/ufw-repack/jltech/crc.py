"""CRC routines (pure Python; no `crcmod` dependency).

Equivalent to the upstream jltech definitions:
    jl_crc16 = crcmod.mkCrcFun(0x11021,     initCrc=0x0000,     rev=False)
    jl_crc32 = crcmod.mkCrcFun(0x104C11DB7, initCrc=0x26536734, rev=True)
Both verified bit-exact against crcmod over random and fixed vectors.
"""

__all__ = ['jl_crc16', 'jl_crc32']


def jl_crc16(data, crc=0x0000):
    """CRC-16, poly 0x1021, non-reflected, no final xor (XMODEM-style)."""
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def jl_crc32(data, crc=0x26536734):
    """CRC-32, poly 0x04C11DB7, reflected, no final xor."""
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFFFFFF
