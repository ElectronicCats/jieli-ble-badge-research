# qix-ble — BLE client for JieLi badges (Qix + RCSP)

Python client for talking to the JieLi AC707N/BR35 "E87" e-badge over BLE. It is the
**main client** in the repo: it covers the Qix protocol (official app, service FD00),
the native JieLi RCSP protocol (service AE00), the 6-step auth handshake, OTA
flash, the partition raw-flash stager, and bind / health-dump utilities.

Original design spec: `docs/superpowers/specs/2026-05-06-qix-ble-client-design.md`.
OTA transmission hardening (reconnect + resume): see `ROBUSTNESS.md`.

## Install

Editable install from the repo root:

```bash
# runtime + tests (bleak, cryptography, dbus-fast on Linux, pytest)
pip install --user -e "tools/qix-ble[dev]"

# if you are going to use the `push` subcommands (image/video/text/pattern/bootanim)
# you also need the `media` extra (Pillow + numpy):
pip install --user -e "tools/qix-ble[dev,media]"
```

Dependencies declared in `pyproject.toml`:
- runtime: `bleak>=0.21`, `cryptography>=42`, `dbus-fast>=2` (Linux only — used by
  `bluez_cleanup.py` to clean up BlueZ zombie connections).
- `media` extra: `Pillow>=10`, `numpy>=1.26` (image/video/AVI generation for `push`).
- `dev` extra: `pytest>=7`.

CLI invocation (two equivalent forms):

```bash
qix …                         # if ~/.local/bin is on PATH (console_script from pyproject)
python tools/qix-ble/qix.py … # direct entry point, without installing the script
```

The first positional argument of almost every subcommand is the badge's `MAC`
(discover it with `qix scan`).

## Global flags

```bash
[-v | -vv]         # -v = INFO, -vv = DEBUG
[--log <path>]     # tee the logs to a file
```

## Subcommands

### Discovery and info

```bash
qix scan [--timeout 10] [--all]              # --all = do not filter by Qix SERVICE_UUID
qix info <mac> [--timeout 5]                 # REQ_BADGE_INFO (Qix cmd 0xC6 → 0xC7)
qix listen <mac> [--timeout 60]             # prints every received frame (passive)
qix auth <mac> [--timeout 5]                # isolated 6-step JieLi RCSP handshake
qix bootstrap <mac> [--timeout] [--json]    # Phase 1-5 community bootstrap (post-auth)
qix sysinfo <mac> [--kind {target,sys,both}] [--json]   # RCSP target_info 0x03 + sys_info 0x07
qix battery <mac> [--json] [--sysinfo] [--legacy] [--no-bootstrap]
```

`battery` by default uses the device-push (cmd 0x27) after auth+bootstrap (the correct path
for the E87 OEM). `--sysinfo` and `--legacy` are alternative paths that do NOT work on
this firmware (kept for other SDKs).

### Bind / health (official app)

```bash
qix bind <mac> [--lang {zh,en}] [--hour12] [--device-id N] [--json] [--no-bootstrap]
qix unbind <mac> [--bootstrap]
qix dump-health <mac> [--types steps,sleep,hr,pressure,oxygen,battery|all] \
    [--bootstrap] [--json] [--timeout 10] [--total-timeout 60]
```

### Content push (needs the `media` extra)

```bash
qix push image  <mac> <path> [--width 360] [--height 360] [--fit cover|contain|stretch] [--zoom 1.0] [--name qix_upload]
qix push video  <mac> <path> [--fps 12] [--duration S]        # GIF/APNG/WebP/AVI
qix push text   <mac> "<text>" [--mode scroll|static|pulse|wave] [--font-size 64] [--bold] [--font file.ttf]
qix push pattern <mac> {gradient|pulse|checker|rainbow|wave|plasma_waves|concentric_waves} [--frames 60]
qix push bootanim <mac> <path> [--query-only]  # EXPERIMENTAL, RGB565 raw — use --query-only first
```

### Flash/RAM dump (test-mode)

```bash
qix dump <mac> --type {flash|memory|ram} --length 0x2000000 --output dump.bin \
    [--addr 0] [--chunk-size 256] [--auth] [--bootstrap]
```

On the E87 production, `--auth --bootstrap` are empirically required to unlock
the TEST_GET_* commands (SET_TEST_MODE 0xA0 is gated without bootstrap).

### Raw commands (debug / probe)

```bash
qix raw <mac> <cmd_hex> [<payload_hex>]                 # arbitrary Qix frame
qix raw-rcsp <mac> <flag_hex> <cmd_hex> [<payload_hex>] [--no-auth]   # arbitrary RCSP frame to AE01
```

### Filesystem (post-auth, RCSP AE00)

```bash
qix fs ls  <mac> [--dev USB|SD0|SD1|FLASH|ALL] [--type 0|1] [--read-num 10] [--json]
qix fs get <mac> <id_hex> --dev USB|SD0|SD1|FLASH --output file.bin
```

### Flash / OTA

```bash
# Qix OTA (official app, service FD00) — .ufw with 27-byte Qix wrapper
qix flash <mac> <wrapped.ufw> [--no-validate] [--probe-only] [--oem] [--bootstrap] [--req-timeout S]

# native JieLi RCSP OTA (service AE00) — for SDK builds (EC-BADGE); .ufw WITHOUT Qix wrapper
qix rcsp-flash <mac> <update.ufw> [--probe-only] [--no-auth] [--json] [--relink-delay 10]
```

- `--probe-only` (both) does ZERO writes to flash: only the negotiation handshake;
  exit 0 if the badge accepts the `.ufw`, 3 if it rejects it.
- `qix flash --oem` = auth+bootstrap + long REQ_UPDATE timeout (~15s): use it for the
  first flash over a stock badge (which reacts slowly).

### Raw-flash stager (custom loader loaded)

These require the badge to be running the STAGER firmware (service 0xD0..0xD4):

```bash
qix rawflash    <mac> <file> --addr {0x17E000|ui_res|virfat|data} [--chunk 224] [--no-erase] [--verify] [--reboot]
qix stager-read <mac> {0x17E000|ui_res|virfat|data} <length> [--timeout 6]     # non-destructive
qix stager-apply <mac> <addr> <length>                                          # applies a staged code .ufw (0xD4) → uboot → reboot
qix deploy-fw   <mac> <code.ufw> [--resources flash2.bin] [--staging-addr 0x2F0000] [--chunk 224] [--verify]
qix patchflash  <mac> <file> --addr 0x300000 [--chunk 160] [--no-erase] [--no-bootstrap]
```

`deploy-fw` is the one-shot loader→custom: it optionally flashes resources to `ui_res`,
stages the code `.ufw` (the 27-byte Qix wrapper is auto-stripped) and does the apply.

### Provision (OEM → custom in one command)

```bash
qix provision <mac> --loader dumps/loader_in_jack.ufw \
    [--fw code.ufw] [--content FILE@ADDR ...] [--resources flash2.bin] \
    [--staging-addr 0x2F0000] [--reboot-timeout 90] [--no-final-reboot]
```

Phase 1 flashes the loader via the OEM lcflash path (auth+bootstrap+REQ_UPDATE), waits for the
reboot, and Phase 2 uses the stager (0xD0-0xD4) to write content and/or stage+apply the
custom firmware. `--content` is repeatable; `ADDR` is hex or a partition name
(`ui_res`/`virfat`/`data`), e.g. `gatito.bin@0x200000`.

### BlueZ housekeeping (Linux)

```bash
qix bond    <mac> [--retries 5] [--timeout 20]   # persistent+trusted bond (run once before rcsp-flash)
qix cleanup <mac> [--remove]                     # Device1.Disconnect() zombie; --remove = RemoveDevice()
qix soft-reset <mac> [--via-ota --ufw <wrapped.ufw>]   # SoC reboot (deprecated alias: `reset`)
```

`qix soft-reset --via-ota` (recommended on E87 OEM production): does the
REQ_UPDATE handshake and disconnects; the badge resets on OTA timeout in ~5-30s, without writing
anything to flash. Plain `qix soft-reset` uses cmd 0xA8 TEST_RESTART, which is gated on the
E87 OEM.

## Environment variables (transport / pairing)

- `QIX_CONNECT_ATTEMPTS` (default 4) — connection retry rounds with backoff.
- `QIX_BREDR_OFF=1` — opt-in pre-flight: `sudo -n btmgmt bredr off` + adjusted conn params
  (prevents the HID appearance from making BlueZ page classic → Page Timeout). See `ROBUSTNESS.md`.
- `QIX_PAIR` / `QIX_NO_PAIR` — force / disable the fresh Just-Works pairing on the connection
  (the badge is BLE-HID and only encrypts after pairing; used by `rcsp-flash`, `rawflash`, etc.).
- `QIX_FLASH_PAIR=1` — enables that fresh pairing in `qix flash` too (off by default so as
  not to touch the proven OEM→custom path).
- `QIX_CONNECT_BY_ADDR=1` — connect by address instead of by scanned object.

## Tests

```bash
cd tools/qix-ble && pytest tests/ -v
```

264 tests: frame/RCSP layer + auth + bind + upload + OTA/stager state machines with
mocked transport. There are NO tests of the real BLE transport (requires HW).
