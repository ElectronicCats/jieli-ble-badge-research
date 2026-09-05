#!/usr/bin/env python3
"""Generate the firmware-EMBEDDED lock-screen wallpaper C header from an image.

This is the compile-time counterpart to make_lockimg.py:
  - make_lockimg.py       -> a .bin blob you push to flash slot 0x300000 over BLE
                             (changes the wallpaper WITHOUT reflashing the firmware).
  - make_lock_wallpaper.py -> a C header (lock_wallpaper.h) compiled INTO the firmware
                             as the built-in default wallpaper (shown when the BLE slot
                             is empty/invalid). Changing it needs a rebuild + firmware OTA.

The output matches the format the badge firmware expects:
  144x144 RGB565, little-endian (LV_COLOR_DEPTH=16 native), wrapped in an
  `lv_img_dsc_t lock_wallpaper` with LV_IMG_CF_TRUE_COLOR — displayed via
  `lv_img_set_src(img, &lock_wallpaper)` (a VARIABLE source, so it bypasses lv_fs).

Usage:
    make_lock_wallpaper.py <source-image> <out lock_wallpaper.h>

Then rebuild the firmware and OTA it (see the custom->custom OTA tutorial).
Requires Pillow:  pip install pillow
"""
import sys
from pathlib import Path
from PIL import Image

W = H = 144   # firmware caps the embedded wallpaper at 144x144 (static RAM buffer)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    src, out = sys.argv[1], sys.argv[2]
    name = Path(src).name

    im = Image.open(src).convert("RGB").resize((W, H), Image.LANCZOS)
    data = bytearray()
    for y in range(H):
        for x in range(W):
            r, g, b = im.getpixel((x, y))
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            data.append(v & 0xFF)   # RGB565 little-endian
            data.append(v >> 8)
    assert len(data) == W * H * 2

    L = []
    L.append(f"/* Auto-generated lock-screen wallpaper: {W}x{H} RGB565 (LV_IMG_CF_TRUE_COLOR).")
    L.append(" * Displayed via lv_img_set_src(img,&lock_wallpaper) (VARIABLE source -> bypasses lv_fs).")
    L.append(f" * Source: {name}. {len(data)} bytes. */")
    L.append("#ifndef LOCK_WALLPAPER_H")
    L.append("#define LOCK_WALLPAPER_H")
    L.append('#include "lvgl.h"')
    L.append("")
    L.append(f"#define LOCK_WP_W {W}")
    L.append(f"#define LOCK_WP_H {H}")
    L.append("")
    L.append(f"static const uint8_t lock_wallpaper_map[{len(data)}] = {{")
    for i in range(0, len(data), 16):
        L.append("    " + ",".join(f"0x{b:02x}" for b in data[i:i + 16]) + ",")
    L.append("};")
    L.append("")
    L.append("static const lv_img_dsc_t lock_wallpaper = {")
    L.append("    .header.always_zero = 0,")
    L.append(f"    .header.w = {W},")
    L.append(f"    .header.h = {H},")
    L.append("    .header.cf = LV_IMG_CF_TRUE_COLOR,")
    L.append(f"    .data_size = {len(data)},")
    L.append("    .data = lock_wallpaper_map,")
    L.append("};")
    L.append("")
    L.append("#endif /* LOCK_WALLPAPER_H */")
    Path(out).write_text("\n".join(L) + "\n")
    print(f"wrote {out} from {name}: {len(data)} bytes ({W}x{H} RGB565)")


if __name__ == "__main__":
    main()
