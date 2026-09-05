"""Helpers BlueZ DBus para limpiar conexiones zombie antes/después de qix sessions.

Problema: tras un crash o Ctrl+C, BlueZ mantiene `Device1.Connected=true` cacheado
aunque el badge ya cerró el slot. El siguiente `BleakClient.connect()` paga 10s de
timeout antes del retry. Estos helpers limpian el estado de forma proactiva.

Diseño:
- API sync (corren en su propio event loop efímero, no chocan con el loop de QixTransport).
- Una sola Disconnect() directa — no pre-check del property `Connected` para evitar
  race condition entre check y call. Capturamos `org.bluez.Error.NotConnected` como éxito.
- Timeout corto (1.5s default): si BlueZ no responde rápido el adapter está hung y
  el connect tampoco va a funcionar — mejor fallar rápido que hacer el user esperar.
- Llamar UNA SOLA VEZ por sesión (pre-connect). El retry interno de `_connect()` en
  transport.py ya hace `BleakClient.disconnect()` que es suficiente al segundo intento.
"""
from __future__ import annotations

import asyncio
import logging
import sys

# BlueZ DBus cleanup only applies on Linux. On Windows/macOS bleak's native backend
# (WinRT/CoreBluetooth) manages connection state itself, so we make these helpers
# no-ops there and avoid importing dbus-fast (which isn't installed off Linux).
if sys.platform.startswith("linux"):
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
        _DBUS_AVAILABLE = True
    except ImportError:
        _DBUS_AVAILABLE = False
else:
    _DBUS_AVAILABLE = False

log = logging.getLogger("qix_ble.bluez_cleanup")

BLUEZ = "org.bluez"
ADAPTER = "hci0"

# DBus error names que tratamos como "ya estaba limpio" (idempotencia).
_BENIGN_DISCONNECT_ERRORS = (
    "org.bluez.Error.NotConnected",
    "org.bluez.Error.NotReady",
    "org.bluez.Error.DoesNotExist",
    "Does Not Exist",       # BlueZ RemoveDevice cuando el device path no existe
    "DoesNotExist",
    # dbus-fast InterfaceNotFoundError: device path existe en BlueZ pero sin
    # Device1 — significa que BlueZ olvidó/nunca vio el device. Sin zombie.
    "interface not found on this object",
)


# Silencia un ERROR conocido de dbus-fast emitido al cerrar el MessageBus
# (intenta remover un match rule sobre una conexión ya cerrada). No afecta
# funcionalidad; solo polución del stderr. Lo bajamos a WARNING para esta lib.
logging.getLogger("dbus_fast.message_bus").setLevel(logging.CRITICAL)


def _device_path(mac: str, adapter: str = ADAPTER) -> str:
    """`AA:BB:CC:DD:EE:FF` → `/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF` (auto-derivable)."""
    return f"/org/bluez/{adapter}/dev_{mac.upper().replace(':', '_')}"


def _adapter_path(adapter: str = ADAPTER) -> str:
    return f"/org/bluez/{adapter}"


def _is_benign(exc: Exception) -> bool:
    msg = str(exc)
    return any(name in msg for name in _BENIGN_DISCONNECT_ERRORS)


async def _force_disconnect_async(mac: str, adapter: str, timeout: float) -> bool:
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        dev_path = _device_path(mac, adapter)
        try:
            intro = await asyncio.wait_for(bus.introspect(BLUEZ, dev_path), timeout=timeout)
        except Exception as e:
            # Device no está en BlueZ tree — nunca se descubrió o ya fue removido.
            # No es error: no había zombie que limpiar.
            log.debug("device %s no en BlueZ tree (%s) — skip", dev_path, e)
            return False
        try:
            proxy = bus.get_proxy_object(BLUEZ, dev_path, intro)
            dev = proxy.get_interface("org.bluez.Device1")
            await asyncio.wait_for(dev.call_disconnect(), timeout=timeout)
            log.info("Device1.Disconnect() OK for %s", mac)
            return True
        except Exception as e:
            if _is_benign(e):
                log.debug("Device1.Disconnect(%s) benign: %s", mac, e)
                return False
            raise
    finally:
        bus.disconnect()


_BENIGN_CONNECT_OK = (
    "org.bluez.Error.AlreadyConnected",
    "Already Connected",
    "AlreadyConnected",
)


async def dbus_connect_async(mac: str, adapter: str = ADAPTER, timeout: float = 60.0) -> bool:
    """Establish the LE link via a raw BlueZ `Device1.Connect()` — the SAME mechanism
    `bluetoothctl connect` and `qix bond` use, which (unlike bleak's connect) has no
    short internal cap and reliably latches the badge's next advert. Awaitable so it
    runs on the caller's event loop (the QixTransport loop). Requires the device to be
    in BlueZ's object tree (cached by a prior scan/bond; Trusted devices persist).
    Returns True on success (incl. already-connected); raises if the device path is
    absent or Connect fails for a non-benign reason."""
    if not _DBUS_AVAILABLE:
        return False
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        dev_path = _device_path(mac, adapter)
        intro = await asyncio.wait_for(bus.introspect(BLUEZ, dev_path),
                                       timeout=min(timeout, 10.0))
        proxy = bus.get_proxy_object(BLUEZ, dev_path, intro)
        dev = proxy.get_interface("org.bluez.Device1")
        try:
            await asyncio.wait_for(dev.call_connect(), timeout=timeout)
            log.info("Device1.Connect() OK for %s", mac)
            return True
        except Exception as e:
            if any(n in str(e) for n in _BENIGN_CONNECT_OK):
                log.info("Device1.Connect(%s) already connected", mac)
                return True
            raise
    finally:
        bus.disconnect()


async def _remove_device_async(mac: str, adapter: str, timeout: float) -> bool:
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        adap_path = _adapter_path(adapter)
        intro = await asyncio.wait_for(bus.introspect(BLUEZ, adap_path), timeout=timeout)
        proxy = bus.get_proxy_object(BLUEZ, adap_path, intro)
        adap = proxy.get_interface("org.bluez.Adapter1")
        try:
            await asyncio.wait_for(
                adap.call_remove_device(_device_path(mac, adapter)), timeout=timeout
            )
            log.info("Adapter1.RemoveDevice() OK for %s", mac)
            return True
        except Exception as e:
            if _is_benign(e):
                log.debug("RemoveDevice(%s) benign: %s", mac, e)
                return False
            raise
    finally:
        bus.disconnect()


def _run_sync(coro, fallback_timeout: float):
    """Corre `coro` en un event loop efímero. Bloquea si ya hay loop corriendo
    en este thread (caso raro: llamada desde async). En uso normal CLI sync esto
    es OK."""
    try:
        asyncio.get_running_loop()
        raise RuntimeError(
            "bluez_cleanup helpers son sync — no llamar desde async context"
        )
    except RuntimeError as e:
        if "no running event loop" not in str(e):
            raise
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=fallback_timeout + 1.0))
    finally:
        loop.close()


def force_disconnect(mac: str, *, adapter: str = ADAPTER, timeout: float = 1.5) -> bool:
    """Idempotente: llama `Device1.Disconnect()` sobre `mac`. Retorna True si
    efectivamente desconectó, False si BlueZ ya lo tenía limpio o el device no
    existe. Nunca tira excepción por estado benigno (NotConnected/DoesNotExist).

    Timeout default 1.5s: si BlueZ no responde rápido el adapter está hung."""
    if not _DBUS_AVAILABLE:
        return False  # non-Linux: bleak's backend handles connection state
    try:
        return _run_sync(_force_disconnect_async(mac, adapter, timeout), timeout)
    except Exception as e:
        log.warning("force_disconnect(%s) failed (continuing anyway): %s", mac, e)
        return False


def _adapter_index(adapter: str = ADAPTER) -> int:
    """`hci0` → 0. Best-effort parse of the trailing digits (default 0)."""
    import re
    m = re.search(r"(\d+)$", adapter)
    return int(m.group(1)) if m else 0


def set_bredr_off(adapter: str = ADAPTER) -> bool:
    """Turn BR/EDR off on the adapter via `sudo -n btmgmt --index <n> bredr off`.

    Why: the badge's HID appearance makes BlueZ page it over classic BR/EDR, which
    page-times-out and starves the LE OTA. Turning BR/EDR off keeps the adapter LE-only.

    Opt-in (callers gate on QIX_BREDR_OFF). Best-effort: uses passwordless sudo (`-n`);
    if sudo would prompt for a password it fails cleanly with a warning instead of
    hanging (NO hardcoded password). Linux only."""
    if not sys.platform.startswith("linux"):
        return False
    import subprocess
    idx = _adapter_index(adapter)
    cmd = ["sudo", "-n", "btmgmt", "--index", str(idx), "bredr", "off"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        log.warning("set_bredr_off: sudo/btmgmt not found — skipping")
        return False
    except Exception as e:
        log.warning("set_bredr_off failed (continuing): %s", e)
        return False
    if r.returncode != 0:
        log.warning("set_bredr_off: `%s` failed (rc=%d): %s — passwordless sudo needed? "
                    "skipping", " ".join(cmd), r.returncode,
                    (r.stderr or r.stdout).strip())
        return False
    log.info("BR/EDR turned off on %s", adapter)
    return True


def set_le_conn_interval(adapter: str = ADAPTER, *, conn_min: int = 12, conn_max: int = 12,
                         latency: int = 0, supervision_timeout: int = 600) -> bool:
    """Pin the LE connection params via debugfs so the OTA link uses a tight interval
    (12 * 1.25 ms = 15 ms) instead of BlueZ defaults. Writes each debugfs file with
    `sudo -n tee` (NO hardcoded password). Best-effort: debugfs may be root-only or
    absent, in which case we warn and continue. Opt-in via callers. Linux only.

    conn_max is written before conn_min so a transient min>max is never rejected."""
    if not sys.platform.startswith("linux"):
        return False
    import subprocess
    base = f"/sys/kernel/debug/bluetooth/{adapter}"
    # Ordered: max before min (avoid transient min>max), then latency + supervision.
    files = [
        ("conn_max_interval", conn_max),
        ("conn_min_interval", conn_min),
        ("conn_latency", latency),
        ("supervision_timeout", supervision_timeout),
    ]
    ok = True
    for name, val in files:
        path = f"{base}/{name}"
        try:
            r = subprocess.run(
                ["sudo", "-n", "tee", path],
                input=f"{val}\n", capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                ok = False
                log.debug("set_le_conn_interval: write %s=%s failed: %s",
                          path, val, (r.stderr or r.stdout).strip())
        except Exception as e:
            ok = False
            log.debug("set_le_conn_interval: write %s raised: %s", path, e)
    if ok:
        log.info("LE conn params pinned on %s (min=%d max=%d lat=%d sto=%d)",
                 adapter, conn_min, conn_max, latency, supervision_timeout)
    else:
        log.warning("set_le_conn_interval: some params not applied (debugfs root-only?)")
    return ok


def remove_device(mac: str, *, adapter: str = ADAPTER, timeout: float = 1.5) -> bool:
    """Opción nuclear: `Adapter1.RemoveDevice()`. BlueZ olvida el device
    completamente — próxima sesión necesita re-descubrir.

    Útil cuando force_disconnect() no basta porque BlueZ tiene state corrupto."""
    if not _DBUS_AVAILABLE:
        return False  # non-Linux: nothing to clean up at the BlueZ layer
    try:
        return _run_sync(_remove_device_async(mac, adapter, timeout), timeout)
    except Exception as e:
        log.warning("remove_device(%s) failed: %s", mac, e)
        return False
