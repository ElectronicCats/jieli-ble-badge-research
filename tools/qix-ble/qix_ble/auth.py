"""Auth JieLi para service RCSP AE00.

Port byte-a-byte de hybridherbst/web-bluetooth-e87/protocol-understanding/jl_auth_v3.py
(verified against ARM64 disassembly of libjl_auth.so).

Tablas SBOX, ISBOX, KS_TABLE embedded como literales — extraídas de
libjl_auth.so armv7 del APK ZRun (`lib/armeabi-v7a/libjl_auth.so`), offsets:
  0x2c90  KS_TABLE  (256B)
  0x2d90  SBOX      (256B, permutation)
  0x2e90  ISBOX     (256B, inverse of SBOX)

Validado byte-exacto contra:
- KAT built-in en jl_auth_v3.py upstream.
- KAT real de nuestro E87 (btsnoop Day 1, packets 562/565 + 571/573).

NOTA importante sobre framing: el handshake auth va RAW sobre AE01/AE02
(no envuelto en frame RCSP fe-dc-ba). El payload de cada step es directo:
  step 1 (phone→AE01): 00 ‖ 16B random
  step 2 (dev→AE02):   01 ‖ 16B encrypted
  step 3 (phone→AE01): 02 70 61 73 73   ("\x02pass")
  step 4 (dev→AE02):   00 ‖ 16B challenge
  step 5 (phone→AE01): 01 ‖ 16B encrypted
  step 6 (dev→AE02):   02 70 61 73 73

Detalles en hw-sessions/2026-05-14/zrun/kat-vectors.md.

API pública:
- JliCipher().encrypt(plaintext_16: bytes) -> bytes
- get_random_auth_data() -> bytes  (16B random)
- AuthSession(transport).do_handshake()  ← Task 7
"""
from __future__ import annotations
import logging
import os

log = logging.getLogger("qix_ble.auth")

# ── Constants (.data del .so) ────────────────────────────────────────────────
STATIC_KEY: bytes = bytes.fromhex("06775F87918DD423005DF1D8CF0C142B")
MAGIC: bytes = bytes.fromhex("112233332211")
MASK: int = 0x9999
BLOCK_SIZE: int = 16

# ── SBOX (.rodata 0x2d90 armv7 / 0x1c4c arm64) ───────────────────────────────
SBOX: tuple[int, ...] = (
    0x01, 0x2d, 0xe2, 0x93, 0xbe, 0x45, 0x15, 0xae, 0x78, 0x03, 0x87, 0xa4, 0xb8, 0x38, 0xcf, 0x3f,
    0x08, 0x67, 0x09, 0x94, 0xeb, 0x26, 0xa8, 0x6b, 0xbd, 0x18, 0x34, 0x1b, 0xbb, 0xbf, 0x72, 0xf7,
    0x40, 0x35, 0x48, 0x9c, 0x51, 0x2f, 0x3b, 0x55, 0xe3, 0xc0, 0x9f, 0xd8, 0xd3, 0xf3, 0x8d, 0xb1,
    0xff, 0xa7, 0x3e, 0xdc, 0x86, 0x77, 0xd7, 0xa6, 0x11, 0xfb, 0xf4, 0xba, 0x92, 0x91, 0x64, 0x83,
    0xf1, 0x33, 0xef, 0xda, 0x2c, 0xb5, 0xb2, 0x2b, 0x88, 0xd1, 0x99, 0xcb, 0x8c, 0x84, 0x1d, 0x14,
    0x81, 0x97, 0x71, 0xca, 0x5f, 0xa3, 0x8b, 0x57, 0x3c, 0x82, 0xc4, 0x52, 0x5c, 0x1c, 0xe8, 0xa0,
    0x04, 0xb4, 0x85, 0x4a, 0xf6, 0x13, 0x54, 0xb6, 0xdf, 0x0c, 0x1a, 0x8e, 0xde, 0xe0, 0x39, 0xfc,
    0x20, 0x9b, 0x24, 0x4e, 0xa9, 0x98, 0x9e, 0xab, 0xf2, 0x60, 0xd0, 0x6c, 0xea, 0xfa, 0xc7, 0xd9,
    0x00, 0xd4, 0x1f, 0x6e, 0x43, 0xbc, 0xec, 0x53, 0x89, 0xfe, 0x7a, 0x5d, 0x49, 0xc9, 0x32, 0xc2,
    0xf9, 0x9a, 0xf8, 0x6d, 0x16, 0xdb, 0x59, 0x96, 0x44, 0xe9, 0xcd, 0xe6, 0x46, 0x42, 0x8f, 0x0a,
    0xc1, 0xcc, 0xb9, 0x65, 0xb0, 0xd2, 0xc6, 0xac, 0x1e, 0x41, 0x62, 0x29, 0x2e, 0x0e, 0x74, 0x50,
    0x02, 0x5a, 0xc3, 0x25, 0x7b, 0x8a, 0x2a, 0x5b, 0xf0, 0x06, 0x0d, 0x47, 0x6f, 0x70, 0x9d, 0x7e,
    0x10, 0xce, 0x12, 0x27, 0xd5, 0x4c, 0x4f, 0xd6, 0x79, 0x30, 0x68, 0x36, 0x75, 0x7d, 0xe4, 0xed,
    0x80, 0x6a, 0x90, 0x37, 0xa2, 0x5e, 0x76, 0xaa, 0xc5, 0x7f, 0x3d, 0xaf, 0xa5, 0xe5, 0x19, 0x61,
    0xfd, 0x4d, 0x7c, 0xb7, 0x0b, 0xee, 0xad, 0x4b, 0x22, 0xf5, 0xe7, 0x73, 0x23, 0x21, 0xc8, 0x05,
    0xe1, 0x66, 0xdd, 0xb3, 0x58, 0x69, 0x63, 0x56, 0x0f, 0xa1, 0x31, 0x95, 0x17, 0x07, 0x3a, 0x28,
)

# ── ISBOX (.rodata 0x2e90 armv7 / 0x1d4c arm64) ──────────────────────────────
ISBOX: tuple[int, ...] = (
    0x80, 0x00, 0xb0, 0x09, 0x60, 0xef, 0xb9, 0xfd, 0x10, 0x12, 0x9f, 0xe4, 0x69, 0xba, 0xad, 0xf8,
    0xc0, 0x38, 0xc2, 0x65, 0x4f, 0x06, 0x94, 0xfc, 0x19, 0xde, 0x6a, 0x1b, 0x5d, 0x4e, 0xa8, 0x82,
    0x70, 0xed, 0xe8, 0xec, 0x72, 0xb3, 0x15, 0xc3, 0xff, 0xab, 0xb6, 0x47, 0x44, 0x01, 0xac, 0x25,
    0xc9, 0xfa, 0x8e, 0x41, 0x1a, 0x21, 0xcb, 0xd3, 0x0d, 0x6e, 0xfe, 0x26, 0x58, 0xda, 0x32, 0x0f,
    0x20, 0xa9, 0x9d, 0x84, 0x98, 0x05, 0x9c, 0xbb, 0x22, 0x8c, 0x63, 0xe7, 0xc5, 0xe1, 0x73, 0xc6,
    0xaf, 0x24, 0x5b, 0x87, 0x66, 0x27, 0xf7, 0x57, 0xf4, 0x96, 0xb1, 0xb7, 0x5c, 0x8b, 0xd5, 0x54,
    0x79, 0xdf, 0xaa, 0xf6, 0x3e, 0xa3, 0xf1, 0x11, 0xca, 0xf5, 0xd1, 0x17, 0x7b, 0x93, 0x83, 0xbc,
    0xbd, 0x52, 0x1e, 0xeb, 0xae, 0xcc, 0xd6, 0x35, 0x08, 0xc8, 0x8a, 0xb4, 0xe2, 0xcd, 0xbf, 0xd9,
    0xd0, 0x50, 0x59, 0x3f, 0x4d, 0x62, 0x34, 0x0a, 0x48, 0x88, 0xb5, 0x56, 0x4c, 0x2e, 0x6b, 0x9e,
    0xd2, 0x3d, 0x3c, 0x03, 0x13, 0xfb, 0x97, 0x51, 0x75, 0x4a, 0x91, 0x71, 0x23, 0xbe, 0x76, 0x2a,
    0x5f, 0xf9, 0xd4, 0x55, 0x0b, 0xdc, 0x37, 0x31, 0x16, 0x74, 0xd7, 0x77, 0xa7, 0xe6, 0x07, 0xdb,
    0xa4, 0x2f, 0x46, 0xf3, 0x61, 0x45, 0x67, 0xe3, 0x0c, 0xa2, 0x3b, 0x1c, 0x85, 0x18, 0x04, 0x1d,
    0x29, 0xa0, 0x8f, 0xb2, 0x5a, 0xd8, 0xa6, 0x7e, 0xee, 0x8d, 0x53, 0x4b, 0xa1, 0x9a, 0xc1, 0x0e,
    0x7a, 0x49, 0xa5, 0x2c, 0x81, 0xc4, 0xc7, 0x36, 0x2b, 0x7f, 0x43, 0x95, 0x33, 0xf2, 0x6c, 0x68,
    0x6d, 0xf0, 0x02, 0x28, 0xce, 0xdd, 0x9b, 0xea, 0x5e, 0x99, 0x7c, 0x14, 0x86, 0xcf, 0xe5, 0x42,
    0xb8, 0x40, 0x78, 0x2d, 0x3a, 0xe9, 0x64, 0x1f, 0x92, 0x90, 0x7d, 0x39, 0x6f, 0xe0, 0x89, 0x30,
)

# ── KS_TABLE (.rodata 0x2c90 armv7 / 0x1b4c arm64) ───────────────────────────
KS_TABLE: tuple[int, ...] = (
    0x64, 0xac, 0x28, 0x5a, 0xc9, 0xb3, 0x37, 0xc5, 0x0a, 0x10, 0xb7, 0xa3, 0xba, 0xb1, 0x97, 0x46,
    0x3d, 0x05, 0xdc, 0x66, 0x6e, 0xf6, 0x9a, 0xf8, 0x0d, 0x58, 0x95, 0x67, 0xc6, 0xaa, 0xab, 0xec,
    0xa0, 0x68, 0x9b, 0x96, 0xd4, 0xeb, 0xbf, 0x43, 0x49, 0x36, 0xe9, 0x6a, 0x89, 0xd8, 0xc3, 0x8a,
    0x94, 0x63, 0x99, 0xbc, 0x7b, 0xbe, 0xc1, 0x22, 0xbb, 0x5c, 0x71, 0xd5, 0x1f, 0x92, 0x57, 0x5d,
    0x8f, 0x44, 0x41, 0x1d, 0x51, 0xe6, 0x40, 0x17, 0xfb, 0xfd, 0x19, 0x32, 0x34, 0xb8, 0x61, 0x2a,
    0xca, 0x23, 0x6f, 0xda, 0x39, 0xf7, 0xa2, 0x01, 0x7f, 0xd6, 0x31, 0xe7, 0xde, 0x80, 0x04, 0xdd,
    0x2c, 0x59, 0x82, 0xaf, 0xa8, 0xe0, 0x0f, 0xcd, 0xa1, 0x12, 0x3e, 0x30, 0xd1, 0x1c, 0xd0, 0x3a,
    0x33, 0x72, 0x2e, 0x4f, 0x90, 0x02, 0x13, 0x06, 0x75, 0xce, 0x87, 0xc2, 0xef, 0xb2, 0xad, 0x7d,
    0x38, 0x15, 0xe1, 0x52, 0x9f, 0x7a, 0x6c, 0x2f, 0x27, 0xc4, 0xe2, 0x81, 0xa9, 0xcf, 0x8d, 0xc0,
    0xd7, 0xdf, 0xff, 0x60, 0x76, 0x14, 0x8c, 0x5e, 0x55, 0x09, 0xe4, 0x08, 0xc7, 0x42, 0x20, 0xfc,
    0xd2, 0x50, 0x91, 0xd9, 0x4c, 0x62, 0x9e, 0xe8, 0xb9, 0xa6, 0xf9, 0x1a, 0x00, 0x21, 0x0b, 0xfa,
    0x35, 0x9c, 0x4e, 0x4b, 0x69, 0x48, 0xcb, 0x0e, 0xc8, 0xa4, 0x5b, 0xea, 0x84, 0x07, 0xb4, 0x18,
    0xf4, 0xae, 0x6b, 0xdb, 0xa7, 0xcc, 0x3f, 0x8b, 0x4a, 0x0c, 0x3c, 0x25, 0xe5, 0x54, 0x4d, 0x45,
    0x83, 0xed, 0x11, 0xf0, 0xb0, 0x53, 0x93, 0xf2, 0x74, 0x26, 0xb5, 0x9d, 0x6d, 0x7c, 0xf3, 0x2d,
    0xf1, 0x56, 0x24, 0x7e, 0x47, 0x1b, 0x86, 0xbd, 0x70, 0x8e, 0x1e, 0x3b, 0x73, 0x16, 0x03, 0xb6,
    0xac, 0x28, 0x5a, 0xc9, 0xb3, 0x37, 0xc5, 0x0a, 0x10, 0xb7, 0xa3, 0xba, 0xb1, 0x97, 0x46, 0x88,
)


# ── Cipher helpers (port directo de jl_auth_v3.py) ──────────────────────────

def _key_schedule(data16: list[int]) -> list[int]:
    """sub_1038 en libjl_auth.so: deriva 272-byte key schedule de 16B seed."""
    out = [0] * 272
    for i in range(16):
        out[i] = data16[i]

    buf = list(data16[:16])
    checksum = 0
    for b in data16:
        checksum ^= b
    buf.append(checksum & 0xFF)

    for rnd in range(16):
        for i in range(17):
            b = buf[i]
            buf[i] = ((b << 3) | (b >> 5)) & 0xFF

        read_pos = (rnd + 1) % 17
        for j in range(16):
            src = buf[read_pos]
            tbl_idx = 0xF + rnd * 16 - j
            out[16 + rnd * 16 + j] = (KS_TABLE[tbl_idx] + src) & 0xFF
            read_pos += 1
            if read_pos > 16:
                read_pos = 0
    return out


def _fibonacci_mix(s: list[int]) -> list[int]:
    """ARM64 register-level Fibonacci butterfly (0x121c-0x1364)."""
    r: dict[int, int] = {}
    r[16] = s[0];  r[17] = s[1];  r[3] = s[2];  r[4] = s[3]
    r[5] = s[4];   r[6] = s[5];   r[7] = s[6];  r[19] = s[7]
    r[20] = s[8];  r[21] = s[9];  r[22] = s[10]; r[23] = s[11]
    r[24] = s[12]; r[25] = s[13]; r[26] = s[14]; r[27] = s[15]

    M = 0xFFFFFFFF
    # Stage 1
    r[28] = (r[17] + r[16] * 2) & M; r[16] = (r[17] + r[16]) & M
    r[17] = (r[4] + r[3] * 2) & M;   r[3] = (r[4] + r[3]) & M
    r[4] = (r[6] + r[5] * 2) & M;    r[5] = (r[6] + r[5]) & M
    r[6] = (r[19] + r[7] * 2) & M;   r[7] = (r[19] + r[7]) & M
    r[19] = (r[21] + r[20] * 2) & M; r[20] = (r[21] + r[20]) & M
    r[21] = (r[23] + r[22] * 2) & M; r[22] = (r[23] + r[22]) & M
    r[23] = (r[25] + r[24] * 2) & M; r[24] = (r[25] + r[24]) & M
    r[25] = (r[27] + r[26] * 2) & M; r[26] = (r[27] + r[26]) & M

    # Stage 2
    r[27] = (r[22] + r[19] * 2) & M; r[19] = (r[22] + r[19]) & M
    r[22] = (r[26] + r[23] * 2) & M; r[23] = (r[26] + r[23]) & M
    r[26] = (r[16] + r[17] * 2) & M; r[16] = (r[17] + r[16]) & M
    r[17] = (r[5] + r[6] * 2) & M;   r[5] = (r[6] + r[5]) & M
    r[6] = (r[20] + r[21] * 2) & M;  r[20] = (r[21] + r[20]) & M
    r[21] = (r[24] + r[25] * 2) & M; r[24] = (r[25] + r[24]) & M
    r[25] = (r[7] + r[28] * 2) & M;  r[7] = (r[7] + r[28]) & M
    r[28] = (r[3] + r[4] * 2) & M;   r[3] = (r[4] + r[3]) & M

    # Stage 3
    r[4] = (r[24] + r[6] * 2) & M;   r[6] = (r[24] + r[6]) & M
    r[24] = (r[3] + r[25] * 2) & M;  r[3] = (r[25] + r[3]) & M
    r[25] = (r[19] + r[22] * 2) & M; r[19] = (r[22] + r[19]) & M
    r[22] = (r[16] + r[17] * 2) & M; r[16] = (r[17] + r[16]) & M
    r[17] = (r[20] + r[21] * 2) & M; r[20] = (r[21] + r[20]) & M
    r[21] = (r[7] + r[28] * 2) & M;  r[7] = (r[7] + r[28]) & M
    r[28] = (r[5] + r[27] * 2) & M;  r[5] = (r[27] + r[5]) & M
    r[27] = (r[23] + r[26] * 2) & M; r[23] = (r[23] + r[26]) & M

    # Stage 4
    r[26] = (r[7] + r[17] * 2) & M;  r[17] = (r[17] + r[7]) & M
    r[7] = (r[23] + r[28] * 2) & M;  r[23] = (r[23] + r[28]) & M
    r[28] = (r[6] + r[24] * 2) & M;  r[6] = (r[6] + r[24]) & M
    r[24] = (r[19] + r[22] * 2) & M; r[19] = (r[19] + r[22]) & M
    r[22] = (r[20] + r[21] * 2) & M; r[20] = (r[20] + r[21]) & M
    r[21] = (r[5] + r[27] * 2) & M;  r[5] = (r[27] + r[5]) & M
    r[27] = (r[16] + r[4] * 2) & M;  r[16] = (r[4] + r[16]) & M
    r[4] = (r[3] + r[25] * 2) & M;   r[3] = (r[25] + r[3]) & M

    return [
        r[26] & 0xFF, r[17] & 0xFF, r[7] & 0xFF, r[23] & 0xFF,
        r[28] & 0xFF, r[6] & 0xFF, r[24] & 0xFF, r[19] & 0xFF,
        r[22] & 0xFF, r[20] & 0xFF, r[21] & 0xFF, r[5] & 0xFF,
        r[27] & 0xFF, r[16] & 0xFF, r[4] & 0xFF, r[3] & 0xFF,
    ]


def _cond_mix(state: list[int], key_block: list[int], mask: int, phase: int) -> list[int]:
    """Conditional XOR/ADD mixing.
    phase=3: bit set → XOR, clear → ADD.
    phase=5: bit set → ADD, clear → XOR.
    """
    result = list(state)
    for i in range(16):
        bit_set = ((1 << i) & mask) != 0
        if phase == 3:
            if bit_set:
                result[i] = result[i] ^ key_block[i]
            else:
                result[i] = (key_block[i] + result[i]) & 0xFF
        else:  # phase 5
            if bit_set:
                result[i] = (key_block[i] + result[i]) & 0xFF
            else:
                result[i] = result[i] ^ key_block[i]
    return result


def _sbox_sub(state: list[int]) -> list[int]:
    """Mixed SBOX/ISBOX substitution (positions even=SBOX, odd-ish=ISBOX)."""
    result = list(state)
    for pos in (0, 3, 4, 7, 8, 11, 12, 15):
        result[pos] = SBOX[result[pos]]
    for pos in (1, 2, 5, 6, 9, 10, 13, 14):
        result[pos] = ISBOX[result[pos]]
    return result


def _block_cipher(state: list[int], ek: list[int], mode: int) -> list[int]:
    """sub_11b8 block cipher.

    Estructura (del disassembly):
    1. Phase3(ek[0..15]) + Sbox + Phase5(ek[16..31])             [x9=0]
    2..8. Fibonacci + (mode-conditional phase2 si x9==2) + Phase3 + Sbox + Phase5  [x9=1..7]
    9. Fibonacci + FinalMix(ek[256..271])                       [x9=8]
    """
    s = list(state)
    initial = list(state)

    # x9 = 0
    s = _cond_mix(s, ek[0:16], MASK, 3)
    s = _sbox_sub(s)
    s = _cond_mix(s, ek[16:32], MASK, 5)

    for x9 in range(1, 9):
        s = _fibonacci_mix(s)
        ek_off = x9 * 0x20

        if x9 == 8:
            s = _cond_mix(s, ek[0x100:0x110], MASK, 3)
            break

        # mode-conditional phase 2 cuando x9 == 2
        if mode != 0 and x9 == 2:
            for i in range(16):
                bit_set = ((1 << i) & MASK) != 0
                if bit_set:
                    s[i] = s[i] ^ initial[i]
                else:
                    s[i] = (initial[i] + s[i]) & 0xFF

        s = _cond_mix(s, ek[ek_off:ek_off + 16], MASK, 3)
        s = _sbox_sub(s)
        s = _cond_mix(s, ek[ek_off + 16:ek_off + 32], MASK, 5)

    for i in range(16):
        state[i] = s[i]
    return state


def _function_e1test(key6: list[int], input16: list[int], seed16: list[int]) -> list[int]:
    """Main encryption (port directo del E1test en libjl_auth.so)."""
    expanded_key = [key6[i % 6] for i in range(16)]
    output = list(input16[:16])
    ks = _key_schedule(list(seed16[:16]))
    _block_cipher(output, ks, 0)

    for i in range(16):
        output[i] = (expanded_key[i] + (output[i] ^ input16[i])) & 0xFF

    # Obfuscación del seed (constants hardcoded de E1test)
    obf = [0] * 16
    obf[0]  = (seed16[0]  - 0x17) & 0xFF
    obf[1]  = (seed16[1]  ^ 0xE5) & 0xFF
    obf[2]  = (seed16[2]  - 0x21) & 0xFF
    obf[3]  = (seed16[3]  ^ 0xC1) & 0xFF
    obf[4]  = (seed16[4]  - 0x4D) & 0xFF
    obf[5]  = (seed16[5]  ^ 0xA7) & 0xFF
    obf[6]  = (seed16[6]  - 0x6B) & 0xFF
    obf[7]  = (seed16[7]  ^ 0x83) & 0xFF
    obf[8]  = (seed16[8]  ^ 0xE9) & 0xFF
    obf[9]  = (seed16[9]  - 0x1B) & 0xFF
    obf[10] = (seed16[10] ^ 0xDF) & 0xFF
    obf[11] = (seed16[11] - 0x3F) & 0xFF
    obf[12] = (seed16[12] ^ 0xB3) & 0xFF
    obf[13] = (seed16[13] - 0x59) & 0xFF
    obf[14] = (seed16[14] ^ 0x95) & 0xFF
    obf[15] = (seed16[15] - 0x7D) & 0xFF

    ks2 = _key_schedule(obf)
    _block_cipher(output, ks2, 1)
    return output


# ── Public API ────────────────────────────────────────────────────────────────

class JliCipher:
    """JieLi block cipher (16B blocks). Stateless, key = STATIC_KEY hardcoded."""

    def __init__(self) -> None:
        # No customization de key/magic — STATIC_KEY y MAGIC son fijos del firmware JieLi.
        pass

    def encrypt(self, plaintext: bytes) -> bytes:
        if len(plaintext) != BLOCK_SIZE:
            raise ValueError(f"JliCipher.encrypt: input debe ser {BLOCK_SIZE} bytes, got {len(plaintext)}")
        out = _function_e1test(list(MAGIC), list(plaintext), list(STATIC_KEY))
        return bytes(out)


def get_random_auth_data() -> bytes:
    """16B random — phone's challenge para step 1 del handshake.

    El step marker (0x00) se añade por separado en AuthSession.
    """
    return os.urandom(BLOCK_SIZE)


# ─── Auth handshake state machine (6 pasos) ──────────────────────────────────

from typing import Protocol

from qix_ble.errors import BadgeRejected

# Markers de cada step en el primer byte del payload AE01/AE02
STEP_CHALLENGE: int = 0x00   # phone→dev o dev→phone: <marker> ‖ 16B random/challenge
STEP_ENCRYPTED: int = 0x01   # phone→dev o dev→phone: <marker> ‖ 16B encrypted
STEP_PASS_LITERAL: bytes = b"\x02pass"   # 5B fijo: step 3 (phone→dev) y step 6 (dev→phone)


class _Ae00RawTransport(Protocol):
    """Interface mínima del transport requerida por AuthSession.
    Implementado por QixTransport (real) y MockTransport (tests).
    """
    def send_to_ae01(self, data: bytes) -> None: ...
    def recv_ae02_raw(self, timeout: float = 5.0) -> bytes: ...


class AuthSession:
    """6-step JieLi RCSP auth handshake sobre service AE00.

    Verificado contra captura btsnoop Day 1 byte-a-byte. Raises BadgeRejected
    si cualquier step no matchea el patrón esperado.

    Uso:
        with QixTransport(mac) as t:
            AuthSession(t).do_handshake()
            # post-auth: t.send_to_ae01(rcsp_frame) o t.send_command(...) según el caso
    """

    def __init__(
        self,
        transport: _Ae00RawTransport,
        cipher: JliCipher | None = None,
        step_timeout: float = 5.0,
    ):
        self.transport = transport
        self.cipher = cipher or JliCipher()
        self.step_timeout = step_timeout

    def do_handshake(self) -> None:
        # ── Step 1: phone → AE01: 00 ‖ random16 ────────────────────────────
        our_random = get_random_auth_data()
        log.info("auth step 1: tx random challenge (16B)")
        self.transport.send_to_ae01(bytes([STEP_CHALLENGE]) + our_random)

        # ── Step 2: dev → AE02: 01 ‖ encrypt(our_random). Validar. ──────────
        resp2 = self.transport.recv_ae02_raw(timeout=self.step_timeout)
        if len(resp2) != 1 + BLOCK_SIZE or resp2[0] != STEP_ENCRYPTED:
            raise BadgeRejected(
                f"auth step 2: payload inválido (len={len(resp2)} marker=0x{resp2[0]:02x} "
                f"expected len={1+BLOCK_SIZE} marker=0x{STEP_ENCRYPTED:02x})"
            )
        device_encrypted = resp2[1:]
        expected_encrypted = self.cipher.encrypt(our_random)
        if device_encrypted != expected_encrypted:
            raise BadgeRejected(
                f"auth step 2: device encryption mismatch — "
                f"got {device_encrypted.hex()}, expected {expected_encrypted.hex()}"
            )
        log.info("auth step 2: device encryption verified")

        # ── Step 3: phone → AE01: "\x02pass" ───────────────────────────────
        log.info("auth step 3: tx literal pass")
        self.transport.send_to_ae01(STEP_PASS_LITERAL)

        # ── Step 4: dev → AE02: 00 ‖ challenge16 ───────────────────────────
        resp4 = self.transport.recv_ae02_raw(timeout=self.step_timeout)
        if len(resp4) != 1 + BLOCK_SIZE or resp4[0] != STEP_CHALLENGE:
            raise BadgeRejected(
                f"auth step 4: payload inválido (len={len(resp4)} marker=0x{resp4[0]:02x} "
                f"expected len={1+BLOCK_SIZE} marker=0x{STEP_CHALLENGE:02x})"
            )
        device_challenge = resp4[1:]
        log.info("auth step 4: rx device challenge (16B)")

        # ── Step 5: phone → AE01: 01 ‖ encrypt(device_challenge) ───────────
        our_encryption = self.cipher.encrypt(device_challenge)
        log.info("auth step 5: tx encrypted device challenge")
        self.transport.send_to_ae01(bytes([STEP_ENCRYPTED]) + our_encryption)

        # ── Step 6: dev → AE02: "\x02pass" exact. Validar. ─────────────────
        resp6 = self.transport.recv_ae02_raw(timeout=self.step_timeout)
        if resp6 != STEP_PASS_LITERAL:
            raise BadgeRejected(
                f"auth step 6: device no devolvió '\\x02pass' — got {resp6.hex()}"
            )
        # Marcar el transport como autenticado para que RcspSession lo verifique
        setattr(self.transport, "_auth_done", True)
        log.info("auth step 6: handshake COMPLETE")
