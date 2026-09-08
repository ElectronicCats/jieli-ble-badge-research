# Pending upstream contributions

Tracker of improvements and bugs detected during the project, to give back to the community repos once the current phase closes. Philosophy: if a tool fails us reproducibly, we return the fix.

## Convention

Each entry includes: target repo · problem description · project evidence where we found it · proposed fix · status.

Statuses: `🔍 documented` → `🛠️ local patch` → `📤 PR open` → `✅ merged`.

---

## 1. Android-Pentesting-Skill — `auto-audit-static.sh` aborts on jadx exit code

**Upstream repo:** https://github.com/DragonJAR/Android-Pentesting-Skill
**Status:** 📤 PR open (2026-05-05) — pending review
**PR URL:** _<to complete — paste the PR link here>_
**Local branch:** `tools/android-skill/` branch `fix/auto-audit-tolerate-jadx-exit` (commit `8f8301e`, rebased against `origin/main`)
**Severity:** functional — blocks use of the script on medium/large APKs

### Observed symptoms

Occurred **twice in a row** in this project:

| Session | APK | Size | Mode | Result | Root cause |
|---|---|---|---|---|---|
| 2026-05-04 | `superband-v2.1.23.apk` | 41 MB / ~8.8k classes | `--full` | ⚠️ jadx exit ≠ 0, script aborts | `ERROR - finished with errors, count: 17` (ignorable, sources are generated) |
| 2026-05-05 (T16 ZRun) | `zrun-v2.2.5.apk` | 50 MB / ~14.9k classes | `--full` and `--quick` | ❌ jadx Killed (OOM) | Default heap insufficient for 14k classes |

In both cases the script aborts in Phase 0 with `[✗] jadx failed`, marking `00-decode-info.txt` as `jadx: FAILED` and skipping Phases 1-3 entirely.

### Root cause (reading the script)

`tools/android-skill/scripts/auto-audit-static.sh` line 319:

```bash
if $jadx_cmd -d "$OUTPUT_DIR/jadx-output" "$APK_FILE" > "$OUTPUT_DIR/jadx.log" 2>&1; then
    log_success "JADX decompiled successfully"
    echo "jadx: SUCCESS" >> "$decode_file"
else
    log_error "jadx failed"
    echo "jadx: FAILED - see $OUTPUT_DIR/jadx.log" >> "$decode_file"
fi
```

Two problems:

1. **Global `set -e`** (assumed from the failure propagation) makes any exit ≠ 0 stop the script. jadx returns ≠ 0 when there are ignorable errors even though the output is usable.
2. **The jadx JVM heap is not controlled**. Large APKs saturate the default `-Xmx` and the kernel `Killed`s it.

### Proposed fix (PR to open)

Three combinable improvements:

**(a) Tolerate exit ≠ 0 if the sources were generated:**
```bash
$jadx_cmd -d "$OUTPUT_DIR/jadx-output" "$APK_FILE" > "$OUTPUT_DIR/jadx.log" 2>&1
jadx_rc=$?
if [ -d "$OUTPUT_DIR/jadx-output/sources" ] && [ -n "$(find "$OUTPUT_DIR/jadx-output/sources" -name '*.java' -print -quit 2>/dev/null)" ]; then
    if [ $jadx_rc -ne 0 ]; then
        log_warning "jadx exited $jadx_rc but sources were generated — continuing with partial output"
        echo "jadx: PARTIAL (exit=$jadx_rc, sources present)" >> "$decode_file"
    else
        log_success "JADX decompiled successfully"
        echo "jadx: SUCCESS" >> "$decode_file"
    fi
else
    log_error "jadx failed and no sources generated"
    echo "jadx: FAILED - see $OUTPUT_DIR/jadx.log" >> "$decode_file"
fi
```

**(b) Raise the default heap to 4G and allow an env override:**
```bash
local jadx_heap="${JADX_HEAP:-4g}"
local jadx_cmd="jadx -j $(nproc) --no-imports"
JAVA_OPTS="-Xmx${jadx_heap}" $jadx_cmd ...
```

**(c) `--reuse-decompile <path>` flag to skip jadx:**
```bash
if [ -n "$REUSE_DECOMPILE" ] && [ -d "$REUSE_DECOMPILE/sources" ]; then
    log_info "Reusing decompile from $REUSE_DECOMPILE"
    ln -sfn "$(realpath "$REUSE_DECOMPILE")" "$OUTPUT_DIR/jadx-output"
    echo "jadx: SKIPPED (reused $REUSE_DECOMPILE)" >> "$decode_file"
else
    # ...invoke jadx normally...
fi
```

Arguments for `--reuse-decompile`:
- In projects with several APKs we decompile once in T15 and want to reuse in T16
- Cuts iterative audit time from minutes to seconds
- Useful in CI when jadx already ran in a previous step

**(d) `--quick` mode should skip jadx entirely** (not just be "fast at grep") — because its checks (manifest + critical grep) only need apktool + strings.

### Plan to open the PR

1. Review the skill's upstream repo (verify the exact name in `tools/android-skill/.git/config`)
2. Fork, branch `fix/auto-audit-tolerate-jadx-exit`
3. Apply (a) + (b) + (d) at minimum; (c) if the review accepts it
4. Test against the two project APKs + a small control one
5. PR with a bug description + evidence (reproducible logs)

### When to open it

**After Gate 1** (close of Plan 1, T22-T25). Before that the RE is our focus; the PR is a contribution that may take external review timing and must not block. **✅ Executed 2026-05-05.**

### Additional notes detected during the fix (not included in this PR)

Pre-existing bug: the `phase0_decode` function renames `OUTPUT_DIR` after extracting the `package_name` from the manifest, but `decode_file` (a `local` variable captured at the start) keeps pointing to the old path. Any subsequent write to `decode_file` fails. Triggers when `OUTPUT_DIR` contains the substring `audit-`. Temporary workaround: pass an explicit `output-dir` without `audit-`.

→ Candidate for a separate follow-up PR.

---

## 2. jl-uboot-tool — missing support for BR35 (AC707N)

**Upstream repo:** https://github.com/kagaimiq/jl-uboot-tool
**Status:** 🛠️ local patch (2026-05-18) — pending HW validation before PR
**Local branch:** `tools/community-re/jl-uboot-tool/` (3 files modified + 1 new binary)
**Severity:** functional — blocks use of the tool for the entire AC707N ecosystem (smartwatches, badges, etc.)

### Observed symptoms

`jl-uboot-tool` (supported-chip matrix in the README) lists BR17-BR34 + BR36, **omits BR35**. For the e-badge user (AC707N, PID 1558), trying `jluboottool.py --chip br35` fails in `get_chip_name()`, returning `None`. There is no loader binary in `data/loaderblobs/usb/` nor an entry in `data/chips.yaml` / `data/usb-loaders.yaml`.

### Root cause

BR35 (AC707N) was introduced in the JieLi catalog after the last update of the `kagaimiq/jl-uboot-tool` repo. The chip is pi32v2 + UBOOT1.00 v2 protocol + MengLi quirk (same family as BR34/BR36, which are present), but the entry simply never materialized.

### Proposed fix (local patch applied, ready for PR post-HW-test)

**(a) `data/loaderblobs/usb/br35loader.bin`** — new binary, copied from `e_badge_707_sdk_200/SDK/cpu/br35/tools/br35loader.bin` (md5 `ab0ae3c35548a06bdc94a2e5774c7a22`, 27328 B).

**(b) `data/chips.yaml`** — `br35:` entry inserted between `br34:` and `br36:` with a full memory map derived from the linker `maskrom_stubs.ld` + `sdk_ld.c`: 5 SRAM regions (isr-base, maskrom-export, ram0, dcache-ram, icache-ram), maskrom ROM, psram, sfc. Quirk `memory-rw-mengli-crypt: yes`.

**(c) `data/usb-loaders.yaml`** — `br35:` entry with `address: 0x102600` (= `_UBOOT_LOADER_RAM_START`), **`encryption: none`** (non-trivial decision: the SDK loader ships plaintext, unlike br23/br25/br28/br34 which are MengLi-encoded on disk; see the header magic heuristic).

### Validation done before the PR

- YAML parses cleanly with SafeLoader
- `get_chip_name("AC707N") → br35` confirmed, mirroring the logic of `jluboottool.py:53-63`
- Memory map cross-checked line-by-line against `maskrom_stubs.ld:196-205` and `sdk_ld.c:62-94, 580-581`
- 0 overlaps between the 8 regions
- Trace of the upload loop's XOR boolean (`jluboottool.py:686-691,705-707`) confirms that with `chip_quirk=True + cipher='none'` the host applies MengLi crypt before send
- 2 rounds of review by an independent agent — the 1st let a numeric bug in cache origins slip through (0x376000 mis-derived), the 2nd, with the explicit instruction "do not trust my arithmetic", found it → corrected to 0x372000

### Latent bug detected in jluboottool.py (candidate for a separate follow-up PR)

Line 682:
```python
block_size = spec.get('blocksize', 512)
```
Looks up the key `blocksize` (no hyphen). All existing configs in `usb-loaders.yaml` use `block-size:` with a hyphen. Result: the code never uses the config value, it always falls back to the default 512. In practice nobody notices because all configs use 512 too. Trivial fix: `spec.get('block-size', spec.get('blocksize', 512))`.

### When to open it

After empirical HW validation of the live flash (BR35 chip in MaskROM mode via a 0x16EF dongle or an M1 soft-trigger). NOT before — if the `encryption: none` is wrong or the memory map has an off-by-N undetected by static review, the PR would be noise upstream. Applies the `feedback_upstream_contributions` philosophy: document local, PR post-Gate.

---

<!-- Template for future entries:

## N. <repo> — <short title>

**Repo:** <url>
**Status:** 🔍 documented | 🛠️ local patch | 📤 PR open | ✅ merged
**Severity:** <functional / improvement / cosmetic>

### Symptoms
### Root cause
### Proposed fix
### Plan to open the PR

-->
