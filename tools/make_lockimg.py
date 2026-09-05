#!/usr/bin/env python3
"""Build a lock-screen wallpaper blob for the badge's BLE-updatable slot (flash 0x300000).

Layout (little-endian, matches lock_img_hdr_t in lvgl_main.c):
  u32 magic = 0x474D4B4C ("LKMG")   u16 w   u16 h   u32 size(=w*h*2)   u32 rsv=0
  ... then w*h*2 bytes of RGB565 (little-endian, LV_COLOR_DEPTH=16 native).

Push it over BLE with no firmware reflash:
  qix rawflash <mac> lockimg.bin --addr 0x300000 --verify --reboot

Usage:  make_lockimg.py <source-image> [out.bin] [size]   (size default 144, square, <=144)
"""
import sys, struct
from PIL import Image

MAGIC = 0x474D4B4C

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "lockimg.bin"
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 144
    if not (16 <= size <= 144):
        print("size must be 16..144 (RAM-buffer cap in firmware)"); sys.exit(1)
    w = h = size
    im = Image.open(src).convert("RGB").resize((w, h), Image.LANCZOS)
    px = im.load()
    data = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            data.append(v & 0xFF)          # RGB565 little-endian
            data.append((v >> 8) & 0xFF)
    hdr = struct.pack("<IHHII", MAGIC, w, h, w * h * 2, 0)
    blob = hdr + bytes(data)
    with open(out, "wb") as f:
        f.write(blob)
    print("wrote %s: %dx%d, %d bytes (header 16 + %d px)" % (out, w, h, len(blob), len(data)))
    print("push:  qix rawflash <mac> %s --addr 0x300000 --verify --reboot" % out)

if __name__ == "__main__":
    main()
