#!/usr/bin/env python3
"""Swap the whole `app.bin` inside a working OEM UFW (jack) with our own app.bin,
preserving ALL OEM flash geometry (uboot, key_mac, app_dir_head, otp_cfg, flash
header, the lcflash ota.bin updater). jack already applies on the badge, so a
pure code swap keeps the exact structure the OEM bootrom + lcflash updater want.

The OEM app_area contains ONLY app.bin (code) — resources/virfat live in other
partitions the app-code OTA never touches — so this is a clean code-only replace.

Our app (smaller) is 0xFF-padded up to the OEM app.bin slot size; the loader runs
app.bin by its own internal size header, so the tail padding is inert. repack
recomputes the app.bin, app_area_head (parent), flash.bin, list, header and Qix CRCs.

Usage:
    swap_app.py <base.ufw> <new_app.bin> <out.ufw>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repack_ufw import parse_ufw, repack  # noqa: E402


def main():
    base_path, app_path, out_path = sys.argv[1:4]
    base = Path(base_path).read_bytes()
    new_app = Path(app_path).read_bytes()
    layout = parse_ufw(base)

    # Find the app.bin JLFS slot to learn its data_size.
    slot = next((je for je in layout.jlfs_app_dir if je.name == "app.bin"), None)
    if slot is None:
        raise SystemExit("no app.bin in app area")
    print(f"  OTA app slot : {slot.data_size} bytes  (OEM code slot @ {slot.data_offset:#x})")
    print(f"  your app.bin : {len(new_app)} bytes")
    if len(new_app) > slot.data_size:
        over = len(new_app) - slot.data_size
        raise SystemExit(
            "\n".join([
                "",
                "  ERROR: app.bin is TOO BIG for the BLE OTA image — nothing was written.",
                f"    your app.bin : {len(new_app):>9} bytes",
                f"    OTA app slot : {slot.data_size:>9} bytes",
                f"    over by      : {over:>9} bytes  ({100 * over / slot.data_size:.1f}% too big)",
                "",
                "  The OEM BLE OTA replaces ONLY the code slot and cannot grow it. To fit, either:",
                "    - move big assets (images, fonts) out of app.bin into raw-flashed content, or",
                "    - trim the firmware (disable unused features / LVGL demos) to shrink app.bin.",
                "",
            ])
        )

    headroom = slot.data_size - len(new_app)
    print(f"  fits OTA slot: {headroom} bytes headroom "
          f"({100 * len(new_app) / slot.data_size:.1f}% of slot used)")
    padded = new_app + b"\xff" * headroom
    out = repack(layout, [("app.bin", 0, padded)])
    Path(out_path).write_bytes(out)
    print(f"  wrote {out_path} ({len(out)} B)")


if __name__ == "__main__":
    main()
