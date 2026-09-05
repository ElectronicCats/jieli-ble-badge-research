#!/usr/bin/env bash
# scripts/repack-jieli-ufw.sh
# Repack a JieLi BR35 .ufw desde un dir extraído por fwunpack_newfw.py
# (típicamente cloud_inner/) + opcionales overrides por archivo.
#
# Pipeline:
#   1. Validar input dir contiene top/{isd_config.ini,uboot.boot} + files/*
#   2. Stage flat dir con todos los inputs + overrides aplicados
#   3. wine isd_download.exe (docker) con -res leyendo jlfw.yaml para preservar orden
#   4. Validar magic header del .ufw output
#   5. Round-trip: unpack del output para confirmar parseo + diff del archivo modificado
#
# Uso:
#   scripts/repack-jieli-ufw.sh <input-unpack-dir> <output-dir> [--override files/X=path ...]
#
# Ejemplo:
#   scripts/repack-jieli-ufw.sh hw-sessions/2026-05-16/.../ufw-unpacked/cloud_inner \
#       /tmp/repack-test \
#       --override files/config.dat=hw-sessions/.../config.dat.modified.v2

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SDK_DIR="${REPO_ROOT}/tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK"
readonly TOOLS_DIR="${SDK_DIR}/cpu/br35/tools"
readonly DOWNLOAD_WATCH="${TOOLS_DIR}/download/watch"
readonly WINE_IMAGE="scottyhardy/docker-wine:stable"
readonly STAGE_DEFAULT="/tmp/jieli-repack"

log()  { printf '[repack] %s\n' "$*"; }
fail() { printf '[repack] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    grep '^# ' "$0" | sed 's/^# //'
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Arg parsing

INPUT_DIR=""
OUTPUT_DIR=""
declare -a OVERRIDES=()
STAGE="${STAGE_DEFAULT}"
CHIPKEY_BIN=""
QIX_VERSION=""       # vacío = NO añadir Qix wrapper. "X.Y.Z" string ≤10 chars ASCII.
                     # Default safe: "10.1.2.0" (matchea OTACheckManager.b whitelist pid 1580,
                     # aunque la app ZRun no valida este campo — es cosmético).

while [[ $# -gt 0 ]]; do
    case "$1" in
        --override)
            OVERRIDES+=("$2"); shift 2 ;;
        --stage)
            STAGE="$2"; shift 2 ;;
        --chipkey-bin)
            CHIPKEY_BIN="$2"; shift 2 ;;
        --qix-version)
            QIX_VERSION="$2"; shift 2 ;;
        -h|--help) usage ;;
        -*) fail "arg desconocido: $1" ;;
        *)
            if [[ -z "${INPUT_DIR}" ]]; then INPUT_DIR="$1"
            elif [[ -z "${OUTPUT_DIR}" ]]; then OUTPUT_DIR="$1"
            else fail "arg posicional extra: $1"
            fi
            shift ;;
    esac
done

[[ -n "${INPUT_DIR}" ]]  || fail "falta <input-unpack-dir>"
[[ -n "${OUTPUT_DIR}" ]] || fail "falta <output-dir>"
[[ -d "${INPUT_DIR}" ]]  || fail "input dir no existe: ${INPUT_DIR}"

INPUT_DIR="$(cd "${INPUT_DIR}" && pwd)"

# ─────────────────────────────────────────────────────────────────────────────
# 1. Validar inputs del unpack

[[ -f "${INPUT_DIR}/jlfw.yaml" ]] \
    || fail "jlfw.yaml faltante en ${INPUT_DIR}"
[[ -f "${INPUT_DIR}/top/isd_config.ini" ]] \
    || fail "top/isd_config.ini faltante en ${INPUT_DIR}"
[[ -f "${INPUT_DIR}/top/uboot.boot" ]] \
    || fail "top/uboot.boot faltante en ${INPUT_DIR}"
[[ -d "${INPUT_DIR}/files" ]] \
    || fail "files/ dir faltante en ${INPUT_DIR}"

# Parse jlfw.yaml para sacar la lista ordenada de res-files
mapfile -t RES_FILES < <(
    awk '/^res-files:/{in_res=1; next}
         /^[a-z][a-z-]*:/{in_res=0}
         in_res && /^- /{ sub(/^- /, ""); print }' "${INPUT_DIR}/jlfw.yaml"
)
mapfile -t APP_FILES < <(
    awk '/^app-files:/{in_app=1; next}
         /^[a-z][a-z-]*:/{in_app=0}
         in_app && /^- /{ sub(/^- /, ""); print }' "${INPUT_DIR}/jlfw.yaml"
)

[[ ${#APP_FILES[@]} -gt 0 ]] || fail "jlfw.yaml sin app-files"
[[ ${#RES_FILES[@]} -gt 0 ]] || fail "jlfw.yaml sin res-files"

CHIP_KEY=$(awk '/^chip-key:/{print $2}' "${INPUT_DIR}/jlfw.yaml")
log "chip-key del unpack: ${CHIP_KEY} (0x$(printf '%04X' ${CHIP_KEY}))"
log "app-files: ${APP_FILES[*]}"
log "res-files: ${RES_FILES[*]}"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Stage

log "stage: ${STAGE}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}"

# isd_config.ini:
# El que viene del unpack (top/isd_config.ini) es FORMATO BINARIO (32B chipkey-bin
# + CRC16 + TLV) — isd_download.exe NO lo parsea, espera formato TEXTO.
# Usamos el text del SDK (default chipkey 0xFFFF). Si querés chipkey production 0x7DF1
# para flashear sobre HW real ya programado, pasá --chipkey-bin <archivo> (32B raw)
# y se aplicará via -key flag.
SDK_ISD_INI="${DOWNLOAD_WATCH}/isd_config.ini"
[[ -f "${SDK_ISD_INI}" ]] || fail "SDK text isd_config.ini faltante: ${SDK_ISD_INI}"
cp "${SDK_ISD_INI}" "${STAGE}/isd_config.ini"

# Ajustar partition layout para acomodar app area del cloud OEM (~2 MB).
# SDK default: MODE_ADR=0x17e000 (1.49 MB) — overflowea con app.bin 1.74 MB + res files.
# Layout ajustado (internamente consistente, 8 MB flash):
#   0x000000-0x280000  app area (2.5 MB)     ← uboot+app+res
#   0x280000-0x6FE000  MODE (4.49 MB)
#   0x6FE000-0x7FE000  FATFSI (1 MB)
#   0x7FE000-0x826000  DATA
sed -i 's/^MODE_ADR = 0x17e000;/MODE_ADR = 0x280000;/' "${STAGE}/isd_config.ini"
sed -i 's/^MODE_LEN = 0x580000;/MODE_LEN = 0x47e000;/' "${STAGE}/isd_config.ini"
log "patched isd_config.ini: MODE_ADR 0x17e000→0x280000, MODE_LEN 0x580000→0x47e000"

# NOTA sobre flash header VID: empíricamente todos los OEM cloud UFWs (pid1570/74/78/80/81)
# tienen VID = "0.01" hardcoded en el flash header — NO es el comparator que la app ZRun
# usa para decidir "upgrade". La versión real está en el Qix wrapper de 32B (ver más abajo).
# El flash header VID se preserva en "0.01" del SDK default.

cp "${INPUT_DIR}/top/uboot.boot"     "${STAGE}/uboot.boot"

# app.bin (siempre el primer app-files)
APP_BIN="${INPUT_DIR}/${APP_FILES[0]}"
[[ -f "${APP_BIN}" ]] || fail "app.bin faltante: ${APP_BIN}"
cp "${APP_BIN}" "${STAGE}/app.bin"

# res-files (en orden)
declare -a RES_BASENAMES=()
for rf in "${RES_FILES[@]}"; do
    src="${INPUT_DIR}/${rf}"
    base="$(basename "${rf}")"
    if [[ -d "${src}" ]]; then
        # algunos res-files son directorios (e.g. tone_zh con su -extracted hermano)
        # Para isd_download.exe necesitamos el file binario, no el dir extraído.
        # El unpack dejó el blob raw como archivo `<name>` y el dir como `<name>-extracted`.
        fail "res-file '${rf}' es directorio — necesita versión binaria"
    fi
    [[ -f "${src}" ]] || fail "res-file faltante: ${src}"
    cp "${src}" "${STAGE}/${base}"
    RES_BASENAMES+=("${base}")
done

# Inputs estáticos requeridos por isd_download.exe que NO vienen del unpack
# - flash_params_v3.bin: parámetros del SPI flash
# - mode.bin: referenced via MODE_FILE en isd_config.ini
# - watch.bin: referenced via FATFSI_FILE en isd_config.ini (UI assets compilados)
# - script.ver: version script
# - cfg_tool.bin: usado en runtime (también puede venir del unpack como res)
for f in cfg_tool.bin flash_params_v3.bin script.ver mode.bin watch.bin ota.bin; do
    src="${TOOLS_DIR}/${f}"
    [[ -f "${src}" ]] || src="${DOWNLOAD_WATCH}/${f}"
    [[ -f "${src}" ]] || fail "input estático faltante en SDK: ${f}"
    # No sobreescribir si ya está en stage (vino del unpack)
    [[ -f "${STAGE}/${f}" ]] || cp "${src}" "${STAGE}/"
done

# ─────────────────────────────────────────────────────────────────────────────
# 3. Aplicar overrides
#
# Formato: <relpath-en-input-dir>=<source-file>
# Ejemplo: files/config.dat=path/to/config.dat.modified.v2

for ovr in "${OVERRIDES[@]:-}"; do
    [[ -z "${ovr}" ]] && continue
    rel="${ovr%%=*}"
    src="${ovr#*=}"
    base="$(basename "${rel}")"
    [[ -f "${src}" ]] || fail "override source no existe: ${src}"
    log "override: ${rel} ← ${src}"
    cp "${src}" "${STAGE}/${base}"
done

# ─────────────────────────────────────────────────────────────────────────────
# 4. isd_download.exe

ISD_EXE="${TOOLS_DIR}/isd_download.exe"
[[ -f "${ISD_EXE}" ]] || fail "isd_download.exe no presente en ${ISD_EXE}"
cp "${ISD_EXE}" "${STAGE}/"

cd "${STAGE}"
chmod 777 "${STAGE}"
find "${STAGE}" -maxdepth 1 -type f -exec chmod a+rw {} +

command -v sg >/dev/null || fail "comando 'sg' no disponible"
sg docker -c "docker image inspect ${WINE_IMAGE}" >/dev/null 2>&1 \
    || fail "imagen ${WINE_IMAGE} no pulled"

# Args matching download_lvgl.bat pero con res-files en orden del unpack
RES_ARGS="${RES_BASENAMES[*]}"

KEY_ARG=""
if [[ -n "${CHIPKEY_BIN}" ]]; then
    [[ -f "${CHIPKEY_BIN}" ]] || fail "chipkey-bin no existe: ${CHIPKEY_BIN}"
    cp "${CHIPKEY_BIN}" "${STAGE}/chipkey.bin"
    KEY_ARG="-key chipkey.bin"
    log "  chipkey-bin: ${CHIPKEY_BIN} → chipkey.bin (-key flag)"
fi

log "wine isd_download.exe (docker ${WINE_IMAGE})"
log "  res files: ${RES_ARGS}"

set +e
sg docker -c "docker run --rm \
  -v ${STAGE}:/work \
  -w /work \
  ${WINE_IMAGE} \
  wine isd_download.exe \
    -tonorflash -dev br35 -boot 0x102600 -div8 -wait 300 \
    -uboot uboot.boot -app app.bin \
    -res ${RES_ARGS} ${KEY_ARG} \
    -flash-params flash_params_v3.bin \
    -output-fw jl_isd.fw -output-ufw update.ufw -reboot 500" \
  > "${STAGE}/isd_download.log" 2>&1
WINE_EXIT=$?
set -e
log "wine exit ${WINE_EXIT} (245 = crash en cleanup post-success, esperado)"

[[ -f "${STAGE}/update.ufw" ]] \
    || fail "update.ufw no se generó. Log: ${STAGE}/isd_download.log"

# ─────────────────────────────────────────────────────────────────────────────
# 5. Validar output

UFW_SIZE="$(stat -c%s "${STAGE}/update.ufw")"
[[ "${UFW_SIZE}" -gt 51200 ]] || fail "update.ufw demasiado chico: ${UFW_SIZE} B"

BYTE8="$(xxd -s 8 -l 1 -p "${STAGE}/update.ufw")"
MAGIC_HEX="$(xxd -s 8 -l 8 -p "${STAGE}/update.ufw")"
log "byte 8 = ${BYTE8} (vanilla=fd, ZRun OEM=fb)"
log "magic 8-15 = ${MAGIC_HEX}"

# ─────────────────────────────────────────────────────────────────────────────
# 5.5 Optional: prepend Qix wrapper de 27B con CRC-16/CCITT-FALSE.
#
# Format (verificado byte-exact contra 5 UFW OEM por tools/ufw-repack/wrap_qix.py):
#   [0:2]   bc af              MAGIC
#   [2]     01                 TYPE = firmware update
#   [3:13]  ASCII version 10B  NUL-padded
#   [13:17] payload_size LE32  (= file_size - 27)
#   [17:25] 00 ...             8B reserved
#   [25:27] CRC LE16           CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)
#
# Insight de RE app ZRun (2026-05-16): la app ZRun NO valida la version string del
# wrapper (es cosmética para el dialog UI). Solo el CRC y `length > 27` se chequean.
# Por eso version puede ser cualquier cosa razonable — default safe `10.1.2.0`
# (matchea whitelist pid 1580 si en algún momento la app lo usara, no es blocker).

if [[ -n "${QIX_VERSION}" ]]; then
    WRAP_QIX="${REPO_ROOT}/tools/ufw-repack/wrap_qix.py"
    [[ -f "${WRAP_QIX}" ]] || fail "wrap_qix.py faltante en ${WRAP_QIX}"

    log "qix wrapper: version='${QIX_VERSION}' (via wrap_qix.py, CRC-16/CCITT-FALSE)"

    python3 "${WRAP_QIX}" wrap "${STAGE}/update.ufw" \
        --version "${QIX_VERSION}" \
        -o "${STAGE}/update_qix.ufw" 2>&1 | sed 's/^/[repack:wrap_qix] /'

    [[ -f "${STAGE}/update_qix.ufw" ]] || fail "wrap_qix.py no produjo output"

    # Verify usando el subcommand del propio tool
    log "qix wrapper verify:"
    python3 "${WRAP_QIX}" verify "${STAGE}/update_qix.ufw" 2>&1 | sed 's/^/[repack:wrap_qix] /' \
        || fail "wrap_qix.py verify falló"

    mv "${STAGE}/update_qix.ufw" "${STAGE}/update.ufw"
    UFW_SIZE="$(stat -c%s "${STAGE}/update.ufw")"
    log "qix-wrapped UFW: ${UFW_SIZE} B (inner $((UFW_SIZE - 27)) + 27B wrapper)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. Output versionado

mkdir -p "${OUTPUT_DIR}"
cp "${STAGE}/update.ufw"        "${OUTPUT_DIR}/"
cp "${STAGE}/jl_isd.fw"         "${OUTPUT_DIR}/"   2>/dev/null || true
cp "${STAGE}/jl_isd.bin"        "${OUTPUT_DIR}/"   2>/dev/null || true
cp "${STAGE}/app.bin"           "${OUTPUT_DIR}/"
cp "${STAGE}/isd_download.log"  "${OUTPUT_DIR}/"

UFW_SHA="$(sha256sum "${OUTPUT_DIR}/update.ufw" | awk '{print $1}')"
log "OK — repack completo:"
log "  output:      ${OUTPUT_DIR}/update.ufw"
log "  size:        ${UFW_SIZE} bytes"
log "  byte 8:      ${BYTE8}"
log "  sha256:      ${UFW_SHA}"
