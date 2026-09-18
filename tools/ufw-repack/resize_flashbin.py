"""Build a .ufw variant whose flash.bin entry spans a different amount of the flash.

Non-destructive research tool: used only to ask the device's header validator whether
it will accept a wider write window. Reuses the vendored jltech ciphers/CRC.

  resize_flashbin.py <base.ufw> <dump.bin> <new_size_hex> <out.ufw>
"""
import struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))   # vendored jltech
from jltech.cipher import jl_enc_cipher
from jltech.crc import jl_crc16
KEY=0xFFFF
def xor(b):
    o=bytearray(b); jl_enc_cipher(o,0,len(o),KEY); return bytes(o)

base, dumpp, newsz, outp = sys.argv[1], sys.argv[2], int(sys.argv[3],0), sys.argv[4]
raw=bytearray(open(base,'rb').read()); dump=open(dumpp,'rb').read()
hdr=bytearray(xor(raw[:0x40]))
hdrcrc, listcrc, imgsize, numents = struct.unpack_from("<HHIH", hdr, 0)
ents=[]
for i in range(numents):
    p=bytearray(xor(raw[0x40+i*0x50:0x40+(i+1)*0x50]))
    etype,eidx,edcrc,ewa1,eoff,esz,esz2 = struct.unpack_from("<HHHHIII", p, 0)
    nm=struct.unpack_from("<16s",p,0x40)[0].split(b"\0")[0].decode()
    ents.append([p,etype,eoff,esz,esz2,nm])
fb_i=[i for i,e in enumerate(ents) if e[5]=="flash.bin"][0]
fb=ents[fb_i]
old=fb[3]; delta=newsz-old
print(f"flash.bin: off=0x{fb[2]:x} size=0x{old:x} size2=0x{fb[4]:x} -> new size 0x{newsz:x} (delta {delta:+d})")
if newsz>len(dump): sys.exit("dump smaller than requested span")

data_start=min(e[2] for e in ents if e[3]>0)
body=bytearray(raw[data_start:])
fb_rel=fb[2]-data_start
new_fb=dump[:newsz]
body[fb_rel:fb_rel+old]=new_fb
struct.pack_into("<H", fb[0], 4, jl_crc16(new_fb))
struct.pack_into("<I", fb[0], 12, newsz)
if fb[4]==old: struct.pack_into("<I", fb[0], 16, newsz)
for e in ents:
    if e[2]>fb[2]:
        e[2]+=delta; struct.pack_into("<I", e[0], 8, e[2])
ent_plain=b"".join(bytes(e[0]) for e in ents)
enc=b"".join(xor(ent_plain[i*0x50:(i+1)*0x50]) for i in range(numents))
struct.pack_into("<H", hdr, 2, jl_crc16(enc))
struct.pack_into("<I", hdr, 4, imgsize+delta)
struct.pack_into("<H", hdr, 0, jl_crc16(bytes(hdr[2:64])))
out=xor(bytes(hdr))+enc+b"\x00"*(data_start-(0x40+numents*0x50))+bytes(body)
Path(outp).write_bytes(out)
print(f"wrote {outp} ({len(out)} B)")
