"""Transport DBus directo para AE00 RCSP — bypass de la limitación bleak+DG01.

Bleak 3.0.1 hangs en `client.connect()` contra DG01 esperando `ServicesResolved=true`
que el firmware no emite consistentemente (problema documentado en internal research notes (not published)
2026-05-15-day1-dg01-hw-first-contact.md). bluer Rust resuelve esto usando
`Device1.ConnectProfile(UUID)` en lugar de `Device1.Connect()` para devices LE-only,
y polling de characteristics en vez de gate en ServicesResolved.

Este módulo replica el patrón Rust en Python via dbus-fast (dep transitiva de bleak,
sin nueva dep). Expone la API mínima que `qix_ble.auth.AuthSession` espera:
- `send_to_ae01(bytes)`
- `recv_ae02_raw(timeout) -> bytes`
- `drain_ae02_raw() -> list[bytes]`
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus

from qix_ble.errors import BleConnectionError, TimeoutError as QixTimeoutError

log = logging.getLogger("baji_ble.ae00_dbus")

BLUEZ = "org.bluez"
ADAPTER = "hci0"
AE00_SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"
AE01_CHAR_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02_CHAR_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"


def _device_path(mac: str, adapter: str = ADAPTER) -> str:
    """`AA:BB:CC:DD:EE:FF` → `/org/bluez/hci0/dev_42_11_10_F4_99_C7`."""
    return f"/org/bluez/{adapter}/dev_{mac.upper().replace(':', '_')}"


class Ae00DbusTransport:
    """AE00-only BLE transport sobre BlueZ DBus (bypass bleak)."""

    def __init__(self, mac: str, *, adapter: str = ADAPTER):
        self.mac = mac
        self.adapter = adapter
        self.dev_path = _device_path(mac, adapter)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._bus: MessageBus | None = None
        self._dev_iface = None
        self._ae01_iface = None
        self._ae02_iface = None
        self._ae02_props_iface = None
        self._ae02_q: queue.Queue[bytes] = queue.Queue()
        self._connected_externally = False  # true si ya estaba Connected al entrar
        # Flag que AuthSession.do_handshake setea cuando termina OK. RcspSession lo
        # chequea como pre-cond (ver qix_ble/rcsp_session.py _check_auth).
        self._auth_done: bool = False

    def __enter__(self) -> "Ae00DbusTransport":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        fut = asyncio.run_coroutine_threadsafe(self._connect_async(), self._loop)
        try:
            fut.result(timeout=60.0)
        except Exception as e:
            self._stop_loop()
            raise BleConnectionError(f"AE00 DBus connect failed: {e}") from e
        log.info("AE00 DBus transport ready (dev_path=%s)", self.dev_path)
        return self

    def __exit__(self, *_):
        try:
            if self._loop and self._loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(self._disconnect_async(), self._loop)
                try:
                    fut.result(timeout=10.0)
                except Exception as e:
                    log.warning("disconnect error: %s", e)
        finally:
            self._stop_loop()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _stop_loop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=3.0)

    async def _connect_async(self):
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # 1) Verificar que el device existe en DBus (necesita scan previo o connect previo)
        intro = await self._bus.introspect(BLUEZ, self.dev_path)
        dev_proxy = self._bus.get_proxy_object(BLUEZ, self.dev_path, intro)
        self._dev_iface = dev_proxy.get_interface("org.bluez.Device1")
        dev_props = dev_proxy.get_interface("org.freedesktop.DBus.Properties")
        already_connected = await dev_props.call_get("org.bluez.Device1", "Connected")
        if already_connected.value:
            log.info("device already Connected — reusing existing link")
            self._connected_externally = True
        else:
            # 2) ConnectProfile(AE00) — más confiable que Connect() para LE-only gear (per bluer)
            log.info("calling Device1.ConnectProfile(%s) ...", AE00_SERVICE_UUID)
            try:
                await asyncio.wait_for(
                    self._dev_iface.call_connect_profile(AE00_SERVICE_UUID),
                    timeout=20.0,
                )
            except Exception as e:
                # Fallback: generic Connect()
                log.warning("ConnectProfile failed (%s), falling back to Connect()", e)
                await asyncio.wait_for(self._dev_iface.call_connect(), timeout=20.0)
            log.info("link established")

        # 3) Poll para que aparezcan los GattCharacteristic1 paths para AE01 + AE02.
        #    Per bluer comment: tras Connect, BlueZ puede tardar en exponer GATT.
        deadline = time.monotonic() + 30.0
        ae01_path = None
        ae02_path = None
        while time.monotonic() < deadline:
            ae01_path, ae02_path = await self._find_ae_chars()
            if ae01_path and ae02_path:
                break
            await asyncio.sleep(0.3)
        if not (ae01_path and ae02_path):
            raise BleConnectionError(
                f"AE01/AE02 chars no aparecieron en 30s (AE01={ae01_path}, AE02={ae02_path})"
            )
        log.info("found AE01 @ %s, AE02 @ %s", ae01_path, ae02_path)

        # 4) Bind AE01 (write) + AE02 (notify) interfaces
        ae01_intro = await self._bus.introspect(BLUEZ, ae01_path)
        ae01_proxy = self._bus.get_proxy_object(BLUEZ, ae01_path, ae01_intro)
        self._ae01_iface = ae01_proxy.get_interface("org.bluez.GattCharacteristic1")

        ae02_intro = await self._bus.introspect(BLUEZ, ae02_path)
        ae02_proxy = self._bus.get_proxy_object(BLUEZ, ae02_path, ae02_intro)
        self._ae02_iface = ae02_proxy.get_interface("org.bluez.GattCharacteristic1")
        self._ae02_props_iface = ae02_proxy.get_interface("org.freedesktop.DBus.Properties")

        # 5) Subscribe PropertiesChanged para AE02.Value → enqueue
        self._ae02_props_iface.on_properties_changed(self._on_ae02_props_changed)

        # 6) StartNotify
        await self._ae02_iface.call_start_notify()
        log.info("AE02 StartNotify done")

    async def _find_ae_chars(self) -> tuple[str | None, str | None]:
        """Busca paths de chars AE01 + AE02 bajo nuestro device. Retorna (None, None) si aún no aparecieron."""
        # Use ObjectManager.GetManagedObjects para enumerar todo bajo /org/bluez
        intro = await self._bus.introspect(BLUEZ, "/")
        proxy = self._bus.get_proxy_object(BLUEZ, "/", intro)
        om_iface = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
        managed = await om_iface.call_get_managed_objects()
        ae01 = ae02 = None
        for path, ifaces in managed.items():
            if not path.startswith(self.dev_path + "/"):
                continue
            char = ifaces.get("org.bluez.GattCharacteristic1")
            if not char:
                continue
            uuid = char.get("UUID")
            if not uuid:
                continue
            uuid_str = uuid.value.lower() if hasattr(uuid, "value") else str(uuid).lower()
            if uuid_str == AE01_CHAR_UUID:
                ae01 = path
            elif uuid_str == AE02_CHAR_UUID:
                ae02 = path
        return ae01, ae02

    def _on_ae02_props_changed(self, interface_name: str, changed: dict, invalidated: list):
        if interface_name != "org.bluez.GattCharacteristic1":
            return
        value_var = changed.get("Value")
        if value_var is None:
            return
        raw = bytes(value_var.value)
        log.debug("RX ae02 len=%d %s", len(raw), raw.hex())
        self._ae02_q.put(raw)

    async def _disconnect_async(self):
        # Stop notify (best effort)
        if self._ae02_iface is not None:
            try:
                await self._ae02_iface.call_stop_notify()
            except Exception as e:
                log.debug("stop_notify err (ignored): %s", e)
        # Solo desconectamos si NOSOTROS abrimos la conexión
        if self._dev_iface is not None and not self._connected_externally:
            try:
                await asyncio.wait_for(self._dev_iface.call_disconnect(), timeout=5.0)
            except Exception as e:
                log.debug("disconnect err (ignored): %s", e)
        if self._bus is not None:
            self._bus.disconnect()

    # ── API que AuthSession espera ──

    def send_to_ae01(self, data: bytes) -> None:
        if self._ae01_iface is None:
            raise BleConnectionError("AE01 not initialized")
        log.debug("TX ae01 len=%d %s", len(data), data.hex())
        fut = asyncio.run_coroutine_threadsafe(
            self._ae01_iface.call_write_value(data, {"type": Variant("s", "command")}),
            self._loop,
        )
        try:
            fut.result(timeout=5.0)
        except Exception as e:
            raise BleConnectionError(f"AE01 WriteValue failed: {e}") from e

    def recv_ae02_raw(self, timeout: float = 5.0) -> bytes:
        try:
            return self._ae02_q.get(timeout=timeout)
        except queue.Empty:
            raise QixTimeoutError(f"no notify from AE02 in {timeout}s")

    def drain_ae02_raw(self) -> list[bytes]:
        out = []
        while True:
            try:
                out.append(self._ae02_q.get_nowait())
            except queue.Empty:
                break
        return out

    # ── RCSP high-level (lo que RcspSession espera) ─────────────────────────

    def send_rcsp_frame(self, frame) -> None:
        """frame: qix_ble.rcsp_frame.RcspFrame. Encode + write to AE01."""
        self.send_to_ae01(frame.encode())

    def recv_rcsp_frame(self, timeout: float = 5.0):
        """Pop next AE02 raw chunk + decode as RcspFrame.

        Asume 1 notify = 1 frame completo (válido para responses chicos como
        target_info, sys_info). Para fragmentación grande usar recv_ae02_raw
        + RcspFrame.from_bytes_stream a mano.
        """
        from qix_ble.rcsp_frame import RcspFrame  # import diferido (evita ciclo)
        raw = self.recv_ae02_raw(timeout=timeout)
        return RcspFrame.decode(raw)
