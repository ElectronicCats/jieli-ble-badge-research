#!/usr/bin/env bash
# scripts/build-jieli-ac707n-ufw.sh
# Toma un sdk.elf vanilla (producido por build-jieli-ac707n.sh) y lo empaqueta
# end-to-end a un .ufw flasheable usando wine isd_download.exe en docker.
#
# Soporta dos SDK sources (ambos AC707N, divergen en loader/packer binaries):
#   --sdk-source ac707n_watch_lvgl    (default, vanilla JieLi para AC707N+LVGL)
#   --sdk-source e_badge_707_sdk_200  (SDK del e-badge OEM, byte-equivalente a ZRun)
#
# Pipeline:
#   1. Stage flat dir con sdk.elf + static inputs del SDK seleccionado
#   2. objcopy 23 secciones del ELF (manejo robusto de secciones faltantes)
#   3. lz4_packet -> dat_bank.lz4
#   4. cat secciones -> app.bin
#   5. wine isd_download.exe en docker -> update.ufw
#   6. Validar magic header + copiar a firmware-builds/<sdk>-<rev>-<ts>/

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LZ4_PACKET="${REPO_ROOT}/tools/community-re/jieli-sdks/jl710_3.0.0_official/SDK/cpu/br56/tools/lz4_packet"
readonly OBJCOPY="/opt/jieli/pi32v2/bin/objcopy"
readonly OBJDUMP="/opt/jieli/pi32v2/bin/objdump"
readonly WINE_IMAGE="scottyhardy/docker-wine:stable"
readonly STAGE="${STAGE:-/tmp/jieli-ufw-test}"
readonly BUILDS_ROOT="${REPO_ROOT}/firmware-builds"

# defaults
SDK_SOURCE="ac707n_watch_lvgl"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sdk-source)
            SDK_SOURCE="$2"; shift 2 ;;
        --sdk-source=*)
            SDK_SOURCE="${1#*=}"; shift ;;
        -h|--help)
            grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
        *)
            echo "ERROR: arg desconocido: $1" >&2; exit 2 ;;
    esac
done

case "${SDK_SOURCE}" in
    ac707n_watch_lvgl|e_badge_707_sdk_200) ;;
    *)
        echo "ERROR: --sdk-source debe ser 'ac707n_watch_lvgl' o 'e_badge_707_sdk_200', no '${SDK_SOURCE}'" >&2
        exit 2 ;;
esac

readonly SDK_SOURCE
readonly SDK_DIR="${REPO_ROOT}/tools/community-re/jieli-sdks/${SDK_SOURCE}/SDK"
readonly TOOLS_DIR="${SDK_DIR}/cpu/br35/tools"
readonly DOWNLOAD_WATCH="${TOOLS_DIR}/download/watch"

log()  { printf '[ufw:%s] %s\n' "${SDK_SOURCE}" "$*"; }
fail() { printf '[ufw:%s] ERROR: %s\n' "${SDK_SOURCE}" "$*" >&2; exit 1; }

# Busca un input estático en orden de prioridad: tools/, tools/download/watch/, tools/utils/
find_input() {
    local name="$1"
    for sub in "" "download/watch" "utils"; do
        local candidate="${TOOLS_DIR}/${sub:+$sub/}${name}"
        if [[ -f "${candidate}" ]]; then
            echo "${candidate}"; return 0
        fi
    done
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. Validar prereqs

[[ -x "${OBJCOPY}" ]]   || fail "objcopy no ejecutable en ${OBJCOPY}"
[[ -x "${OBJDUMP}" ]]   || fail "objdump no ejecutable en ${OBJDUMP}"
[[ -f "${LZ4_PACKET}" ]] || fail "lz4_packet no presente en ${LZ4_PACKET}"
[[ -d "${TOOLS_DIR}" ]] || fail "SDK tools dir no encontrado: ${TOOLS_DIR}"

# sdk.elf viene del build del SDK seleccionado (donde build-jieli-ac707n.sh --sdk-source ${SDK_SOURCE} lo produce).
# Permite producir UFW del SDK que toque (ac707n_watch_lvgl para Vector A, e_badge_707_sdk_200 para M1 v2).
SDK_ELF_SOURCE="${REPO_ROOT}/tools/community-re/jieli-sdks/${SDK_SOURCE}/SDK/cpu/br35/tools/sdk.elf"
[[ -f "${SDK_ELF_SOURCE}" ]] \
    || fail "sdk.elf no encontrado en ${SDK_ELF_SOURCE}. Corre scripts/build-jieli-ac707n.sh primero."

ISD_EXE="$(find_input isd_download.exe)" || fail "isd_download.exe no encontrado en ${TOOLS_DIR}"

command -v sg >/dev/null || fail "comando 'sg' no disponible"
sg docker -c "docker version" >/dev/null 2>&1 \
    || fail "docker no accesible vía 'sg docker'"
sg docker -c "docker image inspect ${WINE_IMAGE}" >/dev/null 2>&1 \
    || fail "imagen ${WINE_IMAGE} no pulled"

log "prereqs OK"
log "sdk.elf source: ${SDK_ELF_SOURCE}"
log "isd_download.exe: ${ISD_EXE}"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Stage

log "stage: ${STAGE}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}"

cp "${SDK_ELF_SOURCE}" "${STAGE}/sdk.elf"

# Static inputs (busca cada uno en multiple paths del SDK seleccionado)
for f in uboot.boot cfg_tool.bin flash_params_v3.bin flash_params_v2.bin \
         p11_code.bin isd_config.ini isd_config_rule.c ota.bin tone_en.cfg \
         br35loader.bin config.dat mode.bin script.ver stream.bin; do
    src="$(find_input "${f}")" || fail "input estático faltante: ${f}"
    cp "${src}" "${STAGE}/"
done

# Inputs específicos por SDK
if [[ "${SDK_SOURCE}" == "ac707n_watch_lvgl" ]]; then
    # vanilla SDK: resfs.bin
    src="$(find_input resfs.bin)" || fail "resfs.bin requerido para ac707n_watch_lvgl"
    cp "${src}" "${STAGE}/"
elif [[ "${SDK_SOURCE}" == "e_badge_707_sdk_200" ]]; then
    # e_badge OEM: watch.bin (FATFSI_FILE referenced en isd_config.ini)
    src="$(find_input watch.bin)" || fail "watch.bin requerido para e_badge_707_sdk_200"
    cp "${src}" "${STAGE}/"
fi

cp "${LZ4_PACKET}" "${ISD_EXE}" "${STAGE}/"
chmod +x "${STAGE}/lz4_packet"

cd "${STAGE}"

# ─────────────────────────────────────────────────────────────────────────────
# 3. objcopy 23 secciones (manejo robusto de secciones faltantes)

log "objcopy: extrayendo secciones del sdk.elf"

SECTIONS=$("${OBJDUMP}" --section-headers sdk.elf 2>/dev/null | awk '/^ +[0-9]+ \./ {print $2}')

extract() {
    local sec=$1 out=$2
    if echo "${SECTIONS}" | grep -qx "${sec}"; then
        "${OBJCOPY}" -O binary -j "${sec}" sdk.elf "${out}"
    else
        : > "${out}"
    fi
}

extract .text                  text.bin
extract .data                  data.bin
extract .data_code             data_code.bin
extract .overlay_aec           aec.bin
extract .overlay_aac           aac.bin
extract .ps_ram_data_code      psr_data_code.bin
extract .dcache_ram_data       d_ram_data.bin
extract .icache_ram_data_code  i_ram_data_code.bin

for i in 0 1 2 3 4 5 6 7 8 9; do extract ".overlay_bank${i}" "bank${i}.bin"; done
for i in 0 1 2 3 4;           do extract ".overlay_vir${i}"  "vir${i}.bin";  done

# ─────────────────────────────────────────────────────────────────────────────
# 4. lz4_packet + concat -> app.bin

log "lz4_packet: comprimiendo data + data_code + banks + virs"
cat data.bin data_code.bin > dat_mix.bin

BANKS_ARGS=""; for i in 0 1 2 3 4 5 6 7 8 9; do BANKS_ARGS="${BANKS_ARGS} bank${i}.bin 0xbbaa"; done
VIRS_ARGS="";  for i in 0 1 2 3 4;           do VIRS_ARGS="${VIRS_ARGS} vir${i}.bin 0xbbaa";   done

./lz4_packet -dict text.bin -input dat_mix.bin 0 ${BANKS_ARGS} ${VIRS_ARGS} -o dat_bank.lz4 \
    > lz4_packet.log 2>&1
[[ -f dat_bank.lz4 ]] || fail "dat_bank.lz4 no se generó. Log: ${STAGE}/lz4_packet.log"

cat text.bin aec.bin aac.bin psr_data_code.bin d_ram_data.bin i_ram_data_code.bin dat_bank.lz4 \
    > app.bin
log "app.bin: $(stat -c%s app.bin) B"

# ─────────────────────────────────────────────────────────────────────────────
# 5. wine isd_download.exe en docker

log "wine isd_download.exe (docker ${WINE_IMAGE})"

chmod 777 "${STAGE}"
find "${STAGE}" -maxdepth 1 -type f -exec chmod a+rw {} +

# Args -res difieren entre SDKs:
#   ac707n_watch_lvgl: cfg_tool.bin p11_code.bin config.dat
#   e_badge_707_sdk_200: cfg_tool.bin p11_code.bin stream.bin config.dat (download_lvgl.bat línea CHIP_CMD)
case "${SDK_SOURCE}" in
    ac707n_watch_lvgl)
        RES_FILES="cfg_tool.bin p11_code.bin config.dat" ;;
    e_badge_707_sdk_200)
        RES_FILES="cfg_tool.bin p11_code.bin stream.bin config.dat" ;;
esac

# NOTA: wine isd_download.exe retorna exit 245 (crash en cleanup post-success).
# Validamos por existencia del .ufw, no por exit code.
set +e
sg docker -c "docker run --rm \
  -v ${STAGE}:/work \
  -w /work \
  ${WINE_IMAGE} \
  wine isd_download.exe \
    -tonorflash -dev br35 -boot 0x102600 -div8 -wait 300 \
    -uboot uboot.boot -app app.bin -tone tone_en.cfg \
    -res ${RES_FILES} \
    -flash-params flash_params_v3.bin \
    -output-fw jl_isd.fw -output-ufw update.ufw -reboot 500" \
  > "${STAGE}/isd_download.log" 2>&1
WINE_EXIT=$?
set -e
log "wine isd_download.exe exit ${WINE_EXIT} (245 = crash en cleanup post-success, esperado)"

# ─────────────────────────────────────────────────────────────────────────────
# 6. Validar update.ufw

[[ -f "${STAGE}/update.ufw" ]] || fail "update.ufw no se generó. Log: ${STAGE}/isd_download.log"

UFW_SIZE="$(stat -c%s "${STAGE}/update.ufw")"
[[ "${UFW_SIZE}" -gt 51200 ]] || fail "update.ufw demasiado chico: ${UFW_SIZE} B (esperado >50KB)"

MAGIC_HEX="$(xxd -s 8 -l 8 -p "${STAGE}/update.ufw")"
echo "${MAGIC_HEX}" | grep -qE 'a[37]67ce' \
    || log "WARN: magic pattern bytes 8-11 no coincide con familia JieLi observada (got: ${MAGIC_HEX})"

BYTE8="$(xxd -s 8 -l 1 -p "${STAGE}/update.ufw")"
log "byte 8 del .ufw = ${BYTE8} (vanilla=fd, ZRun OEM=fb)"

# ─────────────────────────────────────────────────────────────────────────────
# 7. Outputs versionados a firmware-builds/

SDK_REV="$(git -C "${SDK_DIR}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${BUILDS_ROOT}/${SDK_SOURCE}-ufw-${SDK_REV}-${TS}"
mkdir -p "${OUT_DIR}"

cp "${STAGE}/update.ufw"  "${OUT_DIR}/"
cp "${STAGE}/jl_isd.fw"   "${OUT_DIR}/"
cp "${STAGE}/jl_isd.bin"  "${OUT_DIR}/"
cp "${STAGE}/sdk.elf"     "${OUT_DIR}/"
cp "${STAGE}/app.bin"     "${OUT_DIR}/"
cp "${STAGE}/isd_download.log" "${OUT_DIR}/"

( cd "${OUT_DIR}" && sha256sum update.ufw jl_isd.fw jl_isd.bin sdk.elf app.bin > sha256sums.txt )

UFW_SHA="$(sha256sum "${OUT_DIR}/update.ufw" | awk '{print $1}')"
log "OK — .ufw build completo:"
log "  output:      ${OUT_DIR}/update.ufw"
log "  size:        ${UFW_SIZE} bytes"
log "  byte 8:      ${BYTE8}"
log "  sha256:      ${UFW_SHA}"
