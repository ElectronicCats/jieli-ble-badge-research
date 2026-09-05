# qix-ble — Robustez de transmisión OTA (2026-08-24)

Endurecimiento del cliente BLE para sobrevivir a drops/interferencia (incl. un **jammer
Bluetooth**) durante los flashes largos (~1MB Qix 0xC0 y el RCSP loader-download).

## Contexto

En una tanda de vueltas OEM→custom→OEM, con un jammer BT activo cerca, la transmisión se cortaba
a mitad del flash. El cliente viejo (sin reconexión/resume) dejaba el badge con el SDFILE
**borrado-a-medias** (erase hecho, write nunca completado) → textos corruptos. Estos cambios hacen
que un drop se **recupere** en vez de corromper.

## Cambios

### 1. Retry de conexión + verificación de service discovery (`transport.py`)
`_connect` ahora es un loop acotado con backoff que envuelve scan → connect → **verificar
servicios resueltos** → MTU → pair → notify.
- El badge es BLE-HID: BlueZ paginа/encripta a mitad del discovery y el link suele dropear ahí
  ("failed to discover services" / "device disconnected"). Eso es un `BleakError` (no
  `TimeoutError`), que antes **no** se reintentaba y abortaba.
- `_services_resolved()` trata un cliente "connected" cuya tabla GATT no tiene la write char OTA
  (`CHAR_FD02_WRITE` Qix / `CHAR_AE01_WRITE` RCSP) como fallo **reintentable** (el caso de
  servicios vacíos que produce el drop del HID-bond).
- Entre intentos limpia con `BleakClient.disconnect()` (no el force_disconnect DBus).
- Env `QIX_CONNECT_ATTEMPTS` (default 4), backoff `min(2*intento, 6)s`.
- El bloque MTU/pair/notify se extrajo **verbatim** a `_post_connect_setup()`; el pairing
  fresh-per-connection queda intacto.

### 2. `reconnect()` (`transport.py`)
Re-establece el link en el MISMO loop/thread (espejo de `disconnect()`), para reanudar un
flash/loader-download tras un drop. Hereda el retry+backoff de `_connect`.

### 3. Resume por offset del flash Qix 0xC0 (`update_manager.py`, `cli.py`)
La OTA Qix es device-driven por offset (`RET_UPDATE`/`0xC3` devuelven el offset del badge), o sea
**reanudable**. Ante `BleConnectionError`/timeout a mitad de un chunk: `on_reconnect()` →
`drain()` → re-emitir `REQ_UPDATE` (`_reissue_req_update`) → continuar desde el offset que reporta
el badge. `flash(on_reconnect=..., max_reconnects=4)`. En el CLI se pasa `on_reconnect=t.reconnect`
solo cuando NO es el path bootstrap/`--oem` (bootstrap requiere re-correr auth).

### 4. Pre-flight opt-in: `bredr off` + conn params (`bluez_cleanup.py`, `bond.py`, `cli.py`)
- `set_bredr_off()`: `sudo -n btmgmt bredr off` (sin password hardcodeado; warnea limpio si
  necesita pass). Evita que el appearance HID haga que BlueZ pagine classic → Page Timeout.
- `set_le_conn_interval()`: escribe debugfs `conn_{min,max}_interval / conn_latency /
  supervision_timeout = 12/12/0/600` (15ms, latency 0) — link rápido y sin skips.
- Gated por env `QIX_BREDR_OFF=1`. Respeta la regla "NO remover el bond del host" de `bond.py`.

## Uso

```sh
# flash robusto (retry + resume + pre-flight bredr off)
QIX_BREDR_OFF=1 QIX_CONNECT_ATTEMPTS=6 python3 qix.py flash <MAC> firmware.ufw [--oem]
QIX_BREDR_OFF=1 QIX_CONNECT_ATTEMPTS=6 python3 qix.py rcsp-flash <MAC> loaderdl.ufw --relink-delay 14
```

`set_bredr_off()` usa `sudo -n`; para automatizarlo agregá a sudoers:
`<user> ALL=(root) NOPASSWD: /usr/bin/btmgmt`. Si no, corré `sudo btmgmt bredr off` a mano una vez.

## Validación

4 vueltas OEM→custom→OEM seguidas por BLE: 8/8 legs OK, 0 crashes, 0 corrupción de textos.
(El detalle de la firmware vivía en el repo hermano del SDK `e_badge_707_sdk_200`.)
Suite: 264/264 tests verdes tras los cambios (`cd tools/qix-ble && pytest tests/`).
