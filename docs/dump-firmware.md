# Dump the stock OEM firmware (wired, UBOOT)

How to take a **byte-exact dump** of the badge's 4 MB NOR flash, before flashing any custom
firmware — the raw material for the [custom → OEM](ota-howto.md#4-custom--oem-restore-the-stock-firmware)
restore leg and for USB-ISP recovery.

This is the *only* step of the whole flow that is **not over BLE**: it needs physical access
to the chip's `DP`/`DM` pads and a way to put it in USB download (UBOOT) mode — a vendor USB
updater, a DIY adapter such as the prototype [`tools/pi-pico-jl-dongle`](../tools/pi-pico-jl-dongle),
or a unit that fails to boot on its own.

> ⚠️ Security research / educational use only — on hardware you own. See
> [DISCLAIMER.md](../DISCLAIMER.md).

---

## 1. Why you need a dump

- The **custom → OEM** restore (`qix rcsp-flash`) needs the **stock** image as its input, and
  **no OEM firmware is distributed here**.
- A failed OTA can require USB-ISP recovery, which means writing a byte-exact dump of *your*
  unit back.
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

## 4. Turn the dump into a restore `.ufw`

One command, from the repo root — see [`tools/ufw-repack/README.md`](../tools/ufw-repack/README.md):

```sh
python3 tools/ufw-repack/dump2ufw.py oem-dump.bin oem-restore.ufw
```

The result is a **vanilla** RCSP `.ufw` (no Qix wrapper) whose loader rewrites the CODE region
(`[0, 0x17E000)`) **in place**, leaving the resource partitions (`ui_res` / `virfat` / `data`)
untouched. Flash it over BLE with **`qix rcsp-flash <MAC> oem-restore.ufw`** from the custom
firmware — see the [custom → OEM](ota-howto.md#4-custom--oem-restore-the-stock-firmware)
recipe in [ota-howto.md](ota-howto.md).