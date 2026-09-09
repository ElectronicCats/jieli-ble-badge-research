# Updating the lock-screen image

144×144 RGB565 image on the lock screen. The firmware reads a BLE-updatable slot at flash
`0x300000` (falls back to the built-in default if empty). Update it over BLE — no reflash.

## Over BLE (no reflash)

Badge must be on the **Update** screen (advertising `EC-BADGE`).

```sh
# 1. Build the blob (needs Pillow). Square, <= 144.
python3 tools/make_lockimg.py my-picture.png lockimg.bin

# 2. Push it (clear any stale bond first — the Update screen is cleartext)
bluetoothctl remove <MAC>
qix scan --timeout 5 >/dev/null
QIX_CONNECT_VIA_DBUS=1 qix rawflash <MAC> lockimg.bin --addr 0x300000 --verify --reboot
```

The badge reboots showing the new image. The slot survives firmware OTA. A
`le-connection-abort-by-local` on the first attempt is normal — it retries.

## Change the built-in default (needs rebuild)

```sh
python3 tools/make_lock_wallpaper.py my-picture.png <sdk>/SDK/apps/common/ui/lvgl_v8/lock_wallpaper.h
```
