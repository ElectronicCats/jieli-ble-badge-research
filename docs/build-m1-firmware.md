# Build M1 self-built firmware — runbook

Operational guide to reproduce the M1 self-built firmware build for the ZRun E87 badge (PID 1558, SoC AC707N/BR35) and a reference for future agents who pick up this work. Product of the work persisted in `feature/m1-self-built` (session 2026-05-17).

**Output**: UFW flashable via OTA BLE Vector A.1 with 3 visible signs (physical backlight toggle, BT name "M1-E87", `firmwa_version 0x9901`).

**Does NOT produce**: LCD render, functional touch, OEM parity (Alipay/health/notify/AI), RCSP client OTA (M2 task).

> **NOTE (current status)** — This guide documents the **M1 v1** build (`TCFG_UI_ENABLE=0`),
> which **bricked the badge on flash** (no BLE adv / no wake / no backlight post-flash).
> For that reason two of its patches (`sdk-ui-disable.patch` and `sdk-makefile-single-board.patch`)
> were moved to `patches/m1/deprecated/` — **do not apply them in new builds**.
> The replacement is **M1 v2** (`e_badge_707_sdk_200/board_ac707n_csc_demo`, `TCFG_UI_ENABLE=1`,
> LCD ST77916 explicit via `patches/m1/m1-csc-lcd-st77916.patch`). Design in
> `docs/superpowers/specs/2026-05-21-m1-v2-safe-build-design.md` and the rationale for the drop in
> `patches/m1/deprecated/README.md`. The tooling pipeline (steps 1-5, chipkey, Qix wrap)
> is still valid; what changed is the patch set and the SDK source.

---

## 1. Prerequisites

### System

- Linux x86_64 (tested on Ubuntu 24.04 with kernel 6.8)
- `bash`, `git`, `python3 ≥ 3.10`
- Docker daemon + `docker` group configured (script assumes `sg docker` is accessible)
- Wine docker image: `scottyhardy/docker-wine:stable` pulled

```bash
# Verify prerequisites
command -v sg && sg docker -c "docker version" && sg docker -c "docker image inspect scottyhardy/docker-wine:stable" >/dev/null
```

### JieLi pi32v2 toolchain

Symlink `/opt/jieli/pi32v2/` must point to a JieLi pi32v2 toolchain install (clang/objcopy/objdump/ld for target `pi32v2` `e_machine=0xF1`).

```bash
# Expected setup
bash scripts/setup-jieli-toolchain.sh  # creates the /opt/jieli symlink

# Verify
ls -l /opt/jieli/pi32v2/bin/{clang,objcopy,objdump,ld}
```

If `/opt/jieli` does not exist, `build-jieli-ac707n.sh` fails with `ERROR: /opt/jieli no es symlink`.

### Python deps

```bash
pip install --user texture2ddecoder llvmlite
# More deps via tools/qix-ble/pyproject.toml + community-re/jl-misctools
```

`jltech.crc` is imported from `tools/community-re/jl-misctools/firmware/` (cloned from upstream `kagaimiq/jl-misctools` — `scripts/inject_chipkey.py` and `scripts/patch_config_dat.py` add that path to `sys.path` automatically).

---

## 2. Initial setup

### Branch + patches to the gitignored SDK

```bash
git checkout feature/m1-self-built

# Apply the 5 patches to the SDK tree (gitignored, NOT committed from the SDK dir)
cd tools/community-re/jieli-sdks/ac707n_watch_lvgl
for p in ../../../../patches/m1/*.patch; do
    git apply "$p"
done
cd ../../../..

# Verify patches applied
grep "TCFG_UI_ENABLE 0" tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/apps/watch/board/br35/sdk_config.h
grep "m1_backlight_toggle_task" tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/apps/watch/app_main.c
grep "'M', '1', '-', 'E', '8', '7'" tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/apps/watch/user_cfg.c
grep "M1: forzar RCSP_MODE_EN" tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/apps/watch/include/app_config.h
```

**Active patches** in `patches/m1/` (the ones that remain after applying `patches/m1/*.patch`; the glob does not descend into `deprecated/`):
- `patches/m1/m1-backlight-toggle.patch` — `app_main.c` adds `m1_backlight_toggle_task` (PWM IO_LCD_PG every 500ms) + `task_create()` right before `os_start()`.
- `patches/m1/m1-bt-name.patch` — `user_cfg.c:37` `bt_cfg.edr_name` default `"YL-BR30" → "M1-E87"`.
- `patches/m1/m1-rcsp-force-ble.patch` — `app_config.h:178` forces `BT_AI_SEL_PROTOCOL = RCSP_MODE_EN` + `RCSP_CHANNEL_SEL = RCSP_USE_BLE`. Enables the BLE OTA recovery path post-flash. Cost: ~+55 KB in `app.bin` (+10.6%) from linking the RCSP server + `rcsp_update_*` handlers.
- `patches/m1/m1-csc-lcd-st77916.patch` — (M1 v2) `board_ac707n_csc_demo_cfg.h` selects the ST77916 LCD panel (`TCFG_LCD_SPI_ST77916_ENABLE 1`, `TCFG_LCD_GC9307_172X320 0`). Replaces the v1 UI=0 approach.

**Deprecated patches** in `patches/m1/deprecated/` — **do not apply** (they bricked the badge in v1, see `patches/m1/deprecated/README.md`):
- `patches/m1/deprecated/sdk-makefile-single-board.patch` — `Makefile` removes the `board_ac7074_demo` and `board_ac707n_csc_demo` .c files from the build (only `board_ac707n_demo` was compiled). Byte-identical housekeeping (the removed .c files were guarded with undefined `#ifdef CONFIG_BOARD_JL{xxx}_DEMO` → 0 bytes). No real value.
- `patches/m1/deprecated/sdk-ui-disable.patch` — `sdk_config.h` `TCFG_UI_ENABLE 1→0`. Disabled the LCD/touch/LVGL/JL_UI cascade, but also the init paths that start BLE adv → badge with no adv/wake/backlight post-flash.

> The "Verify patches applied" block above (grep `TCFG_UI_ENABLE 0`, etc.) corresponds to the **v1** set and **no longer applies** to the active v2 set.

### Patch to the SDK-shipped `config.dat`

`config.dat` is binary, a traditional patch does not apply. Use the script:

```bash
python3 scripts/patch_config_dat.py \
  tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/download/watch/config.dat \
  --ver-info-ver 0x9901 --in-place

# Verify (should say VER=0x9901, all CRCs OK)
# NOTE: the dump_config_dat.py parser lived in hw-sessions/ (gitignored, not versioned);
# that session tree is no longer in the repo. patch_config_dat.py recomputes and validates the
# CRCs internally, so this external verification step is optional.
```

---

## 3. Build pipeline (5 steps, 4 tools)

```
SDK source (with 6 patches: 5 SDK + 1 config.dat)
    │
    ▼ STEP 1: make + pi32v2/clang  (official JieLi)
    │ → sdk.elf (5.89 MB with RCSP forced; 5.47 MB without RCSP)
    │
    ▼ STEP 2: objcopy + lz4_packet  (official JieLi)
    │ → app.bin (582 KB with RCSP; 526 KB without)
    │
    ▼ STEP 3: wine isd_download.exe  (official JieLi)
    │ → update.ufw (chipkey=0xFFFF default, 1.44 MB with RCSP)
    │
    ▼ STEP 4: inject_chipkey.py  (post-build, ours)
    │ → m1-rcsp-0x9847.ufw (chipkey=0x9847 + app_area re-scrambled)
    │
    ▼ STEP 5: wrap_qix.py wrap  (ours)
    │ → m1-rcsp-0x9847.wrapped.ufw  ← FINAL ARTIFACT
```

### Step 1: Compile SDK

```bash
bash scripts/build-jieli-ac707n.sh 2>&1 | tee out/build-step1.log
```

**Time**: ~5-15 min (LTO link). **Output**:
- `tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/sdk.elf` (~5.47 MB with UI=0)

**The script will end with `make: *** [Makefile:1222: all] Error 127`. THAT IS EXPECTED.** The error is from the SDK's internal post-build script, which looks for tools that are not installed (`/opt/utils/report_segment_usage`, `lz4_packet`, `host-client`). The `sdk.elf` was already produced before the failure. **Check the wrapper's exit code, not make's**:

```bash
ls -la tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/sdk.elf
# size > 5 MB
```

Verify linked symbols (LTO drops unused; the M1 task must be present):

```bash
/opt/jieli/pi32v2/bin/objdump -t tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/sdk.elf \
  | grep -E "m1_backlight|power_gate_pwm"
# Expected:
#   0c00de30 l F .text  0000002a m1_backlight_toggle_task
#   0c00dd72 l F .text  000000be power_gate_pwm_init
```

If those symbols do NOT appear, LTO dropped them because there was no caller. Recheck that `task_create(m1_backlight_toggle_task, ...)` is in `app_main()` before `os_start()`.

### Step 2 + 3: Pack UFW

```bash
bash scripts/build-jieli-ac707n-ufw.sh 2>&1 | tee out/build-step23.log
```

**Time**: ~10-30 seconds. **Output**:
- `firmware-builds/ac707n_watch_lvgl-ufw-<sdk-rev>-<ts>/update.ufw` (~1.33 MB)
- `firmware-builds/.../app.bin` (~526 KB)
- `firmware-builds/.../sha256sums.txt`

The script prints at the end:
```
[ufw:ac707n_watch_lvgl] OK — .ufw build completo:
[ufw:ac707n_watch_lvgl]   output:      firmware-builds/.../update.ufw
[ufw:ac707n_watch_lvgl]   size:        1330656 bytes
[ufw:ac707n_watch_lvgl]   byte 8:      fb
[ufw:ac707n_watch_lvgl]   sha256:      <hash>
```

Capture the output path for Step 4. `byte 8 = fb` (UI=0 + UFW M1) or `fd` (vanilla UI=1) — both are legitimate, it depends on the boot flag.

**The wine isd_download.exe prints `exit 245`. THAT IS EXPECTED.** It is a crash in the .exe's post-success cleanup under wine; the `.ufw` was already produced. The wrapper detects that `update.ufw` exists + size > 50KB and reports OK.

### Step 4: Inject chipkey 0x9847

`isd_download.exe` packs with chipkey=0xFFFF (SDK default) because we do not pass it `-key L3KEY` (PEM signed by JieLi, not publicly available). Post-patch:

```bash
UFW=$(find firmware-builds -name update.ufw -newer out/build-step23.log | head -1)

python3 scripts/inject_chipkey.py \
  --ufw "$UFW" \
  --output out/m1-rcsp-0x9847.ufw \
  --chipkey 0x9847
```

**Expected output** includes:
```
new_chipkey               = 0x9847 (38983)
app_rescramble_applied    = True
skipped                   = False
```

If `app_rescramble_applied = False`, the script did not apply Step 2. Without Step 2, the badge ACCEPTs the OTA but **does not boot** (the boot ROM descrambles with eFuse 0x9847, but the content was scrambled with the 0xFFFF SDK default → garbage post-descramble). The default `--skip-app-rescramble` is `False` (correct); avoid passing it `True` except when testing the Vector A.1 accept gate.

### Step 5: Wrap Qix 27B

```bash
python3 tools/ufw-repack/wrap_qix.py wrap \
  out/m1-rcsp-0x9847.ufw \
  -o out/m1-rcsp-0x9847.wrapped.ufw \
  --version M1.0.0

# Verify wrapper
python3 tools/ufw-repack/wrap_qix.py verify out/m1-rcsp-0x9847.wrapped.ufw
```

**Expected verify output** — all OK:
```
magic         OK: bcaf == bcaf
type          OK: 0x1 == 0x01
version       'M1.0.0'
payload_size  OK: ... == ...
crc16         OK: ... == ...
JLUFW footer  OK: presente=True
```

The Qix 27B wrapper is what the real ZRun app prepends to the UFW before the BLE push. Without it, `qix-ble flash` rejects with `UfwInvalid` (local validation) and the badge probably silent-drops on receiving the first chunk without the expected wire metadata.

---

## 4. Final artifact

```
out/m1-rcsp-0x9847.wrapped.ufw
  size:    1 443 643 B (1.44 MB with RCSP forced + 27 B wrapper)
  format:  Qix-wrapped JieLi UFW (jl-new-fw)
  chipkey: 0x9847 (PID 1558 production)
  entry:   0xC000100
  rcsp:    BLE OTA server active (qix-ble flash recovery viable)
```

Current reference snapshot (session 2026-05-20, branch `feature/m1-self-built`):
```
sha256(m1-rcsp-0x9847.wrapped.ufw) = 85fa7ee7bbb07d65ad21c2e93be70a095cb158246ba0a53756667a532744b724
```

Historical snapshot (session 2026-05-17, build without RCSP — superseded):
```
sha256(m1-0x9847.wrapped.ufw) = e55dd984cbebab7820237d40223716d13251ea583275bb4c469b1ea4c2c2caaf
size = 1 330 683 B
```

The sha256 varies with any modification to the SDK source or config.dat. The build without RCSP remains as a reference but **flashing it is not recommended** — without RCSP there is no BLE path for post-flash recovery if something fails.

---

## 5. Flash to the badge (Tasks 7-8)

**Pre-flight checklist** before any attempt (probe-only or real):

- [ ] Badge battery ≥30% OR USB cable connected during the entire flash window
- [ ] `qix dump --auth` **NOT** run since the badge's last power-cycle (it activates the TEST_MODE flag that silent-rejects REQ_UPDATE — see [[reference_e87_test_mode_blocks_ota]])
- [ ] Phone Bluetooth **OFF** completely (not just closing the app — JieLi badges have 1 connection slot)
- [ ] Badge MAC known or `qix scan` ready

**Probe-only first** (zero-write, smoke test of wire + chipkey accept):

```bash
qix flash <MAC> out/m1-rcsp-0x9847.wrapped.ufw --probe-only
# Expected: state=1 ACCEPTED
# If state=0 or silent: STOP, debug
```

**Real flash** (write to the flash chip; potentially irreversible via OTA):

```bash
qix flash <MAC> out/m1-rcsp-0x9847.wrapped.ufw
# Expected:
#   - state=1 ACCEPTED at REQ_UPDATE
#   - chunk acks via WriteWithoutResponse cadence
#   - final 0xC3 (NOT 0xC5 — see qix-ble commit 4e022cf memory)
#   - badge disconnects + reboots 5-30s post-disconnect
```

**Post-flash validation**:

```bash
# Visual: screen blinks ~1Hz (sustain ≥30s)
# BLE scan: name "M1-E87" instead of "YL-BR30"
qix scan
# RCSP attr 5 query: VER=0x9901 instead of "11.1.0.4"
qix raw-rcsp <MAC> 0xC2 ...   # per qix-ble implementation
```

---

## 6. Gotchas / pitfalls

### Build

| Symptom | Cause | Fix |
|---|---|---|
| `ld: cannot find -ljl_*` | Toolchain `/opt/jieli/pi32v2/` does not point to the correct install | Re-run `scripts/setup-jieli-toolchain.sh` |
| `make terminó con exit 2` after "+POST-BUILD" | Expected (Error 127 from the internal download.sh) | Verify `sdk.elf` exists + size > 5 MB |
| `wine isd_download.exe exit 245` | Expected (crash in wine cleanup) | Verify `update.ufw` exists + size > 50 KB |
| `objdump: m1_backlight_toggle_task not found` post-link | LTO dropped the function for lack of a caller | Verify `task_create(m1_backlight_toggle_task,...)` is in `app_main()` |
| `byte 8 = fd` when expecting `fb` (or vice versa) | Difference between vanilla UI=1 build (fd) and UI=0/OEM (fb) | Cosmetic, does not affect UFW validity |

### Bash shell state between commands

**The cwd persists between Bash invocations. Shell state does NOT.** If a command does `cd <dir>`, the next Bash starts in `<dir>`. Reset explicitly if the next command needs the repo root:

```bash
cd "$(git rev-parse --show-toplevel)" && <next command>
# or always use absolute paths
```

This derailed previous sessions (background tasks that assumed cwd=root but were left in another dir after an earlier `cd`).

### SDK tree gitignored

`tools/community-re/jieli-sdks/*` is in `.gitignore`. **The patches live in `patches/m1/` and are applied to the SDK working tree. NEVER try to `git add` SDK files** — `git` rejects it with `paths are ignored by one of your .gitignore files`.

To track changes to the SDK:
1. Edit the file inside the SDK tree
2. Generate the diff: `git -C tools/community-re/jieli-sdks/ac707n_watch_lvgl diff <file> > patches/m1/<name>.patch`
3. Commit the patch file (not the SDK file)
4. Re-apply on a clean clone: `cd tools/community-re/jieli-sdks/ac707n_watch_lvgl && git apply ../../../../patches/m1/<name>.patch`

### Backlight with UI=0

`lcd_drv_backlight_ctrl_base()` is in `lcd_drive.c` (not in `power_gate.c` as an earlier memory said). With `TCFG_UI_ENABLE=0`, `lcd_drive.c` still compiles but LTO drops it from the final binary + the `__lcd`/`lcd_dat` state it needs is NOT initialized.

**Solution**: use the standalone `power_gate.c` primitives (`power_gate_pwm_init`, `power_gate_pwm_set_duty`). See memory `reference_backlight_power_gate_ui0.md` for detail.

### PWM backlight duty range

`power_gate_pwm_set_duty(IO_LCD_PG, duty)` expects `duty` in the range **0..10000** (not 0..100). Per the header comment: `pwm的低电平的占空比，0~10000对应0%~100%`. The backlight HW's active-high vs active-low polarity may vary — mitigate by toggling opposite extremes (0/10000) for guaranteed visibility.

### isd_download.exe + production chipkey

`isd_download.exe -key <chipkey.bin>` rejects the raw chipkey 0x9847 binary. It expects a `L3KEY/CKEY` PEM signed by JieLi (format `-----BEGIN CHIP KEY-----`) that is NOT publicly available. That is why the build ends with chipkey=0xFFFF SDK default and we apply `scripts/inject_chipkey.py` post-build.

This limitation means the `blimit.bin` (if it exists) generated by `isd_download.exe` is signed against chipkey=0xFFFF. If patching to 0x9847 post-build invalidates an RSA-1024 signature over flash.bin, the bootloader could roll back at boot. **Empirical status**: blimit.bin does NOT appear in the unpack of either OEM or M1 (`fwunpack_newfw.py`). See memory `feedback_blimit_not_in_jlfs_unpack.md`. The only empirical validation = an authorized real flash.

### Qix wrap verify over OEM cloud files

`python3 tools/ufw-repack/wrap_qix.py verify <oem_v103_file>` may report `JLUFW footer presente=False` for the OEM cloud-serving files. That is expected — the cloud files have the Qix wrapper but NOT the JLUFW footer that the packer adds during the wrap. To unwrap the cloud file correctly:

```bash
python3 tools/ufw-repack/wrap_qix.py unwrap <oem_file.bin> -o <unwrapped.ufw>
# Then fwunpack the unwrapped file
```

---

## 7. For future agents

### Before touching the SDK

- Verify that memory `reference_sdk_vs_oem_checklist_pid1558.md` is up to date with your question. If there is already a prior finding, do NOT duplicate the audit.
- Read internal research notes (not published) (design doc) and internal research notes (not published) (current pre-flash status) BEFORE any change.
- `patches/m1/*.patch` list the actual changes applied — they are the source of truth.

### Before a new build

- Confirm you are on branch `feature/m1-self-built` (or a derivative). Do NOT build on `main`.
- Verify `git diff` in the SDK tree (the patches must be applied):
  ```bash
  cd tools/community-re/jieli-sdks/ac707n_watch_lvgl && git diff --stat
  # Expected: sdk_config.h, app_main.c, user_cfg.c modified
  ```

### Build heuristics

- **Do not re-run `make clean` unnecessarily**. The build is 5-15 min. If you only changed a patch, an incremental `make` may be enough (untested; the script default is `make clean && make`).
- **If the build "fails" at post-build**, first verify `sdk.elf` exists. Error 127 is expected, NOT debuggable.
- **If the build fails at link (ld errors)**, those do matter. Stack size warnings are benign.

### Symbol verification

```bash
# BEFORE packing the UFW, verify the change was linked
/opt/jieli/pi32v2/bin/objdump -t <sdk.elf> | grep <symbol-name>
strings <sdk.elf> | grep <string-name>
```

LTO drops aggressively. If your change does not appear, it is probably unused. Look for the caller chain.

### Modifications to `config.dat`

Always use `scripts/patch_config_dat.py`, never byte-edit by hand. The script recomputes the 3 CRC layers:
1. Per-item payload CRC (item_head[idx].crc16)
2. item_head_crc (over the TLV table 0x20:0xE0)
3. self_crc (over 0x06:0x20, which includes item_head_crc + len + item_count)

A hand-edit will invariably leave some CRC stale → bootloader rollback.

### Modifications to the `isd_config.ini` binary

Use `scripts/inject_chipkey.py` with `--source-isd-config` or `--chipkey`. Any modification to the chipkey blob (32 B chipkey-bin + 2 B CRC16 nested in `isd_config.ini` inside the UFW's `flash.bin`) requires a chained recompute:
1. CRC of the chipkey blob
2. JLFS top-dir entry header of `isd_config.ini` (data_crc + header_crc)
3. UFW container `flash.bin` entry data_crc + entry-list listcrc + container hdrcrc

The script does it. A hand-edit will invariably brick.

### Re-scramble app_area (Step 2)

`inject_chipkey.py` runs `_rescramble_app_area` by default (`--skip-app-rescramble=False`). The `app_area` is XOR-scrambled with `jl_sfc_cipher(chipkey)` in the UFW. If you change the chipkey:
- The boot ROM descrambles with the eFuse new_key
- If the app_area is still scrambled with old_key → garbage at runtime → no boot

`jl_sfc_cipher` is XOR-symmetric per 32-byte block. Descramble with old_key + re-scramble with new_key = correct bytes for the boot ROM.

**Only skip Step 2 for Vector A.1 gate tests** (the badge ACCEPTs even though it then does not boot — useful to confirm the wire is OK independent of the boot path).

### About the blimit.bin risk

Memories `feedback_ufw_repack_bank_commit_missing.md` and `reference_blimit_rsa1024_signature.md` claim that Vector A.1 BLE OTA is "architecturally closed" by an RSA-1024 signature over flash.bin that only JieLi can regenerate. Empirically, blimit.bin does NOT appear in the unpack of either OEM or M1 (`feedback_blimit_not_in_jlfs_unpack.md`).

**Status for future agents**: do not assume closed without re-investigating the current empirical path. If your flow changes (more patches to flash.bin, e.g. deep patches in app.bin that the current M1 does not touch), the risk may manifest even though M1 does not trigger it.

### When NOT to use this pipeline

- **No supported JieLi HW available**: the chipkey is per-PID; different PIDs require a different bundled `isd_config.ini` in `hw-sessions/.../isd_config.ini`. If the target ≠ E87 PID 1558, do NOT use 0x9847.
- **You need an invalid OTA for testing**: use `--skip-app-rescramble` in `inject_chipkey.py`. The UFW is ACCEPTed but does not boot.
- **Officially signed build**: needs the JieLi L3KEY PEM. Out of scope for this project.

---

## 8. References

- Design doc: internal research notes (not published)
- Pre-flash build status: internal research notes (not published)
- SDK vs OEM audit: internal research notes (not published)
- Executable plan (detailed steps): `docs/superpowers/plans/2026-05-17-m1-self-built-firmware.md`
- Relevant memories (in `~/.claude/projects/.../memory/`):
  - `reference_m1_build_complete_preflash.md` — entry point
  - `reference_sdk_rcsp_ota_native_ae00.md` — native RCSP OTA opcodes
  - `reference_backlight_power_gate_ui0.md` — backlight via power_gate
  - `feedback_blimit_not_in_jlfs_unpack.md` — empirical blimit
  - `reference_chipkey_injection_app_area_rescramble.md` — chipkey injection
  - `reference_jieli_config_dat_format.md` — JCRT format
  - `reference_jieli_isd_config_binary.md` — isd_config binary layout
  - `reference_ufw_repack_pipeline.md` — general pipeline
- Phase B artifacts: lived in `hw-sessions/2026-05-17/pid1558-firmware-rev/` (parsers + outputs config.dat, isd_config, LCD init, touch fingerprint). `hw-sessions/` is gitignored and that tree is no longer in the repo.
- External cloned tools (gitignored under `tools/community-re/`, not versioned): `tools/community-re/jl-misctools/` (kagaimiq), `tools/community-re/derekfan668-701n_v220_earbox/` (AXS5106 driver ref)
