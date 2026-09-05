#!/usr/bin/env bash
# scripts/build-vector-a.sh
# End-to-end pipeline para Vector A (custom firmware via BLE Qix sin teardown):
#   1. Build JieLi UFW vanilla (asume sdk.elf ya producido por build-jieli-ac707n.sh)
#   2. Wrap el .ufw vanilla con header Qix de 27 bytes (formato byte-exacto verificado
#      contra los 5 .ufw OEM ZRun, ver tools/ufw-repack/wrap_qix.py)
#   3. Output: .ufw flasheable directo via UpdateManager.startUpdate(bytes) por BLE
#
# Pendiente con HW: validar que el badge ZRun acepta este .ufw. Si rechaza por byte 8
# distinto al esperado (`fd` vs `fb` ZRun OEM), recompilar contra e_badge_707_sdk_200.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SDK_SOURCE="${SDK_SOURCE:-ac707n_watch_lvgl}"
readonly VERSION="${VERSION:-99.99.99.99}"

log() { printf '[vector-a] %s\n' "$*"; }
fail() { printf '[vector-a] ERROR: %s\n' "$*" >&2; exit 1; }

# 1. Producir JieLi UFW vanilla
log "step 1/2: build JieLi UFW vanilla (sdk-source=${SDK_SOURCE})"
bash "${REPO_ROOT}/scripts/build-jieli-ac707n-ufw.sh" --sdk-source "${SDK_SOURCE}" >/dev/null

# Localizar el último build
LATEST_BUILD="$(ls -td ${REPO_ROOT}/firmware-builds/${SDK_SOURCE}-ufw-* 2>/dev/null | head -1)"
[[ -d "${LATEST_BUILD}" ]] || fail "no se encontró build reciente de ${SDK_SOURCE}"
VANILLA_UFW="${LATEST_BUILD}/update.ufw"
[[ -f "${VANILLA_UFW}" ]] || fail "vanilla .ufw no encontrado: ${VANILLA_UFW}"

# 2. Wrap con Qix header
TS="$(date +%Y%m%d-%H%M%S)"
WRAPPED="${REPO_ROOT}/firmware-builds/vector-a-${SDK_SOURCE}-${TS}.ufw"

log "step 2/2: wrap con header Qix v=${VERSION}"
python3 "${REPO_ROOT}/tools/ufw-repack/wrap_qix.py" wrap \
    "${VANILLA_UFW}" \
    --version "${VERSION}" \
    -o "${WRAPPED}"

# Verify
log "verify del .ufw wrappeado:"
python3 "${REPO_ROOT}/tools/ufw-repack/wrap_qix.py" verify "${WRAPPED}"

cat <<EOF

[vector-a] OK — candidate firmware para Vector A:
  ${WRAPPED}

Siguiente step (con HW): mandar via BLE Qix:
  UpdateManager.init(listener, isFirmwareUpdate=true)
  UpdateManager.startUpdate(<bytes de este .ufw>)

Si el badge rechaza por byte 8 (vanilla=fd, ZRun OEM=fb), recompilar el SDK base
contra e_badge_707_sdk_200 (Nivel 2 — requiere adaptar build-jieli-ac707n.sh).
EOF
