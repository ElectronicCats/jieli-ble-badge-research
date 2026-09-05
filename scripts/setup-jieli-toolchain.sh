#!/usr/bin/env bash
# scripts/setup-jieli-toolchain.sh
# Descarga e instala el toolchain LLVM pi32v2 de JieLi para compilar el SDK AC707N.
# Idempotente: si todo ya está en su sitio, sale 0 sin tocar nada.
#
# Requiere: curl, tar, sha256sum, sudo (solo para crear /opt/jieli).
# Output: tools/jieli-toolchain/pi32v2/ + symlink /opt/jieli → tools/jieli-toolchain/

set -euo pipefail

# === CONFIGURACIÓN ============================================
# URL pretty (sigue redirect a Aliyun OSS). Versión actual: 20250805.1.
readonly TOOLCHAIN_URL="https://pkgman.jieliapp.com/s/linux-toolchain"
readonly TOOLCHAIN_ARCHIVE_NAME="jieli-linux-toolchains-20250805.1.tar.xz"
readonly TOOLCHAIN_SHA256_EXPECTED="f686586bcfb45e0f0bb27fd2b39c7a7f313cb4f0e88a66a14da621ffa8225958"
# ==============================================================

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly TOOLCHAIN_DIR="${REPO_ROOT}/tools/jieli-toolchain"
readonly CACHE_DIR="${TOOLCHAIN_DIR}/.cache"
readonly EXTRACT_DIR="${TOOLCHAIN_DIR}/pi32v2"
readonly VERSION_FILE="${TOOLCHAIN_DIR}/VERSION.txt"
readonly SYMLINK_PATH="/opt/jieli"

log()  { printf '[setup] %s\n' "$*"; }
fail() { printf '[setup] ERROR: %s\n' "$*" >&2; exit 1; }

# 1. Verificar dependencias
command -v curl >/dev/null || fail "curl no está instalado"
command -v sha256sum >/dev/null || fail "sha256sum no está instalado"
command -v tar >/dev/null || fail "tar no está instalado"

# 2. Descarga (cache-aware)
mkdir -p "${CACHE_DIR}"
readonly ARCHIVE_PATH="${CACHE_DIR}/${TOOLCHAIN_ARCHIVE_NAME}"
if [[ ! -f "${ARCHIVE_PATH}" ]]; then
    log "Descargando toolchain desde ${TOOLCHAIN_URL}"
    curl -fL --progress-bar -o "${ARCHIVE_PATH}.partial" "${TOOLCHAIN_URL}" \
        || fail "descarga falló (HTTP error o network)"
    mv "${ARCHIVE_PATH}.partial" "${ARCHIVE_PATH}"
else
    log "Archive ya en cache: ${ARCHIVE_PATH}"
fi

# 3. Validar sha256 (si se conoce el esperado)
ARCHIVE_SHA256="$(sha256sum "${ARCHIVE_PATH}" | awk '{print $1}')"
readonly ARCHIVE_SHA256
log "sha256 del archive: ${ARCHIVE_SHA256}"
if [[ -n "${TOOLCHAIN_SHA256_EXPECTED}" ]]; then
    [[ "${ARCHIVE_SHA256}" == "${TOOLCHAIN_SHA256_EXPECTED}" ]] \
        || fail "sha256 mismatch (esperado ${TOOLCHAIN_SHA256_EXPECTED}, obtenido ${ARCHIVE_SHA256})"
fi

# 4. Extracción (idempotente: si ya hay clang, skip)
if [[ -x "${EXTRACT_DIR}/bin/clang" ]]; then
    log "Toolchain ya extraído en ${EXTRACT_DIR} — skip"
else
    log "Extrayendo toolchain a ${TOOLCHAIN_DIR}"
    mkdir -p "${TOOLCHAIN_DIR}"
    # NOTA: el archive de JieLi tiene un wrapper dir top-level (jieli-linux-toolchains-X.X.X/).
    # Strip-components=1 lo descarta para que ${TOOLCHAIN_DIR}/{common,pi32v2,...} queden directos,
    # match exacto del layout que el Makefile del SDK espera (/opt/jieli/pi32v2/bin/clang).
    case "${TOOLCHAIN_ARCHIVE_NAME}" in
        *.tar.gz|*.tgz) tar -xzf "${ARCHIVE_PATH}" -C "${TOOLCHAIN_DIR}" --strip-components=1 ;;
        *.tar.xz)       tar -xJf "${ARCHIVE_PATH}" -C "${TOOLCHAIN_DIR}" --strip-components=1 ;;
        *.tar.bz2)      tar -xjf "${ARCHIVE_PATH}" -C "${TOOLCHAIN_DIR}" --strip-components=1 ;;
        *) fail "extensión no soportada: ${TOOLCHAIN_ARCHIVE_NAME}" ;;
    esac
    [[ -x "${EXTRACT_DIR}/bin/clang" ]] \
        || fail "post-extracción no encuentro ${EXTRACT_DIR}/bin/clang — layout del archive inesperado"
fi

# 5. Validar que clang corre
log "Validando clang --version"
CLANG_VERSION="$("${EXTRACT_DIR}/bin/clang" --version 2>&1)"
readonly CLANG_VERSION
log "${CLANG_VERSION}"

# 6. Symlink /opt/jieli (idempotente)
if [[ -L "${SYMLINK_PATH}" ]]; then
    EXISTING_TARGET="$(readlink -f "${SYMLINK_PATH}")"
    EXPECTED_TARGET="$(readlink -f "${TOOLCHAIN_DIR}")"
    if [[ "${EXISTING_TARGET}" == "${EXPECTED_TARGET}" ]]; then
        log "Symlink ${SYMLINK_PATH} ya correcto"
    else
        fail "${SYMLINK_PATH} existe y apunta a ${EXISTING_TARGET} (esperado ${EXPECTED_TARGET}). No sobreescribo. Borralo manual si quieres usar este toolchain."
    fi
elif [[ -e "${SYMLINK_PATH}" ]]; then
    fail "${SYMLINK_PATH} existe pero NO es symlink. No sobreescribo."
else
    log "Creando symlink ${SYMLINK_PATH} → ${TOOLCHAIN_DIR} (requiere sudo)"
    sudo ln -s "${TOOLCHAIN_DIR}" "${SYMLINK_PATH}" \
        || fail "no se pudo crear symlink (sudo denegado o /opt no escribible)"
fi

# 7. Escribir VERSION.txt
cat > "${VERSION_FILE}" <<EOF
url: ${TOOLCHAIN_URL}
archive: ${TOOLCHAIN_ARCHIVE_NAME}
sha256: ${ARCHIVE_SHA256}
installed_at: $(date -Iseconds)
clang_version: |
$(printf '%s\n' "${CLANG_VERSION}" | sed 's/^/  /')
EOF
log "Metadata escrita en ${VERSION_FILE}"

log "OK — toolchain listo. Validación final:"
log "  ls -la ${SYMLINK_PATH}/pi32v2/bin/clang"
ls -la "${SYMLINK_PATH}/pi32v2/bin/clang" || fail "clang no accesible vía symlink"
log "Setup completo."
