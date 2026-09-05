# scripts/

Loose helper scripts for building, packaging, and patching JieLi AC707N/BR35
("E87") firmware, plus a couple of environment-setup and mirroring utilities.

All scripts anchor themselves to the repo root via `$(dirname "$0")/..` (bash) or
`Path(__file__).parents[...]` (python), so they can be invoked from anywhere;
paths below are shown relative to the repo root.

## Typical order

1. `setup-jieli-toolchain.sh` — install the pi32v2 LLVM toolchain (once).
2. `setup-jieli-lvgl-port.sh` — assemble `lv_port_pc_vscode/` from JieLi's public LVGL repos (once, only for the `ac707n_watch_lvgl` SDK).
3. `build-jieli-ac707n.sh` — compile the SDK → `sdk.elf`.
4. `build-jieli-ac707n-ufw.sh` — package `sdk.elf` → flashable `update.ufw`.
5. Optionally `inject_chipkey.py` / `patch_config_dat.py` to post-process the UFW, or `build-vector-a.sh` to wrap it with a Qix OTA header.

## Setup

| Script | What it does | Invocation |
|---|---|---|
| `setup-jieli-toolchain.sh` | Downloads (cache-aware, sha256-checked) and extracts the JieLi pi32v2 LLVM toolchain into `tools/jieli-toolchain/pi32v2/`, then creates the `/opt/jieli` symlink the SDK Makefile expects (needs `sudo` only for the symlink). Idempotent; writes `VERSION.txt`. | `scripts/setup-jieli-toolchain.sh` |
| `setup-jieli-lvgl-port.sh` | Clones JieLi's public `lvgl_portable_code` (wrapper) + `lvgl_core` (LVGL v8) into `ac707n_watch_lvgl/SDK/lv_port_pc_vscode/`, validating the exact paths the SDK Makefile needs. Idempotent. | `scripts/setup-jieli-lvgl-port.sh` |

## Build

| Script | What it does | Invocation |
|---|---|---|
| `build-jieli-ac707n.sh` | Compiles a JieLi SDK (`make clean && make`, with `ulimit -n` raised for LTO) and copies `sdk.elf`/`sdk.ufw` + sha256 + toolchain metadata into a timestamped `firmware-builds/<sdk>-vanilla-<rev>-<ts>/`. Tolerates the POST-BUILD `download.sh` failing as long as `sdk.elf` was linked. Requires `setup-jieli-toolchain.sh` first. | `scripts/build-jieli-ac707n.sh [--sdk-source ac707n_watch_lvgl\|e_badge_707_sdk_200]` (default `ac707n_watch_lvgl`) |
| `build-jieli-ac707n-ufw.sh` | Packages a vanilla `sdk.elf` end-to-end into a flashable `update.ufw`: objcopy of 23 ELF sections → `lz4_packet` → `app.bin` → `wine isd_download.exe` inside the `scottyhardy/docker-wine` container. Validates the UFW magic/size and reports byte 8 (vanilla `fd` vs ZRun OEM `fb`). Output to `firmware-builds/<sdk>-ufw-<rev>-<ts>/`. Needs Docker (via `sg docker`) + the wine image pulled. | `scripts/build-jieli-ac707n-ufw.sh [--sdk-source ac707n_watch_lvgl\|e_badge_707_sdk_200]` (default `ac707n_watch_lvgl`; `STAGE` env overrides the scratch dir) |
| `build-vector-a.sh` | "Vector A" end-to-end pipeline: builds the vanilla UFW (calls `build-jieli-ac707n-ufw.sh`), then wraps it with the 27-byte Qix OTA header via `tools/ufw-repack/wrap_qix.py`, producing a `.ufw` flashable over BLE with `UpdateManager.startUpdate`. Output: `firmware-builds/vector-a-<sdk>-<ts>.ufw`. | `scripts/build-vector-a.sh` (env vars: `SDK_SOURCE` default `ac707n_watch_lvgl`, `VERSION` default `99.99.99.99`) |

## Firmware patching / chipkey

| Script | What it does | Invocation |
|---|---|---|
| `gen_chipkey_keyfile.py` | Generates an `isd_download -key` chipkey file from a 16-bit chipkey value (community-reversed "chipkeyfile" format, AES-ECB + CRC32). Deterministic (fixed embedded AES key → git-stable output); self-checks by round-tripping. Imports `jltech.chipkeyfile` from the vendored `jl-misctools` (needs `pycryptodomex`, `crcmod`). | `python3 scripts/gen_chipkey_keyfile.py <chipkey> [-o <file>]` e.g. `... 0x9847 -o tools/chipkey/chipkey_9847.bin` (no `-o` → stdout) |
| `inject_chipkey.py` | Patches a self-built UFW in-place to swap the chipkey embedded in the nested JLFS `top/isd_config.ini` blob (bypassing `isd_download -key`), then recomputes all affected CRCs (JLFS entry, flash.bin entry, entry-list). Can copy an OEM blob verbatim or synthesize one deterministically; optionally also re-scrambles the app area. | `python3 scripts/inject_chipkey.py --ufw <in> --output <out> (--chipkey 0x9847 \| --source-isd-config <ini>) [--seed 0xC0DE] [--no-autodetect] [--verify-chipkey <k>] [--skip-app-rescramble]` |
| `patch_config_dat.py` | Patches a JCRT `config.dat` in place (or writes `.patched.dat`), recomputing per-item, item-head, and self CRCs. Can set the `ver_info` VER field or replace a named item's full payload (same size only). | `python3 scripts/patch_config_dat.py <config.dat> [--ver-info-ver 0x9901] [--set-payload <name> <hex>] [--in-place] [--dry-run]` |

## Repack (legacy)

| Script | What it does | Invocation |
|---|---|---|
| `repack-jieli-ufw.sh` | **LEGACY.** Repacks a JieLi BR35 `.ufw` from a `fwunpack_newfw.py` unpack dir (+ per-file overrides) via `wine isd_download.exe` in Docker, with optional chipkey `-key` and Qix wrapper. **The canonical repack path is now `tools/ufw-repack/repack_ufw.py`** (pure Python, no wine/Docker); prefer it. Kept for reference. | `scripts/repack-jieli-ufw.sh <input-unpack-dir> <output-dir> [--override files/X=path ...] [--stage DIR] [--chipkey-bin FILE] [--qix-version X.Y.Z]` |

## Misc

| Script | What it does | Invocation |
|---|---|---|
| `mirror-jieli-sdks-to-github.sh` | Mirrors each JieLi SDK clone under `tools/community-re/jieli-sdks/*` to a private GitHub repo: creates the repo (`gh`), adds a `github` remote (leaving `origin` at gitlab.zh-jieli.com), unshallows if needed, and pushes all refs + tags. Idempotent/resumable; logs a TSV summary under `logs/`. | `scripts/mirror-jieli-sdks-to-github.sh [--dry-run] [--limit N] [--only NAME]` |

## Subdirectories

- `hw-vector-b/` — USB-capture scripts for probing the badge's MaskROM/USB-ISP behavior (`capture.sh`, `poll.sh`, `auto-dump.sh`). See `hw-vector-b/README.md`.
- `maskrom-analysis/` — binary-analysis scripts that cross-reference MaskROM stub addresses and the SCSI/USB-update hook against `app.bin` dumps (`maskrom_xref.py`, `check_scsi_hook.py`). See `maskrom-analysis/README.md`.
