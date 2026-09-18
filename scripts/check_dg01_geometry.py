#!/usr/bin/env python3
"""Assert that a packed DG01 image declares the unit's real flash geometry.

The RCSP update loader reconciles the image's area table against the device's own
compiled table; a mismatch makes pass 2 write NOTHING and still report success
(measured on hardware 2026-09-17: E8 notify-size=0, E6=0x00, no firmware installed).

`PRCT_LEN` is derived from the app size, so a few KB of code growth silently pushes
CODE from 0xF7000 to 0xF8000 and breaks the match with no visible error. Hence this
check runs on every --board dg01 --ota build.

    check_dg01_geometry.py <flash.bin | jl_isd.bin>   [chipkey_hex]
"""
import struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ufw-repack"))
from jltech.cipher import jl_enc_cipher, jl_sfc_cipher
from jltech.crc import jl_crc16

ENT = "<HHIIBBH12s4s"

# The DG01's factory table, read from firmware/oem_DG01.bin with every entry CRC verified.
EXPECT = {
    "PRCT": (0x000000, 0x0F7000),
    "VM":   (0x0F7000, 0x02F000),
    "MODE": (0x126000, 0x0D1000),
    "EXIF": (0x3FD000, 0x001000),
    "BTIF": (0x3FE000, 0x001000),
    # child of the EXT_RESERVED directory, grafted on by scripts/fix_area_table.py
    "RESFS": (0x1F7000, 0x204000),
}
EXPECT_APP_DIR_HEAD = 0x1000


def _unpack(b32):
    hcrc, dcrc, eoff, esize, fl, rsv, idx, nm, tag = struct.unpack_from(ENT, b32, 0)
    return dict(eoff=eoff, esize=esize, fl=fl, rsv=rsv, idx=idx,
                name=nm.split(b"\0")[0].decode("ascii", "replace"))


# RESFS must stay inert. The loader DOES walk EXT_RESERVED's children
# (tranverse_ext_reserve_file_head in the loader's fs_v2_update.o), and for each one
# erase_reserve_area_by_erase_flag() decides what to do from the entry's `res` byte:
#
#     res = 0x80 | OPT    OPT 0 = erase this area when downloading code
#                         OPT 1 = do not operate on it
#                         OPT 2 = write-protect
#
# The OEM ships RESFS with res=0x81 (OPT 1) and fix_area_table.py copies the entry
# verbatim, so this holds by construction -- but an OPT regression would be silent and
# would make the loader erase all 0x204000 of the unit's resource partition. Assert it.
RESFS_EXPECT_ATTR = 0x92     # ENCRYPTED | RESERVED | REG
RESFS_EXPECT_RES = 0x81      # 0x80 | OPT1 "do not operate"


def _top_entry(buf, off):
    """Top-level JLFS headers are jl_enc_cipher'd with key 0xFFFF, one 32-byte entry
    at a time. (The app-area children below are PLAIN once SFC-descrambled.)"""
    b = bytearray(buf[off:off + 32]); jl_enc_cipher(b, 0, 32, 0xFFFF)
    return _unpack(bytes(b))


def _entry(buf, off):
    return _unpack(bytes(buf[off:off + 32]))


ALLOW_RESFS_MEMBER = False   # set by main() from the command line


def _flash_bin_from_ufw(raw: bytes):
    """If given a .ufw container, pull its flash.bin member out. The container's own
    header+entries are jl_enc_cipher'd with 0xFFFF (different layer from the JLFS)."""
    def x(b):
        o = bytearray(b); jl_enc_cipher(o, 0, len(o), 0xFFFF); return bytes(o)
    hdr = x(raw[:0x40])
    if jl_crc16(bytes(hdr[2:])) != struct.unpack_from("<H", hdr, 0)[0]:
        return None                      # not a .ufw
    n = struct.unpack_from("<H", hdr, 8)[0]
    found = None
    for i in range(n):
        p = x(raw[0x40 + i * 0x50:0x40 + (i + 1) * 0x50])
        _t, _i, _d, _w, off, sz, _s2, _w2, nm = struct.unpack_from("<HHHHIII44s16s", p, 0)
        member = nm.split(b"\0")[0]
        if member == b"RESFS" and not ALLOW_RESFS_MEMBER:
            # A type-0x32 member whose name matches a reserved area makes the loader take
            # the other branch of erase_reserve_area_by_erase_flag(): erase the whole area
            # and download the member into it. That is the supported way to RESTORE a
            # reserved region over the air -- but in a custom-firmware build it would be an
            # accident, and a 2 MB wipe of the unit's resources. Deliberate restore images
            # pass --allow-resfs-member.
            raise RuntimeError("the .ufw carries a member named RESFS -- the loader would "
                               "erase and rewrite the whole 0x204000 resource partition. "
                               "Pass --allow-resfs-member if that is intended (a restore "
                               "image); otherwise this is a packaging bug")
        if member == b"flash.bin":
            found = bytearray(raw[off:off + sz])
    return found


def main(path: str, chipkey: int = 0xB165) -> int:
    raw = bytearray(Path(path).read_bytes())
    inner = _flash_bin_from_ufw(bytes(raw))
    if inner is not None:
        print(f"  (read flash.bin out of the .ufw container: {len(inner)} bytes)")
        raw = inner

    # top-level chain: find app_dir_head
    app_dir = None
    off = 0x20
    for _ in range(16):
        e = _top_entry(raw, off)
        if not e["name"]:
            break
        if e["name"] == "app_dir_head":
            app_dir = e["eoff"]
        if e["idx"]:
            break
        off += 32
    if app_dir is None:
        print("FAIL: no app_dir_head in the top-level directory", file=sys.stderr)
        return 1

    jl_sfc_cipher(raw, app_dir, len(raw) - app_dir, app_dir, chipkey)

    got = {}
    attrs = {}
    cur = app_dir
    for _ in range(16):
        e = _entry(raw, cur)
        if e["fl"] in (0x83, 0x93):
            c = cur + 32
            for _ in range(16):
                ce = _entry(raw, c)
                if not ce["name"]:
                    break
                got[ce["name"]] = (ce["eoff"], ce["esize"])
                attrs[ce["name"]] = (ce["fl"], ce["rsv"])
                if ce["idx"]:
                    break
                c += 32
        if e["idx"] or not e["esize"]:
            break
        cur += e["esize"]

    bad = []
    if "RESFS" in attrs:
        a, r = attrs["RESFS"]
        if a != RESFS_EXPECT_ATTR:
            bad.append(f"RESFS attr=0x{a:02X} != 0x{RESFS_EXPECT_ATTR:02X}")
        if r != RESFS_EXPECT_RES:
            bad.append(f"RESFS res=0x{r:02X} != 0x{RESFS_EXPECT_RES:02X} -- OPT {r & 3}. "
                       f"OPT 0 makes the loader ERASE all 0x204000 of the resource partition")
    if app_dir != EXPECT_APP_DIR_HEAD:
        bad.append(f"app_dir_head 0x{app_dir:x} != 0x{EXPECT_APP_DIR_HEAD:x} "
                   f"(is -ex_api_bin still being passed?)")
    for name, (a, l) in EXPECT.items():
        if name not in got:
            bad.append(f"{name}: MISSING from app_area_head")
        elif got[name] != (a, l):
            ga, gl = got[name]
            bad.append(f"{name}: 0x{ga:06x}/0x{gl:06x} != expected 0x{a:06x}/0x{l:06x}")

    for name in sorted(set(got) | set(EXPECT) | {"app.bin"}):
        if name in got:
            a, l = got[name]
            mark = "" if name not in EXPECT or got[name] == EXPECT[name] else "   <-- MISMATCH"
            print(f"  {name:14s} off=0x{a:08x} len=0x{l:08x}{mark}")

    if bad:
        print("\nDG01 GEOMETRY CHECK FAILED:", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        print("\nThe unit's update loader will reject or silently no-op this image.",
              file=sys.stderr)
        return 1
    print("\nDG01 geometry OK — matches the unit's factory table")
    return 0


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--allow-resfs-member"]
    ALLOW_RESFS_MEMBER = len(argv) != len(sys.argv) - 1
    if not argv:
        print(__doc__); raise SystemExit(2)
    key = int(argv[1], 16) if len(argv) > 1 else 0xB165
    raise SystemExit(main(argv[0], key))
