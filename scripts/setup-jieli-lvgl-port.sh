#!/usr/bin/env bash
# scripts/setup-jieli-lvgl-port.sh
# Ensambla SDK/lv_port_pc_vscode/ desde dos repos JieLi públicos:
#   lvgl_portable_code  -> SDK/lv_port_pc_vscode/        (wrapper: main/, lv_conf.h, lv_drivers/)
#   lvgl_core           -> SDK/lv_port_pc_vscode/lvgl/   (LVGL v8 source)
# Ambos MIT licensed — son redistribuciones de upstream LVGL por JieLi.
# Idempotente: si ya está ensamblado correctamente, sale 0 sin tocar.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SDK_DIR="${REPO_ROOT}/tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK"
readonly TARGET="${SDK_DIR}/lv_port_pc_vscode"

readonly URL_PORTABLE="https://gitlab.zh-jieli.com/707_lite_lvgl/lvgl_portable_code.git"
readonly URL_CORE="https://gitlab.zh-jieli.com/707_lite_lvgl/lvgl_core.git"

log()  { printf '[lvgl-port] %s\n' "$*"; }
fail() { printf '[lvgl-port] ERROR: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null || fail "git no está instalado"
[[ -d "${SDK_DIR}" ]] || fail "SDK no encontrado en ${SDK_DIR}"

# Idempotente: si los paths críticos ya resuelven, no tocar
if [[ -f "${TARGET}/lvgl/lvgl.mk" \
   && -f "${TARGET}/lv_conf.h" \
   && -f "${TARGET}/main/src/main.c" \
   && -f "${TARGET}/lvgl/tests/src/test_fonts/ubuntu_font.c" ]]; then
    log "lv_port_pc_vscode ya ensamblado en ${TARGET} — skip"
    exit 0
fi

# Si TARGET existe pero está incompleto, rehacer desde cero (clone fallido previo)
if [[ -d "${TARGET}" ]]; then
    log "Limpiando ${TARGET} parcial/incompleto"
    rm -rf "${TARGET}"
fi

log "Cloning lvgl_portable_code (wrapper) → ${TARGET}"
git clone --depth=1 "${URL_PORTABLE}" "${TARGET}" \
    || fail "clone falló: ${URL_PORTABLE} (gitlab.zh-jieli.com puede haber cerrado acceso)"

log "Cloning lvgl_core (LVGL source) → ${TARGET}/lvgl"
git clone --depth=1 "${URL_CORE}" "${TARGET}/lvgl" \
    || fail "clone falló: ${URL_CORE}"

# Validación de paths exactos que el Makefile del SDK requiere
for f in "${TARGET}/lvgl/lvgl.mk" \
         "${TARGET}/lv_conf.h" \
         "${TARGET}/main/src/main.c" \
         "${TARGET}/lvgl/tests/src/test_fonts/ubuntu_font.c"; do
    [[ -f "$f" ]] || fail "post-ensamble path falta: $f"
done

log "OK — lv_port_pc_vscode ensamblado:"
log "  size:  $(du -sh "${TARGET}" | cut -f1)"
log "  paths: lvgl/lvgl.mk, lv_conf.h, main/src/main.c, lvgl/tests/src/test_fonts/ubuntu_font.c"
