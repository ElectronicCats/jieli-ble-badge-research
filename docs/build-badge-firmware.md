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

scripts/setup-badge-build.sh          # once: toolchain + SDK + patch + tools + chipkey
scripts/build-badge-fw.sh --ota       # each build: app.bin + the BLE-OTA .ufw
```

First run is dominated by the 1.1 GB SDK clone — a few minutes on a good link to JieLi's CN
infrastructure, considerably longer on a bad one. After that: **~90 s** for a clean rebuild,
**~5 s** incremental (measured on this repo's reference build).

Flash the resulting image over BLE with `qix flash <MAC> <image>.ufw --oem` — see
[ota-howto.md](ota-howto.md).

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
needs nothing but `app.bin`, which the compiler produces regardless — see §4.

### On the chipkey

`chipkey_9847.bin` is the flashing key burned into AC707N-class E87 units, published here as a
research artifact (see [DISCLAIMER.md](../DISCLAIMER.md)). Without it the build still succeeds
but emits a **keyless** image, and a chipkey-burned unit rejects it with a *Key mismatch*.
For a different unit: `scripts/setup-badge-build.sh --chipkey <id>`, reading
`tools/chipkey/chipkey_<id>.bin`, or `--chipkey none` to build keyless on purpose.

---

## 3. Build

```sh
scripts/build-badge-fw.sh [--clean] [--chipkey <id>] [--ota [PATH]] [--no-menu]
```

It verifies every prerequisite *before* compiling, raises `ulimit -n` itself, and checks the
artifacts afterwards — a build that produced nothing cannot report success.

| Flag | Effect |
|---|---|
| `--clean` | `make clean` first (~90 s full rebuild vs ~5 s incremental) |
| `--chipkey <id>` | `BADGE_CHIPKEY=<id>` (default `9847`; `none` to omit) |
| `--ota [PATH]` | also splice the fresh `app.bin` into a flashable BLE OTA image. Default `PATH`: `firmware-builds/custom-fw-ac707n-<utc>.ufw` |
| `--no-menu` | build the factory watch app instead of the badge menu |

### The equivalent by hand

```sh
export PATH="/opt/jieli/pi32v2/bin:$PATH"
ulimit -n 65536
cd tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK
make clean && BADGE_CHIPKEY=9847 make MENU=1
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
packaged image is **keyless** and a chipkey-burned unit rejects it. See §2.

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

## See also

- [ota-howto.md](ota-howto.md) — flashing the result over BLE (`qix flash --oem`)
- [lock-image.md](lock-image.md) — updating the lock-screen wallpaper without a reflash
- [patches/badge/README.md](../patches/badge/README.md) — what the patch contains
- [jieli-sdk-build.md](jieli-sdk-build.md) — the older wine+docker flow for the *vanilla* SDK
