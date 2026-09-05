#!/usr/bin/env bash
# scripts/build-jieli-ac707n.sh
# Compila un SDK JieLi (ac707n_watch_lvgl o e_badge_707_sdk_200) y copia
# sdk.elf/sdk.ufw a firmware-builds/. Idempotente en outputs.
#
# Uso:
#   scripts/build-jieli-ac707n.sh                                  # default: ac707n_watch_lvgl
#   scripts/build-jieli-ac707n.sh --sdk-source e_badge_707_sdk_200 # OEM E87 SDK
#
# Requiere: scripts/setup-jieli-toolchain.sh ejecutado previamente.

set -euo pipefail

# Parse args
SDK_SOURCE="ac707n_watch_lvgl"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --sdk-source) SDK_SOURCE="$2"; shift 2 ;;
        --sdk-source=*) SDK_SOURCE="${1#*=}"; shift ;;
        -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
        *) printf '[build] ERROR: arg desconocido: %s\n' "$1" >&2; exit 2 ;;
    esac
done
readonly SDK_SOURCE

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SDK_DIR="${REPO_ROOT}/tools/community-re/jieli-sdks/${SDK_SOURCE}/SDK"
readonly TOOLCHAIN_DIR="${REPO_ROOT}/tools/jieli-toolchain"
readonly SYMLINK_PATH="/opt/jieli"
readonly BUILDS_ROOT="${REPO_ROOT}/firmware-builds"

log()  { printf '[build] %s\n' "$*"; }
fail() { printf '[build] ERROR: %s\n' "$*" >&2; exit 1; }

# 1. Validar entorno
[[ -L "${SYMLINK_PATH}" ]] || fail "${SYMLINK_PATH} no es symlink. Corre scripts/setup-jieli-toolchain.sh primero."
SYMLINK_TARGET="$(readlink -f "${SYMLINK_PATH}")"
EXPECTED_TARGET="$(readlink -f "${TOOLCHAIN_DIR}")"
readonly SYMLINK_TARGET EXPECTED_TARGET
[[ "${SYMLINK_TARGET}" == "${EXPECTED_TARGET}" ]] \
    || fail "${SYMLINK_PATH} apunta a ${SYMLINK_TARGET} (esperado ${EXPECTED_TARGET})"

[[ -x "${SYMLINK_PATH}/pi32v2/bin/clang" ]] || fail "clang no ejecutable en ${SYMLINK_PATH}/pi32v2/bin/"
[[ -d "${SDK_DIR}" ]] || fail "SDK no encontrado en ${SDK_DIR}"
[[ -f "${SDK_DIR}/Makefile" ]] || fail "Makefile no encontrado en ${SDK_DIR}"

# 2. Subir ulimit (LTO abre muchísimos archivos)
ulimit -n 8096 2>/dev/null || fail "no se pudo subir ulimit -n a 8096"
log "ulimit -n = $(ulimit -n)"

# 3. Calcular OUT_DIR
SDK_REV="$(git -C "${SDK_DIR}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date +%Y%m%d-%H%M%S)"
readonly SDK_REV TS
readonly OUT_DIR="${BUILDS_ROOT}/${SDK_SOURCE}-vanilla-${SDK_REV}-${TS}"
mkdir -p "${OUT_DIR}"
log "Output dir: ${OUT_DIR}"

# 4. Build (vanilla — lv_port_pc_vscode/ ensamblado por scripts/setup-jieli-lvgl-port.sh)
log "make clean && make en ${SDK_DIR}"
readonly BUILD_LOG="${OUT_DIR}/build.log"
START_TS="$(date +%s)"
readonly START_TS

# Subshell para preservar pwd; tee captura stdout+stderr al log.
# PIPESTATUS[0] preserva el exit del make pese al pipe a tee.
set +e
( cd "${SDK_DIR}" && make clean && make ) 2>&1 | tee "${BUILD_LOG}"
MAKE_EXIT="${PIPESTATUS[0]}"
set -e
readonly MAKE_EXIT

ELAPSED=$(( $(date +%s) - START_TS ))
readonly ELAPSED
log "make terminó en ${ELAPSED}s con exit ${MAKE_EXIT}"

# 5. Localizar y copiar artefactos.
# Nota: POST-BUILD del SDK ejecuta download.sh que invoca herramientas externas
# (host-client, /opt/utils/report_segment_usage) ausentes en este entorno —
# falla con exit != 0 PERO el link step ya produjo sdk.elf. El pipeline real
# de packaging (build-jieli-ac707n-ufw.sh) usa el ELF directo.
# Por eso: ignoramos MAKE_EXIT si sdk.elf existe; solo fallamos si NO existe.
readonly ELF_SRC="${SDK_DIR}/cpu/br35/tools/sdk.elf"
readonly UFW_SRC="${SDK_DIR}/cpu/br35/tools/sdk.ufw"

if [[ ! -f "${ELF_SRC}" ]]; then
    fail "make falló (exit ${MAKE_EXIT}) y sdk.elf no existe en ${ELF_SRC}. Log: ${BUILD_LOG}"
fi
if [[ "${MAKE_EXIT}" -ne 0 ]]; then
    log "WARN: make exit=${MAKE_EXIT} (POST-BUILD downstream tools fallaron) pero sdk.elf existe — continuando"
fi

cp "${ELF_SRC}" "${OUT_DIR}/sdk.elf"
[[ -f "${UFW_SRC}" ]] && cp "${UFW_SRC}" "${OUT_DIR}/sdk.ufw" || log "sdk.ufw no existe (POST-BUILD failed); skip copy"
( cd "${OUT_DIR}" && sha256sum sdk.elf $( [[ -f sdk.ufw ]] && echo sdk.ufw ) > sha256sums.txt )

# 6. Copiar metadata del toolchain
cp "${TOOLCHAIN_DIR}/VERSION.txt" "${OUT_DIR}/toolchain-version.txt"

# 7. Resumen final
UFW_SIZE="$(stat -c%s "${OUT_DIR}/sdk.ufw")"
UFW_SHA="$(sha256sum "${OUT_DIR}/sdk.ufw" | awk '{print $1}')"
readonly UFW_SIZE UFW_SHA
log "OK — build completo:"
log "  output:      ${OUT_DIR}/sdk.ufw"
log "  size:        ${UFW_SIZE} bytes"
log "  sha256:      ${UFW_SHA}"
log "  build time:  ${ELAPSED}s"
log "  log:         ${BUILD_LOG}"
