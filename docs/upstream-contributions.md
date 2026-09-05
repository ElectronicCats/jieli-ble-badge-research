# Contribuciones pendientes a upstream

Tracker de mejoras y bugs detectados durante el proyecto, para aportar de vuelta a los repos comunitarios cuando cierre la fase actual. Filosofía: si una herramienta nos falla de manera reproducible, devolvemos el fix.

## Convención

Cada entrada incluye: repo target · descripción del problema · evidencia del proyecto donde lo encontramos · propuesta de fix · estado.

Estados: `🔍 documentado` → `🛠️ patch local` → `📤 PR abierto` → `✅ merged`.

---

## 1. Android-Pentesting-Skill — `auto-audit-static.sh` aborta por exit code de jadx

**Repo upstream:** https://github.com/DragonJAR/Android-Pentesting-Skill
**Estado:** 📤 PR abierto (2026-05-05) — pendiente review
**PR URL:** _<a completar — pegar el link del PR aquí>_
**Branch local:** `tools/android-skill/` rama `fix/auto-audit-tolerate-jadx-exit` (commit `8f8301e`, rebased contra `origin/main`)
**Severidad:** funcional — bloquea uso del script en APKs medianos/grandes

### Síntomas observados

Ocurrió **dos veces consecutivas** en este proyecto:

| Sesión | APK | Tamaño | Modo | Resultado | Causa raíz |
|---|---|---|---|---|---|
| 2026-05-04 | `superband-v2.1.23.apk` | 41 MB / ~8.8k clases | `--full` | ⚠️ jadx exit ≠ 0, script aborta | `ERROR - finished with errors, count: 17` (ignorables, sources se generan) |
| 2026-05-05 (T16 ZRun) | `zrun-v2.2.5.apk` | 50 MB / ~14.9k clases | `--full` y `--quick` | ❌ jadx Killed (OOM) | Heap default insuficiente para 14k clases |

En ambos casos el script aborta en Phase 0 con `[✗] jadx failed`, marcando `00-decode-info.txt` como `jadx: FAILED` y skipeando Phase 1-3 enteras.

### Causa raíz (lectura del script)

`tools/android-skill/scripts/auto-audit-static.sh` línea 319:

```bash
if $jadx_cmd -d "$OUTPUT_DIR/jadx-output" "$APK_FILE" > "$OUTPUT_DIR/jadx.log" 2>&1; then
    log_success "JADX decompiled successfully"
    echo "jadx: SUCCESS" >> "$decode_file"
else
    log_error "jadx failed"
    echo "jadx: FAILED - see $OUTPUT_DIR/jadx.log" >> "$decode_file"
fi
```

Dos problemas:

1. **`set -e` global** (asumido por la propagación del fallo) hace que cualquier exit ≠ 0 detenga el script. jadx retorna ≠ 0 cuando hay errores ignorables aunque el output sea utilizable.
2. **No se controla heap de la JVM de jadx**. APKs grandes saturan el `-Xmx` default y el kernel lo `Killed`.

### Propuesta de fix (PR a abrir)

Tres mejoras combinables:

**(a) Tolerar exit ≠ 0 si los sources se generaron:**
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

**(b) Subir heap default a 4G y permitir override por env:**
```bash
local jadx_heap="${JADX_HEAP:-4g}"
local jadx_cmd="jadx -j $(nproc) --no-imports"
JAVA_OPTS="-Xmx${jadx_heap}" $jadx_cmd ...
```

**(c) Flag `--reuse-decompile <path>` para skipear jadx:**
```bash
if [ -n "$REUSE_DECOMPILE" ] && [ -d "$REUSE_DECOMPILE/sources" ]; then
    log_info "Reusing decompile from $REUSE_DECOMPILE"
    ln -sfn "$(realpath "$REUSE_DECOMPILE")" "$OUTPUT_DIR/jadx-output"
    echo "jadx: SKIPPED (reused $REUSE_DECOMPILE)" >> "$decode_file"
else
    # ...invocar jadx normalmente...
fi
```

Argumentos para `--reuse-decompile`:
- En proyectos con varios APKs decompilamos una vez en T15 y queremos reutilizar en T16
- Reduce tiempo de auditoría iterativa de minutos a segundos
- Útil en CI cuando jadx ya corrió en step previo

**(d) Modo `--quick` debería skipear jadx completamente** (no solo "rápido en grep") — porque sus checks (manifest + grep crítico) solo necesitan apktool + strings.

### Plan para abrir el PR

1. Revisar el repo upstream del skill (verificar nombre exacto en `tools/android-skill/.git/config`)
2. Forkear, branch `fix/auto-audit-tolerate-jadx-exit`
3. Aplicar (a) + (b) + (d) como mínimo; (c) si la review lo acepta
4. Test contra los dos APKs del proyecto + uno chico de control
5. PR con descripción del bug + evidencia (logs reproducibles)

### Cuándo abrirlo

**Después de Gate 1** (cierre de Plan 1, T22-T25). Antes nos enfoca el RE; el PR es contribución que puede tomar review timing externo y no debe bloquear. **✅ Ejecutado 2026-05-05.**

### Notas adicionales detectadas durante el fix (no incluidas en este PR)

Bug pre-existente: la función `phase0_decode` renombra `OUTPUT_DIR` después de extraer el `package_name` del manifest, pero `decode_file` (variable `local` capturada al inicio) queda apuntando al path viejo. Falla cualquier escritura subsiguiente a `decode_file`. Triggea cuando `OUTPUT_DIR` contiene la subcadena `audit-`. Workaround temporal: pasar `output-dir` explícito sin `audit-`.

→ Candidato para PR follow-up separado.

---

## 2. jl-uboot-tool — falta soporte para BR35 (AC707N)

**Repo upstream:** https://github.com/kagaimiq/jl-uboot-tool
**Estado:** 🛠️ patch local (2026-05-18) — pendiente validación HW antes de PR
**Branch local:** `tools/community-re/jl-uboot-tool/` (3 archivos modificados + 1 nuevo binary)
**Severidad:** funcional — bloquea uso del tool para todo el ecosistema AC707N (smartwatches, badges, etc.)

### Síntomas observados

`jl-uboot-tool` (matriz de chips soportados en README) lista BR17-BR34 + BR36, **omite BR35**. Para el e-badge user (AC707N, PID 1558) intentar `jluboottool.py --chip br35` falla en `get_chip_name()` retornando `None`. No hay loader binary en `data/loaderblobs/usb/` ni entry en `data/chips.yaml` / `data/usb-loaders.yaml`.

### Causa raíz

BR35 (AC707N) fue introducido en el catálogo JieLi después del último update del repo `kagaimiq/jl-uboot-tool`. El chip es pi32v2 + protocol UBOOT1.00 v2 + quirk MengLi (igual familia que BR34/BR36 que sí están), pero la entry simplemente no se materializó.

### Propuesta de fix (patch local aplicado, listo para PR post-HW-test)

**(a) `data/loaderblobs/usb/br35loader.bin`** — nuevo binario, copiado desde `e_badge_707_sdk_200/SDK/cpu/br35/tools/br35loader.bin` (md5 `ab0ae3c35548a06bdc94a2e5774c7a22`, 27328 B).

**(b) `data/chips.yaml`** — entry `br35:` insertada entre `br34:` y `br36:` con memory map completo derivado del linker `maskrom_stubs.ld` + `sdk_ld.c`: 5 regiones SRAM (isr-base, maskrom-export, ram0, dcache-ram, icache-ram), maskrom ROM, psram, sfc. Quirk `memory-rw-mengli-crypt: yes`.

**(c) `data/usb-loaders.yaml`** — entry `br35:` con `address: 0x102600` (= `_UBOOT_LOADER_RAM_START`), **`encryption: none`** (decisión no-trivial: el loader del SDK viene plaintext, distinto a br23/br25/br28/br34 que están MengLi-encoded en disco; ver heurística de header magic).

### Validación realizada antes del PR

- YAML parsea limpio con SafeLoader
- `get_chip_name("AC707N") → br35` confirmado mirroring la lógica de `jluboottool.py:53-63`
- Memory map cross-checked línea-por-línea contra `maskrom_stubs.ld:196-205` y `sdk_ld.c:62-94, 580-581`
- 0 overlaps entre las 8 regiones
- Trace de la XOR boolean del upload loop (`jluboottool.py:686-691,705-707`) confirma que con `chip_quirk=True + cipher='none'` el host aplica MengLi crypt antes de send
- 2 rounds de revisión por agente independiente — la 1ra dejó pasar bug numérico en cache origins (0x376000 mal-derivado), la 2da con instrucción explícita "no confíes en mi aritmética" lo encontró → corregido a 0x372000

### Bug latente detectado en jluboottool.py (candidato a PR follow-up separado)

Línea 682:
```python
block_size = spec.get('blocksize', 512)
```
Busca key `blocksize` (sin guion). Todos los configs existentes en `usb-loaders.yaml` usan `block-size:` con guion. Resultado: el código nunca usa el value del config, siempre cae al default 512. En la práctica nadie lo nota porque todos los configs usan 512 también. Fix trivial: `spec.get('block-size', spec.get('blocksize', 512))`.

### Cuándo abrirlo

Después de validación HW empírica del flash live (BR35 chip en MaskROM mode vía dongle 0x16EF o soft-trigger M1). NO antes — si el `encryption: none` está mal o el memory map tiene un off-by-N indetectado por static review, el PR sería ruido en upstream. Aplica filosofía `feedback_upstream_contributions`: documentar local, PR post-Gate.

---

<!-- Plantilla para próximas entradas:

## N. <repo> — <título corto>

**Repo:** <url>
**Estado:** 🔍 documentado | 🛠️ patch local | 📤 PR abierto | ✅ merged
**Severidad:** <funcional / mejora / cosmético>

### Síntomas
### Causa raíz
### Propuesta de fix
### Plan para abrir el PR

-->
