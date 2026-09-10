#!/usr/bin/env python3
"""dump.bin -> loader-download .ufw  (the custom→OEM OTA leg, for `qix rcsp-flash`).

Wraps the CODE region of a full 4 MB flash dump (`dump[0 : flash.bin size]`,
i.e. [0, 0xFC000), taken verbatim in its on-flash SFC-scrambled form) into a
VANILLA RCSP UFW whose loader rewrites CODE `[0, 0x17E000)` IN PLACE — it never
stages over the resource partitions (SDFILE 0x17E000 / VIRFAT 0x33E000), so the
target's resources/textos survive. (The 0xC0 / 0xD4 staging paths cannibalize
that span and corrupt resources — that's why this leg exists.)

The loader bundle (`ota.bin`, incl. `lcflash_ota.bin`) and the small config
entries come from a one-time BASE built from any SDK `update.ufw`:

    dump2ufw.py --make-base <native_update.ufw>     # -> loaderdl-base.ufw (once)
    dump2ufw.py <dump.bin> <out.ufw>                # dump.bin -> out.ufw

`--base <file>` overrides the default base (a native `update.ufw` works too — its
`flash2.bin` is dropped automatically).
"""
import struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))   # vendored jltech
from jltech.cipher import jl_enc_cipher
from jltech.crc import jl_crc16

KEY = 0xFFFF
BASE = Path(__file__).resolve().parent / "loaderdl-base.ufw"

def xor(b):                       # jl_enc_cipher is symmetric (dec == enc)
    o = bytearray(b); jl_enc_cipher(o, 0, len(o), KEY); return bytes(o)

def parse(raw):
    assert raw[:2] != b"\xbc\xaf", "expected a VANILLA .ufw (no Qix wrapper)"
    hdr = bytearray(xor(raw[:0x40]))
    numents = struct.unpack_from("<H", hdr, 8)[0]
    ents = []   # [plain 0x50 header, type, off, size, name]
    for i in range(numents):
        p = bytearray(xor(raw[0x40+i*0x50 : 0x40+(i+1)*0x50]))
        etype,_i,_d,_,off,sz,_,_,nm = struct.unpack_from("<HHHHIII44s16s", p, 0)
        ents.append([p, etype, off, sz, nm.split(b"\0")[0].decode("ascii","replace")])
    return hdr, ents

def rebuild(hdr, ents, body_raw, drop=("flash2.bin",)):
    dropped = [e for e in ents if e[4] in drop]
    kept    = [e for e in ents if e[4] not in drop]
    for e in kept:
        e[2] -= sum(d[3] for d in dropped if d[2] < e[2])   # shift offsets past cuts
    ne = len(kept)
    ent_plain = bytearray()
    for p,_t,off,_sz,_n in kept:
        pp = bytearray(p); struct.pack_into("<I", pp, 8, off); ent_plain += pp
    enc_ents = b"".join(xor(ent_plain[i*0x50:(i+1)*0x50]) for i in range(ne))
    imgsize = struct.unpack_from("<I", hdr, 4)[0] - sum(d[3] for d in dropped)
    struct.pack_into("<H", hdr, 2, jl_crc16(enc_ents))          # listcrc
    struct.pack_into("<I", hdr, 4, imgsize)
    struct.pack_into("<H", hdr, 8, ne)
    struct.pack_into("<H", hdr, 0, jl_crc16(bytes(hdr[2:64])))  # hdrcrc
    data_start = min(off for _p,_t,off,sz,_n in ents if sz > 0)
    cuts = sorted((d[2], d[2]+d[3]) for d in dropped)
    body = bytearray(); pos = data_start
    for cs, ce in cuts: body += body_raw[pos:cs]; pos = ce
    body += body_raw[pos:]
    return xor(bytes(hdr)) + enc_ents + b"\x00"*(data_start - (0x40+ne*0x50)) + bytes(body)

def make_base(native_ufw):
    """Maintainer step: distill any SDK update.ufw into the shipped base — strip
    flash2.bin and ZERO the flash.bin slot (the code is supplied per-dump; zeros
    keep the base code-free and tiny in git). Users don't run this; the base is
    committed. Only re-run it if the SDK loader (ota.bin) changes."""
    raw = open(native_ufw, "rb").read()
    hdr, ents = parse(raw)
    fb = next(e for e in ents if e[4] == "flash.bin")
    body = bytearray(raw)
    body[fb[2]:fb[2]+fb[3]] = b"\x00" * fb[3]                  # blank the placeholder code
    struct.pack_into("<H", fb[0], 4, jl_crc16(b"\x00"*fb[3]))  # dcrc of zeros (dump2ufw resets it)
    BASE.write_bytes(rebuild(hdr, ents, body))                 # + strip flash2.bin
    print(f"wrote {BASE} ({BASE.stat().st_size} B, flash.bin zeroed) — base ready")

def dump2ufw(dump_path, outp, base=BASE):
    raw = Path(base).read_bytes()
    dump = open(dump_path, "rb").read()
    hdr, ents = parse(raw)
    fb = next((e for e in ents if e[4] == "flash.bin"), None)
    assert fb, "no flash.bin entry in the base"
    off, size = fb[2], fb[3]
    new = dump[:size]
    assert len(new) == size, f"dump too small: need {size} (0x{size:x}), got {len(dump)}"
    struct.pack_into("<H", fb[0], 4, jl_crc16(new))            # flash.bin dcrc
    body_raw = bytearray(raw); body_raw[off:off+size] = new
    open(outp, "wb").write(rebuild(hdr, ents, body_raw))        # base app-only -> drop is a no-op
    print(f"wrote {outp} ({Path(outp).stat().st_size} B): "
          f"flash.bin <- {Path(dump_path).name}[0:0x{size:x}] dcrc={jl_crc16(new):#06x}")

if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "--make-base":
        make_base(a[1])
    elif len(a) >= 2 and a[0] != "--make-base":
        base = BASE
        if "--base" in a:
            base = a[a.index("--base")+1]; a = [x for j,x in enumerate(a)
                    if j not in (a.index("--base"), a.index("--base")+1)]
        if base is BASE and not BASE.exists():
            sys.exit(f"no base yet — run: dump2ufw.py --make-base <native_update.ufw>  (creates {BASE})")
        dump2ufw(a[0], a[1], base)
    else:
        print(__doc__); sys.exit(1)
