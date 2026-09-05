#!/usr/bin/env python3
"""
Decrypt blimit.bin using jl_crypto::cd03_crc_encode (16-bit LFSR XOR stream cipher).

The cipher uses a 16-bit state. The first plaintext byte XOR ciphertext byte
gives us key_low (8 bits). For the remaining 8 bits (key_high) we brute-force
the 256 possibilities and pick the one whose first 8 decrypted bytes match the
known "JLDGAUTH" magic.

RE source: hw-sessions/2026-05-17-pm/blimit-re/ghidra-notes/01-blimit-generator-decoded.md
"""
import struct
import sys
from pathlib import Path

MAGIC = b"JLDGAUTH"  # DAT_00d29ad8 in isd_download_br35.exe
# Plaintext bytes 0..11: MAGIC (8B) + padding (4B) per FUN_0055ccd0 _memset
# Including the trailing zeros disambiguates candidates uniquely.
KNOWN_PT = MAGIC + b"\x90\x00"  # size=0x90 at offset 8 also known


def cd03_crc_encode(buf: bytearray, key_state: int, aux: int = 0) -> None:
    """In-place XOR stream cipher matching FUN_004a6560 in isd_download_br35.exe.

    Symmetric: applying it twice with the same key restores the original.
    """
    if aux != 0:
        key_state ^= (aux >> 2)
    key_state &= 0xFFFF

    n = len(buf)
    if n == 0:
        return

    # First byte: XOR with low byte of state (no state update yet)
    buf[0] ^= (key_state & 0xFF)

    for i in range(1, n):
        hi = (key_state >> 8) & 0xFF
        lo = key_state & 0xFF
        msb = hi >> 7
        stream_byte = ((((msb << 4) ^ lo) << 1) | msb) & 0xFF
        buf[i] ^= stream_byte
        new_hi = ((((msb << 3) ^ hi) << 1) | (lo >> 7)) & 0xFF
        new_lo = stream_byte
        key_state = (new_hi << 8) | new_lo


def recover_key_from_known_plaintext(ciphertext: bytes, known_pt: bytes) -> int | None:
    """Recover the 16-bit initial key state given known plaintext.

    With 2+ known plaintext bytes we have enough constraint to uniquely
    determine the 16-bit state (or eliminate all but one candidate).
    """
    assert len(known_pt) >= 2, "need at least 2 known plaintext bytes"
    # First byte gives us key_low
    key_low = ciphertext[0] ^ known_pt[0]
    # Brute-force 256 candidates for key_high
    candidates = []
    for key_high in range(256):
        state = (key_high << 8) | key_low
        trial = bytearray(ciphertext[:len(known_pt)])
        cd03_crc_encode(trial, state, 0)
        if bytes(trial) == known_pt:
            candidates.append(state)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        print(f"WARN: {len(candidates)} candidate keys — need more known plaintext", file=sys.stderr)
        return candidates[0]
    return None


def decrypt_blimit(path: Path) -> tuple[int, bytes]:
    """Decrypt a 144B blimit.bin file. Returns (key_state, plaintext)."""
    ciphertext = path.read_bytes()
    if len(ciphertext) != 144:
        raise ValueError(f"Expected 144 bytes, got {len(ciphertext)}")
    key = recover_key_from_known_plaintext(ciphertext, KNOWN_PT)
    if key is None:
        raise RuntimeError("Could not recover key — magic mismatch or wrong cipher")
    plain = bytearray(ciphertext)
    cd03_crc_encode(plain, key, 0)
    return key, bytes(plain)


def hexdump(data: bytes, start: int = 0) -> str:
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off+16]
        h = " ".join(f"{b:02x}" for b in chunk)
        a = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{start+off:08x}: {h:<48}  {a}")
    return "\n".join(lines)


def parse_blimit_struct(plain: bytes) -> dict:
    """Parse decrypted blimit per the layout in 01-blimit-generator-decoded.md."""
    if len(plain) != 144:
        raise ValueError("expected 144B plaintext")
    d = {
        "magic_a": plain[0x00:0x04].hex(),
        "magic_b": plain[0x04:0x08].hex(),
        "magic_str": plain[0x00:0x08].decode("ascii", errors="replace"),
        "total_size": struct.unpack("<H", plain[0x08:0x0a])[0],
        "crc_body": struct.unpack("<H", plain[0x0a:0x0c])[0],
        "field_0x0c": struct.unpack("<H", plain[0x0c:0x0e])[0],
        "crc_ts": struct.unpack("<H", plain[0x0e:0x10])[0],
        "chip_info_0": struct.unpack("<I", plain[0x10:0x14])[0],
        "chip_info_1": struct.unpack("<I", plain[0x14:0x18])[0],
        "flash_checksum": struct.unpack("<I", plain[0x18:0x1c])[0],
        "flash_size_low": struct.unpack("<H", plain[0x1c:0x1e])[0],
        "type_or_ver": plain[0x1e],
        "pad_0x1f": plain[0x1f],
        "const_0x20": struct.unpack("<I", plain[0x20:0x24])[0],
        "const_0x24": struct.unpack("<I", plain[0x24:0x28])[0],
        "timestamp_ms": struct.unpack("<Q", plain[0x28:0x30])[0],
        "chip_info_rest": plain[0x30:0x40].hex(),
        "payload_hex": plain[0x40:0x90].hex(),
        "payload_ascii": plain[0x40:0x90].decode("ascii", errors="replace"),
    }
    return d


def encrypt_blimit(plain: bytes, key_state: int) -> bytes:
    """Encrypt a 144B blimit plaintext with the given LFSR key state.

    The cipher is symmetric: encrypt and decrypt are the same operation.
    """
    if len(plain) != 144:
        raise ValueError(f"Expected 144 bytes, got {len(plain)}")
    out = bytearray(plain)
    cd03_crc_encode(out, key_state, 0)
    return bytes(out)


def roundtrip_test(path: Path) -> bool:
    """decrypt → encrypt with same key → should match original ciphertext."""
    ct = path.read_bytes()
    key, plain = decrypt_blimit(path)
    re_ct = encrypt_blimit(plain, key)
    return re_ct == ct


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="JieLi blimit.bin codec (cd03_crc_encode LFSR cipher)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("decrypt", help="Decrypt and parse a blimit.bin file")
    sp.add_argument("path")

    sp = sub.add_parser("roundtrip", help="Verify decrypt→encrypt→ct round-trip")
    sp.add_argument("path")

    args = ap.parse_args()
    path = Path(args.path)

    if args.cmd == "decrypt":
        key, plain = decrypt_blimit(path)
        print(f"Recovered key state: 0x{key:04x}  (low={key & 0xff:02x} high={(key>>8) & 0xff:02x})")
        print(f"\nDecrypted plaintext (144 B):")
        print(hexdump(plain))
        print(f"\nParsed fields:")
        fields = parse_blimit_struct(plain)
        import datetime
        for k, v in fields.items():
            if k == "timestamp_ms":
                try:
                    dt = datetime.datetime.fromtimestamp(v / 1000, datetime.UTC)
                    print(f"  {k}: {v}  ({dt.isoformat()})")
                except (OSError, ValueError):
                    print(f"  {k}: {v}  (invalid as unix-ms, may be different encoding)")
            elif isinstance(v, int):
                print(f"  {k}: 0x{v:x}  ({v})")
            else:
                print(f"  {k}: {v}")
    elif args.cmd == "roundtrip":
        ok = roundtrip_test(path)
        print(f"Round-trip {'OK ✅' if ok else 'FAIL ❌'}")
        sys.exit(0 if ok else 1)
