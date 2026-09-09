#!/usr/bin/env bash
# scripts/setup-badge-build.sh
# One-shot, idempotent setup of everything needed to build the custom badge
# firmware from source. Safe to re-run: it skips whatever is already in place.
#
# What it does:
#   1. pi32v2 cross toolchain     -> /opt/jieli/pi32v2 (via setup-jieli-toolchain.sh)
#   2. vanilla JieLi SDK          -> tools/community-re/jieli-sdks/e_badge_707_sdk_200
#                                    (git submodule, anonymous clone from JieLi GitLab)
#   3. badge-menu patch           -> applied onto the SDK worktree
#   4. native post-build tools    -> SDK/tools/linux/ (JieLi "linux-postbuild" package)
#   5. chipkey                    -> SDK/cpu/br35/tools/download/watch/
#
# Then build with:  scripts/build-badge-fw.sh
#
# Requires: git, curl, tar, sha256sum. Step 1 needs sudo once (for /opt/jieli).

set -euo pipefail

# === CONFIG ===================================================
# Pretty URL, follows a redirect to Aliyun OSS. Always serves the *latest*
# package, so the sha256 below is the version this repo was validated against,
# not a hard pin: a newer one warns and continues.
readonly POSTBUILD_URL="https://pkgman.jieliapp.com/s/linux-postbuild"
readonly POSTBUILD_ARCHIVE="jieli-linux-post-build-tools-20260908.1.tar.xz"
readonly POSTBUILD_SHA256_VALIDATED="db95ce04689f39ef2b74775fbb722330a2f7a8d4d99a57204184bb5791fec52c"
readonly POSTBUILD_TOOLS=(isd_download ufw_maker fw_add packres json_to_res fat_comm remove_tailing_zeros)

readonly SDK_REL="tools/community-re/jieli-sdks/e_badge_707_sdk_200"
readonly PATCH_REL="patches/badge/badge-menu.patch"
readonly DEFAULT_CHIPKEY="9847"
# ==============================================================

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SDK_ROOT="${REPO_ROOT}/${SDK_REL}"
readonly CACHE_DIR="${REPO_ROOT}/tools/jieli-toolchain/.cache"

CHIPKEY_ID="${DEFAULT_CHIPKEY}"
SKIP_TOOLCHAIN=0
SKIP_POSTBUILD=0

log()  { printf '\033[1;36m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup] WARNING:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[setup] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
usage: scripts/setup-badge-build.sh [options]

  --chipkey <id>     chipkey to install (default: ${DEFAULT_CHIPKEY}; "none" to skip).
                     Reads tools/chipkey/chipkey_<id>.bin.
  --skip-toolchain   do not touch /opt/jieli (no sudo prompt)
  --skip-postbuild   do not download SDK/tools/linux (BLE-OTA-only builds
                     need just app.bin, which the compiler produces anyway)
  -h, --help         this message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --chipkey)        CHIPKEY_ID="${2:?--chipkey needs a value}"; shift 2 ;;
        --skip-toolchain) SKIP_TOOLCHAIN=1; shift ;;
        --skip-postbuild) SKIP_POSTBUILD=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        *)                usage >&2; fail "unknown option: $1" ;;
    esac
done

for c in git curl tar sha256sum; do
    command -v "$c" >/dev/null || fail "$c is not installed"
done

# --- 1. toolchain ---------------------------------------------------------
if [[ "${SKIP_TOOLCHAIN}" == 1 ]]; then
    log "1/5 toolchain: skipped (--skip-toolchain)"
elif [[ -x /opt/jieli/pi32v2/bin/clang ]]; then
    log "1/5 toolchain: already at /opt/jieli/pi32v2 — skip"
else
    log "1/5 toolchain: installing pi32v2 (setup-jieli-toolchain.sh)"
    "${REPO_ROOT}/scripts/setup-jieli-toolchain.sh"
fi

# --- 2. SDK submodule -----------------------------------------------------
if [[ -f "${SDK_ROOT}/SDK/Makefile" ]]; then
    log "2/5 SDK: already checked out at ${SDK_REL} — skip"
else
    log "2/5 SDK: cloning the vanilla JieLi SDK (~1.1 GB, several minutes)"
    log "        source: $(git -C "${REPO_ROOT}" config -f .gitmodules \
                            --get "submodule.${SDK_REL}.url")"
    git -C "${REPO_ROOT}" submodule update --init --depth 1 "${SDK_REL}" \
        || fail "submodule clone failed — see docs/build-badge-firmware.md §Troubleshooting"
    [[ -f "${SDK_ROOT}/SDK/Makefile" ]] || fail "SDK checked out but SDK/Makefile is missing"
fi

# --- 3. badge patch -------------------------------------------------------
readonly PATCH_ABS="${REPO_ROOT}/${PATCH_REL}"
[[ -f "${PATCH_ABS}" ]] || fail "patch not found: ${PATCH_REL}"

if git -C "${SDK_ROOT}" apply --reverse --check "${PATCH_ABS}" 2>/dev/null; then
    log "3/5 patch: badge-menu.patch already applied — skip"
elif git -C "${SDK_ROOT}" apply --check "${PATCH_ABS}" 2>/dev/null; then
    log "3/5 patch: applying badge-menu.patch"
    git -C "${SDK_ROOT}" apply --whitespace=nowarn "${PATCH_ABS}"
else
    fail "badge-menu.patch neither applies nor is applied — the SDK worktree is dirty.
       Reset it with:  git -C ${SDK_REL} checkout . && git -C ${SDK_REL} clean -fd"
fi

# --- 4. native post-build tools -------------------------------------------
readonly POST_DIR="${SDK_ROOT}/SDK/tools/linux"
if [[ "${SKIP_POSTBUILD}" == 1 ]]; then
    log "4/5 post-build tools: skipped (--skip-postbuild) — no update.ufw/jl_isd.fw"
else
    missing=0
    for t in "${POSTBUILD_TOOLS[@]}"; do
        [[ -x "${POST_DIR}/${t}" ]] || missing=1
    done
    if [[ "${missing}" == 0 ]]; then
        log "4/5 post-build tools: already in SDK/tools/linux — skip"
    else
        mkdir -p "${CACHE_DIR}"
        archive="${CACHE_DIR}/${POSTBUILD_ARCHIVE}"
        if [[ -f "${archive}" ]]; then
            log "4/5 post-build tools: archive already cached"
        else
            log "4/5 post-build tools: downloading (~37 MB) from ${POSTBUILD_URL}"
            curl -fL --progress-bar -o "${archive}.partial" "${POSTBUILD_URL}" \
                || fail "download failed"
            mv "${archive}.partial" "${archive}"
        fi

        got="$(sha256sum "${archive}" | awk '{print $1}')"
        if [[ "${got}" != "${POSTBUILD_SHA256_VALIDATED}" ]]; then
            warn "post-build package sha256 ${got}"
            warn "differs from the validated ${POSTBUILD_SHA256_VALIDATED}."
            warn "JieLi ships a rolling 'latest' package, so this is most likely just a"
            warn "newer release. Continuing; report it if the build then misbehaves."
        fi

        tmp="$(mktemp -d)"
        trap 'rm -rf "${tmp}"' EXIT
        tar -xJf "${archive}" -C "${tmp}"
        src="$(dirname "$(find "${tmp}" -name isd_download -type f -print -quit)")"
        [[ -n "${src}" && -f "${src}/isd_download" ]] \
            || fail "isd_download not found inside ${POSTBUILD_ARCHIVE}"

        mkdir -p "${POST_DIR}"
        for t in "${POSTBUILD_TOOLS[@]}"; do
            [[ -f "${src}/${t}" ]] || fail "${t} missing from the package"
            install -m 0755 "${src}/${t}" "${POST_DIR}/${t}"
        done
        log "      installed ${#POSTBUILD_TOOLS[@]} tools into SDK/tools/linux/"
    fi
fi

# --- 5. chipkey -----------------------------------------------------------
readonly KEY_DST_DIR="${SDK_ROOT}/SDK/cpu/br35/tools/download/watch"
if [[ "${CHIPKEY_ID}" == "none" ]]; then
    log "5/5 chipkey: skipped — the build will emit a keyless image"
else
    key_src="${REPO_ROOT}/tools/chipkey/chipkey_${CHIPKEY_ID}.bin"
    if [[ ! -f "${key_src}" ]]; then
        warn "5/5 chipkey: ${key_src#"${REPO_ROOT}"/} not found."
        warn "    The build will emit a KEYLESS image, which a chipkey-burned unit rejects."
        warn "    Dump your unit's key or pass --chipkey none to silence this."
    else
        install -m 0644 "${key_src}" "${KEY_DST_DIR}/chipkey_${CHIPKEY_ID}.bin"
        log "5/5 chipkey: installed chipkey_${CHIPKEY_ID}.bin into the SDK download dir"
    fi
fi

cat <<EOF

$(log "setup complete.")

  Build it:   scripts/build-badge-fw.sh --chipkey ${CHIPKEY_ID}
  Docs:       docs/build-badge-firmware.md
EOF
