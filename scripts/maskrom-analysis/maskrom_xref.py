#!/usr/bin/env python3
"""Cross-reference MaskROM stub addresses against app.bin binaries.

For each address listed in maskrom_stubs.ld, scan target binaries for
4-byte little-endian occurrences. Each hit = the firmware probably loads
that function pointer to call it.
"""
import re
import struct
import sys
from pathlib import Path
from collections import defaultdict

# Repo root, anchored on this file (scripts/maskrom-analysis/ → parents[2]).
REPO = Path(__file__).resolve().parents[2]

LD = REPO / "tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK/cpu/br35/maskrom_stubs.ld"

TARGETS = {
    "OEM_pid1558_V1.0.3":  REPO / "hw-sessions/2026-05-17/pid1558-firmware-rev/app.bin",
    "OEM_pid1581_Alipay":  REPO / "hw-sessions/2026-05-16/libjl_ota_auth-reverse/ufw-unpacked/cloud_inner/files/app.bin",
    "SDK_self_built":      REPO / "hw-sessions/2026-05-16/libjl_ota_auth-reverse/ufw-unpacked/self_built/files/app.bin",
}

# Parse LD: lines like "  symbol = ABSOLUTE(0xXXXX);"
LD_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*ABSOLUTE\s*\(\s*(0x[0-9a-fA-F]+)\s*\)\s*;')

# MaskROM code is in 0xf8000–0xfffff region. Data/RAM globals are in 0x100000+.
# We focus on CODE symbols (function pointers) — they're what proves the firmware
# uses the MaskROM ABI. Data globals would also count but are noisier.
CODE_LO, CODE_HI = 0xF0000, 0x100000

def parse_ld():
    funcs = {}        # addr -> symbol  (code, MaskROM functions)
    data  = {}        # addr -> symbol  (RAM globals, 0x100xxx+)
    with open(LD) as f:
        for line in f:
            m = LD_RE.match(line)
            if not m:
                continue
            name, addr_s = m.group(1), m.group(2)
            addr = int(addr_s, 16)
            if CODE_LO <= addr < CODE_HI:
                funcs[addr] = name
            elif addr >= 0x100000:
                data[addr] = name
    return funcs, data

def scan(path, addr_set):
    """For each addr in set, count LE 4-byte occurrences in binary."""
    blob = Path(path).read_bytes()
    counts = defaultdict(int)
    for addr in addr_set:
        needle = struct.pack("<I", addr)
        off = 0
        while True:
            i = blob.find(needle, off)
            if i < 0:
                break
            counts[addr] += 1
            off = i + 1
    return counts, len(blob)

def main():
    funcs, data = parse_ld()
    print(f"=== maskrom_stubs.ld parsed: {len(funcs)} MaskROM functions, {len(data)} RAM globals\n")

    addr_set = set(funcs.keys())
    results = {}
    for label, path in TARGETS.items():
        c, sz = scan(path, addr_set)
        results[label] = c
        used = sum(1 for a in addr_set if c[a] > 0)
        print(f"{label:24s}  {sz:>8d} bytes   stubs used: {used:3d}/{len(funcs)}   total xrefs: {sum(c.values())}")

    # Build matrix
    print(f"\n=== Per-symbol usage matrix (only symbols used in ≥1 target)\n")
    print(f"{'addr':>10s}  {'symbol':35s}  " + "  ".join(f"{l:>22s}" for l in TARGETS))
    print("-" * (50 + 24 * len(TARGETS)))
    rows = []
    for addr, sym in sorted(funcs.items()):
        row = [results[l][addr] for l in TARGETS]
        if any(row):
            rows.append((addr, sym, row))

    for addr, sym, row in rows:
        rs = "  ".join(f"{c:>22d}" for c in row)
        print(f"  0x{addr:06x}  {sym:35s}  {rs}")

    # Coverage summary by category
    print(f"\n=== Symbols ONLY in OEM-1558 (used by user's badge FW but not by SDK template)\n")
    only_oem1558 = [(a, s) for a, s, r in rows if r[0] > 0 and r[2] == 0]
    for a, s in only_oem1558:
        print(f"  0x{a:06x}  {s}")

    print(f"\n=== Symbols in SDK template but NOT in OEM-1558 (template-only)\n")
    only_sdk = [(a, s) for a, s, r in rows if r[2] > 0 and r[0] == 0]
    for a, s in only_sdk:
        print(f"  0x{a:06x}  {s}")

if __name__ == "__main__":
    main()
