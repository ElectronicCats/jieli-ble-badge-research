#!/usr/bin/env bash
# scripts/build-badge-fw.sh
# Build the custom badge-menu firmware and (optionally) repack it into the
# BLE-OTA-flashable .ufw. Checks every prerequisite up front and verifies the
# artifacts afterwards, so a half-finished build cannot look like a good one.
#
# Prerequisites: scripts/setup-badge-build.sh (run it once).
#
# Outputs:
#   SDK/cpu/br35/tools/app.bin   the raw app image — ALL the BLE OTA path needs
#   SDK/output/update.ufw        native update image (USB flashing)
#   SDK/output/jl_isd.fw/.bin    full flash image (USB flashing)
#   firmware-builds/*.ufw        BLE-OTA image, with --ota

set -euo pipefail

readonly SDK_REL="tools/community-re/jieli-sdks/e_badge_707_sdk_200"
readonly PATCH_REL="patches/badge/badge-menu.patch"
readonly OEM_BASE_REL="firmware/custom-fw-ac707n.ufw"
readonly NEEDED_ULIMIT=65536

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SDK_ROOT="${REPO_ROOT}/${SDK_REL}"
readonly SDK_DIR="${SDK_ROOT}/SDK"

CHIPKEY_ID="9847"
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

  --clean            make clean first (full rebuild, ~4 min instead of ~1)
  --chipkey <id>     chipkey id passed as BADGE_CHIPKEY (default: 9847; "none" to omit)
  --ota [PATH]       also splice the fresh app.bin into the OTA image.
                     Default PATH: firmware-builds/custom-fw-ac707n-<utc>.ufw
                     (${OEM_BASE_REL} is only read, as the OEM-geometry base)
  --no-menu          build the factory watch app instead of the badge menu
  -h, --help         this message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
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
    warn "chipkey_${CHIPKEY_ID}.bin is not in the SDK download dir — the packaged"
    warn "image will be KEYLESS and a chipkey-burned unit will reject it."
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
make_args=()
[[ "${MENU}" == 1 ]] && make_args+=(MENU=1)
[[ "${CHIPKEY_ID}" != "none" ]] && export BADGE_CHIPKEY="${CHIPKEY_ID}"

readonly APP_BIN="${SDK_DIR}/cpu/br35/tools/app.bin"
rm -f "${APP_BIN}"

if [[ "${DO_CLEAN}" == 1 ]]; then
    log "make clean"
    make -C "${SDK_DIR}" clean >/dev/null
fi

log "make ${make_args[*]:-} (BADGE_CHIPKEY=${BADGE_CHIPKEY:-unset})"
make -C "${SDK_DIR}" ${make_args[@]+"${make_args[@]}"}

# --- verify ---------------------------------------------------------------
[[ -s "${APP_BIN}" ]] || fail "the build reported success but produced no app.bin"
app_size="$(stat -c%s "${APP_BIN}")"
log "app.bin: ${app_size} bytes"

for f in "${SDK_DIR}/output/update.ufw" "${SDK_DIR}/output/jl_isd.fw" "${SDK_DIR}/output/jl_isd.bin"; do
    if [[ -s "$f" ]]; then
        log "$(basename "$f"): $(stat -c%s "$f") bytes  (SDK/output/)"
    fi
done

# --- OTA repack -----------------------------------------------------------
if [[ "${DO_OTA}" == 1 ]]; then
    base="${REPO_ROOT}/${OEM_BASE_REL}"
    [[ -f "${base}" ]] || fail "OTA base image not found: ${OEM_BASE_REL}.
       swap_app.py needs it for the OEM uboot + flash geometry + Qix wrapper."

    if [[ -z "${OTA_OUT}" ]]; then
        mkdir -p "${REPO_ROOT}/firmware-builds"
        OTA_OUT="${REPO_ROOT}/firmware-builds/custom-fw-ac707n-$(date -u +%Y%m%dT%H%M%SZ).ufw"
    fi

    log "repacking app.bin into the OTA image"
    python3 "${REPO_ROOT}/tools/ufw-repack/swap_app.py" "${base}" "${APP_BIN}" "${OTA_OUT}"
    [[ -s "${OTA_OUT}" ]] || fail "swap_app.py produced no output"
    log "OTA image: ${OTA_OUT} ($(stat -c%s "${OTA_OUT}") bytes)"
    log "flash it:  qix flash <MAC> ${OTA_OUT#"${REPO_ROOT}"/} --oem   (see docs/ota-howto.md)"
fi

log "done."
