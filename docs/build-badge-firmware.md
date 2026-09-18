# Build the custom badge firmware from source

End-to-end, reproducible build of the **custom badge-menu firmware** (LVGL touch UI + BLE
tools) for the JieLi **AC707N / BR35 e-badge (E87)**, on Linux.

> ⚠️ Security research / educational use only, on hardware you own — see
> [DISCLAIMER.md](../DISCLAIMER.md).

The firmware is kept as a **patch on top of the vanilla JieLi SDK**, which is *not* stored in
this repo: it is a git submodule pointing at JieLi's own GitLab. Nothing proprietary is
redistributed here — you fetch it from the vendor yourself.

---

## TL;DR

```sh
git clone https://github.com/ElectronicCats/jieli-ble-badge-research
cd jieli-ble-badge-research

scripts/setup-badge-build.sh                  # once: toolchain + SDK + patch + tools + chipkey
scripts/build-badge-fw.sh --ota               # E87 badge  (default board)
scripts/build-badge-fw.sh --board dg01 --ota  # DG01 wristband
```

First run is dominated by the 1.1 GB SDK clone — a few minutes on a good link to JieLi's CN
infrastructure, considerably longer on a bad one. After that: **~90 s** for a clean rebuild,
**~5 s** incremental (measured on this repo's reference build).

Flash the resulting image over BLE with `qix flash <MAC> <image>.ufw --oem` (E87) or
`qix rcsp-flash <MAC> <image>.ufw` (DG01) — see [ota-howto.md](ota-howto.md). The build
prints the exact command for the board it just built.

---

## 1. Prerequisites

| Need | Why | Check |
|---|---|---|
| Linux x86-64 | The toolchain and post-build tools are Linux x86-64 ELF binaries | `uname -m` |
| `git`, `curl`, `tar`, `sha256sum`, `make`, `python3` | setup + build | — |
| ~3 GB free disk | 1.1 GB SDK checkout + ~1 GB build objects | `df -h .` |
| `sudo`, once | only to create the `/opt/jieli` symlink | — |
| `ulimit -n` ≥ 65536 | the LTO link opens ~1130 files at once | `ulimit -n` |

Everything else (`setup-badge-build.sh`) is fetched automatically.

---

## 2. What the setup does, and where each piece comes from

`scripts/setup-badge-build.sh` is idempotent — re-run it any time; it skips whatever is
already in place.

| # | Piece | Source | Lands in |
|---|---|---|---|
| 1 | **pi32v2 toolchain** (JieLi's clang 4.0.1 fork, target `pi32v2 -mcpu=r3`) | `pkgman.jieliapp.com/s/linux-toolchain` (public, no account) | `tools/jieli-toolchain/` + `/opt/jieli` symlink |
| 2 | **Vanilla JieLi SDK** | `gitlab.zh-jieli.com/e_badge/e_badge_707_sdk_200`, git submodule pinned at `d016768` | `tools/community-re/jieli-sdks/e_badge_707_sdk_200` |
| 3 | **badge-menu patch** (the custom firmware: 49 files, ~4.8 k lines of code plus ~10.7 k lines of embedded image data) | this repo, `patches/badge/badge-menu.patch` | applied into the SDK worktree |
| 4 | **Native post-build tools** (`isd_download`, `ufw_maker`, `fw_add`, `packres`, `json_to_res`, `fat_comm`, `remove_tailing_zeros`) | `pkgman.jieliapp.com/s/linux-postbuild` (public, no account) | `SDK/tools/linux/` |
| 5 | **Chipkey** `chipkey_9847.bin` | this repo, `tools/chipkey/` | `SDK/cpu/br35/tools/download/watch/` |

### On the SDK submodule

JieLi's GitLab allows **anonymous read-only clone** — no account, no token:

```sh
git ls-remote https://gitlab.zh-jieli.com/e_badge/e_badge_707_sdk_200
# d0167685d032d745d88fe50233302edd46941622  HEAD
```

The submodule is declared `shallow = true`, so `--init` pulls only that one commit
(~1.1 GB checkout instead of the full history). It is also `ignore = dirty`, because the
badge patch is applied *into* the submodule worktree by design — the submodule is
permanently "modified" while you are building, and that is expected.

### On the post-build tools

They do **not** ship with the SDK (which carries only the Windows `.exe` versions), and they
are **not** part of the toolchain package either. They live in JieLi's separate
*"下载目录工具（Linux版）" / download-directory tools (Linux)* package. `setup-badge-build.sh`
downloads it and checks its sha256 against the version this repo was validated with
(`jieli-linux-post-build-tools-20260908.1.tar.xz`). JieLi serves a rolling *latest* at that
URL, so a **sha mismatch is a warning, not an error** — it usually just means a newer release.

They are only needed for the **USB** images (`update.ufw`, `jl_isd.fw`). The **BLE OTA** path
needs nothing but `app.bin`, which the compiler produces regardless — see
[Build](#3-build).

### On the chipkey

`chipkey_9847.bin` is the flashing key burned into AC707N-class E87 units, published here as a
research artifact (see [DISCLAIMER.md](../DISCLAIMER.md)). Without it the build still succeeds
but emits a **keyless** image, and a chipkey-burned unit rejects it with a *Key mismatch*.
For a different unit: `scripts/setup-badge-build.sh --chipkey <id>`, reading
`tools/chipkey/chipkey_<id>.bin`, or `--chipkey none` to build keyless on purpose.

---

## 3. Build

```sh
scripts/build-badge-fw.sh [--board e87|dg01] [--clean] [--chipkey <id>] [--ota [PATH]] [--no-menu]
```

It verifies every prerequisite *before* compiling, raises `ulimit -n` itself, and checks the
artifacts afterwards — a build that produced nothing cannot report success.

| Flag | Effect |
|---|---|
| `--board e87\|dg01` | which board to build for (default `e87`). See [Two boards](#two-boards-e87-and-dg01) |
| `--clean` | `make clean` first (~90 s full rebuild vs ~5 s incremental) |
| `--chipkey <id>` | `BADGE_CHIPKEY=<id>` (default `9847` for `e87`, `b165` for `dg01`; `none` to omit) |
| `--ota [PATH]` | also package the fresh build as a flashable BLE OTA image. Default `PATH`: `firmware-builds/custom-fw-ac707n-<utc>.ufw` (e87) / `firmware-builds/custom-fw-dg01-<utc>.ufw` (dg01) |
| `--no-menu` | build the factory watch app instead of the badge menu |

### Two boards: E87 and DG01

The same badge-menu application runs on two different AC707N units. They share the SoC, the
360×360 round display resolution and the 4 MB flash, and **nothing else that matters to the
drivers** — an image built for one does not work on the other:

| | `--board e87` (default) | `--board dg01` |
|---|---|---|
| Unit | E87 / DragonJAR badge | DG01 wristband (`LJ733_MB_V1.1`) |
| Panel driver | `lcd_qspi_st77916_320x385.c`, retargeted 360×360 | `lcd_qspi_badge2_360x360.c` (lc9855-class, `DE`/`DF` paging) |
| Touch | AXS5106L, bit-banged I²C on **PB1/PB2** | CST816D, bit-banged I²C on **PA5/PA6** |
| Buttons | 2 × ADKEY (3 AD windows) | 1 button on **PB7**, read through the PMU `PADC0` path |
| Chipkey | `0x9847` | `0xB165` |
| OTA protocol | Qix (`FD00`, opcode `0xC0`) | pure native RCSP (`AE00`, `E1…E8`) |
| OTA image | Qix-wrapped *fw-custom* | vanilla `.ufw`, app-only, re-keyed |
| Flash command | `qix flash <MAC> <img> --oem` | `qix rcsp-flash <MAC> <img>` |

`--board` sets `BOARD=` on the make line, which defines `CONFIG_BADGE_BOARD_E87` or
`CONFIG_BADGE_BOARD_DG01`. That define does exactly one thing: it flips
`TCFG_LCD_SPI_ST77916_ENABLE` / `TCFG_LCD_SPI_BADGE2_ENABLE` in
`board_ac707n_demo_cfg.h`. Every other board difference — the touch pins, the touch driver,
the ADKEY thresholds, the screen size — is already gated on `TCFG_LCD_SPI_BADGE2_ENABLE` in
`board_ac707n_demo.c`, `board/adkey_config.c` and `ui/lcd/lcd_conf.h`, so that one knob
carries the whole board.

Switching boards **forces a full rebuild**: `BOARD=` only changes `-D` defines, which this
SDK's Makefile does not track as a dependency, so an incremental build would silently mix
objects from both panels. The script stamps the board in
`SDK/cpu/br35/tools/.badge-board` and runs `make clean` itself whenever it changes.

### The equivalent by hand

```sh
export PATH="/opt/jieli/pi32v2/bin:$PATH"
ulimit -n 65536
cd tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK
make clean && BADGE_CHIPKEY=9847 make MENU=1 BOARD=e87      # or BOARD=dg01
```

`MENU=1` adds `-DCONFIG_BADGE_MENU`, which is the single define that selects the badge-menu
application (LVGL touch UI) instead of the factory watch boot test. The firmware-internal
detail of what that define switches on is in the SDK's own `docs/BUILD.md` (created by the
patch).

---

## 4. What you get

| File | Size (this build) | What it is |
|---|---|---|
| `SDK/cpu/br35/tools/app.bin` | ~973 KB | The raw application image. **This is the only input the BLE OTA path needs.** |
| `SDK/output/update.ufw` | ~2.27 MB | Native update image — **USB flashing only**. Carries the *native* uboot, so it is **not** OTA-flashable. |
| `SDK/output/jl_isd.fw` / `jl_isd.bin` | ~1.24 MB / ~1.03 MB | Full flash image for USB-ISP |
| `firmware-builds/custom-fw-ac707n-<utc>.ufw` | 1 079 363 B | With `--ota`: the **BLE-flashable** image |

### Why the OTA image is repacked, not built

The badge's OEM BLE OTA hands the image to the **resident OEM uboot**, which is what rewrites
the CODE partition. An SDK-built `update.ufw` carries the *native* uboot instead and the badge
bricks silently at apply. So the OTA image is built by splicing your fresh `app.bin` into an
existing image that already has the OEM uboot, flash geometry and Qix wrapper:

```sh
python3 tools/ufw-repack/swap_app.py \
    firmware/custom-fw-ac707n.ufw \        # base: read-only, for OEM geometry
    <SDK>/cpu/br35/tools/app.bin \         # your build
    firmware-builds/my-build.ufw           # output
```

`--ota` does exactly this. `swap_app.py` recomputes every CRC (JLFS `app.bin`,
`app_area_head`, `flash.bin`, list/header, Qix wrapper), so the output is flash-ready. Two
builds of the same source differ in the embedded `__TIME__` bytes and the CRCs covering them —
**a byte-different `.ufw` of the same size is expected**; don't compare by sha256. Full
protocol details in [ota-howto.md](ota-howto.md).

---

## 5. Troubleshooting

Real failures, with the exact message you will see.

### `error: pathspec 'tools/community-re/...' did not match any file(s) known to git`

`git submodule update --init <path>` on a clone where the submodule gitlink is missing from
the index. Fixed in this repo; if you hit it on an old checkout, `git pull` and retry.

### `fatal: could not read Username for 'https://gitlab.zh-jieli.com'`

The clone was interrupted and git fell back to prompting. Anonymous read *does* work — retry.
It is a large, slow clone from CN infrastructure (~1.1 GB, tens of minutes on a bad link):

```sh
git submodule update --init --depth 1 tools/community-re/jieli-sdks/e_badge_707_sdk_200
```

### `file not recognized: File format not recognized` on some arbitrary `.o`

**`ulimit -n` is too low** — the classic one. The final link opens ~1130 objects and libraries
at once; past the descriptor limit `open()` fails and BFD misreports it as a bad file format.
The named `.o` is fine.

```sh
ulimit -n 65536      # 1024 is a common default
```

`env -i` does **not** reset ulimits, so a "clean" environment still carries the low one.
`build-badge-fw.sh` raises it for you; if the *hard* limit is also low, raise it in
`/etc/security/limits.conf` and re-login.

### `download.sh: line NN: .../SDK/tools/linux/isd_download: No such file or directory`

The native post-build tools are missing. **Historically this failed silently** — `make` still
exited `0`, no `.ufw` was produced, and the build looked successful. It now prints a loud
block and tells you `app.bin` was still built (which is all the BLE OTA path needs). To get
the tools: `scripts/setup-badge-build.sh`.

### `download.sh: using chipkey none`

No `chipkey_*.bin` in `SDK/cpu/br35/tools/download/watch/`. The build completes but the
packaged image is **keyless** and a chipkey-burned unit rejects it. See
[On the chipkey](#on-the-chipkey).

### `Device Offline` at the end of the build

**Not an error.** `isd_download` always finishes by trying a real USB flash and exits **245**
when no badge is attached — *after* it has already written `update.ufw` and `jl_isd.fw`. The
build judges the run by the images, not by that exit code.

### `badge-menu.patch neither applies nor is applied`

The SDK worktree is dirty (a half-applied patch, or your own edits). Reset it:

```sh
SDK=tools/community-re/jieli-sdks/e_badge_707_sdk_200
git -C "$SDK" checkout . && git -C "$SDK" clean -fd
scripts/setup-badge-build.sh
```

### `FAIL R2 STAGER: built without STAGER=1`

From `SDK/tools/check_custom_fw.py`, and **stale**: it predates the direct
`qix flash --oem` path and still insists on the legacy STAGER loader chain, which this
firmware no longer uses. The build only runs that checker under `make STAGER=1`, so you will
only see it if you invoke it by hand. Ignore the R2 line; R1 (app fits the OEM slot) and R3
(touch controller) are still meaningful.

---

## 6. Modifying the firmware

The SDK worktree is a normal git checkout: edit sources in it, rebuild, and when you are happy
regenerate the patch so the change is tracked in *this* repo:

```sh
SDK=tools/community-re/jieli-sdks/e_badge_707_sdk_200
cd "$SDK"
git add -A -- ':!SDK/tools/linux' ':!output' ':!SDK/output'
git diff --cached -- ':!*.a' ':!*.bin' ':!SDK/tools/linux/**' ':!*.cbp' ':!**/*.bat' \
  ':!**/json.txt' ':!SDK/build/fileList.dumy' ':!output/**' ':!SDK/output/**' \
  > ../../../../patches/badge/badge-menu.patch
git reset
```

The exclusions keep vendor binaries, Windows-only files and build artifacts out: the patch
stays **text-only and reviewable**. `SDK/build/fileList.dumy` in particular is regenerated by
`pre_build` on every build and is read by nothing — the source list that actually drives the
compile comes from `SDK/build/genFileList.c`, which *is* in the patch.

Vendor `.a` libraries are used **as shipped** — the build needs no binary library patch.

---

## 7. Per-board peripherals

Both boards share `board_ac707n_demo.c`. Everything board-specific hangs off
`TCFG_LCD_SPI_BADGE2_ENABLE`, which `--board dg01` sets (see
`board_ac707n_demo_cfg.h`).

### Touch panel

| | badge 1 (E87) | badge 2 (DG01) |
|---|---|---|
| Controller | AXS5106L (AiXin) | CST816D (Hynitron) |
| 7-bit address | `0x63` | `0x15` |
| Chip-ID register | `0x08` (2 B) | `0xA7` (1 B) — reads **`0xB6`** [M] |
| SDA / SCL | PB1 / PB2 | PA5 / PA6 (the HW-iic0 pins, bit-banged) |
| RST / INT | PA2 / PB8 | PA2 / PB8 |

Both are driven by the same software-I²C primitives and selected at compile time by
`badge_tp_init` / `badge_tp_read`. The driver **polls** (`axs_poll_task`); the INT line
is configured as an input but never used as an interrupt source.

> **The CST816D register base is not the AXS5106L's.** [M] This was wrong until
> 2026-09-17: the burst read started at register `0x01` but kept the AXS byte indices,
> which are relative to `0x00`, so every field was off by one register. `d[1]` — meant
> to be the finger count — was actually the *top nibble of X*, so a touch only ever
> registered while X happened to land in 256..511, and the coordinates it then reported
> were two unrelated register halves spliced together. The CST816x map is:
>
> | Reg | Field |
> |---|---|
> | `0x00` | GestureID (`01` up, `02` down, `03` left, `04` right, `05` click, `0B` double, `0C` long) |
> | `0x01` | FingerNum (low nibble) |
> | `0x02` | bits[7:6] event (0 down, 1 up, 2 contact), bits[3:0] X[11:8] |
> | `0x03` | X[7:0] |
> | `0x04` | bits[3:0] Y[11:8] |
> | `0x05` | Y[7:0] |
>
> The fix is the burst base: `CST_REG_TOUCH 0x00`.

A polled driver also has to stop the panel sleeping: the CST816x auto-sleeps after a
couple of idle seconds and then stops ACKing until a touch or a reset wakes it, which
makes touch come and go. `cst816d_init()` now writes `0xFE = 0x01` (DisAutoSleep) after
the ID probe; a failure there is logged, not fatal.

> **Confirmed on hardware 2026-09-18.** The DG01's probe prints
> `tp: CST816D on PA5/PA6, id=0xb6` on the UART, so both the controller and the pin pair
> are as assumed. The burst base is `0x01` — an earlier edit changed it to `0x00` on the
> strength of the generic CST816S datasheet and that was wrong; the authority is the
> vendor's own driver in this tree, `cst816d.c:899`, which reads from `0x02` with a 5-byte
> burst, putting `FingerNum` at register `0x02` rather than `0x01`.
>
> Orientation **is** transformed: touch tracks the display rotated 180°, corrected in
> `badge_tp_read_any()` (both axes inverted; the panel is square so no swap). [M]

### Battery gauge

`vbat_check()`'s discharge branch only ever *decremented* `cur_battery_percent`, making
the gauge a one-way downward ratchet: once seeded low it could never climb back on
battery power, however healthy the cell. It gets seeded low by a stale SOC restored
from VM (`bat_info_need_recheck()` returns the stored percent and discards the
measurement when the two are within 10 %), or by a first sample taken while the
backlight is at full and the cell sags — and the DG01 now boots with the display **on**.
Measured on the DG01 2026-09-17: stuck at 7 %, which on the curve is ~3542 mV.

The curve itself is not the difference: it is **byte-identical** to the OEM's (12 points
at `cfg_tool.bin+0x50`, 3300 mV @ 0 % → 4120 mV @ 100 %, as
`union battery_data {u8 rsv; u16 mV; u8 pct}`), and `config.dat` differs from the OEM's
in 7 bytes of 352, all of them CRCs. Both firmwares convert the same way:
`adc_get_voltage(AD_CH_PMU_VBAT_4) * 4`, interpolated on that curve.

Note how steep the curve is: **30 mV per 10 %** between 20 % and 50 %. A ~250 mV error
moves the reading from 40 % to 7 %, so the percentage is useless for telling a flat cell
from a mis-scaled ADC — which is why the raw mV is now shown next to it on the warning
and charge screens.

The BLE tool gate is now on **voltage**, not percentage, since the voltage has no such
memory: 3770 mV on the E87 (exactly the old 30 % point, so behaviour is unchanged there)
and provisionally 3400 mV on the DG01 until its vbat is checked against a meter.

> **The gate also guards OTA** (`badge_ble_battery_gate()` is called from the update
> entry). A gauge stuck low therefore locks the radio *and* locks over-the-air updates,
> with the charge screen owning the UI whenever USB is plugged in. The way out on a
> locked unit is to charge until the existing charge-branch snap-up lifts the gauge past
> the gate, then unplug.

## See also

- [ota-howto.md](ota-howto.md) — flashing the result over BLE (`qix flash --oem`)
- [lock-image.md](lock-image.md) — updating the lock-screen wallpaper without a reflash
- [patches/badge/README.md](../patches/badge/README.md) — what the patch contains
- [jieli-sdk-build.md](jieli-sdk-build.md) — the older wine+docker flow for the *vanilla* SDK
