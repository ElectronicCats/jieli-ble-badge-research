#!/usr/bin/env python3
"""
Patch JCRT config.dat in place. Re-computes all affected CRCs:
  - per-item payload CRC (item_head[item_idx].crc16)
  - item_head_crc (jl_crc16 over file[0x20:0xE0])
  - self_crc (jl_crc16 over file[0x06:0x20])

Usage:
  patch_config_dat.py <config.dat> --ver-info-ver <u16hex>
  patch_config_dat.py <config.dat> --set-payload <item_name> <hex_bytes>
"""
import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/community-re/jl-misctools/firmware"))
from jltech.crc import jl_crc16


def parse_items(data: bytes):
    magic, self_crc, item_head_crc, total_len, item_count = struct.unpack_from(
        "<4sHHIH", data, 0
    )
    assert magic == b"JCRT", f"magic mismatch: {magic!r}"
    items = []
    for i in range(item_count):
        hdr_off = 0x20 + i * 32
        idx, type_, p_off, p_size, crc, _pad = struct.unpack_from(
            "<HHIIHH", data, hdr_off
        )
        name = (
            data[hdr_off + 0x10 : hdr_off + 0x20]
            .rstrip(b"\x00")
            .rstrip(b"\xff")
            .decode(errors="replace")
        )
        items.append(
            dict(
                idx=idx,
                type=type_,
                p_off=p_off,
                p_size=p_size,
                crc=crc,
                hdr_off=hdr_off,
                name=name,
            )
        )
    return items, item_count


def patch_payload(data: bytearray, item: dict, new_payload: bytes):
    if len(new_payload) != item["p_size"]:
        raise ValueError(
            f"payload size mismatch: existing {item['p_size']} vs new {len(new_payload)} "
            f"(item {item['name']!r}). Resize not supported."
        )
    # 1. Splice new payload
    data[item["p_off"] : item["p_off"] + item["p_size"]] = new_payload
    # 2. Recompute item CRC + update in header
    new_crc = jl_crc16(new_payload)
    struct.pack_into("<H", data, item["hdr_off"] + 0x0C, new_crc)


def recompute_global_crcs(data: bytearray):
    item_head_crc = jl_crc16(bytes(data[0x20:0xE0]))
    struct.pack_into("<H", data, 0x06, item_head_crc)
    self_crc = jl_crc16(bytes(data[0x06:0x20]))
    struct.pack_into("<H", data, 0x04, self_crc)
    return self_crc, item_head_crc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config_dat")
    ap.add_argument(
        "--ver-info-ver",
        help="Set ver_info VER field (BE u16, e.g. 0x9901) — bytes 4-5 of payload",
    )
    ap.add_argument(
        "--set-payload",
        nargs=2,
        metavar=("ITEM_NAME", "HEX_BYTES"),
        action="append",
        help="Replace full payload of item <name> with given hex bytes (must match existing size)",
    )
    ap.add_argument(
        "--in-place", action="store_true", help="Modify file in place (default writes .patched.dat)"
    )
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, don't write")
    args = ap.parse_args()

    src = Path(args.config_dat)
    data = bytearray(src.read_bytes())

    items, item_count = parse_items(bytes(data))
    by_name = {it["name"]: it for it in items}

    changes = []

    if args.ver_info_ver is not None:
        ver_item = by_name.get("ver_info")
        if not ver_item:
            sys.exit("no 'ver_info' item found")
        new_ver = int(args.ver_info_ver, 0)
        old_payload = bytes(data[ver_item["p_off"] : ver_item["p_off"] + ver_item["p_size"]])
        if ver_item["p_size"] != 6:
            sys.exit(f"ver_info payload size {ver_item['p_size']} != 6 expected")
        # ver_info = struct.pack(">HHH", vid, pid, ver)
        vid, pid, _old_ver = struct.unpack(">HHH", old_payload)
        new_payload = struct.pack(">HHH", vid, pid, new_ver)
        print(
            f"[ver_info]   VER: 0x{_old_ver:04X} -> 0x{new_ver:04X}  (VID=0x{vid:04X} PID=0x{pid:04X} preserved)"
        )
        patch_payload(data, ver_item, new_payload)
        changes.append("ver_info")

    if args.set_payload:
        for name, hex_bytes in args.set_payload:
            it = by_name.get(name)
            if not it:
                sys.exit(f"item {name!r} not found")
            new_payload = bytes.fromhex(hex_bytes)
            print(f"[{name}]    payload -> {new_payload!r} ({len(new_payload)} B)")
            patch_payload(data, it, new_payload)
            changes.append(name)

    if not changes:
        sys.exit("no changes requested — use --ver-info-ver or --set-payload")

    new_self_crc, new_item_head_crc = recompute_global_crcs(data)
    print(f"\nRecomputed item_head_crc=0x{new_item_head_crc:04X} self_crc=0x{new_self_crc:04X}")

    if args.dry_run:
        print("[dry-run] not writing")
        return

    out = src if args.in_place else src.with_suffix(".patched.dat")
    out.write_bytes(bytes(data))
    print(f"=> wrote {out}")


if __name__ == "__main__":
    main()
