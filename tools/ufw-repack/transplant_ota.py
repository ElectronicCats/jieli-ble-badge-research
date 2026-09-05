#!/usr/bin/env python3
"""Transplant a different OTA-updater (`ota.bin`, UFW entry type 100) into an
existing Qix-wrapped UFW, recomputing every CRC the bootloader checks.

Why: the OEM badge applies its OTA via the `lcflash_ota.bin` updater (internal
dual-bank flash update). Our SDK build packages the generic testbox/rcsp/edr
updater instead, because `isd_download` does not recognise `lcflash_ota.bin`
("unknown upgrade file ... ignore"). The staged UFW's embedded updater is the
program the resident uboot executes at apply time, so the wrong updater faults.

Minimal-change strategy: the OEM ota.bin (23 688 B) is smaller than the stager's
slot (204 917 B), so we overwrite the slot in place with the OEM updater + 0xFF
padding and only rewrite that one entry's size+data_crc. No other entry moves,
so their offsets stay valid. Then recompute listcrc, hdrcrc and the Qix CRC.

Usage:
    transplant_ota.py <stager.ufw> <oem_ota.bin> <out.ufw>
"""
import struct, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools/community-re/jl-misctools/firmware"))
from jltech.cipher import jl_enc_cipher          # noqa: E402
from jltech.crc import jl_crc16                  # noqa: E402

QIX_HEADER_LEN = 27
UFW_HEADERKEY = 0xFFFF
ENTRY_SZ = 0x50


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def dec(b, k=UFW_HEADERKEY):
    o = bytearray(b)
    jl_enc_cipher(o, 0, len(o), k)
    return bytes(o)


def main():
    stager_path, oem_ota_path, out_path = sys.argv[1:4]
    raw = bytearray(Path(stager_path).read_bytes())
    oem_ota = Path(oem_ota_path).read_bytes()
    assert raw[:2] == b"\xbc\xaf", "stager UFW missing Qix magic"

    inner_off = QIX_HEADER_LEN
    inner = raw[inner_off:]                       # view into the copy via slicing later

    hdr = dec(bytes(raw[inner_off:inner_off + 0x40]))
    numents = struct.unpack_from("<H", hdr, 8)[0]

    # Locate the ota.bin entry (type 100).
    ota_idx = None
    ota_off = ota_sz = 0
    for i in range(numents):
        eoff = inner_off + 0x40 + i * ENTRY_SZ
        pe = dec(bytes(raw[eoff:eoff + ENTRY_SZ]))
        etype, eindex, edcrc, _, e_off, esize = struct.unpack_from("<HHHHII", pe, 0)
        name = pe[64:80].split(b"\0")[0].decode("ascii", "replace")
        if name == "ota.bin" or etype == 100:
            ota_idx, ota_off, ota_sz = i, e_off, esize
            print(f"  found ota.bin: entry[{i}] off={e_off} sz={esize} dcrc={edcrc:#06x}")
            break
    if ota_idx is None:
        raise SystemExit("no ota.bin (type 100) entry found")
    if len(oem_ota) > ota_sz:
        raise SystemExit(f"OEM ota.bin {len(oem_ota)} > slot {ota_sz}; cannot in-place transplant")

    # 1) Overwrite the data slot: OEM updater + 0xFF padding to keep total size.
    data_lo = inner_off + ota_off
    raw[data_lo:data_lo + len(oem_ota)] = oem_ota
    raw[data_lo + len(oem_ota):data_lo + ota_sz] = b"\xff" * (ota_sz - len(oem_ota))

    # 2) Rewrite the ota.bin entry header: esize -> real OEM size, edcrc -> its crc.
    new_dcrc = jl_crc16(oem_ota)
    eoff = inner_off + 0x40 + ota_idx * ENTRY_SZ
    pe = bytearray(dec(bytes(raw[eoff:eoff + ENTRY_SZ])))
    struct.pack_into("<H", pe, 4, new_dcrc)       # edcrc @4
    struct.pack_into("<I", pe, 12, len(oem_ota))  # esize @12
    enc = bytearray(pe)
    jl_enc_cipher(enc, 0, ENTRY_SZ, UFW_HEADERKEY)
    raw[eoff:eoff + ENTRY_SZ] = enc
    print(f"  ota.bin entry -> sz={len(oem_ota)} dcrc={new_dcrc:#06x}")

    # 3) listcrc = jl_crc16 over the *encrypted* entry-header block.
    hs = inner_off + 0x40
    enc_entries = bytes(raw[hs:hs + numents * ENTRY_SZ])
    new_listcrc = jl_crc16(enc_entries)

    # 4) Patch + re-encrypt the UFW header (listcrc @2, hdrcrc @0 over plain [2:64]).
    hp = bytearray(dec(bytes(raw[inner_off:inner_off + 0x40])))
    struct.pack_into("<H", hp, 2, new_listcrc)
    new_hdrcrc = jl_crc16(bytes(hp[2:64]))
    struct.pack_into("<H", hp, 0, new_hdrcrc)
    ench = bytearray(hp)
    jl_enc_cipher(ench, 0, 0x40, UFW_HEADERKEY)
    raw[inner_off:inner_off + 0x40] = ench
    print(f"  listcrc -> {new_listcrc:#06x}  hdrcrc -> {new_hdrcrc:#06x}")

    # 5) Qix wrapper CRC @25 over the whole inner UFW.
    qix_crc = crc16_ccitt_false(bytes(raw[inner_off:]))
    struct.pack_into("<H", raw, 25, qix_crc)
    print(f"  Qix CRC -> {qix_crc:#06x}")

    Path(out_path).write_bytes(raw)
    print(f"  wrote {out_path} ({len(raw)} B)")


if __name__ == "__main__":
    main()
