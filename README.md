# jieli-ble-badge-research

Reverse-engineering a **$10 JieLi AC707N / BR35 e-badge (E87)** — round 360×360 RGB565 display,
AXS5106L capacitive touch, 2 ADKEY buttons, 4 MB NOR flash, BLE — and flashing **100 % custom
firmware onto a sealed OEM badge over BLE**: no USB, no opening the case, no loader.

<!-- TODO: photo of the badge running the custom firmware goes here, plus a screenshot of the
     BLE-scanner screen next to "Custom firmware features". -->

> ⚠️ **Security research / educational use only.** Read **[DISCLAIMER.md](DISCLAIMER.md)**
> before using anything here, and use it **only on hardware you own**. Disclosure
> posture is in **[SECURITY.md](SECURITY.md)**.

---

## The badge

A cheap consumer "e-badge" / otaku name badge built on JieLi's **AC707N** (BR35 family) BT/BLE
SoC. Confirm you have the right unit before flashing anything:

| | |
|---|---|
| SoC | JieLi **AC707N**, BR35 family (`CONFIG_CPU_BR35`) |
| Display | 360×360 round, RGB565, QSPI |
| Touch | **AXS5106L** capacitive (*not* CST816D — a common near-identical variant) |
| Buttons | 2 × ADKEY |
| Flash | 4 MB NOR |
| PCB marking | `qx7613_v1.3` |
| USB PID | **1558** |
| Chipkey | `0x9847` on AC707N-class units — see the [security note](#security-note--device-chipkey) |
| Stock app | **ZRun** (the phone app whose BLE OTA path this work hijacks) |

A different PID needs a different chipkey and `isd_config.ini`; **do not reuse `0x9847`** on one.

## What this does

The OEM badge exposes exactly one foothold: an **app-only** BLE OTA (the path the ZRun app
uses). This turns that into full control, entirely over BLE:

```
OEM ──► custom firmware      ( qix flash --oem  ·  Qix FD00 0xC0  ·  fw-custom )
```

- The custom firmware is packaged **fw-custom** — your `app.bin` spliced into an OEM `.ufw`, which
  keeps the **OEM uboot** byte-identical. A single `qix flash --oem` hands it to the resident OEM
  uboot, which rewrites the CODE partition and boots the custom. No cable, no loader.
  A raw SDK `make` image carries the *native* uboot instead, which cannot do that rewrite — flash
  one and the badge bricks silently at apply.
- The badge's **MAC is constant** (it lives in the preserved `key_mac`); only the advertised
  **name** changes — the custom firmware advertises as **`EC-BADGE`**. All tooling addresses the
  badge **by MAC**, never by name.

**Validation status:** both legs — `OEM → custom` and `custom → custom` — have been run
end-to-end on real hardware. The prebuilt image in [`firmware/`](firmware) is the one that was
flashed.

## Requirements

**To flash** (BLE, no cable):

- The badge, charged to **≥ 30 %** or on USB for the whole flash window.
- Python 3.10+ and `pip install -e tools/qix-ble`.
- A BLE adapter. The client is built on [`bleak`](https://github.com/hbldh/bleak), so the
  transport runs on **Linux (BlueZ), macOS (CoreBluetooth) and Windows (WinRT)** — every
  BlueZ/DBus helper inside it is gated behind `sys.platform == "linux"` and is a no-op elsewhere.
  **Linux is what has been validated on hardware**, and the recipes in
  [ota-howto.md](docs/ota-howto.md) are the Linux ones; their `rfkill` / `hciconfig` /
  `qix cleanup` / `QIX_CONNECT_VIA_DBUS=1` steps are BlueZ workarounds that macOS and Windows
  neither need nor have.

**To build from source:** **Linux x86-64 only** — the JieLi toolchain and post-build tools are
Linux ELF binaries. Also ~3 GB free disk and `ulimit -n` ≥ 65536. Full list in
[docs/build-badge-firmware.md](docs/build-badge-firmware.md#1-prerequisites).

## Before you flash

⚠️ A failed OTA can require USB-ISP recovery. Every one of these will make a flash fail, and some
fail *silently*:

- **Battery ≥ 30 %, or USB connected for the whole window.** A brown-out while the code region is
  being rewritten bricks the unit.
- **Turn the phone's Bluetooth fully off.** The badge accepts **one** connection; closing the ZRun
  app is not enough.
- **Do not run `qix dump --auth` since the last power-cycle.** It sets a TEST_MODE flag that makes
  the badge **silently reject** `REQ_UPDATE`. Power-cycle to clear it.
- **Never flash a raw SDK `make` image** as the OTA payload — native uboot, silent brick.
- **The ~1 MB transfer takes several minutes. Do not interrupt it.**
- **Take a dump of your own unit first.** Recovery means writing a byte-exact dump of *your* badge
  back over USB-ISP; no OEM firmware is distributed here. The DIY dongle for that
  ([`tools/pi-pico-jl-dongle`](tools/pi-pico-jl-dongle)) is still a prototype.

## Quick start

- **Flash the prebuilt firmware over BLE** (OEM→custom and custom→custom) — read
  [Before you flash](#before-you-flash) first:
  ```sh
  pip install -e tools/qix-ble                 # installs the `qix` client
  qix scan                                     # find your badge's MAC
  qix flash <MAC> firmware/custom-fw-ac707n.ufw --oem
  ```
  On a **Linux/BlueZ** host the bare command above tends to hang; the validated recipe adds an
  adapter reset plus a deterministic DBus connect. Full reference:
  **[docs/ota-howto.md](docs/ota-howto.md)**. (The legacy `qix provision` / `deploy-fw` STAGER
  chain is no longer used — see the doc.)

- **Build the firmware from source** — two commands:
  ```sh
  scripts/setup-badge-build.sh      # once: toolchain + SDK + patch + post-build tools + chipkey
  scripts/build-badge-fw.sh --ota   # each build: app.bin + a flashable BLE-OTA .ufw
  ```
  The custom firmware lives as a patch ([`patches/badge/badge-menu.patch`](patches/badge)) on top
  of the vanilla JieLi SDK, which is the git submodule at
  `tools/community-re/jieli-sdks/e_badge_707_sdk_200` (pinned at the vanilla `main` commit and
  **clonable anonymously** from JieLi's GitLab — never redistributed from here). Full guide,
  provenance of every downloaded piece, and a troubleshooting table:
  **[docs/build-badge-firmware.md](docs/build-badge-firmware.md)**. (The
  `scripts/build-jieli-ac707n*.sh` helpers + [`patches/m1/`](patches/m1) set remain for the
  vanilla/M1 variants.)

- **Update the lock-screen image** (over BLE, no reflash): `tools/make_lockimg.py` +
  `qix rawflash <MAC> lockimg.bin --addr 0x300000 --verify --reboot`. See
  **[docs/lock-image.md](docs/lock-image.md)**. (`rawflash` talks to the `0xD0–0xD4` raw-flash
  service that the *custom* firmware serves on its Update screen — not the legacy STAGER loader.)

- **Repack / inject into a UFW**: [`tools/ufw-repack/`](tools/ufw-repack) (pure Python, no wine).

## Custom firmware features

The **DragonJAR badge** runs this custom firmware to explore and experiment with Bluetooth —
research tools plus an on-device UI driven entirely from the badge. Its features:

- **Bluetooth Spoofing** — pick a detected BLE device and clone its advertisement, for
  experimentation and auditing.
- **Bluetooth Mouse** — the badge acts as a BLE-HID mouse (the touchscreen is the trackpad) to
  drive compatible hosts.
- **Bluetooth Scanner** — scans nearby BLE devices; pick one to browse its GATT services and
  characteristics.
- **Interactive GUI** — LVGL touch menu with direct access to the Bluetooth tools and the device
  settings.
- **Physical-button navigation** — move between options, select, and go back using the badge's
  buttons.
- **Lock screen with gesture** — on wake from suspend it shows a lock screen, unlocked with a touch
  gesture (a swipe). The wallpaper is updatable over BLE — see
  [docs/lock-image.md](docs/lock-image.md).
- **Suspend** — the device suspends and wakes on any physical button.
- **Brightness** — adjustable from the settings menu.
- **Update mode** — an option to enter firmware-update mode (BLE OTA).
- **Open research firmware** — part of a JieLi e-badge reverse-engineering project, with the tools
  and docs published here to explore how it works and build on it.

> The on-device UI is in **Spanish** (e.g. the update screen is **Ajustes → Actualizar**).

## Documentation

| Doc | What |
|---|---|
| [build-badge-firmware.md](docs/build-badge-firmware.md) | **Build the custom firmware from source** — prerequisites, provenance of every downloaded piece, troubleshooting |
| [ota-howto.md](docs/ota-howto.md) | **Flash it over BLE** — preflight, `OEM → custom`, `custom → custom`, protocol details |
| [lock-image.md](docs/lock-image.md) | Update the lock-screen wallpaper over BLE, no reflash |
| [jieli-sdk-build.md](docs/jieli-sdk-build.md) | The older wine + docker flow for the **vanilla** SDK |
| [build-m1-firmware.md](docs/build-m1-firmware.md) | The **M1** variant build (a different JieLi target tracked in this repo) |
| [captura-ble-howto.md](docs/captura-ble-howto.md) | Capturing the phone↔badge BLE traffic (Android HCI snoop) for protocol RE |
| [upstream-contributions.md](docs/upstream-contributions.md) | Notes on contributing findings back to other projects |

## Repository map

| Dir | Purpose |
|---|---|
| [`tools/`](tools) | Standalone tools + BLE client libraries (see the table below). |
| [`scripts/`](scripts) | Build & utility CLIs — badge build, JieLi SDK build, UFW repack, chipkey inject, HW capture. See [`scripts/README.md`](scripts/README.md). |
| [`patches/badge/`](patches/badge) | The badge-menu firmware, as a patch applied onto the SDK submodule. See [its README](patches/badge/README.md). |
| [`patches/m1/`](patches/m1) | Active feature patches for the M1 variant (`deprecated/` holds archived ones). |
| [`firmware/`](firmware) | The self-built custom firmware (`custom-fw-ac707n.ufw`, 1 079 363 B). |
| [`docs/`](docs) | Chip-level how-tos — see the table above. |
| `firmware-builds/` | Gitignored. Where `build-badge-fw.sh --ota` writes timestamped images. |

## Tools (`tools/`)

| Tool | What it is | Status |
|---|---|---|
| [**qix-ble**](tools/qix-ble) | Main BLE client: Qix (FD00) + RCSP, auth handshake, OTA flash, raw-flash, btsnoop parse. Cross-platform (`bleak`). `provision` / `deploy-fw` are **legacy**. | active (core) |
| [**ufw-repack**](tools/ufw-repack) | Pure-Python UFW pack/parse + the 27-byte Qix OTA wrapper. No wine needed. | active |
| [**baji-ble**](tools/baji-ble) | BLE client for the JieLi **Baji** command path on the DG01 / SuperBand wristband — a *sibling* JieLi device, kept here for protocol comparison. | active |
| [**btsnoop-parser**](tools/btsnoop-parser) | Android HCI snoop → ATT writes/notifications, for protocol RE. | active |
| [**jl-flash-decrypt**](tools/jl-flash-decrypt) | Decrypt / re-encrypt the SFC-scrambled CODE region of a BR35 flash dump. | active |
| [**chipkey**](tools/chipkey) | Device chipkey blob + format docs. ⚠ see the [security note](#security-note--device-chipkey). | reference |
| [**blimit-re**](tools/blimit-re) | Decrypt `blimit.bin` (144-byte LFSR cipher). | experimental |
| [**pi-pico-jl-dongle**](tools/pi-pico-jl-dongle) | DIY USB-ISP recovery dongle (Raspberry Pi Pico). Incomplete — most phases pending. | prototype |
| [**yunthinker-scrape**](tools/yunthinker-scrape) | Idempotent crawler for public JieLi datasheet PDFs. | reference |

## Glossary

| Term | What it means here |
|---|---|
| **JieLi (杰理)** | The SoC vendor. **AC707N** is the chip; **BR35** its family. |
| **E87** | The badge model. Also called the *DragonJAR badge* in this repo; it advertises as `EC-BADGE` once flashed. |
| **ZRun** | The stock phone app. Its BLE OTA is the only foothold the OEM firmware exposes. |
| **Qix** | The OEM's proprietary OTA protocol on BLE service **`FD00`**; opcode **`0xC0`** starts an app-only update. |
| **RCSP** | JieLi's generic command protocol (service `AE00`), which Qix rides on. |
| **`.ufw`** | JieLi's update-image container: a header + CRC-checked entry list wrapping `flash.bin`, `ota.bin`, config… |
| **uboot** | The resident bootloader that performs the CODE-partition rewrite. The **OEM** one can do it over BLE; the SDK-native one cannot. |
| **fw-custom** | Not a firmware — a *packaging*: your `app.bin` spliced into an OEM `.ufw` so the OEM uboot survives. |
| **`app.bin`** | The raw application image the SDK build emits. The only input the BLE OTA path needs. |
| **chipkey** | A per-chip flashing key (`0x9847` here) that `isd_download` embeds; a burned unit rejects an image packed without it. |
| **`key_mac`** | The preserved flash region holding the BT MAC — why the MAC survives a reflash. |
| **STAGER** | The **legacy** loader chain (`OEM → loader → custom`, raw-flash `0xD0–0xD3` + apply `0xD4`). Superseded by the direct `flash --oem`. |
| **`ui_res` / `virfat`** | Separate resource partitions the app-only OTA cannot reach. The custom embeds its resources in the code image instead, so it doesn't need them. |
| **USB-ISP** | The vendor's USB recovery path — the only way back from a brick. |
| **LVGL** | The embedded GUI library the custom firmware's touch menu is built on. |
| **pi32v2** | JieLi's LLVM/clang fork and target triple; the cross-toolchain the SDK builds with. |

## Contributing

The firmware code added by the patch lives, in order of size:

| File | Added | What |
|---|---|---|
| `SDK/apps/common/ui/lvgl_v8/lvgl_main.c` | +1358 | The badge menu, spoofer, mouse and scanner screens |
| `SDK/apps/common/third_party_profile/jieli/rcsp/qix_ota_server.c` | +577 | The Qix FD00 OTA server the custom serves |
| `SDK/apps/watch/board/br35/board_ac707n_demo/board_ac707n_demo.c` | +438 | Board bring-up (display, touch, keys) |
| `SDK/apps/common/third_party_profile/jieli/rcsp/qix_raw_flash.c` | +351 | The `0xD0–0xD4` raw-flash service |

(Of the patch's ~15.5 k added lines, ~10.7 k are two embedded image headers; the real code delta
is ~4.8 k.) Edit inside the SDK worktree, rebuild, then regenerate the patch — recipe in
[docs/build-badge-firmware.md §6](docs/build-badge-firmware.md#6-modifying-the-firmware).

Tests — **there is no CI yet, so run them before opening a PR**:

```sh
( cd tools/qix-ble  && python3 -m pytest tests/ -q )   # 250 tests, mocked transport
( cd tools/baji-ble && python3 -m pytest tests/ -q )   # 61 tests
```

They cover protocol/packing logic against a mocked transport — **no test touches real BLE or real
hardware**, so a green suite is not evidence that a flash works.

## Security note — device chipkey

⚠ `tools/chipkey/` and the `firmware/` images embed the on-device flashing key
(`0x9847`) for AC707N-class units, because the repack / inject / flash tooling needs
it. It is published here as a **research artifact** — see **[DISCLAIMER.md](DISCLAIMER.md)**.
No OEM firmware, application binaries, application/cloud crypto keys, or vendor
datasheets are included in this repository.

## Dependencies

**The build and BLE-flash paths are self-contained** — clone the repo and go. The JieLi
crypto helpers (`jltech`: cipher / CRC / chipkey codecs, reverse-engineered from the
`jl-misctools` project) are **vendored** as pure Python in `tools/ufw-repack/jltech/`, with no
`crcmod` and no external checkout; `tools/jl-flash-decrypt` carries its own standalone copies.

One script still needs the external `jl-misctools` mirror (gitignored under
`tools/community-re/`, obtained separately): **`scripts/gen_chipkey_keyfile.py`**, which uses
`jltech.chipkeyfile` — a module that is not vendored here. It tells you so when you run it.

## Talk

**Upcoming** at **DragonJarCon** —
*"Compré un e-badge otaku de 10 dólares y terminé hackeándolo"*
("I bought a $10 otaku e-badge and ended up hacking it"; talk in Spanish).
<!-- TODO: add the date and, once it airs, the recording / slides link. -->

The research started from **DragonJAR SAS**'s APK-auditing skill
([Android-Pentesting-Skill](https://github.com/DragonJAR/Android-Pentesting-Skill)).

## Authors

- **Andrés Sabas** ([@sabas1080](https://github.com/sabas1080)) — [Electronic Cats](https://electroniccats.com)
- **Heikki Perea** ([@HeikkiRadu](https://github.com/HeikkiRadu)) — [Electronic Cats](https://electroniccats.com)

## License

MIT — see [LICENSE](LICENSE).
