# Dump & restore the stock OEM firmware (wired, UBOOT)

How to take a **byte-exact dump** of the badge's 4 MB NOR flash before flashing any custom
firmware — and how to put it back, either **wired over USB-ISP** (no OTA at all, the recovery
path) or **over BLE** as the [custom → OEM](ota-howto.md#4-custom--oem-restore-the-stock-firmware)
restore leg.

All of it is the *only* part of the whole flow that is **not over BLE**: it needs physical access
to the chip's `DP`/`DM` pads and a way to put it in USB download (UBOOT) mode — a vendor USB
updater, a DIY adapter such as the prototype [`tools/pi-pico-jl-dongle`](../tools/pi-pico-jl-dongle),
or a unit that fails to boot on its own.

> ⚠️ Security research / educational use only — on hardware you own. See
> [DISCLAIMER.md](../DISCLAIMER.md).

---

## 1. Why you need a dump

- The **custom → OEM** restore needs the **stock** image as its input — the
  [BLE leg](#5-restore-over-ble--dump2ufwpy--qix-rcsp-flash) below, or
  [wired over USB-ISP](#4-restore-over-usb-isp-wired-no-ota) — and **no OEM firmware is
  distributed here**.
- A failed OTA or a bricked flash means writing a byte-exact dump of *your* unit back: that is
  exactly the wired [restore over USB-ISP](#4-restore-over-usb-isp-wired-no-ota).
- The dump is **per-unit**: it carries `key_mac` and the stock name. **Take it from your own
  badge, *before* the first custom flash** — once the unit runs the custom firmware you can no
  longer dump the original from it.

---

## 2. Prerequisites

| Need | Why |
|---|---|
| `tools/jl-uboot-tool` (git submodule) | the JieLi USB flasher/dumper that issues the `read` |
| Python 3 + the tool's deps | `requirements.txt`: `tqdm`, `pyyaml`, `crcmod`, `pycryptodomex` |
| Badge `DP`/`DM`/`GND` pads + a way to enter USB download (UBOOT) mode | the wired half of the flow; never over BLE |

Clone with the submodule, or `git submodule update --init` it in an existing checkout:

```sh
git clone --recurse-submodules https://github.com/ElectronicCats/jieli-ble-badge-research

pip install -r tools/jl-uboot-tool/requirements.txt
```

The tracked fork (pin at commit `ef77f9c`) adds **BR35 / AC707N** support (`data/chips.yaml`)
to kagaimiq's upstream `jl-uboot-tool`.

---

## 3. Dump the flash

1. Put the chip in USB download (UBOOT) mode and connect it to the host. The tool finds the
   device itself (`jldevfind`); if it can't, it asks which `UBOOT` / `UDISK` / `DEVICE` to use
   (`--device` selects it directly). The low-level details of how the mode is entered live in
   the tool's own docs, inside the submodule: `docs/what-is-uboot.md`,
   `docs/how-to-enter-uboot.md`, `docs/usb-protocol.md`.

2. Read the **entire** 4 MB flash, from the tool's directory:

```sh
cd tools/jl-uboot-tool
python3 jluboottool.py "read 0x0 0x400000 oem-dump.bin"
```

- `0x0` — start of the flash.
- `0x400000` = **4 MiB**, the E87's whole NOR flash. `2 MiB` would be too short to restore:
  `dump2ufw.py` needs the first `0xFC000` bytes (CODE slot), and the full image is your
  recovery.
- It writes the flash **verbatim, in its on-flash (SFC-scrambled) form** — exactly what the
  OTA path consumes. **Do not** decrypt or "fix" the dump.

3. Verify it before anything else:

```sh
ls -l oem-dump.bin        # → 4194304 B
sha256sum oem-dump.bin    # record it — this is your recovery + restore image
```

---

## 4. Restore over USB-ISP (wired, no OTA)

When the badge is reachable over the `DP`/`DM` pads (it never left the bench, or it came back
for a recovery), the simplest restore is to write the **full 4 MB dump back verbatim** — no
`.ufw`, no BLE. Works from any state, including a brick: UBOOT mode is driven by the chip's
Boot ROM, the firmware on flash is irrelevant.

```sh
# 1. Enter USB download (UBOOT) mode and confirm the device:
lsusb | grep -i 4c4a:2942          # should show "BR35UBOOT1.00"

# 2. Write the whole OEM dump back at 0x0, then reset out of UBOOT:
cd tools/jl-uboot-tool
python3 jluboottool.py "write 0x0 oem-dump.bin" "reset"

# 3. Confirm it left UBOOT mode (the 4c4a:2942 device is gone):
sleep 2 && lsusb | grep -qi 4c4a:2942 \
  && echo "still in UBOOT — retry" \
  || echo "OK, rebooted"
```

- `write` erases and rewrites the **entire** 4 MB at `0x0` — the whole flash, including
  `key_mac`, the resource partitions and the boot config — so the unit returns to its exact
  factory state. **Use your own dump**: the MAC and stock name will be the ones in the file,
  so a dump from another unit would clone that unit's identity.
- This full-flash write is the difference from the [BLE path](#5-restore-over-ble--dump2ufwpy--qix-rcsp-flash),
  which only rewrites the CODE region and deliberately leaves `key_mac`/resources alone.
- If `lsusb` still shows `4c4a:2942` after the reset, power-cycle the badge, re-enter UBOOT,
  and re-run step 2.

---

## 5. Restore over BLE — `dump2ufw.py` → `qix rcsp-flash`

No USB, no opening the case: turn the dump into a **vanilla** RCSP `.ufw` and flash it from the
custom firmware over the native RCSP OTA. One command from the repo root — see
[`tools/ufw-repack/README.md`](../tools/ufw-repack/README.md):

```sh
python3 tools/ufw-repack/dump2ufw.py oem-dump.bin oem-restore.ufw
```

The `dump2ufw.py` loader rewrites the CODE region (`[0, 0x17E000)`) **in place**, leaving the
resource partitions (`ui_res` / `virfat` / `data`) untouched. Flash it with
**`qix rcsp-flash <MAC> oem-restore.ufw`** from the badge's Update screen — the step-by-step
[custom → OEM](ota-howto.md#4-custom--oem-restore-the-stock-firmware) recipe lives in
[ota-howto.md](ota-howto.md).