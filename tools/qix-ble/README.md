# qix-ble — cliente BLE para badges JieLi (Qix + RCSP)

Cliente Python para hablar con el e-badge JieLi AC707N/BR35 "E87" por BLE. Es el
**cliente principal** del repo: cubre el protocolo Qix (app oficial, service FD00),
el protocolo nativo JieLi RCSP (service AE00), el handshake de auth de 6 pasos, OTA
flash, el stager raw-flash de particiones, y utilidades de bind / health-dump / OTA
cloud check.

Spec de diseño original: `docs/superpowers/specs/2026-05-06-qix-ble-client-design.md`.
Endurecimiento de la transmisión OTA (reconexión + resume): ver `ROBUSTNESS.md`.

## Install

Instalación editable desde la raíz del repo:

```bash
# runtime + tests (bleak, cryptography, dbus-fast en Linux, pytest)
pip install --user -e "tools/qix-ble[dev]"

# si vas a usar los subcomandos `push` (image/video/text/pattern/bootanim)
# necesitás además el extra `media` (Pillow + numpy):
pip install --user -e "tools/qix-ble[dev,media]"
```

Dependencias declaradas en `pyproject.toml`:
- runtime: `bleak>=0.21`, `cryptography>=42`, `dbus-fast>=2` (solo Linux — lo usa
  `bluez_cleanup.py` para limpiar conexiones zombie de BlueZ).
- extra `media`: `Pillow>=10`, `numpy>=1.26` (generación de imagen/video/AVI para `push`).
- extra `dev`: `pytest>=7`.

Invocación del CLI (dos formas equivalentes):

```bash
qix …                         # si ~/.local/bin está en PATH (console_script del pyproject)
python tools/qix-ble/qix.py … # entry point directo, sin instalar el script
```

El primer argumento posicional de casi todos los subcomandos es la `MAC` del badge
(descubrila con `qix scan`).

## Flags globales

```bash
[-v | -vv]         # -v = INFO, -vv = DEBUG
[--log <path>]     # tee de los logs a un archivo
```

## Subcomandos

### Descubrimiento e info

```bash
qix scan [--timeout 10] [--all]              # --all = no filtrar por SERVICE_UUID Qix
qix info <mac> [--timeout 5]                 # REQ_BADGE_INFO (Qix cmd 0xC6 → 0xC7)
qix listen <mac> [--timeout 60]             # imprime cada frame recibido (pasivo)
qix auth <mac> [--timeout 5]                # handshake JieLi RCSP de 6 pasos aislado
qix bootstrap <mac> [--timeout] [--json]    # Phase 1-5 community bootstrap (post-auth)
qix sysinfo <mac> [--kind {target,sys,both}] [--json]   # RCSP target_info 0x03 + sys_info 0x07
qix battery <mac> [--json] [--sysinfo] [--legacy] [--no-bootstrap]
```

`battery` por default usa el device-push (cmd 0x27) tras auth+bootstrap (path correcto
para el E87 OEM). `--sysinfo` y `--legacy` son paths alternativos que NO funcionan en
este firmware (dejados para otros SDKs).

### Bind / health (app oficial)

```bash
qix bind <mac> [--lang {zh,en}] [--hour12] [--device-id N] [--json] [--no-bootstrap]
qix unbind <mac> [--bootstrap]
qix dump-health <mac> [--types steps,sleep,hr,pressure,oxygen,battery|all] \
    [--bootstrap] [--json] [--timeout 10] [--total-timeout 60]
```

### Push de contenido (necesita el extra `media`)

```bash
qix push image  <mac> <path> [--width 360] [--height 360] [--fit cover|contain|stretch] [--zoom 1.0] [--name qix_upload]
qix push video  <mac> <path> [--fps 12] [--duration S]        # GIF/APNG/WebP/AVI
qix push text   <mac> "<text>" [--mode scroll|static|pulse|wave] [--font-size 64] [--bold] [--font file.ttf]
qix push pattern <mac> {gradient|pulse|checker|rainbow|wave|plasma_waves|concentric_waves} [--frames 60]
qix push bootanim <mac> <path> [--query-only]  # EXPERIMENTAL, RGB565 raw — usar --query-only primero
```

### Dump de flash/RAM (test-mode)

```bash
qix dump <mac> --type {flash|memory|ram} --length 0x2000000 --output dump.bin \
    [--addr 0] [--chunk-size 256] [--auth] [--bootstrap]
```

En el E87 production, `--auth --bootstrap` son empíricamente necesarios para desbloquear
los comandos TEST_GET_* (SET_TEST_MODE 0xA0 está gated sin bootstrap).

### Comandos crudos (debug / probe)

```bash
qix raw <mac> <cmd_hex> [<payload_hex>]                 # frame Qix arbitrario
qix raw-rcsp <mac> <flag_hex> <cmd_hex> [<payload_hex>] [--no-auth]   # frame RCSP arbitrario a AE01
```

### Filesystem (post-auth, RCSP AE00)

```bash
qix fs ls  <mac> [--dev USB|SD0|SD1|FLASH|ALL] [--type 0|1] [--read-num 10] [--json]
qix fs get <mac> <id_hex> --dev USB|SD0|SD1|FLASH --output file.bin
```

### Flash / OTA

```bash
# OTA Qix (app oficial, service FD00) — .ufw con wrapper Qix de 27 bytes
qix flash <mac> <wrapped.ufw> [--no-validate] [--probe-only] [--oem] [--bootstrap] [--req-timeout S]

# OTA nativa JieLi RCSP (service AE00) — para builds del SDK (EC-BADGE); .ufw SIN wrapper Qix
qix rcsp-flash <mac> <update.ufw> [--probe-only] [--no-auth] [--json] [--relink-delay 10]
```

- `--probe-only` (ambos) hace ZERO escritura a flash: solo el handshake de negociación;
  exit 0 si el badge acepta el `.ufw`, 3 si lo rechaza.
- `qix flash --oem` = auth+bootstrap + timeout largo de REQ_UPDATE (~15s): usarlo para el
  primer flash sobre un badge stock (que reacciona lento).

### Stager raw-flash (loader custom cargado)

Requieren que el badge esté corriendo la firmware STAGER (servicio 0xD0..0xD4):

```bash
qix rawflash    <mac> <file> --addr {0x17E000|ui_res|virfat|data} [--chunk 224] [--no-erase] [--verify] [--reboot]
qix stager-read <mac> {0x17E000|ui_res|virfat|data} <length> [--timeout 6]     # no destructivo
qix stager-apply <mac> <addr> <length>                                          # aplica un code .ufw staged (0xD4) → uboot → reboot
qix deploy-fw   <mac> <code.ufw> [--resources flash2.bin] [--staging-addr 0x2F0000] [--chunk 224] [--verify]
qix patchflash  <mac> <file> --addr 0x300000 [--chunk 160] [--no-erase] [--no-bootstrap]
```

`deploy-fw` es el one-shot loader→custom: opcionalmente flashea recursos a `ui_res`,
stagea el code `.ufw` (el wrapper Qix de 27 bytes se auto-strippea) y hace apply.

### Provision (OEM → custom en un comando)

```bash
qix provision <mac> --loader dumps/loader_in_jack.ufw \
    [--fw code.ufw] [--content FILE@ADDR ...] [--resources flash2.bin] \
    [--staging-addr 0x2F0000] [--reboot-timeout 90] [--no-final-reboot]
```

Phase 1 flashea el loader por el path OEM lcflash (auth+bootstrap+REQ_UPDATE), espera el
reboot, y Phase 2 usa el stager (0xD0-0xD4) para escribir contenido y/o stagear+aplicar la
firmware custom. `--content` es repetible; `ADDR` es hex o nombre de partición
(`ui_res`/`virfat`/`data`), ej. `gatito.bin@0x200000`.

### Housekeeping BlueZ (Linux)

```bash
qix bond    <mac> [--retries 5] [--timeout 20]   # bond persistente+trusted (correr 1 vez antes de rcsp-flash)
qix cleanup <mac> [--remove]                     # Device1.Disconnect() zombie; --remove = RemoveDevice()
qix soft-reset <mac> [--via-ota --ufw <wrapped.ufw>]   # reboot del SoC (alias deprecado: `reset`)
```

`qix soft-reset --via-ota` (recomendado en E87 OEM production): hace el handshake
REQ_UPDATE y se desconecta; el badge se resetea por timeout OTA en ~5-30s, sin escribir
nada a flash. `qix soft-reset` a secas usa cmd 0xA8 TEST_RESTART, que está gated en el
E87 OEM.

## Variables de entorno (transporte / pairing)

- `QIX_CONNECT_ATTEMPTS` (default 4) — rondas de retry de conexión con backoff.
- `QIX_BREDR_OFF=1` — pre-flight opt-in: `sudo -n btmgmt bredr off` + conn params ajustados
  (evita que el appearance HID haga que BlueZ pagine classic → Page Timeout). Ver `ROBUSTNESS.md`.
- `QIX_PAIR` / `QIX_NO_PAIR` — fuerza / desactiva el pairing Just-Works fresco en la conexión
  (el badge es BLE-HID y solo encripta tras pairing; usado por `rcsp-flash`, `rawflash`, etc.).
- `QIX_FLASH_PAIR=1` — habilita ese pairing fresco también en `qix flash` (off por default para
  no tocar el path OEM→custom probado).
- `QIX_CONNECT_BY_ADDR=1` — conectar por dirección en vez de por objeto scaneado.

## Tests

```bash
cd tools/qix-ble && pytest tests/ -v
```

264 tests: frame/RCSP layer + auth + bind + upload + state machines de OTA/stager con
transport mockeado. NO hay tests del transport BLE real (requiere HW).
