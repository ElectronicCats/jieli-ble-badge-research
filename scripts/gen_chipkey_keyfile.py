#!/usr/bin/env python3
"""
gen_chipkey_keyfile.py — Generate a JieLi `isd_download -key` chipkey file
from a 16-bit chipkey value.

Background
----------
When a JieLi chip (e.g. AC707N / BR35) has a chipkey burned into its eFuse,
`isd_download.exe` refuses to flash it over USB MaskROM with:

    ERROR: Key has been burned with a key, please use the "-key <key file>" ...

The `-key` file is NOT the JieLi-signed L3KEY PEM (that was an earlier wrong
conclusion). It is the community-reversed "chipkeyfile" format: an AES-ECB
wrapped 16-bit chipkey + CRC32, marker `val2 == 0xa000`.

Validated on HW 2026-06-05 against the PID 1558 badge (chipkey 0x9847,
Chip Version C, 4M flash): the file produced here passes the gate and lets
isd_download flash via USB MaskROM — i.e. the anti-brick / Vector B recovery
path, with no JieLi-signed PEM required.

The 16-bit chipkey value itself (0x9847 for PID 1558) is recovered separately
by decoding the chipkey-bin blob inside the OEM `isd_config.ini`
(see scripts/inject_chipkey.py / chipkeybin_decode).

This script is DETERMINISTIC: same chipkey -> byte-identical output, so the
committed artifact under tools/chipkey/ can be regenerated and verified. The
embedded AES key is stored inside the file itself, so fixing it is purely
cosmetic (the upstream community helper randomizes it).

Usage
-----
    python3 scripts/gen_chipkey_keyfile.py 0x9847 -o tools/chipkey/chipkey_9847.bin
    python3 scripts/gen_chipkey_keyfile.py 9847            # print to stdout

Dependencies (same as the vendored jl-misctools): pycryptodomex, crcmod
    python3 -m venv .venv && .venv/bin/pip install pycryptodomex crcmod
    .venv/bin/python scripts/gen_chipkey_keyfile.py 0x9847 -o tools/chipkey/chipkey_9847.bin
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JLTECH = REPO / "tools" / "community-re" / "jl-misctools" / "firmware"
sys.path.insert(0, str(JLTECH))

# Fixed 16-byte AES key -> deterministic, git-stable output. The key is stored
# verbatim inside the key file (bytes [16:32] of the decoded blob), so its
# actual value is irrelevant to isd_download; pinning it just makes the
# generated file reproducible. The upstream keyfile_create() uses random bytes.
DET_AES_KEY = bytes.fromhex("0123456789abcdef0123456789abcdef")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate an isd_download -key chipkey file from a 16-bit chipkey value"
    )
    ap.add_argument("chipkey", help="16-bit chipkey in hex, e.g. 0x9847 or 9847")
    ap.add_argument("-o", "--output", help="output file (default: print to stdout)")
    args = ap.parse_args()

    try:
        from jltech.chipkeyfile import keyfile_encode, keyfile_parse
    except ModuleNotFoundError as exc:
        sys.stderr.write(
            f"missing dependency: {exc.name}\n"
            f"  python3 -m venv .venv && .venv/bin/pip install pycryptodomex crcmod\n"
            f"  then run with .venv/bin/python\n"
        )
        return 2

    key = int(args.chipkey, 16) & 0xFFFF

    # Mirror the community keyfile_create() payload ("<key:04x>a000\0"), but
    # encode with a fixed AES key so the output is reproducible.
    payload = f"{key:04x}a000\0".encode()
    kf = keyfile_encode(payload, key=DET_AES_KEY)

    # Self-check: the file must decode back to the chipkey we asked for.
    parsed = keyfile_parse(kf)
    if parsed != key:
        sys.stderr.write(f"FATAL: round-trip mismatch 0x{parsed:04X} != 0x{key:04X}\n")
        return 1

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(kf)
        print(f"wrote {out}  (chipkey 0x{key:04X}, {len(kf)} chars, round-trip OK)")
    else:
        print(kf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
