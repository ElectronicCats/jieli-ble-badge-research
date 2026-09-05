#!/usr/bin/env python3
"""
inject_chipkey.py — Patch a JieLi UFW in-place to swap the chipkey embedded
in the nested JLFS `top/isd_config.ini` entry.

Background
----------
The official `isd_download.exe -key <file>` flag expects a PEM-style
`-----BEGIN CHIP KEY-----` chip-key file (L3KEY/CKEY format) issued by
JieLi tooling and bound to a specific chip. Raw 32-byte chipkey-bin,
ASCII "0x9847", LE16 etc. are all rejected. Without that PEM file we
can't get the SDK build to embed the production chipkey for PID 1558
(0x9847), so every self-built UFW ends up with the SDK default 0xFFFF
and is rejected by the production badge in `parse_fw_info`.

This script bypasses `isd_download.exe` entirely: after the self-built
UFW is produced, it locates the 34-byte chipkey blob inside the nested
JLFS `top/isd_config.ini` entry (chipkey-bin 32B + jl_crc16 LE16) and
replaces it with either:
  - the OEM blob taken verbatim from a known-good production UFW
    (deterministic, default mode), or
  - a freshly encoded blob for an arbitrary chipkey using a seeded
    RNG (deterministic too).

The TLV body (bytes 34..end of isd_config.ini) is left untouched so
the self-built flash layout is preserved. We then recompute:
  - the JLFS top-dir entry's data_crc (edcrc) over the new 146B
  - the JLFS top-dir entry's header CRC (hcrc), since edcrc lives in
    the header that is XOR-scrambled with key=0xFFFF
  - the outer UFW container's flash.bin entry data_crc (edcrc) and
    the entry-list CRC (listcrc), since flash.bin contents changed

Outer UFW container header CRC (hdrcrc @0x00) does NOT depend on
flash.bin contents, only on the header itself, so we leave it alone.

Layout cheat sheet (verified against
  firmware-builds/e_badge_707_sdk_200-ufw-*/update.ufw):

  UFW container
   [0x00..0x40)            header (jl_enc_cipher key=0xFFFF)
     <HHIIHH48s>
       hdrcrc, listcrc, imgsize, numents, wa3, wa4, chipname
   [0x40..0x40+0x50*N)     N entries (each 0x50 B, jl_enc_cipher 0xFFFF)
     <HHHHIII44s16s>
       etype, eindex, edcrc, ewa1, eoffset, esize, esize2, ewa2, ename
     etype=0 → flash.bin (contains the nested JLFS we want)

  Inside flash.bin
   [0..32)                  flash header (jl_enc_cipher 0xFFFF)
                            <H30s> hcrc, payload
   [32..)                   sequential JLFS entries (32 B each, hdr
                            jl_enc_cipher 0xFFFF, data NOT scrambled
                            in the top dir)
     <HIIBBH16s>
       edcrc, eoff, esize, eflags, eresvd, eindex, ename
     ename == 'isd_config.ini' → 146 B blob

  isd_config.ini blob (146 B, plain text-ish on disk, NOT scrambled)
   [0..32)                  chipkey-bin (32 B)
   [32..34)                 chipkey-bin CRC16 (jl_crc16 LE16)
   [34..end)                TLV body (SYS_CFG_PARAM + others)
"""

import argparse
import hashlib
import struct
import sys
from pathlib import Path

# Make jl-misctools importable
_REPO_ROOT = Path(__file__).resolve().parents[1]
_JLMISC = _REPO_ROOT / "tools" / "community-re" / "jl-misctools" / "firmware"
if str(_JLMISC) not in sys.path:
    sys.path.insert(0, str(_JLMISC))

from jltech.cipher import jl_enc_cipher, jl_sfc_cipher  # noqa: E402
from jltech.chipkeybin import chipkeybin_decode, chipkeybin_encode  # noqa: E402
from jltech.crc import jl_crc16  # noqa: E402

UFW_KEY = 0xFFFF
ISD_HEADER_BLOB_SIZE = 34  # chipkey-bin (32) + CRC16 (2)


def _find_ufw_flash_bin_entry(ufw: bytearray):
    """Locate the UFW container's flash.bin entry (etype=0).

    Returns dict with descrambled header bytes, header_off (offset of
    the 0x40-byte container header), entry_idx, eoffset, esize, edcrc,
    numents, listcrc, headersize, plus a copy of the descrambled
    full header (0x40 + N*0x50 bytes) for later CRC recomputation.
    """
    header = bytearray(ufw[:0x40])
    jl_enc_cipher(header, 0, 0x40, key=UFW_KEY)
    hdrcrc, listcrc, imgsize, numents, _wa3, _wa4, _chipname = struct.unpack_from(
        "<HHIHHI48s", header, 0
    )
    if jl_crc16(bytes(header[2:])) != hdrcrc:
        raise RuntimeError("UFW container header CRC mismatch — not a JieLi UFW?")

    headersize = 0x40 + numents * 0x50

    # NB: listcrc is computed over the RAW (still-scrambled) entry bytes
    # on disk — see fwunpack_newfw.py load_ufw_now() which checks
    # listcrc BEFORE descrambling the entries individually.
    if jl_crc16(bytes(ufw[0x40:headersize])) != listcrc:
        raise RuntimeError("UFW container entry-list CRC mismatch (raw)")

    # Build a fully-descrambled view of the header for later editing
    full_header = bytearray(ufw[:headersize])
    jl_enc_cipher(full_header, 0, 0x40, key=UFW_KEY)
    for off in range(0x40, headersize, 0x50):
        jl_enc_cipher(full_header, off, 0x50, key=UFW_KEY)

    for i in range(numents):
        off = 0x40 + i * 0x50
        etype, eindex, edcrc, ewa1, eoffset, esize, esize2, ewa2, ename = struct.unpack_from(
            "<HHHHIII44s16s", full_header, off
        )
        if etype == 0:
            return {
                "header_descrambled": full_header,
                "entry_idx": i,
                "entry_off": off,
                "eoffset": eoffset,
                "esize": esize,
                "esize2": esize2,
                "edcrc": edcrc,
                "ename": ename.split(b"\0")[0].decode("ascii", errors="replace"),
                "numents": numents,
                "headersize": headersize,
                "listcrc": listcrc,
            }
    raise RuntimeError("No flash.bin entry (etype=0) in UFW container")


def _find_isd_config_in_flashbin(ufw: bytearray, flash_abs: int):
    """Locate isd_config.ini inside flash.bin at absolute offset flash_abs.

    Returns dict with header_off (abs), data_off (abs), data_size,
    plus the original descrambled JLFS header bytes (32 B) and the
    fresh data_crc/header_crc the caller will need to recompute.

    Tries the standard flash-header offsets relative to flash.bin.
    """
    for rel in [0, 0x1000, 0x10000, 0x80000, 0x100000, 0x180000]:
        abs_off = flash_abs + rel
        hdr = bytearray(ufw[abs_off : abs_off + 32])
        jl_enc_cipher(hdr, 0, 32, key=UFW_KEY)
        hcrc = int.from_bytes(hdr[:2], "little")
        if hcrc != 0 and jl_crc16(bytes(hdr[2:])) == hcrc:
            baseoff_abs = abs_off
            break
    else:
        raise RuntimeError("Could not locate flash header inside flash.bin")

    offset = 32
    for _ in range(64):
        entoff = baseoff_abs + offset
        hdr = bytearray(ufw[entoff : entoff + 32])
        jl_enc_cipher(hdr, 0, 32, key=UFW_KEY)
        hcrc, dhdr = struct.unpack_from("<H30s", hdr, 0)
        if jl_crc16(bytes(dhdr)) != hcrc:
            break
        edcrc, eoff, esize, eflags, eresvd, eindex, ename = struct.unpack(
            "<HIIBBH16s", dhdr
        )
        name = ename.split(b"\0")[0].decode("ascii", errors="replace")
        if name == "isd_config.ini":
            data_abs = eoff + baseoff_abs
            return {
                "header_off": entoff,
                "data_off": data_abs,
                "data_size": esize,
                "edcrc": edcrc,
                "eflags": eflags,
                "eresvd": eresvd,
                "eindex": eindex,
                "ename": ename,
                "eoff_field": eoff,
            }
        offset += 32
        if eindex != 0:
            break
    raise RuntimeError("No isd_config.ini entry in flash.bin top dir")


def _find_app_dir_head_in_flashbin(ufw: bytearray, flash_abs: int) -> dict:
    """Locate the `app_dir_head` JLFS entry inside flash.bin.

    The app area is the byte range starting at `flash_abs + eoff` of the
    `app_dir_head` top-dir entry (flags=0x81). Its `esize` field is
    usually `0xFFFFFFFF` (meaning "rest of flash"), so the actual byte
    span we need to (re-)scramble is bounded by the flash.bin entry
    size from the outer UFW container.

    This walks the same scrambled top-dir as `_find_isd_config_in_flashbin`,
    but locates `app_dir_head` instead of `isd_config.ini`.
    """
    for rel in [0, 0x1000, 0x10000, 0x80000, 0x100000, 0x180000]:
        abs_off = flash_abs + rel
        hdr = bytearray(ufw[abs_off : abs_off + 32])
        jl_enc_cipher(hdr, 0, 32, key=UFW_KEY)
        hcrc = int.from_bytes(hdr[:2], "little")
        if hcrc != 0 and jl_crc16(bytes(hdr[2:])) == hcrc:
            baseoff_abs = abs_off
            break
    else:
        raise RuntimeError("Could not locate flash header inside flash.bin")

    offset = 32
    for _ in range(64):
        entoff = baseoff_abs + offset
        hdr = bytearray(ufw[entoff : entoff + 32])
        jl_enc_cipher(hdr, 0, 32, key=UFW_KEY)
        hcrc, dhdr = struct.unpack_from("<H30s", hdr, 0)
        if jl_crc16(bytes(dhdr)) != hcrc:
            break
        edcrc, eoff, esize, eflags, eresvd, eindex, ename = struct.unpack(
            "<HIIBBH16s", dhdr
        )
        name = ename.split(b"\0")[0].decode("ascii", errors="replace")
        # The community parser uses flags==0x81 + name=='app_dir_head' as the marker;
        # match on flags to be robust (community comments suggest there can be more
        # than one app_dir_head, only the first is used as appbase).
        if eflags == 0x81 and name == "app_dir_head":
            data_abs = eoff + baseoff_abs
            return {
                "header_off": entoff,
                "data_off": data_abs,
                "eoff_field": eoff,
                "esize": esize,
                "eflags": eflags,
                "eindex": eindex,
                "ename": ename,
            }
        offset += 32
        if eindex != 0:
            break
    raise RuntimeError("No app_dir_head entry in flash.bin top dir")


def _rescramble_app_area(
    ufw: bytearray,
    *,
    app_area_abs: int,
    app_area_end_abs: int,
    old_key: int,
    new_key: int,
) -> None:
    """Re-scramble the JLFS app area in place from `old_key` to `new_key`.

    `jl_sfc_cipher` is XOR-symmetric per-block: each 32-byte block is
    XOR'd against an LFSR stream seeded with
        effective_key = chipkey ^ ((block_off - base) >> 2)
    where `base = app_area_abs`. So applying `jl_sfc_cipher(... old_key)`
    descrambles to plaintext, then applying `jl_sfc_cipher(... new_key)`
    re-scrambles for a chip whose efuse key is `new_key`.

    The Boot ROM (and `fwunpack_newfw.py:JLFSIterator(..., sfc=True)`)
    will then read the app area, descramble with the efuse chipkey, and
    walk the inner JLFS entries successfully.

    This is a no-op (and returns early) when `old_key == new_key`.
    """
    if old_key == new_key:
        return
    if app_area_end_abs <= app_area_abs:
        raise RuntimeError(
            f"Empty app area: start=0x{app_area_abs:x} end=0x{app_area_end_abs:x}"
        )
    length = app_area_end_abs - app_area_abs
    # Step A: descramble with old key → plaintext bytes in `ufw` slice
    jl_sfc_cipher(ufw, app_area_abs, length, base=app_area_abs, key=old_key)
    # Step B: re-scramble with new key → bytes that the production boot
    # ROM (efuse=new_key) will descramble cleanly.
    jl_sfc_cipher(ufw, app_area_abs, length, base=app_area_abs, key=new_key)


def _build_chipkey_blob_from_int(chipkey: int, seed: int = 0xC0DE) -> bytes:
    """Build a deterministic 34-byte chipkey-bin + CRC16 blob.

    The community chipkeybin_encode uses random.randint, which is
    process-local. To get byte-exact idempotence, we seed Python's
    `random` module with a fixed value before calling encode().
    """
    import random

    state = random.getstate()
    try:
        random.seed(seed)
        ckdata = chipkeybin_encode(chipkey)
    finally:
        random.setstate(state)
    if len(ckdata) != 32:
        raise RuntimeError("chipkeybin_encode produced != 32 bytes")
    # sanity: round-trip must give the same key
    decoded = chipkeybin_decode(ckdata)
    if decoded != chipkey:
        raise RuntimeError(
            f"chipkeybin round-trip mismatch: encoded {chipkey:#x} → decoded {decoded:#x}"
        )
    crc = jl_crc16(ckdata)
    return bytes(ckdata) + struct.pack("<H", crc)


def _autodetect_oem_isd_config(chipkey: int) -> Path:
    """For known PID chipkeys, return the path to the OEM isd_config.ini in hw-sessions/.

    Currently knows PID 1558 (chipkey 0x9847). Falls back to RuntimeError.
    """
    table = {
        0x9847: _REPO_ROOT
        / "hw-sessions"
        / "2026-05-17"
        / "pid1558-firmware-rev"
        / "isd_config.ini",
    }
    p = table.get(chipkey)
    if p is None:
        raise RuntimeError(
            f"No bundled OEM isd_config.ini for chipkey 0x{chipkey:04x}. "
            "Pass --source-isd-config explicitly."
        )
    if not p.is_file():
        raise RuntimeError(f"Bundled OEM isd_config.ini not found at {p}")
    return p


def inject(
    input_ufw: Path,
    output_ufw: Path,
    *,
    chipkey: int | None = None,
    source_isd_config: Path | None = None,
    seed: int = 0xC0DE,
    verify_chipkey: int | None = None,
    skip_app_rescramble: bool = False,
) -> dict:
    """Perform the chipkey injection.

    Exactly one of (chipkey, source_isd_config) should be primary.
    If both are given, source_isd_config wins (used verbatim) and
    chipkey is used only as a post-inject decode check.

    By default, this performs BOTH:
      - Step 1: swap the 34-byte chipkey blob inside the nested
        `isd_config.ini` JLFS entry (covers OTA `parse_fw_info` accept).
      - Step 2: re-scramble the app area below `flash_abs + 0x1000` with
        `jl_sfc_cipher(old_key) -> jl_sfc_cipher(new_key)` (covers Boot
        ROM descrambling post-flash). Set `skip_app_rescramble=True` to
        emit only the Step-1 patch (useful for OTA-accept-only probes).
    """
    ufw = bytearray(input_ufw.read_bytes())
    sha_in = hashlib.sha256(bytes(ufw)).hexdigest()

    # Pick the 34-byte chipkey blob to inject
    if source_isd_config is not None:
        src = source_isd_config.read_bytes()
        if len(src) < ISD_HEADER_BLOB_SIZE:
            raise RuntimeError(
                f"source isd_config.ini too short: {len(src)} B < 34 B"
            )
        new_blob = src[:ISD_HEADER_BLOB_SIZE]
        # Validate the source blob CRC
        ckdata, ckcrc = struct.unpack_from("<32sH", new_blob, 0)
        if jl_crc16(ckdata) != ckcrc:
            raise RuntimeError(
                "source isd_config.ini chipkey-bin CRC16 invalid — not an OEM blob?"
            )
        src_key = chipkeybin_decode(ckdata)
        if verify_chipkey is not None and src_key != verify_chipkey:
            raise RuntimeError(
                f"source chipkey 0x{src_key:04x} != --chipkey 0x{verify_chipkey:04x}"
            )
        chosen_key = src_key
        source_desc = f"file {source_isd_config}"
    elif chipkey is not None:
        new_blob = _build_chipkey_blob_from_int(chipkey, seed=seed)
        chosen_key = chipkey
        source_desc = f"deterministic encode(0x{chipkey:04x}, seed=0x{seed:x})"
    else:
        raise ValueError("Provide either --chipkey or --source-isd-config")

    # Locate flash.bin entry in UFW container
    ufw_meta = _find_ufw_flash_bin_entry(ufw)
    flash_abs = ufw_meta["eoffset"]
    flash_size = ufw_meta["esize"]

    # Locate isd_config.ini inside flash.bin
    isd_meta = _find_isd_config_in_flashbin(ufw, flash_abs)
    data_off = isd_meta["data_off"]
    data_size = isd_meta["data_size"]
    header_off = isd_meta["header_off"]

    # Locate app_dir_head inside flash.bin (needed for Step-2 rescramble)
    app_meta = _find_app_dir_head_in_flashbin(ufw, flash_abs)
    app_area_abs = app_meta["data_off"]  # absolute file offset where app area begins
    app_area_end_abs = flash_abs + flash_size  # ends at end of flash.bin

    # Read the existing isd_config.ini full content & decode current chipkey
    old_data = bytes(ufw[data_off : data_off + data_size])
    old_ckdata, old_ckcrc = struct.unpack_from("<32sH", old_data, 0)
    old_key = chipkeybin_decode(old_ckdata) if jl_crc16(old_ckdata) == old_ckcrc else None

    # Splice in the new chipkey blob, preserving the TLV body
    new_data = bytearray(old_data)
    new_data[:ISD_HEADER_BLOB_SIZE] = new_blob
    new_data = bytes(new_data)

    rescramble_applied = False
    if new_data == old_data:
        print(
            f"[skip] isd_config.ini already encodes chipkey "
            f"0x{old_key:04x} == requested 0x{chosen_key:04x}; nothing to do"
        )
        ufw_out = bytes(ufw)
    else:
        # 1) write new data into UFW
        ufw[data_off : data_off + data_size] = new_data

        # 1.5) Re-scramble app area from old_key → new_key (jl_sfc_cipher).
        # MUST happen before recomputing flash.bin's data_crc below, because
        # this mutation changes flash.bin bytes (everything below
        # `flash_abs + app_dir_head.eoff`, typically `flash_abs + 0x1000`).
        # jl_sfc_cipher is XOR-symmetric per 32-byte block, so descramble
        # with old_key then re-scramble with new_key restores the bytes
        # the production Boot ROM (efuse=new_key) needs to see on flash.
        if not skip_app_rescramble:
            if old_key is None:
                raise RuntimeError(
                    "Cannot perform app-area rescramble: failed to decode old "
                    "chipkey from existing isd_config.ini. Re-run with "
                    "--skip-app-rescramble if you only need Step-1 (OTA accept)."
                )
            _rescramble_app_area(
                ufw,
                app_area_abs=app_area_abs,
                app_area_end_abs=app_area_end_abs,
                old_key=old_key,
                new_key=chosen_key,
            )
            rescramble_applied = old_key != chosen_key

        # 2) recompute JLFS entry header (data_crc + header_crc)
        new_data_crc = jl_crc16(new_data)
        new_hdr_inner = struct.pack(
            "<HIIBBH16s",
            new_data_crc,
            isd_meta["eoff_field"],
            data_size,
            isd_meta["eflags"],
            isd_meta["eresvd"],
            isd_meta["eindex"],
            isd_meta["ename"],
        )
        new_hcrc = jl_crc16(new_hdr_inner)
        new_full_hdr = struct.pack("<H", new_hcrc) + new_hdr_inner  # 32 B
        # Re-scramble with jl_enc_cipher key=0xFFFF
        scrambled = bytearray(new_full_hdr)
        jl_enc_cipher(scrambled, 0, 32, key=UFW_KEY)
        ufw[header_off : header_off + 32] = scrambled

        # 3) recompute UFW container flash.bin entry data_crc and listcrc
        new_flash_data = bytes(ufw[flash_abs : flash_abs + flash_size])
        new_flash_crc = jl_crc16(new_flash_data)

        full_header_desc = ufw_meta["header_descrambled"]
        entry_off = ufw_meta["entry_off"]
        # patch edcrc field in descrambled entry (offset +4 within entry)
        struct.pack_into("<H", full_header_desc, entry_off + 4, new_flash_crc)

        headersize = ufw_meta["headersize"]

        # Re-scramble the entries area FIRST, because listcrc is taken
        # over the SCRAMBLED bytes that end up on disk (matches the
        # behaviour of fwunpack_newfw.py:load_ufw_now()).
        scrambled_entries = bytearray(full_header_desc[0x40:headersize])
        for off in range(0, len(scrambled_entries), 0x50):
            jl_enc_cipher(scrambled_entries, off, 0x50, key=UFW_KEY)
        new_list_crc = jl_crc16(bytes(scrambled_entries))
        # Write listcrc + hdrcrc into the descrambled top 0x40-byte header
        struct.pack_into("<H", full_header_desc, 0x02, new_list_crc)
        new_hdr_crc = jl_crc16(bytes(full_header_desc[2:0x40]))
        struct.pack_into("<H", full_header_desc, 0x00, new_hdr_crc)

        # Scramble only the 0x40-byte container header; entries already
        # scrambled above.
        scrambled_top = bytearray(full_header_desc[:0x40])
        jl_enc_cipher(scrambled_top, 0, 0x40, key=UFW_KEY)
        ufw[:0x40] = scrambled_top
        ufw[0x40:headersize] = scrambled_entries

        ufw_out = bytes(ufw)

    output_ufw.write_bytes(ufw_out)
    sha_out = hashlib.sha256(ufw_out).hexdigest()

    return {
        "input_path": str(input_ufw),
        "output_path": str(output_ufw),
        "input_sha256": sha_in,
        "output_sha256": sha_out,
        "input_size": len(ufw),
        "old_chipkey": old_key,
        "new_chipkey": chosen_key,
        "chipkey_source": source_desc,
        "isd_data_off": data_off,
        "isd_jlfs_header_off": header_off,
        "ufw_flash_entry_off": ufw_meta["entry_off"],
        "ufw_flash_abs": flash_abs,
        "ufw_flash_size": flash_size,
        "app_area_abs": app_area_abs,
        "app_area_size": app_area_end_abs - app_area_abs,
        "app_rescramble_applied": rescramble_applied,
        "skipped": new_data == old_data,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Inject a JieLi chipkey into a self-built UFW (PID 1558 = 0x9847)."
    )
    ap.add_argument("--ufw", required=True, type=Path, help="Input UFW file")
    ap.add_argument("--output", required=True, type=Path, help="Output UFW file")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--chipkey",
        type=lambda s: int(s, 0),
        help="Chipkey as int (e.g. 0x9847). Uses bundled OEM blob if known, "
        "else encodes deterministically with --seed.",
    )
    g.add_argument(
        "--source-isd-config",
        type=Path,
        help="Path to an OEM top/isd_config.ini whose first 34 B will be used verbatim.",
    )
    ap.add_argument(
        "--seed",
        type=lambda s: int(s, 0),
        default=0xC0DE,
        help="Deterministic seed for chipkeybin_encode (default 0xC0DE)",
    )
    ap.add_argument(
        "--no-autodetect",
        action="store_true",
        help="With --chipkey, do not try to use the bundled OEM isd_config.ini; "
        "always synthesize the blob via chipkeybin_encode(seed=...).",
    )
    ap.add_argument(
        "--verify-chipkey",
        type=lambda s: int(s, 0),
        default=None,
        help="With --source-isd-config, assert the decoded chipkey matches.",
    )
    ap.add_argument(
        "--skip-app-rescramble",
        action="store_true",
        help="Only swap the isd_config.ini chipkey blob (Step 1). Skip the "
        "jl_sfc_cipher re-scramble of the app area (Step 2). Useful for "
        "probing OTA accept gates without producing a boot-able image. "
        "Default = both steps run.",
    )
    args = ap.parse_args()

    if not args.ufw.is_file():
        print(f"ERROR: --ufw not found: {args.ufw}", file=sys.stderr)
        sys.exit(2)

    source_isd = args.source_isd_config
    if args.chipkey is not None and not args.no_autodetect:
        try:
            auto = _autodetect_oem_isd_config(args.chipkey)
            source_isd = auto
            print(f"[auto] using bundled OEM blob: {auto}")
        except RuntimeError as e:
            print(f"[auto] {e}; synthesizing instead.")

    result = inject(
        args.ufw,
        args.output,
        chipkey=args.chipkey,
        source_isd_config=source_isd,
        seed=args.seed,
        verify_chipkey=args.verify_chipkey,
        skip_app_rescramble=args.skip_app_rescramble,
    )

    print()
    print("=== chipkey injection result ===")
    for k, v in result.items():
        if isinstance(v, int) and k.endswith(("_off", "_abs", "_size")):
            print(f"  {k:25s} = 0x{v:08x}")
        elif isinstance(v, int) and k.endswith("chipkey"):
            print(f"  {k:25s} = 0x{v:04x} ({v})" if v is not None else f"  {k:25s} = None")
        else:
            print(f"  {k:25s} = {v}")


if __name__ == "__main__":
    main()
