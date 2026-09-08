# qix-ble — OTA transmission robustness (2026-08-24)

Hardening of the BLE client to survive drops/interference (including a **Bluetooth
jammer**) during the long flashes (~1MB Qix 0xC0 and the RCSP loader-download).

## Context

During a batch of OEM→custom→OEM round-trips, with a BT jammer active nearby, the transmission cut out
midway through the flash. The old client (without reconnect/resume) left the badge with the SDFILE
**half-erased** (erase done, write never completed) → corrupted texts. These changes make
a drop **recover** instead of corrupting.

## Changes

### 1. Connection retry + service-discovery verification (`transport.py`)
`_connect` is now a bounded loop with backoff that wraps scan → connect → **verify
resolved services** → MTU → pair → notify.
- The badge is BLE-HID: BlueZ pages/encrypts midway through discovery and the link usually drops there
  ("failed to discover services" / "device disconnected"). That is a `BleakError` (not
  `TimeoutError`), which previously was **not** retried and aborted.
- `_services_resolved()` treats a "connected" client whose GATT table lacks the OTA write char
  (`CHAR_FD02_WRITE` Qix / `CHAR_AE01_WRITE` RCSP) as a **retryable** failure (the empty-services
  case produced by the HID-bond drop).
- Between attempts it cleans up with `BleakClient.disconnect()` (not the DBus force_disconnect).
- Env `QIX_CONNECT_ATTEMPTS` (default 4), backoff `min(2*attempt, 6)s`.
- The MTU/pair/notify block was extracted **verbatim** into `_post_connect_setup()`; the
  fresh-per-connection pairing stays intact.

### 2. `reconnect()` (`transport.py`)
Re-establishes the link on the SAME loop/thread (mirror of `disconnect()`), to resume a
flash/loader-download after a drop. Inherits the retry+backoff from `_connect`.

### 3. Offset resume of the Qix 0xC0 flash (`update_manager.py`, `cli.py`)
The Qix OTA is device-driven by offset (`RET_UPDATE`/`0xC3` return the badge's offset), i.e.
**resumable**. On `BleConnectionError`/timeout midway through a chunk: `on_reconnect()` →
`drain()` → re-emit `REQ_UPDATE` (`_reissue_req_update`) → continue from the offset the
badge reports. `flash(on_reconnect=..., max_reconnects=4)`. In the CLI, `on_reconnect=t.reconnect`
is passed only when it is NOT the bootstrap/`--oem` path (bootstrap requires re-running auth).

### 4. Opt-in pre-flight: `bredr off` + conn params (`bluez_cleanup.py`, `bond.py`, `cli.py`)
- `set_bredr_off()`: `sudo -n btmgmt bredr off` (no hardcoded password; warns cleanly if
  it needs a pass). Prevents the HID appearance from making BlueZ page classic → Page Timeout.
- `set_le_conn_interval()`: writes debugfs `conn_{min,max}_interval / conn_latency /
  supervision_timeout = 12/12/0/600` (15ms, latency 0) — fast link with no skips.
- Gated by env `QIX_BREDR_OFF=1`. Respects the "do NOT remove the host bond" rule from `bond.py`.

## Usage

```sh
# robust flash (retry + resume + pre-flight bredr off)
QIX_BREDR_OFF=1 QIX_CONNECT_ATTEMPTS=6 python3 qix.py flash <MAC> firmware.ufw [--oem]
QIX_BREDR_OFF=1 QIX_CONNECT_ATTEMPTS=6 python3 qix.py rcsp-flash <MAC> loaderdl.ufw --relink-delay 14
```

`set_bredr_off()` uses `sudo -n`; to automate it add to sudoers:
`<user> ALL=(root) NOPASSWD: /usr/bin/btmgmt`. Otherwise, run `sudo btmgmt bredr off` by hand once.

## Validation

4 OEM→custom→OEM round-trips in a row over BLE: 8/8 legs OK, 0 crashes, 0 text corruption.
(The firmware detail lived in the sibling SDK repo `e_badge_707_sdk_200`.)
Suite: 264/264 tests green after the changes (`cd tools/qix-ble && pytest tests/`).
