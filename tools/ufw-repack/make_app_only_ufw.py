#!/usr/bin/env python3
"""Strip flash2.bin (the dual-bank duplicate copy) from a VANILLA JieLi UFW,
producing an app-only single-copy .ufw that fits one bank / passes allow_check.

Reuses jltech ciphers/CRC. Operates ONLY on the outer UFW container; flash.bin
(our app_dir) is kept byte-identical.

Usage: make_app_only_ufw.py <in.ufw> <out.ufw>
"""
import struct, sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools/community-re/jl-misctools/firmware"))
from jltech.cipher import jl_enc_cipher
from jltech.crc import jl_crc16

KEY = 0xFFFF

def dec(b):
    out = bytearray(b); jl_enc_cipher(out, 0, len(out), KEY); return bytes(out)
def enc(b):
    out = bytearray(b); jl_enc_cipher(out, 0, len(out), KEY); return bytes(out)

def main(inp, outp, drop_names):
    raw = open(inp, "rb").read()
    assert raw[:2] != b"\xbc\xaf", "expected VANILLA ufw (no Qix wrapper)"
    hdr = bytearray(dec(raw[:0x40]))
    hdrcrc, listcrc, imgsize, numents, wa3, wa4 = struct.unpack_from("<HHIHHI", hdr, 0)

    ents = []          # (plain 0x50 header, type, off, size, name)
    for i in range(numents):
        p = bytearray(dec(raw[0x40 + i*0x50: 0x40 + (i+1)*0x50]))
        etype, eidx, edcrc, _, e_off, esize, _, _, ename = struct.unpack_from("<HHHHIII44s16s", p, 0)
        name = ename.split(b"\0")[0].decode("ascii", "replace")
        ents.append([p, etype, e_off, esize, name])

    drop = [e for e in ents if e[4] in drop_names]
    for e in drop:
        print(f"removing {e[4]} off={e[2]} size={e[3]}")
    # cut each dropped data block; entries after a cut shift up by the cut size
    kept = [e for e in ents if e[4] not in drop_names]
    for e in kept:
        e[2] -= sum(d[3] for d in drop if d[2] < e[2])
    total_cut = sum(d[3] for d in drop)
    new_numents = len(kept)

    # rebuild plain entry headers with patched e_off
    new_ent_plain = bytearray()
    for p, etype, e_off, esize, name in kept:
        pp = bytearray(p)
        struct.pack_into("<I", pp, 8, e_off)   # e_off is at byte 8 (HHHH then I)
        new_ent_plain += pp

    enc_entries = bytearray()
    for i in range(new_numents):
        enc_entries += enc(new_ent_plain[i*0x50:(i+1)*0x50])
    new_listcrc = jl_crc16(bytes(enc_entries))

    new_imgsize = imgsize - total_cut
    struct.pack_into("<H", hdr, 2, new_listcrc)
    struct.pack_into("<I", hdr, 4, new_imgsize)
    struct.pack_into("<H", hdr, 8, new_numents)
    new_hdrcrc = jl_crc16(bytes(hdr[2:64]))
    struct.pack_into("<H", hdr, 0, new_hdrcrc)
    enc_header = enc(bytes(hdr))

    # assemble: new header + new entry table, then the ORIGINAL data region
    # (from old headersize' data start = fixed 0x400 pad) with flash2 block cut out.
    old_headersize = 0x40 + numents*0x50
    new_headersize = 0x40 + new_numents*0x50
    # data region begins after padding; padding target is where entry[0] data sits
    data_start = min(e[2] for e in ents if e[3] > 0 for _ in [0])  # smallest orig off with data
    data_start = min(e_off for _,_,e_off,esize,_ in ents if esize > 0)
    # original bytes from data_start onward, with each dropped block excised
    cuts = sorted((d[2], d[2] + d[3]) for d in drop)
    body = bytearray()
    pos = data_start
    for cs, ce in cuts:
        body += raw[pos:cs]
        pos = ce
    body += raw[pos:]

    out = bytearray()
    out += enc_header
    out += enc_entries
    out += b"\x00" * (data_start - new_headersize)   # pad to data_start
    out += body
    open(outp, "wb").write(out)
    print(f"wrote {outp}: {len(out)} bytes (was {len(raw)}), numents {numents}->{new_numents}, "
          f"imgsize {imgsize}->{new_imgsize}, listcrc {listcrc:#06x}->{new_listcrc:#06x}, "
          f"hdrcrc {hdrcrc:#06x}->{new_hdrcrc:#06x}")

if __name__ == "__main__":
    # extra args after in/out = additional entry names to drop (default: flash2.bin)
    names = set(sys.argv[3:]) or {"flash2.bin"}
    main(sys.argv[1], sys.argv[2], names)
