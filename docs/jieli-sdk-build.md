# Build JieLi AC707N SDK end-to-end (sdk.elf → update.ufw)

How-to permanente para reproducir el firmware vanilla del SDK `ac707n_watch_lvgl` desde fuente hasta `.ufw` flasheable, en Linux con wine + docker. Este flow reemplaza el `download_lvgl.bat` Windows del SDK upstream.

> Status: **validado end-to-end 2026-05-06**. `update.ufw` 3.6 MB producido y copiado a `firmware-builds/`. Pendiente flash en HW (badges físicos esperados ~mediados de mayo 2026).

## Pipeline (3 scripts, idempotentes)

```
scripts/setup-jieli-toolchain.sh         (one-time)
scripts/setup-jieli-lvgl-port.sh         (one-time)
scripts/build-jieli-ac707n.sh            (cada cambio de fuente → sdk.elf)
scripts/build-jieli-ac707n-ufw.sh        (cada cambio de fuente → update.ufw)
```

## One-time setup

### 1. Toolchain pi32v2 (LLVM-fork de JieLi)

```bash
./scripts/setup-jieli-toolchain.sh
# requiere sudo solo para el symlink /opt/jieli (one-time)
```

Descarga `jieli-linux-toolchains-20250805.1.tar.xz` (25 MB) de `pkgman.jieliapp.com/s/linux-toolchain` (sin auth, redirige a Aliyun OSS), extrae a `tools/jieli-toolchain/`, crea symlink `/opt/jieli/`. clang 4.0.1 fork con target `pi32v2 -mcpu=r3`.

### 2. LVGL port assembly

```bash
./scripts/setup-jieli-lvgl-port.sh
```

Clona los repos públicos de `gitlab.zh-jieli.com` (acceso vía git daemon anónimo, **GET no HEAD** — ver bitácora 2026-05-05) y los ensambla en `SDK/lv_port_pc_vscode/`:

```
lv_port_pc_vscode/         <- gitlab.zh-jieli.com/707_lite_lvgl/lvgl_portable_code
lv_port_pc_vscode/lvgl/    <- gitlab.zh-jieli.com/707_lite_lvgl/lvgl_core
```

### 3. Mirror defensivo (para `lz4_packet` Linux)

El packer `lz4_packet` Linux ELF necesario en el pipeline NO viene con el SDK ac707n (ese trae solo `.exe`). Lo tomamos del SDK `jl710_3.0.0_official` (otro chip de la misma family — el binario es chip-agnostic, sha1 idéntico cross-family).

```bash
# Ya hecho en side-quest C — ver internal research notes (not published)
ls tools/community-re/jieli-sdks/jl710_3.0.0_official/SDK/cpu/br56/tools/lz4_packet
```

### 4. Docker + wine image

```bash
# Add user a grupo docker (one-time, requiere sudo)
sudo usermod -aG docker $USER
# Logout/login O usar `sg docker -c` en sesiones existentes

# Pull wine image (one-time, ~1.5 GB)
sg docker -c "docker pull scottyhardy/docker-wine:stable"
```

> **No usar wine32 nativo en Ubuntu 24.04** — el t64 transition + el conflicto `libgd3` del PPA `ondrej/php` rompe la instalación. Docker es el bypass clean.

## Build cada vez

### sdk.elf (vanilla)

```bash
./scripts/build-jieli-ac707n.sh
```

- Tiempo: ~71s (LTO link es lo lento)
- Output: `tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/sdk.elf` (10 MB)
- Copia versionada a `firmware-builds/ac707n-vanilla-<rev>-<ts>/`

### update.ufw

```bash
./scripts/build-jieli-ac707n-ufw.sh
```

- Tiempo: ~30s (mayoría es spin-up del docker container)
- Output: `firmware-builds/ac707n-ufw-<rev>-<ts>/update.ufw` (3.6 MB)
- Side outputs: `jl_isd.fw` (2.4 MB), `jl_isd.bin` (1.18 MB), `app.bin` (1.15 MB), `sdk.elf` (10 MB), `sha256sums.txt`, `isd_download.log`

## Lo que hace `build-jieli-ac707n-ufw.sh` (replica reducida del `.bat`)

El `.bat` original (`SDK/cpu/br35/tools/download/watch/download_lvgl.bat`) hace dos cosas:

1. **Build de UI assets LVGL**: `json_to_res.exe json.txt` → `packres.exe` 3 veces → `mode.bin` + `resfs.bin`
2. **Pack del firmware**: `isd_download.exe` con `app.bin` + 12+ static inputs → `update.ufw`

**Decisión arquitectónica:** saltamos (1) y reusamos los `mode.bin`/`resfs.bin` pre-built del SDK upstream. Para iterar UI custom habría que añadir wine `packres.exe` + `json_to_res.exe` en el pipeline (no validado, deferred a iteración futura).

### Pipeline interno (7 pasos)

1. **Stage** `/tmp/jieli-ufw-test/` flat con `sdk.elf` + 14 static inputs + `lz4_packet` Linux ELF + `isd_download.exe`
2. **objcopy 23 secciones** del ELF: `.text`, `.data`, `.data_code`, `.overlay_aec`, `.overlay_aac`, `.ps_ram_data_code`, `.dcache_ram_data`, `.icache_ram_data_code`, `.overlay_bank0..9`, `.overlay_vir0..4`. Secciones faltantes se reemplazan con archivo vacío (en build vanilla, `.overlay_aec/.overlay_aac/.overlay_vir3/.overlay_vir4` no existen; el resto existe pero size=0)
3. **`dat_mix.bin = data.bin + data_code.bin`**
4. **`lz4_packet -dict text.bin -input dat_mix.bin 0 bank0..9.bin 0xbbaa vir0..4.bin 0xbbaa -o dat_bank.lz4`** (compresión LZ4 con diccionario)
5. **`app.bin = text.bin + aec.bin + aac.bin + psr_data_code.bin + d_ram_data.bin + i_ram_data_code.bin + dat_bank.lz4`**
6. **`chmod 777` + `chmod a+rw`** sobre stage (wineuser dentro del container es uid 1010 ≠ host uid 1000, necesita o+w)
7. **`sg docker -c "docker run --rm -v stage:/work -w /work scottyhardy/docker-wine:stable wine isd_download.exe ..."`** → produce `update.ufw`, `jl_isd.fw`, `jl_isd.bin`

### Args de `isd_download.exe`

```
-tonorflash -dev br35 -boot 0x102600 -div8 -wait 300
-uboot uboot.boot -app app.bin -tone tone_en.cfg
-res cfg_tool.bin p11_code.bin config.dat
-flash-params flash_params_v3.bin
-output-fw jl_isd.fw -output-ufw update.ufw -reboot 500
```

## Validación

### Magic header del `.ufw`

Bytes 0-3 son nonce/salt build-specific (varían entre corridas). Bytes 8-31 son el patrón JieLi consistente cross-family:

```
?? ?? ?? ?? ?? ?? ?? f8 ?? c1 a7 67 ce bf 5b 97 4e 5d ?? 48 ?? cd 27 4e ...
```

| Family | Bytes 0-3 (build-specific) | Bytes 8-15 (pattern) |
|---|---|---|
| **br35 (nuestro)** | `b923 1904` / `f8c8 6b85` / `db1c bbdd ...` | `fdc1 a767 cebf 5b97` |
| br25 (ac696x) | `8502 af7b` | `fac1 a767 cebf 5b97` |
| br29 (ac706n) | `c51b 742f` | `fbc1 a767 cebf 5b97` |
| br23 (ac695n) | `efea b424` | `f6c1 a367 cebd 5b97` |
| br28 (ac701n) | `ceff a2ba` | `fac1 a767 cebf 5b97` |

El script valida bytes 8-11 contra el regex `a[37]67ce`.

### Builds NO son byte-equivalent entre corridas

Por el nonce de los primeros 4 bytes, cada corrida produce un `.ufw` con sha256 distinto pese a las mismas fuentes. **Reproducibilidad funcional sí, byte-for-byte no.**

## Quirks conocidos

### Wine retorna exit 245 después del éxito

`isd_download.exe` produce todos los outputs y luego crashea en cleanup (probable segfault al desmount/exit del wine prefix). El script captura el exit code pero **valida por existencia del `update.ufw`**, no por exit. Logs útiles al final del `isd_download.log`:

```
Device Offline                                        ← esperado (no hay USB device en container)
0024:fixme:kernelbase:AppPolicyGetProcessTerminationMethod
[exit 245]
```

### Permisos del bind mount

El `wineuser` (uid 1010) dentro del container ≠ host user (uid 1000). Para que pueda escribir outputs al volume, el script hace `chmod 777` en el dir + `chmod a+rw` en files antes del `docker run`. Outputs quedan owned por uid 1010 — el script los `cp` (no `mv`) al `firmware-builds/<rev>-<ts>/` para preservarlos con el owner del usuario host.

### `lz4_packet` ejecutable

`cp` desde el mirror dropea el bit `+x`. El script hace `chmod +x stage/lz4_packet` después del copy.

## Limitaciones del flow actual

- **No regenera UI assets LVGL** (skip `packres` + `json_to_res`). Para UI custom: añadir step que invoque vía wine ambos `.exe` antes de `isd_download.exe`.
- **No firma el `.ufw`**. Si el bootloader requiere firma cripto (no observado pero posible), este flow no la añade.
- **Wine errores no fatales** llenan el log: fixmes de winebth, OLE marshaling, mountmgr. Esperados, ignorables.
- **Sin tests de flash en HW**. Hardware esperado mediados mayo 2026 — primera prueba de flash requerirá HW (USB BSL o, más probable, vía path BLE Vector A si los `TEST_GET_*` opcodes están abiertos).

## Referencias

- Spec/plan: `docs/superpowers/specs/2026-05-05-jieli-sdk-build-pipeline-design.md` + `docs/superpowers/plans/2026-05-05-jieli-sdk-build-pipeline.md`
- Bitácoras: internal research notes (not published) (Task 6 — primera compilación) + internal research notes (not published) (Task 7 — pipeline completo)
- `.bat` original: `tools/community-re/jieli-sdks/ac707n_watch_lvgl/SDK/cpu/br35/tools/download_lvgl.bat` (entry point) + `download/watch/download_lvgl.bat` (sub-script con `isd_download.exe`)
