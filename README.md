# jieli-ble-badge-research

Reverse-engineering the **JieLi AC707N / BR35 e-badge (E87)** — round 360×360
RGB565 display, AXS5106L capacitive touch, 2 ADKEY buttons, 4 MB NOR flash, BLE —
and flashing **100% custom firmware onto a sealed OEM badge over BLE**: no USB, no
opening the case.

> ⚠️ **Security research / educational use only.** Read **[DISCLAIMER.md](DISCLAIMER.md)**
> before using anything here, and use it **only on hardware you own**. Disclosure
> posture is in **[SECURITY.md](SECURITY.md)**.

## 🎤 Talk

Presented at **DragonJarCon** (BSides LatAm) —
*"Compré un e-badge otaku de 10 dólares y terminé hackeándolo"* (talk in Spanish).
The research started from **DragonJAR SAS**'s APK-auditing skill
([Android-Pentesting-Skill](https://github.com/DragonJAR/Android-Pentesting-Skill)).

## The result (the chain that works)

The OEM badge only exposes an **app-only** BLE OTA (the path the ZRun app uses). This
turns that single foothold into full control, entirely over BLE:

```
OEM ──► custom firmware      ( qix flash --oem  ·  Qix FD00 0xC0  ·  fw-custom )
```

- The custom firmware is packaged **fw-custom** (your `app.bin` spliced into an OEM
  `.ufw`, keeping the **OEM uboot**). A single `qix flash --oem` hands it to the resident
  OEM uboot, which rewrites the CODE partition and boots the custom — no cable, no loader.
- The badge's **MAC is constant** (it lives in the preserved `key_mac`); only the advertised
  **name** changes `OEM → custom firmware`, so all tooling addresses the badge **by MAC**, not by name.
- **Legacy note:** an older `provision`/`deploy-fw` **STAGER** loader chain
  (`OEM → loader → custom`, raw-flash `0xD0–0xD3` + apply `0xD4`) is **no longer used** — kept
  only for a custom that must write the separate `ui_res`/`virfat` resource partitions.
  See [docs/ota-howto.md](docs/ota-howto.md).

## Repository map

| Dir | Purpose |
|---|---|
| `tools/` | Standalone tools + BLE client libraries (see table). |
| `scripts/` | Build & utility CLIs — JieLi SDK build, UFW repack, chipkey inject, HW capture. See `scripts/README.md`. |
| `patches/` | JieLi-SDK `.patch` files: `badge/` (the badge-menu firmware, applied on the SDK submodule — see `patches/badge/README.md`), `m1/` (active feature patches), `deprecated/` (archived). |
| `firmware/` | The self-built custom firmware (`custom-fw-ac707n.ufw`). |
| `docs/` | Chip-level how-tos (SDK build, BLE capture, M1 build) + upstream-contribution notes. |

## Tools (`tools/`)

| Tool | What it is | Status |
|---|---|---|
| **qix-ble** | Main BLE client: Qix (FD00) + RCSP, auth handshake, OTA flash, stager (`rawflash` / `deploy-fw` / `provision`), btsnoop parse. | active (core) |
| **ufw-repack** | Pure-Python UFW pack/parse (`repack_ufw.py`, `parse_outer.py`, `swap_app.py`, `transplant_ota.py`, `make_app_only_ufw.py`) + `wrap_qix.py` (27-byte Qix OTA wrapper). No wine needed. | active |
| **baji-ble** | BLE client for the JieLi **Baji** command path (NUS + AE00/RCSP) on the DG01 / SuperBand ("Legend Smartwatch") wristband. | active |
| **btsnoop-parser** | Android HCI snoop → ATT writes/notifications (`parse_att.py`), for protocol RE. | active |
| **jl-flash-decrypt** | Decrypt / re-encrypt the SFC-scrambled CODE region of a BR35 flash dump. | active |
| **chipkey** | Device chipkey blob + format docs. ⚠ see Security note below. | reference |
| **blimit-re** | Decrypt `blimit.bin` (144-byte LFSR cipher). | experimental |
| **pi-pico-jl-dongle** | DIY USB-ISP recovery dongle (Raspberry Pi Pico). | prototype |
| **yunthinker-scrape** | Idempotent bash+curl crawler for public JieLi datasheet PDFs. | reference |

## Third-party dependency

The low-level crypto helpers (`jltech`: cipher / CRC / chipkey codecs, from the
`jl-misctools` project) are **obtained separately** and not vendored here (see
`.gitignore` → `tools/community-re/`). `tools/jl-flash-decrypt` carries standalone
cipher copies so it runs without that mirror present.

## Quick start

- **Flash custom firmware over BLE** (OEM→custom and custom→custom): `tools/qix-ble` →
  `qix flash <MAC> firmware/custom-fw-ac707n.ufw --oem`. Full command reference and
  prerequisites: **[docs/ota-howto.md](docs/ota-howto.md)**. (The legacy `qix provision` /
  `deploy-fw` STAGER chain is no longer used — see the doc.)
- **Build firmware**: the JieLi SDK is the git submodule at
  `tools/community-re/jieli-sdks/e_badge_707_sdk_200` (JieLi's private GitLab, pinned at the vanilla
  `main` commit — obtained separately, never redistributed). Init it, apply the badge patch, and
  build — full steps in **[patches/badge/README.md](patches/badge/README.md)**:
  ```sh
  git submodule update --init tools/community-re/jieli-sdks/e_badge_707_sdk_200
  git -C tools/community-re/jieli-sdks/e_badge_707_sdk_200 apply "$PWD/patches/badge/badge-menu.patch"
  export PATH="/opt/jieli/pi32v2/bin:$PATH" && ulimit -n 65536
  ( cd tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK && make clean && BADGE_CHIPKEY=9847 make MENU=1 )
  ```
  (The `scripts/build-jieli-ac707n*.sh` helpers + `patches/m1/` set remain for the vanilla/M1 variants.)
- **Repack / inject into a UFW**: `tools/ufw-repack/` (pure Python).
- **USB recovery** (if a flash bricks the display path): write a byte-exact dump of
  **your own** unit back over USB-ISP. The OEM firmware is **not distributed** — dump
  it from your own unit first.

## Security note ⚠ — device chipkey

`tools/chipkey/` and the `firmware/` images embed the on-device flashing key
(`0x9847`) for AC707N-class units, because the repack / inject / flash tooling needs
it. It is published here as a **research artifact** — see **[DISCLAIMER.md](DISCLAIMER.md)**.
No OEM firmware, application binaries, application/cloud crypto keys, or vendor
datasheets are included in this repository.

## Authors

- **Andrés Sabas** ([@sabas1080](https://github.com/sabas1080)) — [Electronic Cats](https://electroniccats.com)
- **Heikki Perea** ([@HeikkiRadu](https://github.com/HeikkiRadu)) — [Electronic Cats](https://electroniccats.com)
