#!/usr/bin/env python3
"""Splice the OEM's EXT_RESERVED/RESFS directory into a built DG01 .ufw.

WHY THIS EXISTS
---------------
The DG01's factory table carries RESFS (0x1F7000, 0x204000) as the single child of
a separate `EXT_RESERVED` directory that sits in the app-area chain right after
`app_area_head`:

    @0x001000 app_area_head  fl=0x83 esize=0x000ef444
                  app.bin / VM / PRCT / MODE / EXIF / BTIF
    @0x0f0444 EXT_RESERVED   fl=0x93 esize=0x00000040
                  RESFS      off=0x001f7000 len=0x00204000

isd_download cannot generate that. It enforces a single globally ascending address
ordering and always merges [RESERVED_EXPAND_CONFIG] AFTER [RESERVED_CONFIG], so
RESFS(0x1F7000) following BTIF(0x3FE000) is rejected outright -- measured:

    ERROR: The reserved(RESFS) config address is less then the prev reserved(BTIF)
    config address, please re-config.

Declaring RESFS in [RESERVED_CONFIG] instead is NOT equivalent: it would become a
7th child of app_area_head, i.e. a reserved zone the update loader walks, which the
OEM's is not. So the build leaves RESFS undeclared and this script splices the OEM's
directory back in afterwards.

HOW
---
The two 32-byte entries are copied VERBATIM out of the OEM dump after SFC-descramble.
Their hcrc is jl_crc16 over bytes 2..32 and the dir's dcrc covers its own child, so
both are position-independent -- nothing inside the block needs recomputing. Only the
container around it does:

  * flash.bin grows by 0x40; everything after the insertion point shifts up by 0x40
    (safe: those entries are reached by walking `cur += esize`, never by absolute
    offset, and each carries a self-relative eoff=0x20)
  * the UFW flash.bin entry's esize/esize2/edcrc
  * every later UFW member's eoffset, and the container imgsize
  * the entry-list CRC (over the SCRAMBLED bytes) and the container header CRC

    fix_area_table.py <in.ufw> [-o out.ufw] [--oem firmware/oem_DG01.bin]
                      [--chipkey 0xB165]
"""
import argparse, struct, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "ufw-repack"))
from jltech.cipher import jl_enc_cipher, jl_sfc_cipher  # noqa: E402
from jltech.crc import jl_crc16  # noqa: E402

UFW_KEY = 0xFFFF
ENT = "<HHIIBBH12s4s"


def _unpack(b32):
    hcrc, dcrc, eoff, esize, fl, rsv, idx, nm, tag = struct.unpack_from(ENT, b32, 0)
    return dict(hcrc=hcrc, dcrc=dcrc, eoff=eoff, esize=esize, fl=fl, rsv=rsv, idx=idx,
                name=nm.split(b"\0")[0].decode("ascii", "replace"))


def _top_entry(buf, off):
    b = bytearray(buf[off:off + 32])
    jl_enc_cipher(b, 0, 32, UFW_KEY)
    return _unpack(bytes(b))


def _app_dir_head(flash: bytes) -> int:
    off = 0x20
    for _ in range(16):
        e = _top_entry(flash, off)
        if not e["name"]:
            break
        if e["name"] == "app_dir_head":
            return e["eoff"]
        if e["idx"]:
            break
        off += 32
    raise RuntimeError("no app_dir_head in the top-level directory")


def _chain(plain_flash: bytes, app_dir: int):
    """Walk the app-area chain of an ALREADY-descrambled flash image."""
    out, cur = [], app_dir
    for _ in range(32):
        e = _unpack(bytes(plain_flash[cur:cur + 32]))
        if not e["name"] or not e["esize"] or e["esize"] == 0xFFFFFFFF:
            break
        e["off"] = cur
        out.append(e)
        if e["idx"]:
            break
        cur += e["esize"]
    return out


def _descrambled(path: Path, chipkey: int):
    raw = bytearray(path.read_bytes())
    app_dir = _app_dir_head(bytes(raw))
    jl_sfc_cipher(raw, app_dir, len(raw) - app_dir, app_dir, chipkey)
    return raw, app_dir


def _ufw_members(ufw: bytearray):
    hdr = bytearray(ufw[:0x40])
    jl_enc_cipher(hdr, 0, 0x40, UFW_KEY)
    hdrcrc, listcrc, imgsize, numents, _w3, _w4, _chip = struct.unpack_from("<HHIHHI48s", hdr, 0)
    if jl_crc16(bytes(hdr[2:])) != hdrcrc:
        raise RuntimeError("UFW container header CRC mismatch -- not a JieLi UFW?")
    headersize = 0x40 + numents * 0x50
    if jl_crc16(bytes(ufw[0x40:headersize])) != listcrc:
        raise RuntimeError("UFW container entry-list CRC mismatch")
    full = bytearray(ufw[:headersize])
    jl_enc_cipher(full, 0, 0x40, UFW_KEY)
    for off in range(0x40, headersize, 0x50):
        jl_enc_cipher(full, off, 0x50, UFW_KEY)
    return full, numents, headersize


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ufw", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--oem", type=Path, default=ROOT / "firmware" / "oem_DG01.bin")
    ap.add_argument("--chipkey", default="0xB165")
    a = ap.parse_args()
    key = int(a.chipkey, 16) if isinstance(a.chipkey, str) else a.chipkey
    out_path = a.output or a.ufw

    # ---- 1. lift the EXT_RESERVED block out of the OEM dump, in the clear ----
    oem, oem_app_dir = _descrambled(a.oem, key)
    src = next((e for e in _chain(oem, oem_app_dir) if e["name"] == "EXT_RESERVED"), None)
    if src is None:
        print(f"FAIL: no EXT_RESERVED directory in {a.oem}", file=sys.stderr)
        return 1
    block = bytes(oem[src["off"]:src["off"] + src["esize"]])
    child = _unpack(block[0x20:0x40])
    if jl_crc16(block[2:32]) != src["hcrc"] or jl_crc16(block[0x22:0x40]) != child["hcrc"]:
        print("FAIL: OEM EXT_RESERVED entry CRCs do not verify", file=sys.stderr)
        return 1
    print(f"  OEM EXT_RESERVED  @0x{src['off']:06x} esize=0x{src['esize']:x}  "
          f"child {child['name']} off=0x{child['eoff']:06x} len=0x{child['esize']:06x}"
          f"  (both hcrc verified)")

    # ---- 2. locate flash.bin inside the container ----
    ufw = bytearray(a.ufw.read_bytes())
    hdr, numents, headersize = _ufw_members(ufw)
    fb = None
    for i in range(numents):
        off = 0x40 + i * 0x50
        etype, eidx, edcrc, w1, eoffset, esize, esize2, w2, nm = struct.unpack_from(
            "<HHHHIII44s16s", hdr, off)
        if etype == 0:
            fb = dict(entry_off=off, off=eoffset, size=esize, size2=esize2)
            break
    if fb is None:
        print("FAIL: no flash.bin (etype=0) member in the container", file=sys.stderr)
        return 1

    flash = bytearray(ufw[fb["off"]:fb["off"] + fb["size"]])
    raw_flash = bytes(flash)          # pre-descramble copy: padding is 0xFF only here
    app_dir = _app_dir_head(bytes(flash))
    jl_sfc_cipher(flash, app_dir, len(flash) - app_dir, app_dir, key)

    chain = _chain(flash, app_dir)
    if any(e["name"] == "EXT_RESERVED" for e in chain):
        print("  already has EXT_RESERVED -- nothing to do")
        return 0
    head = next((e for e in chain if e["name"].startswith("app_area_hea")), None)
    if head is None:
        print("FAIL: no app_area_head in the app-area chain", file=sys.stderr)
        return 1
    if head["idx"]:
        print("FAIL: app_area_head is the terminal entry; nothing may follow it",
              file=sys.stderr)
        return 1

    # ---- 3. splice, in the clear, immediately after app_area_head's data ----
    # The insertion must NOT change flash.bin's length. It is exactly CODE_LEN, and the
    # LAST 32 bytes are a trailer the boot ROM reads -- measured in the OEM dump at
    # 0x0F6FE0: "0700..03011dff4d001408a1e37e052f7b5800", with raw 0xFF padding below it.
    # So this shifts only the chain region up by one block and pays for it out of that
    # padding, leaving both the file length and the trailer untouched.
    #
    # "Is it padding?" can only be answered on the RAW bytes: the padding is written as
    # unscrambled 0xFF, so after SFC descrambling it reads as noise like everything else.
    ins = head["off"] + head["esize"]
    tail_end = max((e["off"] + e["esize"]) for e in chain)
    n = len(block)
    # What makes the shift safe is STRUCTURAL, not a byte pattern: `tail_end` is the end of
    # the last entry in the chain, so nothing between it and the end of the image is
    # reachable by any reader that walks the chain. (A byte test does not work here --
    # the OEM leaves raw 0xFF, but inject_chipkey re-scrambles the whole app area including
    # that padding, so by this point it is indistinguishable from data.)
    #
    # The one thing that IS read without walking the chain is the trailer at the very end
    # of the CODE region -- visible in the OEM dump at 0x0F6FE0. Keep a wide berth.
    TRAILER_GUARD = 0x100
    slack = len(flash) - TRAILER_GUARD - tail_end
    if slack < n:
        print(f"FAIL: need 0x{n:x} bytes between the end of the chain (0x{tail_end:06x}) and "
              f"the trailer guard at 0x{len(flash) - TRAILER_GUARD:06x}; only {slack} B free. "
              f"The app has grown into the space this directory needs.", file=sys.stderr)
        return 1
    print(f"  inserting 0x{n:x} bytes at flash 0x{ins:06x} "
          f"(after app_area_head, before {chain[chain.index(head)+1]['name']}), "
          f"shifting 0x{tail_end - ins:x} bytes up into the {slack} B of unreferenced "
          f"space after the chain end at 0x{tail_end:06x}")
    flash[ins + n:tail_end + n] = flash[ins:tail_end]   # bounded move, length preserved
    flash[ins:ins + n] = block

    # ---- 4. re-scramble and verify the new chain before touching the container ----
    jl_sfc_cipher(flash, app_dir, len(flash) - app_dir, app_dir, key)
    check = bytearray(flash)
    jl_sfc_cipher(check, app_dir, len(check) - app_dir, app_dir, key)
    names = []
    for e in _chain(check, app_dir):
        blob = bytes(check[e["off"]:e["off"] + 32])
        if jl_crc16(blob[2:32]) != e["hcrc"]:
            print(f"FAIL: hcrc broken on {e['name']} after splice", file=sys.stderr)
            return 1
        names.append(e["name"])
    if "EXT_RESERVED" not in names or names[-1] != "config.dat":
        print(f"FAIL: chain did not survive the splice: {names}", file=sys.stderr)
        return 1
    print(f"  app-area chain now: {' -> '.join(names)}  (all hcrc verified)")

    # ---- 5. rebuild the container around the larger flash.bin ----
    assert len(flash) == fb["size"], "flash.bin length must not change"
    ufw[fb["off"]:fb["off"] + fb["size"]] = flash
    struct.pack_into("<H", hdr, fb["entry_off"] + 4, jl_crc16(bytes(flash)))
    # Nothing else in the container moves: same length, same offsets, same imgsize.

    ents = bytearray(hdr[0x40:headersize])
    for off in range(0, len(ents), 0x50):
        jl_enc_cipher(ents, off, 0x50, UFW_KEY)
    struct.pack_into("<H", hdr, 0x02, jl_crc16(bytes(ents)))
    struct.pack_into("<H", hdr, 0x00, jl_crc16(bytes(hdr[2:0x40])))
    top = bytearray(hdr[:0x40])
    jl_enc_cipher(top, 0, 0x40, UFW_KEY)
    ufw[:0x40] = top
    ufw[0x40:headersize] = ents

    out_path.write_bytes(bytes(ufw))
    print(f"  wrote {out_path} ({len(ufw)} bytes; flash.bin still 0x{len(flash):X})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
