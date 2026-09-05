#!/usr/bin/env python3
"""Pure-Python UFW repacker for JieLi OEM firmware (BR35 / AC707N).

Preserves chipkey from isd_config.ini, recomputes all CRC layers needed to
pass the bootloader's dual_bank_update_verify integrity check.

Layered structure (outer→inner):
  - Qix wrapper (27 B, CRC LE16 over inner UFW)
  - UFW container (jl_enc_cipher key=0xFFFF on header + each entry header)
      * SYD_HEAD_V1 (64 B) → hdrcrc + listcrc
      * N × FILE_HEAD_V1 (0x50 B each) → u16Crc per entry
      * entry data blocks
  - flash.bin payload (jl_sfc_cipher key=chipkey, 32-B blocks)
      * JLFS top: flash header (0x20) + iterated entries
      * Each JLFS entry header: hcrc + data_crc

All JieLi ciphers are stream-XOR, so byte modifications in scrambled view
== same modifications in plain view. We exploit this to update CRC fields
in encrypted regions without re-encrypting full blocks.

Usage:
    repack_ufw.py roundtrip <oem.bin> <out.bin>
        Round-trip test (patches empty). Output should be byte-exact.

    repack_ufw.py patch <oem.bin> <out.bin> --file app.bin --offset 0xXX --bytes HEX
        Patch bytes in a JLFS-internal file, recompute all CRCs.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JL_MISCTOOLS = REPO_ROOT / "tools/community-re/jl-misctools/firmware"
sys.path.insert(0, str(JL_MISCTOOLS))

from jltech.cipher import jl_enc_cipher, jl_sfc_cipher  # noqa: E402
from jltech.chipkeybin import chipkeybin_decode  # noqa: E402
from jltech.crc import jl_crc16  # noqa: E402

QIX_MAGIC = b"\xbc\xaf"
QIX_HEADER_LEN = 27
UFW_HEADERKEY = 0xFFFF


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass
class UfwEntry:
    """One UFW_FILE_HEAD_V1 (0x50 B) entry."""
    index: int           # 0..N-1
    plain_header: bytes  # decrypted 0x50 B
    etype: int
    eindex: int
    edcrc: int
    eoffset: int         # offset within UFW inner (post Qix strip)
    esize: int
    name: str


@dataclass
class JlfsEntry:
    """One JLFS entry header (32 B) within flash.bin."""
    hdr_off: int         # offset of entry header within plain flash.bin
    plain_header: bytes  # 32 B
    data_crc: int
    eoff: int            # data offset (relative to base or hdr+32 for daisychained)
    esize: int
    eflags: int
    eindex: int
    name: str
    data_offset: int     # absolute offset of data within plain flash.bin
    data_size: int       # actual data size


@dataclass
class UfwLayout:
    """Parsed UFW structure with offsets, plain headers, decrypted flash.bin."""
    raw: bytes                     # full OEM (Qix + UFW)
    inner_offset: int              # = QIX_HEADER_LEN
    header_plain: bytes            # 0x40 B decrypted
    numents: int
    entries: list[UfwEntry]
    flash_entry_idx: int           # entry index of etype=0 (flash.bin)
    flash_bin_plain: bytes         # decrypted with chipkey via jl_sfc_cipher
    chipkey: int
    jlfs_top: list[JlfsEntry] = field(default_factory=list)
    jlfs_app_dir: list[JlfsEntry] = field(default_factory=list)
    app_dir_appbase: int = 0       # data_offset of the app_dir_head entry
    sfc_dec_end: int = 0           # last offset reached by SFC decryption (= app area extent)


def _decrypt_block(scrambled: bytes, key: int) -> bytearray:
    """jl_enc_cipher decrypt = same as encrypt (XOR stream)."""
    out = bytearray(scrambled)
    jl_enc_cipher(out, 0, len(out), key)
    return out


def _sfc_decrypt(scrambled: bytes, base: int, key: int) -> bytearray:
    """jl_sfc_cipher decrypt = same as encrypt."""
    out = bytearray(scrambled)
    jl_sfc_cipher(out, 0, len(out), base, key)
    return out


def parse_ufw(oem_bytes: bytes) -> UfwLayout:
    """Parse OEM UFW. Tolerates stale CRCs (does not validate)."""
    assert oem_bytes[:2] == QIX_MAGIC, "Qix magic missing"
    inner_offset = QIX_HEADER_LEN
    inner = oem_bytes[inner_offset:]

    header_plain = bytes(_decrypt_block(inner[:0x40], UFW_HEADERKEY))
    hdrcrc, listcrc, imgsize, numents, _, _ = struct.unpack_from("<HHIHHI", header_plain, 0)
    headersize = 0x40 + numents * 0x50

    entries: list[UfwEntry] = []
    flash_idx = -1
    for i in range(numents):
        eoff = 0x40 + i * 0x50
        plain_ent = bytes(_decrypt_block(inner[eoff:eoff + 0x50], UFW_HEADERKEY))
        etype, eindex, edcrc, _, e_off, esize, _, _, ename = \
            struct.unpack_from("<HHHHIII44s16s", plain_ent, 0)
        name = ename.split(b"\0")[0].decode("ascii", errors="replace")
        entries.append(UfwEntry(
            index=i, plain_header=plain_ent,
            etype=etype, eindex=eindex, edcrc=edcrc,
            eoffset=e_off, esize=esize, name=name,
        ))
        if etype == 0:
            flash_idx = i

    if flash_idx < 0:
        raise RuntimeError("No flash.bin entry (etype=0) found")

    flash_entry = entries[flash_idx]
    flash_scram = inner[flash_entry.eoffset:flash_entry.eoffset + flash_entry.esize]

    # Decrypt flash.bin first 32 B with key=0xFFFF (Boot ROM flash header)
    # then iterate JLFS; entries within app_area_head use chipkey.
    flash_plain = bytearray(flash_scram)
    # Header @ offset 0 of flash.bin: decrypted with 0xFFFF
    jl_enc_cipher(flash_plain, 0, 0x20, UFW_HEADERKEY)
    # Now iterate top JLFS entries starting at offset 0x20 (header is 32 B at offset 0)
    # The flash header is descrambled in place at 0..0x20

    # Find chipkey: iterate top entries, locate isd_config.ini
    chipkey = None
    appbase = None
    # Top entries are encrypted with 0xFFFF
    top_entries: list[JlfsEntry] = []
    cur_off = 0x20  # start of first entry header
    while True:
        # decrypt entry header (32 B) with 0xFFFF
        jl_enc_cipher(flash_plain, cur_off, 32, UFW_HEADERKEY)
        ent_buf = bytes(flash_plain[cur_off:cur_off + 32])
        hcrc, edcrc, eoff, esize, eflags, eresvd, eindex, ename = struct.unpack(
            "<HHIIBBH16s", ent_buf)
        name = ename.split(b"\0")[0].decode("ascii", errors="replace")
        je = JlfsEntry(
            hdr_off=cur_off, plain_header=ent_buf, data_crc=edcrc, eoff=eoff,
            esize=esize, eflags=eflags, eindex=eindex, name=name,
            data_offset=eoff, data_size=esize,
        )
        top_entries.append(je)

        # Capture isd_config.ini chipkey
        if name == "isd_config.ini":
            # Top JLFS region: headers encrypted with 0xFFFF, data is PLAINTEXT.
            # First 32 B = chipkey-bin, next 2 B = CRC16 (LE16).
            ck_off = eoff
            ckdata = bytes(flash_plain[ck_off:ck_off + 32])
            (ckcrc,) = struct.unpack_from("<H", flash_plain, ck_off + 32)
            if jl_crc16(ckdata) == ckcrc:
                chipkey = chipkeybin_decode(ckdata)

        # Capture app_dir_head (flags=0x81)
        if eflags == 0x81 and name == "app_dir_head":
            if appbase is None:
                appbase = eoff

        if eindex != 0:
            break
        cur_off += 32

    if chipkey is None:
        raise RuntimeError("Could not extract chipkey from isd_config.ini")
    if appbase is None:
        raise RuntimeError("No app_dir_head entry found")

    # Now decrypt the app area iteration: first iterator scans starting at appbase
    # with key=chipkey, sfc=True. We need to follow the same logic.
    layout = UfwLayout(
        raw=oem_bytes, inner_offset=inner_offset, header_plain=header_plain,
        numents=numents, entries=entries, flash_entry_idx=flash_idx,
        flash_bin_plain=bytes(flash_plain), chipkey=chipkey,
        jlfs_top=top_entries, app_dir_appbase=appbase,
    )
    # Decrypt + parse app area
    _parse_app_area(layout)
    return layout


def _parse_app_area(layout: UfwLayout) -> None:
    """Decrypt + parse app_area_head iterator (SFC cipher, chipkey).

    Mirrors fwunpack_newfw's JLFSIterator(sfc=True) decryption pattern:
      - Before reading entry header: decrypt up to align_to(entoff+32, 32)
      - After reading: skip by entry.size, then decrypt up to next aligned position
      - The head entry's full data region gets decrypted in this second step,
        which makes children readable by a non-sfc inner iterator.
    """
    flash = bytearray(layout.flash_bin_plain)
    appbase = layout.app_dir_appbase
    chipkey = layout.chipkey

    def align_up(x: int, a: int) -> int:
        return (x + a - 1) & ~(a - 1)

    cur_off = appbase   # offset within flash, relative to appbase
    dec_off = appbase   # tracks decryption boundary
    is_over = False
    is_first = True

    while not is_over:
        entoff = cur_off
        # Pre-read: decrypt up to entoff+32 aligned
        target = align_up(entoff + 32, 32)
        if dec_off < target:
            jl_sfc_cipher(flash, dec_off, target - dec_off, appbase, chipkey)
            dec_off = target

        ent_buf = bytes(flash[entoff:entoff + 32])
        hcrc, edcrc, eoff, esize, eflags, eresvd, eindex, ename = struct.unpack(
            "<HHIIBBH16s", ent_buf)
        name = ename.split(b"\0")[0].decode("ascii", errors="replace")

        # For sfc area: data_after_header=True → data_offset = hdr_off + 32
        # data_size = hdr_off + size - data_offset = size - 32
        data_offset = entoff + 32
        data_size = entoff + esize - data_offset

        layout.jlfs_app_dir.append(JlfsEntry(
            hdr_off=entoff, plain_header=ent_buf, data_crc=edcrc, eoff=eoff,
            esize=esize, eflags=eflags, eindex=eindex, name=name,
            data_offset=data_offset, data_size=data_size,
        ))

        is_over = eindex != 0

        # Post-read: skip block, decrypt to next aligned position
        cur_off += esize
        next_pos = cur_off
        if not is_over:
            next_pos = align_up(next_pos + 32, 32)
        if dec_off < next_pos:
            jl_sfc_cipher(flash, dec_off, next_pos - dec_off, appbase, chipkey)
            dec_off = next_pos

        # For the first entry (app_area_head, flags=0x83), iterate children
        # with NON-sfc inner iterator (data already decrypted by outer step above)
        if is_first:
            # inner_iter: base = entoff (the head's hdr_off), off = data_offset - hdr_off = 32
            inner_base = entoff
            inner_cur = entoff + 32
            while True:
                child_buf = bytes(flash[inner_cur:inner_cur + 32])
                c_hcrc, c_edcrc, c_eoff, c_esize, c_eflags, c_eresvd, c_eindex, c_ename = \
                    struct.unpack("<HHIIBBH16s", child_buf)
                c_name = c_ename.split(b"\0")[0].decode("ascii", errors="replace")
                # For non-sfc inner iter: data_offset = entry.offset + database
                # where database = baseaddr (passed to JLFSIterator) = entoff
                c_data_off = c_eoff + inner_base
                layout.jlfs_app_dir.append(JlfsEntry(
                    hdr_off=inner_cur, plain_header=child_buf, data_crc=c_edcrc,
                    eoff=c_eoff, esize=c_esize, eflags=c_eflags, eindex=c_eindex,
                    name=c_name,
                    data_offset=c_data_off, data_size=c_esize,
                ))
                if c_eindex != 0:
                    break
                inner_cur += 32
            is_first = False

    layout.flash_bin_plain = bytes(flash)
    layout.sfc_dec_end = dec_off


def repack(layout: UfwLayout, patches: list[tuple[str, int, bytes]]) -> bytes:
    """Apply patches to JLFS files and recompute all CRCs.

    patches: list of (jlfs_filename, offset_in_file, new_bytes)
    """
    # Mutable plain flash.bin
    flash = bytearray(layout.flash_bin_plain)

    # Apply each patch
    affected_jlfs_entries: dict[str, JlfsEntry] = {}
    for fname, off, new_bytes in patches:
        # Find entry by name in app_dir
        target = None
        for je in layout.jlfs_app_dir:
            if je.name == fname:
                target = je
                break
        if target is None:
            raise RuntimeError(f"JLFS file {fname!r} not found in app area")
        if off + len(new_bytes) > target.data_size:
            raise RuntimeError(
                f"Patch goes beyond file {fname!r} size {target.data_size}")
        flash[target.data_offset + off:target.data_offset + off + len(new_bytes)] = new_bytes
        affected_jlfs_entries[fname] = target

    # Recompute JLFS entry CRCs for each affected entry, then walk up to any
    # PARENT entry whose data range covers the patched bytes. The bootloader's
    # dual_bank_update_verify check validates parent CRCs (e.g. app_area_head)
    # too, so leaf-only updates leave the parent stale → bank rollback.
    entries_to_update: dict[int, JlfsEntry] = {}  # keyed by hdr_off (dedup)

    # Step 1: collect every JLFS entry whose data range overlaps any patch
    # AND whose original data_crc != 0xFFFF. 0xFFFF is the placeholder value
    # used for partition-like entries (PRCT, VM, BTIF, EXIF, key_mac, otp_cfg)
    # where the bootloader does not enforce a data CRC — overwriting with a
    # real CRC would itself break the firmware.
    for fname, off, new_bytes in patches:
        leaf = affected_jlfs_entries[fname]
        patch_lo = leaf.data_offset + off
        patch_hi = patch_lo + len(new_bytes)
        for je in layout.jlfs_app_dir:
            if je.data_crc == 0xFFFF:
                continue  # placeholder — preserve as 0xFFFF
            # The entry's data range is [data_offset, data_offset+data_size).
            # data_size for the parent (e.g. app_area_head, flags=0x83) covers
            # all child headers+payloads, so any inner patch falls inside it.
            je_lo = je.data_offset
            je_hi = je.data_offset + je.data_size
            if patch_lo < je_hi and patch_hi > je_lo:
                entries_to_update[je.hdr_off] = je

    # Step 2: order updates inside-out (largest data_offset first, so a
    # child's freshly-written CRC bytes are included in the parent's hash)
    for je in sorted(entries_to_update.values(), key=lambda e: -e.data_offset):
        data = bytes(flash[je.data_offset:je.data_offset + je.data_size])
        new_data_crc = jl_crc16(data)
        # Write new data_crc at hdr_off + 2 (LE16)
        struct.pack_into("<H", flash, je.hdr_off + 2, new_data_crc)
        # Recompute hcrc over bytes [hdr_off+2 .. hdr_off+32]
        new_hcrc = jl_crc16(bytes(flash[je.hdr_off + 2:je.hdr_off + 32]))
        struct.pack_into("<H", flash, je.hdr_off, new_hcrc)
        tag = "" if je.name in affected_jlfs_entries else "  (parent)"
        print(f"  JLFS {je.name}{tag}: data_crc {je.data_crc:#06x} → {new_data_crc:#06x}")

    # Now re-encrypt flash.bin:
    # The flash header (first 32 B) was decrypted with 0xFFFF — re-encrypt with 0xFFFF
    flash_reenc = bytearray(flash)
    jl_enc_cipher(flash_reenc, 0, 0x20, UFW_HEADERKEY)
    # Top JLFS entries were decrypted in place (each 32 B with 0xFFFF) — re-encrypt
    for je in layout.jlfs_top:
        jl_enc_cipher(flash_reenc, je.hdr_off, 32, UFW_HEADERKEY)
    # App area was decrypted with sfc(chipkey) — re-encrypt the SAME range
    # that was decrypted (tracked as sfc_dec_end during parsing).
    appbase = layout.app_dir_appbase
    sfc_range_size = layout.sfc_dec_end - appbase
    jl_sfc_cipher(flash_reenc, appbase, sfc_range_size, appbase, layout.chipkey)

    # Now flash_reenc should be byte-equivalent to original scrambled flash.bin in
    # unmodified regions. Verify by comparing to layout.raw's flash.bin slice.
    flash_orig = layout.raw[layout.inner_offset + layout.entries[layout.flash_entry_idx].eoffset:
                            layout.inner_offset + layout.entries[layout.flash_entry_idx].eoffset
                            + layout.entries[layout.flash_entry_idx].esize]

    if not patches:
        # Pure round-trip: must match exactly
        if bytes(flash_reenc) != flash_orig:
            # Find first diff
            for i, (a, b) in enumerate(zip(flash_reenc, flash_orig)):
                if a != b:
                    print(f"  flash.bin diff @ {i:#x}: repacked={a:#04x} orig={b:#04x}")
                    if i > 64:
                        break
            print(f"  flash.bin lengths: repacked={len(flash_reenc)} orig={len(flash_orig)}")
            raise RuntimeError("Round-trip flash.bin mismatch — encrypt path wrong")

    # Recompute UFW entry data_crc for flash.bin.
    # Per fwunpack_newfw load_ufw_now: jl_crc16(fw) where fw is the raw bytes
    # read from the file (= the SCRAMBLED flash.bin, no decryption). So we
    # compute CRC over the re-encrypted flash_reenc, not the plain flash.
    flash_entry = layout.entries[layout.flash_entry_idx]
    new_flash_dcrc = jl_crc16(bytes(flash_reenc))
    print(f"  UFW entry flash.bin data_crc: {flash_entry.edcrc:#06x} → {new_flash_dcrc:#06x}")

    # Rebuild UFW: header + entries + entry data blobs
    # Plain header has bytes: hdrcrc(2) listcrc(2) imgsize(4) numents(2) wa3(2) wa4(4) chipname(48)
    # We need to update listcrc + hdrcrc after building entries.
    new_entries_plain = bytearray()
    for ent in layout.entries:
        ph = bytearray(ent.plain_header)
        if ent.index == layout.flash_entry_idx:
            struct.pack_into("<H", ph, 4, new_flash_dcrc)  # u16Crc at offset 4
        # Other entries' data_crc: if we touched any of their data, recompute too
        # (for now, no patches modify non-flash.bin entries)
        new_entries_plain.extend(ph)

    # Encrypt each entry first (key=0xFFFF, fresh stream per entry), because
    # listcrc is computed over the SCRAMBLED entry bytes (per fwunpack: the
    # listcrc check runs BEFORE the per-entry decrypt loop).
    enc_entries = bytearray()
    for i in range(layout.numents):
        block = bytearray(new_entries_plain[i * 0x50:(i + 1) * 0x50])
        jl_enc_cipher(block, 0, 0x50, UFW_HEADERKEY)
        enc_entries.extend(block)

    new_listcrc = jl_crc16(bytes(enc_entries))
    new_header = bytearray(layout.header_plain)
    struct.pack_into("<H", new_header, 2, new_listcrc)
    # hdrcrc IS over plain bytes 2..64 (parser decrypts header first, then checks)
    new_hdrcrc = jl_crc16(bytes(new_header[2:64]))
    struct.pack_into("<H", new_header, 0, new_hdrcrc)
    print(f"  UFW listcrc: {struct.unpack_from('<H', layout.header_plain, 2)[0]:#06x} → {new_listcrc:#06x}")
    print(f"  UFW hdrcrc:  {struct.unpack_from('<H', layout.header_plain, 0)[0]:#06x} → {new_hdrcrc:#06x}")

    # Encrypt UFW header (key=0xFFFF, fresh stream)
    enc_header = bytearray(new_header)
    jl_enc_cipher(enc_header, 0, 0x40, UFW_HEADERKEY)

    # Assemble final inner UFW: header + entries + entry data blobs
    # Entry data blobs come at their eoffset positions.
    # We need to keep ALL bytes from inner_orig at positions >= headersize, except flash.bin region.
    inner_orig = layout.raw[layout.inner_offset:]
    headersize = 0x40 + layout.numents * 0x50
    final_inner = bytearray(inner_orig)
    final_inner[:0x40] = enc_header
    final_inner[0x40:headersize] = enc_entries
    # Replace flash.bin region with our re-encrypted version
    final_inner[flash_entry.eoffset:flash_entry.eoffset + flash_entry.esize] = flash_reenc

    # Build final OEM: Qix wrapper + inner
    qix = bytearray(layout.raw[:QIX_HEADER_LEN])
    out = bytes(qix) + bytes(final_inner)

    # Recompute Qix wrapper CRC over inner
    qix_crc = crc16_ccitt_false(bytes(final_inner))
    out_ba = bytearray(out)
    struct.pack_into("<H", out_ba, 25, qix_crc)
    print(f"  Qix wrapper CRC: → {qix_crc:#06x}")

    return bytes(out_ba)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("roundtrip", help="Test unpack→repack with no patches")
    sp.add_argument("oem")
    sp.add_argument("out")

    sp = sub.add_parser("patch", help="Patch bytes + recompute all CRCs")
    sp.add_argument("oem")
    sp.add_argument("out")
    sp.add_argument("--file", action="append", default=[],
                    help="JLFS filename (repeatable, paired con --offset y --bytes)")
    sp.add_argument("--offset", action="append", default=[],
                    help="Offset within file (hex or dec, repeatable)")
    sp.add_argument("--bytes", action="append", default=[],
                    help="Hex bytes to write (repeatable)")

    sp = sub.add_parser("info", help="Dump layout info")
    sp.add_argument("oem")

    args = ap.parse_args()

    oem = Path(args.oem).read_bytes()
    layout = parse_ufw(oem)

    print(f"=== UFW layout ===")
    print(f"  Inner size: {len(oem) - QIX_HEADER_LEN} ({len(oem)} total)")
    print(f"  numents: {layout.numents}, chipkey: {layout.chipkey:#06x}")
    print(f"  UFW entries:")
    for ent in layout.entries:
        print(f"    [{ent.index}] type={ent.etype:3d} dcrc={ent.edcrc:#06x} "
              f"off={ent.eoffset:7d} sz={ent.esize:7d} name={ent.name!r}")
    print(f"  JLFS top entries:")
    for je in layout.jlfs_top:
        print(f"    @{je.hdr_off:#06x} dcrc={je.data_crc:#06x} flags={je.eflags:#04x} "
              f"off={je.eoff:#06x} sz={je.esize:6d} name={je.name!r}")
    print(f"  JLFS app_dir entries:")
    for je in layout.jlfs_app_dir:
        print(f"    @{je.hdr_off:#06x} dcrc={je.data_crc:#06x} flags={je.eflags:#04x} "
              f"data_off={je.data_offset:#08x} sz={je.data_size:6d} name={je.name!r}")

    if args.cmd == "info":
        return

    patches = []
    if args.cmd == "patch":
        if not (len(args.file) == len(args.offset) == len(args.bytes)):
            print(f"ERROR: --file, --offset, --bytes deben repetirse en paralelo "
                  f"(got {len(args.file)}/{len(args.offset)}/{len(args.bytes)})",
                  file=sys.stderr)
            sys.exit(2)
        if not args.file:
            print("ERROR: al menos un --file/--offset/--bytes requerido", file=sys.stderr)
            sys.exit(2)
        for fname, off_s, bs_hex in zip(args.file, args.offset, args.bytes):
            off = int(off_s, 0)
            bs = bytes.fromhex(bs_hex)
            patches.append((fname, off, bs))
            print(f"Patch: {fname}@{off:#x} = {bs.hex()} ({len(bs)} B)")

    print(f"\n=== Repack ===")
    out = repack(layout, patches)

    print(f"\n=== Output ===")
    Path(args.out).write_bytes(out)
    print(f"  Wrote {args.out} ({len(out)} B)")
    print(f"  SHA256 input:  {hashlib.sha256(oem).hexdigest()}")
    print(f"  SHA256 output: {hashlib.sha256(out).hexdigest()}")
    if args.cmd == "roundtrip":
        same = oem == out
        print(f"  Round-trip byte-exact: {'OK ✅' if same else 'FAIL ❌'}")
        if not same:
            for i, (a, b) in enumerate(zip(oem, out)):
                if a != b:
                    print(f"    First diff @ {i:#x}: in={a:#04x} out={b:#04x}")
                    break
            sys.exit(1)


if __name__ == "__main__":
    main()
