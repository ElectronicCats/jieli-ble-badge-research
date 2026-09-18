#!/usr/bin/env bash
# scripts/build-badge-fw.sh
# Build the custom badge-menu firmware and (optionally) repack it into the
# BLE-OTA-flashable .ufw. Checks every prerequisite up front and verifies the
# artifacts afterwards, so a half-finished build cannot look like a good one.
#
# Prerequisites: scripts/setup-badge-build.sh (run it once).
#
# Two boards are supported, selected with --board (they are NOT interchangeable —
# different panel, touch controller and buttons):
#   e87   (default)  E87 / DragonJAR badge  — ST77916 panel, AXS5106L touch, 2 ADKEY.
#                    OTA image = Qix-wrapped fw-custom, flashed with `qix flash --oem`.
#   dg01             DG01 wristband         — badge2_360 panel, CST816D touch, 1 button.
#                    OTA image = vanilla .ufw, flashed with `qix rcsp-flash` (pure RCSP).
#
# Outputs:
#   SDK/cpu/br35/tools/app.bin   the raw app image — ALL the BLE OTA path needs
#   SDK/output/update.ufw        native update image (USB flashing; also the DG01 OTA base)
#   SDK/output/jl_isd.fw/.bin    full flash image (USB flashing)
#   firmware-builds/*.ufw        BLE-OTA image, with --ota

set -euo pipefail

readonly SDK_REL="tools/community-re/jieli-sdks/e_badge_707_sdk_200"
readonly PATCH_REL="patches/badge/badge-menu.patch"
readonly OEM_BASE_REL="firmware/custom-fw-ac707n.ufw"
readonly BOARD_STAMP_REL="cpu/br35/tools/.badge-board"
readonly NEEDED_ULIMIT=65536

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SDK_ROOT="${REPO_ROOT}/${SDK_REL}"
readonly SDK_DIR="${SDK_ROOT}/SDK"

BOARD="e87"
CHIPKEY_ID=""          # empty = per-board default (see below)
DO_CLEAN=0
DO_OTA=0
OTA_OUT=""
MENU=1

log()  { printf '\033[1;36m[build]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[build] WARNING:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[build] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
usage: scripts/build-badge-fw.sh [options]

  --board <b>        target board: e87 (default) or dg01. Switching boards forces
                     a full rebuild — the flag only changes -D defines, which this
                     SDK's Makefile does not track as a dependency.
  --clean            make clean first (full rebuild, ~4 min instead of ~1)
  --chipkey <id>     chipkey id passed as BADGE_CHIPKEY ("none" to omit).
                     Default: 9847 for --board e87, b165 for --board dg01.
  --ota [PATH]       also package the fresh build as a BLE-OTA image. Default PATH:
                       e87   firmware-builds/custom-fw-ac707n-<utc>.ufw
                             (app.bin spliced into ${OEM_BASE_REL},
                              which is only read, as the OEM-geometry base)
                       dg01  firmware-builds/custom-fw-dg01-<utc>.ufw
                             (SDK/output/update.ufw with the DG01 chipkey injected)
  --no-menu          build the factory watch app instead of the badge menu
  -h, --help         this message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --board)   BOARD="${2:?--board needs a value}"; shift 2 ;;
        --clean)   DO_CLEAN=1; shift ;;
        --chipkey) CHIPKEY_ID="${2:?--chipkey needs a value}"; shift 2 ;;
        --no-menu) MENU=0; shift ;;
        --ota)
            DO_OTA=1; shift
            if [[ $# -gt 0 && "$1" != --* ]]; then OTA_OUT="$1"; shift; fi
            ;;
        -h|--help) usage; exit 0 ;;
        *)         usage >&2; fail "unknown option: $1" ;;
    esac
done

# --- board -----------------------------------------------------------------
case "${BOARD}" in
    e87)  DEFAULT_CHIPKEY="9847"; OTA_STEM="custom-fw-ac707n" ;;
    dg01) DEFAULT_CHIPKEY="b165"; OTA_STEM="custom-fw-dg01"   ;;
    *)    fail "unknown --board '${BOARD}' — use e87 or dg01" ;;
esac
[[ -n "${CHIPKEY_ID}" ]] || CHIPKEY_ID="${DEFAULT_CHIPKEY}"
readonly BOARD CHIPKEY_ID OTA_STEM

# --- prerequisites --------------------------------------------------------
[[ -x /opt/jieli/pi32v2/bin/clang ]] \
    || fail "pi32v2 toolchain not at /opt/jieli/pi32v2 — run scripts/setup-badge-build.sh"
[[ -f "${SDK_DIR}/Makefile" ]] \
    || fail "SDK not checked out at ${SDK_REL} — run scripts/setup-badge-build.sh"

if ! git -C "${SDK_ROOT}" apply --reverse --check "${REPO_ROOT}/${PATCH_REL}" 2>/dev/null; then
    fail "badge-menu.patch is not applied to the SDK — run scripts/setup-badge-build.sh"
fi

if [[ "${MENU}" == 1 && ! -x "${SDK_DIR}/tools/linux/isd_download" ]]; then
    warn "SDK/tools/linux/ is missing: app.bin will be built (enough for BLE OTA),"
    warn "but update.ufw / jl_isd.fw for USB flashing will not be. Run"
    warn "scripts/setup-badge-build.sh to install them."
fi

if [[ "${CHIPKEY_ID}" != "none" \
      && ! -f "${SDK_DIR}/cpu/br35/tools/download/watch/chipkey_${CHIPKEY_ID}.bin" ]]; then
    if [[ "${BOARD}" == "dg01" ]]; then
        # isd_download's -key file format carries a CRC we have not reversed, so we
        # cannot synthesize chipkey_b165.bin. It is not needed: isd_download packs
        # with whatever key file it does find (or 0xFFFF), and the --ota step below
        # rewrites the chipkey blob AND re-scrambles the app area to 0xB165 with
        # scripts/inject_chipkey.py. Only the USB isd_download path would need the file.
        log "no chipkey_${CHIPKEY_ID}.bin — the --ota step injects chipkey 0xB165 instead"
    else
        warn "chipkey_${CHIPKEY_ID}.bin is not in the SDK download dir — the packaged"
        warn "image will be KEYLESS and a chipkey-burned unit will reject it."
    fi
fi

# The final link opens ~1130 files at once. Below that, ld fails with a
# misleading "File format not recognized" on an arbitrary object.
if [[ "$(ulimit -n)" -lt "${NEEDED_ULIMIT}" ]]; then
    ulimit -n "${NEEDED_ULIMIT}" 2>/dev/null \
        || fail "cannot raise ulimit -n to ${NEEDED_ULIMIT} (hard limit: $(ulimit -Hn)).
       Raise it in /etc/security/limits.conf and re-login."
    log "raised ulimit -n to $(ulimit -n)"
fi

export PATH="/opt/jieli/pi32v2/bin:${PATH}"

# --- build ----------------------------------------------------------------
make_args=(BOARD="${BOARD}")
[[ "${MENU}" == 1 ]] && make_args+=(MENU=1)
[[ "${CHIPKEY_ID}" != "none" ]] && export BADGE_CHIPKEY="${CHIPKEY_ID}"
# download.sh reads this to decide whether to pass -ex_api_bin. The -D on the
# make line is the source of truth; this is belt-and-braces for a hand-run download.sh.
export BADGE_BOARD="${BOARD}"

readonly APP_BIN="${SDK_DIR}/cpu/br35/tools/app.bin"
readonly BOARD_STAMP="${SDK_DIR}/${BOARD_STAMP_REL}"
rm -f "${APP_BIN}"

# BOARD only changes -D defines, which this SDK's Makefile does not track as a
# dependency — an incremental build after a board switch would silently mix objects
# from both panels. Stamp the board and force a clean whenever it changes.
prev_board=""
[[ -f "${BOARD_STAMP}" ]] && prev_board="$(cat "${BOARD_STAMP}")"
if [[ -n "${prev_board}" && "${prev_board}" != "${BOARD}" ]]; then
    log "board changed ${prev_board} -> ${BOARD}: forcing a full rebuild"
    DO_CLEAN=1
fi

if [[ "${DO_CLEAN}" == 1 ]]; then
    log "make clean"
    make -C "${SDK_DIR}" clean >/dev/null
fi

log "make ${make_args[*]} (BADGE_CHIPKEY=${BADGE_CHIPKEY:-unset})"
if ! make -C "${SDK_DIR}" "${make_args[@]}"; then
    rm -f "${BOARD_STAMP}"      # unknown object mix — make the next run clean
    fail "make failed"
fi
printf '%s' "${BOARD}" > "${BOARD_STAMP}"

# --- verify ---------------------------------------------------------------
[[ -s "${APP_BIN}" ]] || fail "the build reported success but produced no app.bin"
app_size="$(stat -c%s "${APP_BIN}")"
log "app.bin: ${app_size} bytes"

for f in "${SDK_DIR}/output/update.ufw" "${SDK_DIR}/output/jl_isd.fw" "${SDK_DIR}/output/jl_isd.bin"; do
    if [[ -s "$f" ]]; then
        log "$(basename "$f"): $(stat -c%s "$f") bytes  (SDK/output/)"
    fi
done

# --- OTA packaging ---------------------------------------------------------
# The two boards take DIFFERENT OTA paths, so they need differently packaged images:
#
#   e87   Qix (FD00, opcode 0xC0) app-only OTA. The OEM uboot performs the CODE
#         rewrite, so the image must keep it: app.bin is spliced into an OEM-geometry
#         base that already carries the OEM uboot and the 27-byte Qix wrapper.
#   dg01  pure native RCSP OTA (AE00, E1..E8). The whole vanilla .ufw is transferred,
#         loader included, so the SDK's own update.ufw IS the image — it only needs
#         the unit's chipkey (0xB165) in place of whatever isd_download packed.
if [[ "${DO_OTA}" == 1 ]]; then
    if [[ -z "${OTA_OUT}" ]]; then
        mkdir -p "${REPO_ROOT}/firmware-builds"
        OTA_OUT="${REPO_ROOT}/firmware-builds/${OTA_STEM}-$(date -u +%Y%m%dT%H%M%SZ).ufw"
    fi

    case "${BOARD}" in
    e87)
        base="${REPO_ROOT}/${OEM_BASE_REL}"
        [[ -f "${base}" ]] || fail "OTA base image not found: ${OEM_BASE_REL}.
       swap_app.py needs it for the OEM uboot + flash geometry + Qix wrapper."

        log "repacking app.bin into the OTA image"
        python3 "${REPO_ROOT}/tools/ufw-repack/swap_app.py" "${base}" "${APP_BIN}" "${OTA_OUT}"
        [[ -s "${OTA_OUT}" ]] || fail "swap_app.py produced no output"
        log "OTA image: ${OTA_OUT} ($(stat -c%s "${OTA_OUT}") bytes)"
        log "flash it:  qix flash <MAC> ${OTA_OUT#"${REPO_ROOT}"/} --oem   (see docs/ota-howto.md)"
        ;;
    dg01)
        native="${SDK_DIR}/output/update.ufw"
        [[ -s "${native}" ]] || fail "SDK/output/update.ufw was not produced — the RCSP OTA
       image is the native .ufw itself. Install the post-build tools with
       scripts/setup-badge-build.sh and rebuild."

        # 1. Drop flash2.bin — the 1 MB resource/second-bank image. This build embeds its
        #    resources in the code, so shipping it would only double the over-the-air
        #    transfer and overwrite the unit's OEM sdfile/virfat partitions for nothing.
        apponly="$(mktemp -t dg01-apponly-XXXXXX.ufw)"
        trap 'rm -f "${apponly}"' EXIT
        log "stripping flash2.bin from the native update.ufw"
        python3 "${REPO_ROOT}/tools/ufw-repack/make_app_only_ufw.py" "${native}" "${apponly}"
        [[ -s "${apponly}" ]] || fail "make_app_only_ufw.py produced no output"

        # 2. Re-key to the DG01's chipkey: rewrite the isd_config.ini blob AND re-scramble
        #    the app area, or the unit's boot ROM cannot descramble the code it XIPs.
        log "injecting chipkey 0x${CHIPKEY_ID^^} into the app-only .ufw"
        python3 "${REPO_ROOT}/scripts/inject_chipkey.py" \
            --ufw "${apponly}" --output "${OTA_OUT}" --chipkey "0x${CHIPKEY_ID}"
        [[ -s "${OTA_OUT}" ]] || fail "inject_chipkey.py produced no output"
        # 3. Splice in the OEM's EXT_RESERVED/RESFS directory. isd_download cannot emit
        #    it (it forces globally ascending addresses and sorts the expand section
        #    last, so RESFS 0x1F7000 after BTIF 0x3FE000 is rejected), so the ini leaves
        #    RESFS undeclared and we graft the factory block on afterwards.
        log "splicing the OEM EXT_RESERVED/RESFS directory into the app-area chain"
        python3 "${REPO_ROOT}/scripts/fix_area_table.py" "${OTA_OUT}" \
            --chipkey "0x${CHIPKEY_ID}" \
            || fail "fix_area_table.py could not graft EXT_RESERVED — see above."

        # 4. Assert the declared geometry matches the unit's factory table. PRCT_LEN is
        #    derived from the app size, so a few KB of code growth silently pushes CODE
        #    past 0xF7000; the loader then writes nothing and still reports success.
        log "checking the declared flash geometry against the DG01's factory table"
        python3 "${REPO_ROOT}/scripts/check_dg01_geometry.py" "${OTA_OUT}" \
            || fail "the packed image does not declare this unit's geometry — see above.
       Most likely the app grew past the CODE window (0xF7000), or -ex_api_bin came back."

        # 5. Also emit a Qix-wrapped copy. Two OTA paths reach this firmware and they are
        #    NOT equivalent:
        #      - RCSP (qix rcsp-flash) is the SDK's own updater. It works, but it is gated:
        #        rcsp_update.c:394 refuses below battery level 3 unless the charger is
        #        online. That gate is what blocks a drained unit.
        #      - Qix (qix flash) is this project's own server, qix_ota_server.c, which has
        #        no battery check at all. It is the custom->custom path, the same one the
        #        E87 uses, and the only one that works on a low battery.
        #    Note qix flash needs QIX_FLASH_PAIR=1 on this host: the custom firmware's
        #    update screen is BLE-HID and BlueZ drops the link on the first FD02 write
        #    unless a Just-Works agent pairs fresh (see cli.py cmd_flash).
        qixout="${OTA_OUT%.ufw}-qix.ufw"
        log "wrapping a Qix copy for the custom->custom path"
        python3 "${REPO_ROOT}/tools/ufw-repack/wrap_qix.py" wrap "${OTA_OUT}" -o "${qixout}" \
            || fail "wrap_qix.py failed"
        python3 "${REPO_ROOT}/tools/ufw-repack/wrap_qix.py" verify "${qixout}" >/dev/null \
            || fail "the Qix wrapper did not verify"

        log "OTA image: ${OTA_OUT} ($(stat -c%s "${OTA_OUT}") bytes)"
        log "  custom->custom (no battery gate):"
        log "     QIX_FLASH_PAIR=1 qix flash <MAC> ${qixout#"${REPO_ROOT}"/}"
        log "  from OEM firmware (RCSP, refuses below battery level 3):"
        log "     qix rcsp-flash <MAC> ${OTA_OUT#"${REPO_ROOT}"/}"
        log "  wired, always works:  UBOOT — extract flash.bin and write it at 0x0 (see"
        log "                         docs/dump-firmware.md); it is exactly CODE_LEN, so"
        log "                         VM/MODE/RESFS/EXIF/BTIF/key_mac are left untouched"
        ;;
    esac
fi

log "done."
