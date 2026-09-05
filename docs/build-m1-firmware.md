# Build M1 self-built firmware — runbook

Guía operativa para reproducir el build del M1 self-built firmware del badge ZRun E87 (PID 1558, SoC AC707N/BR35) y referencia para futuros agentes que retomen este trabajo. Producto del trabajo persistido en `feature/m1-self-built` (sesión 2026-05-17).

**Output**: UFW flasheable via OTA BLE Vector A.1 con 3 visible signs (backlight toggle físico, BT name "M1-E87", `firmwa_version 0x9901`).

**NO produce**: LCD render, touch funcional, paridad OEM (Alipay/health/notify/AI), OTA cliente RCSP (M2 task).

> **NOTA (estado actual)** — Esta guía documenta el build **M1 v1** (`TCFG_UI_ENABLE=0`),
> que **brickeó el badge al flashear** (sin BLE adv / sin wake / sin backlight post-flash).
> Por eso dos de sus patches (`sdk-ui-disable.patch` y `sdk-makefile-single-board.patch`)
> se movieron a `patches/m1/deprecated/` — **no aplicarlos en builds nuevos**.
> El reemplazo es **M1 v2** (`e_badge_707_sdk_200/board_ac707n_csc_demo`, `TCFG_UI_ENABLE=1`,
> LCD ST77916 explícito vía `patches/m1/m1-csc-lcd-st77916.patch`). Diseño en
> `docs/superpowers/specs/2026-05-21-m1-v2-safe-build-design.md` y racional del drop en
> `patches/m1/deprecated/README.md`. El pipeline de tooling (steps 1-5, chipkey, wrap Qix)
> sigue siendo válido; lo que cambió es el set de patches y el SDK source.

---

## 1. Prerequisitos

### Sistema

- Linux x86_64 (probado Ubuntu 24.04 con kernel 6.8)
- `bash`, `git`, `python3 ≥ 3.10`
- Docker daemon + grupo `docker` configurado (script asume `sg docker` accesible)
- Wine docker image: `scottyhardy/docker-wine:stable` pulled

```bash
# Verificar prerequisitos
command -v sg && sg docker -c "docker version" && sg docker -c "docker image inspect scottyhardy/docker-wine:stable" >/dev/null
```

### Toolchain JieLi pi32v2

Símbolo `/opt/jieli/pi32v2/` debe apuntar a un install del toolchain JieLi pi32v2 (clang/objcopy/objdump/ld para target `pi32v2` `e_machine=0xF1`).

```bash
# Setup esperado
bash scripts/setup-jieli-toolchain.sh  # crea /opt/jieli symlink

# Verificar
ls -l /opt/jieli/pi32v2/bin/{clang,objcopy,objdump,ld}
```

Si `/opt/jieli` no existe, `build-jieli-ac707n.sh` falla con `ERROR: /opt/jieli no es symlink`.

### Python deps

```bash
pip install --user texture2ddecoder llvmlite
# Más deps via tools/qix-ble/pyproject.toml + community-re/jl-misctools
```

`jltech.crc` se importa desde `tools/community-re/jl-misctools/firmware/` (clonado del upstream `kagaimiq/jl-misctools` — `scripts/inject_chipkey.py` y `scripts/patch_config_dat.py` agregan ese path al `sys.path` automáticamente).

---

## 2. Setup inicial

### Branch + patches al SDK gitignored

```bash
git checkout feature/m1-self-built

# Aplicar 5 patches al SDK tree (gitignored, NO se commitea desde el SDK dir)
cd tools/community-re/jieli-sdks/ac707n_watch_lvgl
for p in ../../../../patches/m1/*.patch; do
    git apply "$p"
done
cd ../../../..

# Verificar patches aplicados
grep "TCFG_UI_ENABLE 0" tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/apps/watch/board/br35/sdk_config.h
grep "m1_backlight_toggle_task" tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/apps/watch/app_main.c
grep "'M', '1', '-', 'E', '8', '7'" tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/apps/watch/user_cfg.c
grep "M1: forzar RCSP_MODE_EN" tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/apps/watch/include/app_config.h
```

**Patches activos** en `patches/m1/` (los que quedan tras aplicar `patches/m1/*.patch`; el glob no entra en `deprecated/`):
- `patches/m1/m1-backlight-toggle.patch` — `app_main.c` agrega `m1_backlight_toggle_task` (PWM IO_LCD_PG cada 500ms) + `task_create()` justo antes de `os_start()`.
- `patches/m1/m1-bt-name.patch` — `user_cfg.c:37` `bt_cfg.edr_name` default `"YL-BR30" → "M1-E87"`.
- `patches/m1/m1-rcsp-force-ble.patch` — `app_config.h:178` fuerza `BT_AI_SEL_PROTOCOL = RCSP_MODE_EN` + `RCSP_CHANNEL_SEL = RCSP_USE_BLE`. Habilita el path BLE OTA recovery post-flash. Costo: ~+55 KB en `app.bin` (+10.6%) por linkear el server RCSP + handlers `rcsp_update_*`.
- `patches/m1/m1-csc-lcd-st77916.patch` — (M1 v2) `board_ac707n_csc_demo_cfg.h` selecciona el panel LCD ST77916 (`TCFG_LCD_SPI_ST77916_ENABLE 1`, `TCFG_LCD_GC9307_172X320 0`). Reemplaza el approach UI=0 de v1.

**Patches deprecados** en `patches/m1/deprecated/` — **NO aplicar** (brickearon el badge en v1, ver `patches/m1/deprecated/README.md`):
- `patches/m1/deprecated/sdk-makefile-single-board.patch` — `Makefile` elimina del build los .c de `board_ac7074_demo` y `board_ac707n_csc_demo` (solo `board_ac707n_demo` se compilaba). Housekeeping byte-idéntico (los .c quitados estaban guarded con `#ifdef CONFIG_BOARD_JL{xxx}_DEMO` no definidos → 0 bytes). Sin valor real.
- `patches/m1/deprecated/sdk-ui-disable.patch` — `sdk_config.h` `TCFG_UI_ENABLE 1→0`. Desactivaba la cascada LCD/touch/LVGL/JL_UI, pero también los init paths que arrancan BLE adv → badge sin adv/wake/backlight post-flash.

> El bloque "Verificar patches aplicados" de arriba (grep `TCFG_UI_ENABLE 0`, etc.) corresponde al set **v1** y **ya no aplica** al set activo v2.

### Patch al SDK shipped `config.dat`

`config.dat` es binario, no aplica patch tradicional. Usar el script:

```bash
python3 scripts/patch_config_dat.py \
  tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/download/watch/config.dat \
  --ver-info-ver 0x9901 --in-place

# Verificar (debe decir VER=0x9901, todos CRCs OK)
# NOTA: el parser dump_config_dat.py vivía en hw-sessions/ (gitignored, no versionado);
# ese árbol de sesión ya no está en el repo. patch_config_dat.py recomputa y valida los
# CRCs internamente, así que este paso de verificación externo es opcional.
```

---

## 3. Build pipeline (5 steps, 4 tools)

```
SDK source (con 6 patches: 5 SDK + 1 config.dat)
    │
    ▼ STEP 1: make + pi32v2/clang  (oficial JieLi)
    │ → sdk.elf (5.89 MB con RCSP forced; 5.47 MB sin RCSP)
    │
    ▼ STEP 2: objcopy + lz4_packet  (oficial JieLi)
    │ → app.bin (582 KB con RCSP; 526 KB sin)
    │
    ▼ STEP 3: wine isd_download.exe  (oficial JieLi)
    │ → update.ufw (chipkey=0xFFFF default, 1.44 MB con RCSP)
    │
    ▼ STEP 4: inject_chipkey.py  (post-build, nuestro)
    │ → m1-rcsp-0x9847.ufw (chipkey=0x9847 + app_area re-scrambled)
    │
    ▼ STEP 5: wrap_qix.py wrap  (nuestro)
    │ → m1-rcsp-0x9847.wrapped.ufw  ← ARTIFACT FINAL
```

### Step 1: Compile SDK

```bash
bash scripts/build-jieli-ac707n.sh 2>&1 | tee out/build-step1.log
```

**Tiempo**: ~5-15 min (LTO link). **Output**:
- `tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/sdk.elf` (~5.47 MB con UI=0)

**El script terminará con `make: *** [Makefile:1222: all] Error 127`. ESO ES ESPERADO.** El error es del post-build script interno del SDK que busca tools no instalados (`/opt/utils/report_segment_usage`, `lz4_packet`, `host-client`). El `sdk.elf` ya fue producido antes del fail. **Verificar exit code del wrapper, no del make**:

```bash
ls -la tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/sdk.elf
# size > 5 MB
```

Verificar symbols linked (LTO drops unused; M1 task debe estar):

```bash
/opt/jieli/pi32v2/bin/objdump -t tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/sdk.elf \
  | grep -E "m1_backlight|power_gate_pwm"
# Expected:
#   0c00de30 l F .text  0000002a m1_backlight_toggle_task
#   0c00dd72 l F .text  000000be power_gate_pwm_init
```

Si esos symbols NO aparecen, LTO los dropeó por falta de caller. Reverificar que `task_create(m1_backlight_toggle_task, ...)` está en `app_main()` antes de `os_start()`.

### Step 2 + 3: Pack UFW

```bash
bash scripts/build-jieli-ac707n-ufw.sh 2>&1 | tee out/build-step23.log
```

**Tiempo**: ~10-30 segundos. **Output**:
- `firmware-builds/ac707n_watch_lvgl-ufw-<sdk-rev>-<ts>/update.ufw` (~1.33 MB)
- `firmware-builds/.../app.bin` (~526 KB)
- `firmware-builds/.../sha256sums.txt`

El script imprime al final:
```
[ufw:ac707n_watch_lvgl] OK — .ufw build completo:
[ufw:ac707n_watch_lvgl]   output:      firmware-builds/.../update.ufw
[ufw:ac707n_watch_lvgl]   size:        1330656 bytes
[ufw:ac707n_watch_lvgl]   byte 8:      fb
[ufw:ac707n_watch_lvgl]   sha256:      <hash>
```

Capturar el output path para Step 4. `byte 8 = fb` (UI=0 + UFW M1) o `fd` (vanilla UI=1) — ambos son legítimos, depende del flag de boot.

**El wine isd_download.exe imprime `exit 245`. ESO ES ESPERADO.** Es un crash en cleanup post-success del .exe en wine; el `.ufw` ya fue producido. El wrapper detecta `update.ufw` existe + size > 50KB y reporta OK.

### Step 4: Inject chipkey 0x9847

`isd_download.exe` pack con chipkey=0xFFFF (SDK default) porque no le pasamos `-key L3KEY` (PEM firmada por JieLi, no disponible públicamente). Post-patch:

```bash
UFW=$(find firmware-builds -name update.ufw -newer out/build-step23.log | head -1)

python3 scripts/inject_chipkey.py \
  --ufw "$UFW" \
  --output out/m1-rcsp-0x9847.ufw \
  --chipkey 0x9847
```

**Output esperado** incluye:
```
new_chipkey               = 0x9847 (38983)
app_rescramble_applied    = True
skipped                   = False
```

Si `app_rescramble_applied = False`, el script no aplicó Paso 2. Sin Paso 2, el badge ACCEPT el OTA pero **no bootea** (boot ROM descrambla con eFuse 0x9847, pero el contenido fue scrambleado con 0xFFFF SDK default → garbage post-descramble). El default `--skip-app-rescramble` es `False` (correct), evitar pasarlo `True` salvo testing del Vector A.1 accept gate.

### Step 5: Wrap Qix 27B

```bash
python3 tools/ufw-repack/wrap_qix.py wrap \
  out/m1-rcsp-0x9847.ufw \
  -o out/m1-rcsp-0x9847.wrapped.ufw \
  --version M1.0.0

# Verify wrapper
python3 tools/ufw-repack/wrap_qix.py verify out/m1-rcsp-0x9847.wrapped.ufw
```

**Output verify esperado** — todos OK:
```
magic         OK: bcaf == bcaf
type          OK: 0x1 == 0x01
version       'M1.0.0'
payload_size  OK: ... == ...
crc16         OK: ... == ...
JLUFW footer  OK: presente=True
```

El wrapper Qix 27B es lo que la app ZRun real prepende al UFW antes del push BLE. Sin él, `qix-ble flash` rechaza con `UfwInvalid` (validación local) y el badge probablemente silent-drop al recibir el primer chunk sin metadata wire esperada.

---

## 4. Artifact final

```
out/m1-rcsp-0x9847.wrapped.ufw
  size:    1 443 643 B (1.44 MB con RCSP forced + 27 B wrapper)
  format:  Qix-wrapped JieLi UFW (jl-new-fw)
  chipkey: 0x9847 (PID 1558 production)
  entry:   0xC000100
  rcsp:    BLE OTA server activo (qix-ble flash recovery viable)
```

Reference snapshot actual (sesión 2026-05-20, branch `feature/m1-self-built`):
```
sha256(m1-rcsp-0x9847.wrapped.ufw) = 85fa7ee7bbb07d65ad21c2e93be70a095cb158246ba0a53756667a532744b724
```

Snapshot histórico (sesión 2026-05-17, build sin RCSP — superseded):
```
sha256(m1-0x9847.wrapped.ufw) = e55dd984cbebab7820237d40223716d13251ea583275bb4c469b1ea4c2c2caaf
size = 1 330 683 B
```

El sha256 varía con cualquier modificación al SDK source o config.dat. El build sin RCSP queda como referencia pero **no se recomienda flashearlo** — sin RCSP no hay path BLE para recovery post-flash si algo falla.

---

## 5. Flash al badge (Tasks 7-8)

**Pre-flight checklist** antes de cualquier intento (probe-only o real):

- [ ] Batería del badge ≥30% O cable USB conectado durante toda la ventana de flash
- [ ] `qix dump --auth` **NO** ejecutado desde el último power-cycle del badge (activa TEST_MODE flag que silent-rejecta REQ_UPDATE — ver [[reference_e87_test_mode_blocks_ota]])
- [ ] Bluetooth del teléfono **OFF** completamente (no solo cerrar app — JieLi badges 1 connection slot)
- [ ] MAC del badge conocido o `qix scan` listo

**Probe-only primero** (zero-write, smoke test wire + chipkey accept):

```bash
qix flash <MAC> out/m1-rcsp-0x9847.wrapped.ufw --probe-only
# Expected: state=1 ACCEPTED
# Si state=0 o silent: STOP, debug
```

**Real flash** (write al chip flash; potencialmente irreversible vía OTA):

```bash
qix flash <MAC> out/m1-rcsp-0x9847.wrapped.ufw
# Expected:
#   - state=1 ACCEPTED al REQ_UPDATE
#   - chunk acks vía WriteWithoutResponse cadence
#   - final 0xC3 (NOT 0xC5 — ver memoria commit 4e022cf de qix-ble)
#   - badge disconnects + reboots 5-30s post-disconnect
```

**Validation post-flash**:

```bash
# Visual: pantalla parpadea ~1Hz (sustain ≥30s)
# BLE scan: nombre "M1-E87" en lugar de "YL-BR30"
qix scan
# RCSP attr 5 query: VER=0x9901 en lugar de "11.1.0.4"
qix raw-rcsp <MAC> 0xC2 ...   # según implementación qix-ble
```

---

## 6. Gotchas / pitfalls

### Build

| Síntoma | Causa | Fix |
|---|---|---|
| `ld: cannot find -ljl_*` | Toolchain `/opt/jieli/pi32v2/` no apunta al install correcto | Re-correr `scripts/setup-jieli-toolchain.sh` |
| `make terminó con exit 2` después de "+POST-BUILD" | Esperado (Error 127 del download.sh interno) | Verificar `sdk.elf` existe + size > 5 MB |
| `wine isd_download.exe exit 245` | Esperado (crash en cleanup wine) | Verificar `update.ufw` existe + size > 50 KB |
| `objdump: m1_backlight_toggle_task not found` post-link | LTO dropeó la función por falta de caller | Verificar `task_create(m1_backlight_toggle_task,...)` está en `app_main()` |
| `byte 8 = fd` cuando esperaba `fb` (o viceversa) | Diferencia entre vanilla UI=1 build (fd) y UI=0/OEM (fb) | Cosmético, no afecta validez del UFW |

### Bash shell state entre comandos

**El cwd se persiste entre invocaciones de Bash. Shell state NO.** Si un comando hace `cd <dir>`, el siguiente Bash arranca en `<dir>`. Reset explícito si el siguiente comando necesita root del repo:

```bash
cd "$(git rev-parse --show-toplevel)" && <next command>
# o usar absolute paths siempre
```

Esto descarriló sesiones previas (background tasks que asumieron cwd=root pero quedaron en otro dir tras un `cd` anterior).

### SDK tree gitignored

`tools/community-re/jieli-sdks/*` está en `.gitignore`. **Los patches viven en `patches/m1/` y se aplican al working tree del SDK. NUNCA intentar `git add` de archivos del SDK** — `git` lo rechaza con `paths are ignored by one of your .gitignore files`.

Para tracking de cambios al SDK:
1. Editar el file dentro del SDK tree
2. Generar diff: `git -C tools/community-re/jieli-sdks/ac707n_watch_lvgl diff <file> > patches/m1/<name>.patch`
3. Commit el patch file (no el archivo SDK)
4. Re-aplicar en clean clone: `cd tools/community-re/jieli-sdks/ac707n_watch_lvgl && git apply ../../../../patches/m1/<name>.patch`

### Backlight con UI=0

`lcd_drv_backlight_ctrl_base()` está en `lcd_drive.c` (no en `power_gate.c` como decía una memoria anterior). Con `TCFG_UI_ENABLE=0`, `lcd_drive.c` aún compila pero LTO la dropea del binary final + el `__lcd`/`lcd_dat` state que necesita NO está inicializado.

**Solución**: usar primitivas `power_gate.c` standalone (`power_gate_pwm_init`, `power_gate_pwm_set_duty`). Ver memoria `reference_backlight_power_gate_ui0.md` para detalle.

### Duty range del PWM backlight

`power_gate_pwm_set_duty(IO_LCD_PG, duty)` espera `duty` en rango **0..10000** (no 0..100). Per comment del header: `pwm的低电平的占空比，0~10000对应0%~100%`. Polaridad active-high vs active-low del backlight HW puede variar — mitigar togglearndo extremos opuestos (0/10000) para visibilidad garantizada.

### isd_download.exe + chipkey production

`isd_download.exe -key <chipkey.bin>` rechaza el binario raw del chipkey 0x9847. Espera PEM `L3KEY/CKEY` firmada por JieLi (formato `-----BEGIN CHIP KEY-----`) que NO está disponible públicamente. Por eso el build queda con chipkey=0xFFFF SDK default y aplicamos `scripts/inject_chipkey.py` post-build.

Esta limitación implica que el `blimit.bin` (si existe) generado por `isd_download.exe` está firmado contra chipkey=0xFFFF. Si post-patch a 0x9847 invalida una firma RSA-1024 sobre el flash.bin, el bootloader podría hacer rollback al boot. **Status empírico**: blimit.bin NO aparece en unpack ni de OEM ni de M1 (`fwunpack_newfw.py`). Ver memoria `feedback_blimit_not_in_jlfs_unpack.md`. Validación empírica única = real flash autorizado.

### Wrap Qix verify sobre archivos cloud OEM

`python3 tools/ufw-repack/wrap_qix.py verify <oem_v103_file>` puede reportar `JLUFW footer presente=False` para los OEM cloud serving files. Eso es esperado — los cloud files tienen wrapper Qix pero NO el footer JLUFW que el packer agrega en el wrap. Para unwrap correctamente del cloud file:

```bash
python3 tools/ufw-repack/wrap_qix.py unwrap <oem_file.bin> -o <unwrapped.ufw>
# Luego fwunpack del unwrapped
```

---

## 7. Para futuros agentes

### Antes de tocar el SDK

- Verificar que la memoria `reference_sdk_vs_oem_checklist_pid1558.md` esté al día con tu pregunta. Si ya hay un hallazgo previo, NO duplicar audit.
- Leer internal research notes (not published) (design doc) y internal research notes (not published) (status pre-flash actual) ANTES de cualquier cambio.
- `patches/m1/*.patch` listan los cambios reales aplicados — son el source of truth.

### Antes de un nuevo build

- Confirmar que estás en branch `feature/m1-self-built` (o derivado). NO buildear sobre `main`.
- Verificar `git diff` en SDK tree (los patches deben estar aplicados):
  ```bash
  cd tools/community-re/jieli-sdks/ac707n_watch_lvgl && git diff --stat
  # Expected: sdk_config.h, app_main.c, user_cfg.c modified
  ```

### Build heurísticas

- **No re-correr `make clean` innecesariamente**. El build es 5-15 min. Si solo cambiás un patch, `make` incremental puede ser suficiente (no probado, default del script es `make clean && make`).
- **Si el build "falla" en post-build**, primero verificar `sdk.elf` existe. El error 127 es esperado, NO debug-able.
- **Si el build falla en link (ld errors)**, esos sí importan. Stack size warnings son benignos.

### Verificación de symbols

```bash
# ANTES de pack UFW, verificar que el cambio quedó linked
/opt/jieli/pi32v2/bin/objdump -t <sdk.elf> | grep <symbol-name>
strings <sdk.elf> | grep <string-name>
```

LTO drops aggressive. Si tu cambio no aparece, probablemente está unused. Buscar el caller chain.

### Modificaciones a `config.dat`

Usar `scripts/patch_config_dat.py` siempre, nunca byte-edit a mano. El script recompute las 3 capas de CRC:
1. Per-item payload CRC (item_head[idx].crc16)
2. item_head_crc (sobre tabla TLV 0x20:0xE0)
3. self_crc (sobre 0x06:0x20 que incluye item_head_crc + len + item_count)

Hand-edit invariablemente dejará algún CRC stale → bootloader rollback.

### Modificaciones a `isd_config.ini` binary

Usar `scripts/inject_chipkey.py` con `--source-isd-config` o `--chipkey`. Cualquier modificación al chipkey blob (32 B chipkey-bin + 2 B CRC16 nested en `isd_config.ini` dentro de `flash.bin` del UFW) requiere recompute en cadena:
1. CRC del chipkey blob
2. JLFS top-dir entry header del `isd_config.ini` (data_crc + header_crc)
3. UFW container `flash.bin` entry data_crc + entry-list listcrc + container hdrcrc

El script lo hace. Hand-edit invariablemente brickea.

### Re-scramble app_area (Paso 2)

`inject_chipkey.py` corre `_rescramble_app_area` por default (`--skip-app-rescramble=False`). El `app_area` está XOR-scrambleado con `jl_sfc_cipher(chipkey)` en el UFW. Si cambias el chipkey:
- Boot ROM descrambla con eFuse new_key
- Si app_area sigue scrambleado con old_key → garbage en runtime → no boot

`jl_sfc_cipher` es XOR-symmetric per 32-byte block. Descramble con old_key + re-scramble con new_key = bytes correctos para boot ROM.

**Solo skip Paso 2 para tests del Vector A.1 gate** (badge ACCEPTs aunque luego no booteee — útil para confirmar wire OK independiente del boot path).

### Sobre el risk blimit.bin

Memorias `feedback_ufw_repack_bank_commit_missing.md` y `reference_blimit_rsa1024_signature.md` claim que Vector A.1 BLE OTA está "cerrado arquitecturalmente" por una firma RSA-1024 sobre flash.bin que solo JieLi puede regenerar. Empíricamente blimit.bin NO aparece en unpack ni de OEM ni de M1 (`feedback_blimit_not_in_jlfs_unpack.md`).

**Status para futuros agentes**: no asumir cerrado sin re-investigar el path empírico actual. Si tu flow cambia (más patches al flash.bin, ej. patches profundos en app.bin que no toca M1 actual), el risk puede manifestarse aunque M1 no lo dispare.

### Cuándo NO usar este pipeline

- **No hay HW JieLi de los soportados**: el chipkey es per-PID; PIDs distintos requieren bundled `isd_config.ini` distinto en `hw-sessions/.../isd_config.ini`. Si target ≠ E87 PID 1558, NO usar 0x9847.
- **Necesitás OTA inválido para test**: usar `--skip-app-rescramble` en `inject_chipkey.py`. UFW ACCEPT pero no boot.
- **Build firmado oficialmente**: necesita L3KEY PEM de JieLi. Out of scope de este proyecto.

---

## 8. Referencias

- Design doc: internal research notes (not published)
- Build status pre-flash: internal research notes (not published)
- Audit SDK vs OEM: internal research notes (not published)
- Plan ejecutable (steps detallados): `docs/superpowers/plans/2026-05-17-m1-self-built-firmware.md`
- Memorias relevantes (en `~/.claude/projects/.../memory/`):
  - `reference_m1_build_complete_preflash.md` — entry point
  - `reference_sdk_rcsp_ota_native_ae00.md` — RCSP OTA opcodes nativos
  - `reference_backlight_power_gate_ui0.md` — backlight via power_gate
  - `feedback_blimit_not_in_jlfs_unpack.md` — empírica blimit
  - `reference_chipkey_injection_app_area_rescramble.md` — chipkey injection
  - `reference_jieli_config_dat_format.md` — JCRT format
  - `reference_jieli_isd_config_binary.md` — isd_config binary layout
  - `reference_ufw_repack_pipeline.md` — pipeline general
- Phase B artifacts: vivían en `hw-sessions/2026-05-17/pid1558-firmware-rev/` (parsers + outputs config.dat, isd_config, LCD init, touch fingerprint). `hw-sessions/` está gitignored y ese árbol ya no está en el repo.
- Tools externos clonados (gitignored bajo `tools/community-re/`, no versionados): `tools/community-re/jl-misctools/` (kagaimiq), `tools/community-re/derekfan668-701n_v220_earbox/` (AXS5106 driver ref)
