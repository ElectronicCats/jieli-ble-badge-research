# jieli-ble-badge-research

Reverse-engineering the **JieLi AC707N / BR35 e-badge (E87)** — round 360×360
RGB565 display, AXS5106L capacitive touch, 2 ADKEY buttons, 4 MB NOR flash, BLE —
and flashing **100% custom firmware onto a sealed OEM badge over BLE**: no USB, no
opening the case.

> ⚠️ **Security research / educational use only.** Read **[DISCLAIMER.md](DISCLAIMER.md)**
> before using anything here, and use it **only on hardware you own**. Disclosure
> posture is in **[SECURITY.md](SECURITY.md)**.

## The result (the chain that works)

The OEM badge only exposes an **app-only** BLE OTA (the path the ZRun app uses). This
turns that single foothold into full control, entirely over BLE:

```
OEM app-only OTA ──► minimal loader ("STAGER" bridge) ──► custom firmware
                     raw-flash 0xD0–0xD3 + apply 0xD4       (your UI / content)
```

- The badge's **MAC is constant** across all three stages (it lives in the preserved
  `key_mac`); only the advertised **name** changes `OEM → STAGER → EC-BADGE`, so all
  tooling addresses the badge **by MAC**, never by name.
- Resources are written via raw-flash opcodes; the code image is applied through the
  loader's `0xD4` path (uboot `lcflash`).

## Repository map

| Dir | Purpose |
|---|---|
| `tools/` | Standalone tools + BLE client libraries (see table). |
| `scripts/` | Build & utility CLIs — JieLi SDK build, UFW repack, chipkey inject, HW capture. See `scripts/README.md`. |
| `patches/` | Incremental JieLi-SDK `.patch` files (`m1/` active, `deprecated/` archived). |
| `firmware/` | Self-built AC707N images: the M1 firmware and the STAGER loader. |
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

- **Flash custom firmware over BLE** (from any state): `tools/qix-ble` →
  `qix provision …` (OEM → loader → custom) or `qix deploy-fw …` (loader → custom).
- **Build firmware**: the JieLi SDK lives outside this repo; build with the
  `scripts/build-jieli-ac707n*.sh` helpers and the `patches/m1/` patch set.
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
