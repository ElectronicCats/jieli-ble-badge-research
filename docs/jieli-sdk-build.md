# Build JieLi AC707N SDK end-to-end (sdk.elf → update.ufw)

Permanent how-to for reproducing the vanilla `ac707n_watch_lvgl` SDK firmware from source to a flashable `.ufw`, on Linux with wine + docker. This flow replaces the SDK upstream's Windows `download_lvgl.bat`.

> Status: **validated end-to-end 2026-05-06**. `update.ufw` 3.6 MB produced and copied to `firmware-builds/`. Pending flash on HW (physical badges expected ~mid-May 2026).

## Pipeline (3 scripts, idempotent)

```
scripts/setup-jieli-toolchain.sh         (one-time)
scripts/setup-jieli-lvgl-port.sh         (one-time)
scripts/build-jieli-ac707n.sh            (each source change → sdk.elf)
scripts/build-jieli-ac707n-ufw.sh        (each source change → update.ufw)
```

## One-time setup

### 1. pi32v2 toolchain (JieLi's LLVM fork)

```bash
./scripts/setup-jieli-toolchain.sh
# needs sudo only for the /opt/jieli symlink (one-time)
```

Downloads `jieli-linux-toolchains-20250805.1.tar.xz` (25 MB) from `pkgman.jieliapp.com/s/linux-toolchain` (no auth, redirects to Aliyun OSS), extracts to `tools/jieli-toolchain/`, creates the `/opt/jieli/` symlink. clang 4.0.1 fork with target `pi32v2 -mcpu=r3`.

### 2. LVGL port assembly

```bash
./scripts/setup-jieli-lvgl-port.sh
```

Clones the public repos from `gitlab.zh-jieli.com` (access via anonymous git daemon, **GET not HEAD** — see log 2026-05-05) and assembles them into `SDK/lv_port_pc_vscode/`:

```
lv_port_pc_vscode/         <- gitlab.zh-jieli.com/707_lite_lvgl/lvgl_portable_code
lv_port_pc_vscode/lvgl/    <- gitlab.zh-jieli.com/707_lite_lvgl/lvgl_core
```

### 3. Defensive mirror (for `lz4_packet` Linux)

The `lz4_packet` Linux ELF packer needed in the pipeline does NOT ship with the ac707n SDK (that one ships only the `.exe`). We take it from the `jl710_3.0.0_official` SDK (another chip in the same family — the binary is chip-agnostic, sha1 identical cross-family).

```bash
# Already done in side-quest C — see internal research notes (not published)
ls tools/community-re/jieli-sdks/jl710_3.0.0_official/SDK/cpu/br56/tools/lz4_packet
```

### 4. Docker + wine image

```bash
# Add user to the docker group (one-time, needs sudo)
sudo usermod -aG docker $USER
# Logout/login OR use `sg docker -c` in existing sessions

# Pull the wine image (one-time, ~1.5 GB)
sg docker -c "docker pull scottyhardy/docker-wine:stable"
```

> **Do not use native wine32 on Ubuntu 24.04** — the t64 transition + the `libgd3` conflict from the `ondrej/php` PPA break the install. Docker is the clean bypass.

## Build each time

### sdk.elf (vanilla)

```bash
./scripts/build-jieli-ac707n.sh
```

- Time: ~71s (the LTO link is the slow part)
- Output: `tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/sdk.elf` (10 MB)
- Versioned copy to `firmware-builds/ac707n-vanilla-<rev>-<ts>/`

### update.ufw

```bash
./scripts/build-jieli-ac707n-ufw.sh
```

- Time: ~30s (mostly the docker container spin-up)
- Output: `firmware-builds/ac707n-ufw-<rev>-<ts>/update.ufw` (3.6 MB)
- Side outputs: `jl_isd.fw` (2.4 MB), `jl_isd.bin` (1.18 MB), `app.bin` (1.15 MB), `sdk.elf` (10 MB), `sha256sums.txt`, `isd_download.log`

## What `build-jieli-ac707n-ufw.sh` does (reduced replica of the `.bat`)

The original `.bat` (`SDK/cpu/br35/tools/download/watch/download_lvgl.bat`) does two things:

1. **Build of LVGL UI assets**: `json_to_res.exe json.txt` → `packres.exe` 3 times → `mode.bin` + `resfs.bin`
2. **Firmware pack**: `isd_download.exe` with `app.bin` + 12+ static inputs → `update.ufw`

**Architectural decision:** we skip (1) and reuse the pre-built `mode.bin`/`resfs.bin` from the SDK upstream. To iterate custom UI, one would have to add wine `packres.exe` + `json_to_res.exe` in the pipeline (not validated, deferred to a future iteration).

### Internal pipeline (7 steps)

1. **Stage** `/tmp/jieli-ufw-test/` flat with `sdk.elf` + 14 static inputs + `lz4_packet` Linux ELF + `isd_download.exe`
2. **objcopy 23 sections** from the ELF: `.text`, `.data`, `.data_code`, `.overlay_aec`, `.overlay_aac`, `.ps_ram_data_code`, `.dcache_ram_data`, `.icache_ram_data_code`, `.overlay_bank0..9`, `.overlay_vir0..4`. Missing sections are replaced with an empty file (in the vanilla build, `.overlay_aec/.overlay_aac/.overlay_vir3/.overlay_vir4` do not exist; the rest exist but size=0)
3. **`dat_mix.bin = data.bin + data_code.bin`**
4. **`lz4_packet -dict text.bin -input dat_mix.bin 0 bank0..9.bin 0xbbaa vir0..4.bin 0xbbaa -o dat_bank.lz4`** (LZ4 compression with dictionary)
5. **`app.bin = text.bin + aec.bin + aac.bin + psr_data_code.bin + d_ram_data.bin + i_ram_data_code.bin + dat_bank.lz4`**
6. **`chmod 777` + `chmod a+rw`** over the stage (the wineuser inside the container is uid 1010 ≠ host uid 1000, needs o+w)
7. **`sg docker -c "docker run --rm -v stage:/work -w /work scottyhardy/docker-wine:stable wine isd_download.exe ..."`** → produces `update.ufw`, `jl_isd.fw`, `jl_isd.bin`

### `isd_download.exe` args

```
-tonorflash -dev br35 -boot 0x102600 -div8 -wait 300
-uboot uboot.boot -app app.bin -tone tone_en.cfg
-res cfg_tool.bin p11_code.bin config.dat
-flash-params flash_params_v3.bin
-output-fw jl_isd.fw -output-ufw update.ufw -reboot 500
```

## Validation

### Magic header of the `.ufw`

Bytes 0-3 are a build-specific nonce/salt (they vary between runs). Bytes 8-31 are the consistent JieLi pattern cross-family:

```
?? ?? ?? ?? ?? ?? ?? f8 ?? c1 a7 67 ce bf 5b 97 4e 5d ?? 48 ?? cd 27 4e ...
```

| Family | Bytes 0-3 (build-specific) | Bytes 8-15 (pattern) |
|---|---|---|
| **br35 (ours)** | `b923 1904` / `f8c8 6b85` / `db1c bbdd ...` | `fdc1 a767 cebf 5b97` |
| br25 (ac696x) | `8502 af7b` | `fac1 a767 cebf 5b97` |
| br29 (ac706n) | `c51b 742f` | `fbc1 a767 cebf 5b97` |
| br23 (ac695n) | `efea b424` | `f6c1 a367 cebd 5b97` |
| br28 (ac701n) | `ceff a2ba` | `fac1 a767 cebf 5b97` |

The script validates bytes 8-11 against the regex `a[37]67ce`.

### Builds are NOT byte-equivalent between runs

Because of the nonce in the first 4 bytes, each run produces a `.ufw` with a different sha256 despite the same sources. **Functional reproducibility yes, byte-for-byte no.**

## Known quirks

### Wine returns exit 245 after success

`isd_download.exe` produces all the outputs and then crashes in cleanup (probable segfault at unmount/exit of the wine prefix). The script captures the exit code but **validates by the existence of `update.ufw`**, not by exit. Useful logs at the end of `isd_download.log`:

```
Device Offline                                        ← expected (no USB device in the container)
0024:fixme:kernelbase:AppPolicyGetProcessTerminationMethod
[exit 245]
```

### Bind mount permissions

The `wineuser` (uid 1010) inside the container ≠ the host user (uid 1000). So it can write outputs to the volume, the script does `chmod 777` on the dir + `chmod a+rw` on files before the `docker run`. Outputs end up owned by uid 1010 — the script `cp`s them (not `mv`) to `firmware-builds/<rev>-<ts>/` to preserve them with the host user's ownership.

### `lz4_packet` executable

`cp` from the mirror drops the `+x` bit. The script does `chmod +x stage/lz4_packet` after the copy.

## Limitations of the current flow

- **Does not regenerate LVGL UI assets** (skips `packres` + `json_to_res`). For custom UI: add a step that invokes both `.exe` via wine before `isd_download.exe`.
- **Does not sign the `.ufw`**. If the bootloader requires a crypto signature (not observed but possible), this flow does not add it.
- **Non-fatal wine errors** fill the log: winebth fixmes, OLE marshaling, mountmgr. Expected, ignorable.
- **No HW flash tests**. Hardware expected mid-May 2026 — the first flash test will require HW (USB BSL or, more likely, via the BLE Vector A path if the `TEST_GET_*` opcodes are open).

## References

- Spec/plan: `docs/superpowers/specs/2026-05-05-jieli-sdk-build-pipeline-design.md` + `docs/superpowers/plans/2026-05-05-jieli-sdk-build-pipeline.md`
- Logs: internal research notes (not published) (Task 6 — first compilation) + internal research notes (not published) (Task 7 — full pipeline)
- Original `.bat`: `tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/download_lvgl.bat` (entry point) + `download/watch/download_lvgl.bat` (sub-script with `isd_download.exe`)
