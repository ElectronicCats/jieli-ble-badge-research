#!/usr/bin/env python3
"""
jl_sfc_decrypt.py — Decrypt a JieLi AC70x (BR35) raw flash CODE region.

The on-flash app area is XOR-scrambled with the SFC (serial-flash-controller)
cipher, keyed by the chip's eFuse chipkey. The Boot ROM descrambles it on the
fly during XIP. This reverses that so the code can be disassembled/analysed.

Scheme (from `reference_chipkey_injection_app_area_rescramble`, verified against
this hardware): per 32-byte block, XOR the block against the jl_enc_cipher LFSR
stream seeded with
    effective_key = chipkey ^ (((block_off - base) >> 2) & 0xFFFF)
The app area starts at flash 0x1000 and `base` = 0x1000. XOR-symmetric, so the
same call re-encrypts.

Get the chipkey from: `jluboottool.py --device /dev/sgN exit`  → "Chip key: 0xXXXX".

Usage: jl_sfc_decrypt.py <flash_dump.bin> <chipkey_hex> [out.bin]
"""
import sys


def jl_enc_cipher(buff, off, size, key=0xFFFF):
    """JieLi ENC cipher: CRC16-poly (0x1021) LFSR, XOR keystream."""
    for i in range(size):
        buff[off + i] ^= key & 0xFF
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF
    return key


def jl_sfc_cipher(buff, off, size, base, key):
    """SFC flash scramble — per-32B-block, address-seeded jl_enc_cipher. Symmetric."""
    end = off + size
    o = off
    while o < end:
        blk = min(32, end - o)
        seed = (key ^ (((o - base) >> 2) & 0xFFFF)) & 0xFFFF
        jl_enc_cipher(buff, o, blk, seed)
        o += blk


def decrypt_code(flash: bytes, chipkey: int, app_start: int = 0x1000) -> bytes:
    """Descramble the app area [app_start:] of a raw flash dump."""
    b = bytearray(flash)
    jl_sfc_cipher(b, app_start, len(b) - app_start, base=app_start, key=chipkey)
    return bytes(b)


def _self_check():
    # XOR-symmetric round-trip: encrypt then decrypt == identity.
    import os
    data = bytearray(os.urandom(0x1000)) if hasattr(os, "urandom") else bytearray(range(256)) * 16
    orig = bytes(data)
    jl_sfc_cipher(data, 0x1000 - 0x1000, len(data), base=0, key=0xB165)  # base=0 slice
    assert bytes(data) != orig, "cipher did nothing"
    jl_sfc_cipher(data, 0, len(data), base=0, key=0xB165)
    assert bytes(data) == orig, "not symmetric"
    print("self-check OK (XOR-symmetric)")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--check":
        _self_check(); sys.exit(0)
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    flash = open(sys.argv[1], "rb").read()
    chipkey = int(sys.argv[2], 0)
    out = sys.argv[3] if len(sys.argv) > 3 else sys.argv[1].rsplit(".", 1)[0] + "_DECRYPTED.bin"
    open(out, "wb").write(decrypt_code(flash, chipkey))
    print(f"decrypted (chipkey 0x{chipkey:04X}, app@0x1000) -> {out}")
