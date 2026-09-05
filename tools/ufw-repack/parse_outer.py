#!/usr/bin/env python3
"""Parse the OUTER UFW entry table of a UFW file.

Accepts files with or without the 27-byte Qix wrapper. Detects the wrapper
by checking for the Qix magic at offset 0.
"""
import struct, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools/community-re/jl-misctools/firmware"))
from jltech.cipher import jl_enc_cipher  # noqa

QIX_MAGIC = b"\xbc\xaf"
QIX_HEADER_LEN = 27
UFW_HEADERKEY = 0xFFFF


def dec(scr, key=UFW_HEADERKEY):
    out = bytearray(scr)
    jl_enc_cipher(out, 0, len(out), key)
    return bytes(out)


def parse(path):
    raw = Path(path).read_bytes()
    has_qix = raw[:2] == QIX_MAGIC
    inner_off = QIX_HEADER_LEN if has_qix else 0
    inner = raw[inner_off:]
    hdr = dec(inner[:0x40])
    hdrcrc, listcrc, imgsize, numents, wa3, wa4 = struct.unpack_from("<HHIHHI", hdr, 0)
    chipname = hdr[0x10:0x40].split(b"\0")[0].decode("ascii", "replace")
    print(f"FILE: {path}")
    print(f"  total={len(raw)} qix={has_qix} inner={len(inner)}")
    print(f"  hdrcrc={hdrcrc:#06x} listcrc={listcrc:#06x} imgsize={imgsize} numents={numents} chip={chipname!r}")
    entries = []
    total_data = 0
    for i in range(numents):
        eoff = 0x40 + i * 0x50
        pe = dec(inner[eoff:eoff + 0x50])
        etype, eindex, edcrc, _, e_off, esize, _, _, ename = \
            struct.unpack_from("<HHHHIII44s16s", pe, 0)
        name = ename.split(b"\0")[0].decode("ascii", "replace")
        entries.append((i, etype, eindex, edcrc, e_off, esize, name))
        total_data += esize
        print(f"    [{i}] type={etype:3d} idx={eindex} dcrc={edcrc:#06x} off={e_off:9d} sz={esize:9d} name={name!r}")
    print(f"  sum(entry sizes)={total_data}")
    return entries


if __name__ == "__main__":
    for p in sys.argv[1:]:
        parse(p)
        print()
