#!/usr/bin/env python3
"""Add a FILE_TYPE_FW_RESERVED_ZONE_FILE (0x32) member to a JieLi .ufw.

WHY
---
The RCSP update loader does not only rewrite CODE. `erase_reserve_area_by_erase_flag()`
(loader/.../liba/br35/update.a, fs_v2_update.o) walks every reserved area in the image's
area table -- including the children of EXT_RESERVED -- and for each one:

    if (local_reserved_area_match_ufw_reserved_file(p->name, &remote, &len, &crc)) {
        flash_erase_addr_n_len_without_align(p->u32Address, p->u32Length, ...);
        ufw_data_download_handle(remote, p->u32Address, len, key, remote);
    }

So a .ufw member of type 0x32 whose NAME matches a reserved area makes the loader erase
that area and download the member into it. That is the supported mechanism for restoring
a reserved region over the air -- it is how VM and MODE are meant to be shipped, and it
works the same for RESFS.

`FILE_TYPE_FW_RESERVED_ZONE_FILE` = 0x32, from
apps/common/update/user_file_download/reserve_file_download.c:35.

LAYOUT NOTE
-----------
The container is [0x40 header][N x 0x50 entries][data]. In the images this project builds
the data starts at 0x400 while the header area ends at 0x40 + N*0x50 = 0x360, so there is
room for several more entries without moving any existing member. This script therefore
appends the new member's DATA at the end of the file and leaves every existing offset
untouched; if the entry list ever grew past the first data offset it would refuse rather
than silently relocate things.

    add_reserved_member.py <in.ufw> <name> <payload.bin> -o <out.ufw>
"""
import argparse, struct, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "ufw-repack"))
from jltech.cipher import jl_enc_cipher, jl_sfc_cipher  # noqa: E402
from jltech.crc import jl_crc16  # noqa: E402

UFW_KEY = 0xFFFF
ENT = "<HHHHIII44s16s"
FILE_TYPE_FW_RESERVED_ZONE_FILE = 0x32


def _x(b):
    o = bytearray(b); jl_enc_cipher(o, 0, len(o), UFW_KEY); return bytes(o)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ufw", type=Path)
    ap.add_argument("name", help="must match the on-flash reserved-area name exactly "
                                 "(strcmp, case-sensitive, <=11 chars), e.g. RESFS")
    ap.add_argument("payload", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--chipkey", default="0xB165",
                    help="the unit's chipkey; the payload is scrambled with it (see below)")
    a = ap.parse_args()

    raw = bytearray(a.ufw.read_bytes())
    payload = a.payload.read_bytes()

    hdr = bytearray(_x(raw[:0x40]))
    hdrcrc, listcrc, imgsize, numents, w3, w4, chip = struct.unpack_from("<HHIHHI48s", hdr, 0)
    if jl_crc16(bytes(hdr[2:])) != hdrcrc:
        print("FAIL: container header CRC mismatch -- not a JieLi .ufw?", file=sys.stderr)
        return 1
    headersize = 0x40 + numents * 0x50
    if jl_crc16(bytes(raw[0x40:headersize])) != listcrc:
        print("FAIL: entry-list CRC mismatch", file=sys.stderr)
        return 1

    ents = [bytearray(_x(raw[0x40 + i * 0x50:0x40 + (i + 1) * 0x50])) for i in range(numents)]
    parsed = [struct.unpack_from(ENT, bytes(e), 0) for e in ents]
    names = [p[8].split(b"\0")[0].decode("ascii", "replace") for p in parsed]
    if a.name in names:
        print(f"FAIL: a member named {a.name} already exists", file=sys.stderr)
        return 1

    first_data = min(p[4] for p in parsed if p[5] or p[4])
    if 0x40 + (numents + 1) * 0x50 > first_data:
        print(f"FAIL: no room for another entry: header would reach "
              f"0x{0x40 + (numents + 1) * 0x50:x}, first data at 0x{first_data:x}",
              file=sys.stderr)
        return 1

    # Layout. Every SDK-produced container keeps two invariants that the first version of
    # this script broke: member offsets are strictly ASCENDING, and `tail.bin` (type 0xFF)
    # is both last in the entry list AND physically last in the file. Appending after
    # tail.bin satisfied neither, and the device then never requested the new member at all
    # (measured: its highest requested offset stayed at the old EOF). So the new payload
    # goes where tail.bin was, and tail.bin moves to the new end.
    tail_i = next((i for i, q in enumerate(parsed) if q[0] == 0xFF), None)
    if tail_i is None:
        print("FAIL: no tail.bin (type 0xFF) member to anchor the layout on", file=sys.stderr)
        return 1
    tail_off, tail_sz = parsed[tail_i][4], parsed[tail_i][5]
    tail_data = bytes(raw[tail_off:tail_off + tail_sz])

    body_end = max(q[4] + q[5] for i, q in enumerate(parsed) if i != tail_i)
    new_off = (body_end + 511) & ~511   # 512-aligned: keeps the outer read chunking
                                        # and the 32-byte cipher schedule in phase
    # No "must fit before tail.bin" check: tail.bin is relocated to the new EOF below, so
    # the payload may start at or past its old position. Anything left between the end of
    # the last real member and the payload is unreferenced padding.
    new_tail_off = (new_off + len(payload) + 31) & ~31

    def aligned(n):
        return (n + 31) & ~31

    used_idx = {q[1] for q in parsed}
    new_idx = next(i for i in range(1, 256) if i not in used_idx)
    # The 0x50 head is stFW_FILE_HEAD_V1. The loader does NOT match on the trailing
    # name[16] -- that is the container file's own name. It matches on
    # szReserveZoneName[12] at BYTE 36, inside the region this struct format lumps into a
    # 44-byte blob. Recovered from the loader IR (reserved_zone_update.o,
    # ufw_op_find_reserved_area_update_file_return_cnt -> memcpy(tab.name,
    # &head->szReserveZoneName, 12); local_reserved_area_match_ufw_reserved_file ->
    # strcmp(area_name, tab[i].name)). strcmp, so NUL-terminated and case-sensitive,
    # 11 chars + NUL at most.
    #
    # An earlier version of this script wrote the name only into name[16]; the loader then
    # compared against an empty string, never matched, and silently skipped the member --
    # which is exactly what the hardware did.
    # The struct format packs bytes 20..63 into the 44-byte blob (HHHH = 0..7,
    # III = 8..19), so entry byte 36 is blob index 16.
    BLOB_BASE = 20
    NAME_AT = 36
    blob = bytearray(44)
    blob[NAME_AT - BLOB_BASE:NAME_AT - BLOB_BASE + 12] = \
        a.name.encode("ascii").ljust(12, b"\0")[:12]
    new_ent = bytearray(struct.pack(
        ENT, FILE_TYPE_FW_RESERVED_ZONE_FILE, new_idx, jl_crc16(payload), 0,
        new_off, len(payload), aligned(len(payload)), bytes(blob),
        (a.name + ".bin").encode("ascii").ljust(16, b"\0")[:16]))

    # entry list: everything but tail, then the new member, then tail -- ascending offsets
    ents = [e for i, e in enumerate(ents) if i != tail_i]
    ents.append(new_ent)
    tail_ent = bytearray(struct.pack(ENT, *parsed[tail_i][:4], new_tail_off,
                                     tail_sz, aligned(tail_sz), parsed[tail_i][7],
                                     parsed[tail_i][8]))
    ents.append(tail_ent)

    print(f"  + {a.name}: type=0x{FILE_TYPE_FW_RESERVED_ZONE_FILE:02X} idx={new_idx} "
          f"off=0x{new_off:x} size={len(payload):,} dcrc=0x{jl_crc16(payload):04x}")
    print(f"    tail.bin moved 0x{tail_off:x} -> 0x{new_tail_off:x}; offsets stay ascending")

    new_n = len(ents)
    new_headersize = 0x40 + new_n * 0x50
    if new_headersize > first_data:
        print(f"FAIL: entry list would reach 0x{new_headersize:x}, first data at "
              f"0x{first_data:x}", file=sys.stderr)
        return 1

    out = bytearray(raw[:body_end])
    out += b"\xff" * (new_off - body_end)      # unreferenced padding, incl. tail.bin's old slot
    out += payload
    out += b"\xff" * (new_tail_off - len(out))
    out += tail_data

    # The payload must be SCRAMBLED in the container. The loader's reserved-area path runs
    #     ufw_data_download_handle(s_addr, t_addr, len, key=get_chip_key(), dec_addr=s_addr)
    # and inside it decode_data_by_user_key() is called UNCONDITIONALLY before
    # norflash_write -- there is no pass-through branch. Its schedule is, per 32-byte block,
    #     seed = key ^ (dec_addr >> 2)
    # with dec_addr advancing with the data and STARTING AT THE CONTAINER OFFSET, not the
    # flash address. That is exactly jl_sfc_cipher(buf, off=A, base=0, key=chipkey).
    #
    # This is the opposite of how flash.bin is handled: type-0 bulk data never goes through
    # ufw_data_download_handle at all, it is written verbatim and descrambled by hardware on
    # read. Type 50 is descrambled in software on the way in -- which is why RESFS ends up
    # as plaintext on flash, and why what we store here has to be the scrambled form.
    key = int(a.chipkey, 16)
    jl_sfc_cipher(out, new_off, len(payload), 0, key)

    # Round-trip proof. The cipher is an involution, so applying it again reproduces exactly
    # what the loader will write to flash. Assert that equals the plaintext we were given --
    # this simulates the 2 MB write with no hardware risk.
    check = bytearray(out)          # whole container: `off` is both index and cipher anchor
    jl_sfc_cipher(check, new_off, len(payload), 0, key)
    check = bytes(check[new_off:new_off + len(payload)])
    if check != payload:
        print("FAIL: round-trip mismatch -- the loader would write garbage", file=sys.stderr)
        return 1
    if jl_crc16(check) != jl_crc16(payload):
        print("FAIL: round-trip CRC mismatch", file=sys.stderr)
        return 1
    print(f"    payload scrambled with chipkey 0x{key:04X} at container offset 0x{new_off:x}; "
          f"round-trip verified against the plaintext")

    scr = bytearray()
    for e in ents:
        t = bytearray(e); jl_enc_cipher(t, 0, 0x50, UFW_KEY); scr += t
    struct.pack_into("<H", hdr, 8, new_n)
    struct.pack_into("<I", hdr, 4, len(out))
    struct.pack_into("<H", hdr, 2, jl_crc16(bytes(scr)))
    struct.pack_into("<H", hdr, 0, jl_crc16(bytes(hdr[2:0x40])))
    top = bytearray(hdr[:0x40]); jl_enc_cipher(top, 0, 0x40, UFW_KEY)
    out[:0x40] = top
    out[0x40:0x40 + len(scr)] = scr

    a.output.write_bytes(bytes(out))
    print(f"  wrote {a.output} ({len(out):,} bytes, was {len(raw):,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
