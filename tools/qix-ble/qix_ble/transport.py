"""Transport BLE Qix: wrap de bleak con queue + sync API.

Threading model:
  - Main thread llama send_command()/recv_frame() (sync)
  - 1 thread aparte corre asyncio loop con BleakClient
  - Notifications BLE → callback (asyncio thread) → frame.from_bytes_stream() → rx_queue.put()
  - Main thread: queue.Queue.get(timeout) para recibir
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import logging
import os
import queue
import signal
import threading
from typing import Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from qix_ble.bluez_cleanup import force_disconnect as bluez_force_disconnect
from qix_ble.errors import BleConnectionError, TimeoutError
from qix_ble.frame import QixFrame
from qix_ble.service import (
    SERVICE_UUID,
    CHAR_FD02_WRITE,
    CHAR_AE01_WRITE,
    CHAR_AE02_NOTIFY,
    NOTIFY_CHARS,
    RCSP_NOTIFY_CHARS,
    ALL_NOTIFY_CHARS,
)

log = logging.getLogger("qix_ble.transport")


class QixTransport:
    """Cliente BLE sync sobre bleak. Use as context manager."""

    def __init__(self, mac: str, mtu_hint: int = 247):
        self.mac = mac
        self.mtu_hint = mtu_hint
        self._client: BleakClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # ── Qix (FD00) RX state ──
        self._rx_queue: queue.Queue[QixFrame] = queue.Queue()
        self._rx_buffer = bytearray()
        self._rx_lock = threading.Lock()
        # ── RCSP AE02 RX state (raw bytes per-notification, no framing assumed) ──
        # Cada item del queue es UN notify chunk del device. AuthSession espera
        # 1 chunk = 1 step del handshake (paquetes pequeños siempre fit en MTU).
        self._ae02_rx_queue: queue.Queue[bytes] = queue.Queue()
        # Set por AuthSession.do_handshake() — RcspSession lo chequea como pre-cond.
        self._auth_done: bool = False
        # Lowercase set para comparar UUIDs del callback bleak (que da lowercase).
        self._rcsp_notify_chars_lower: set[str] = {c.lower() for c in RCSP_NOTIFY_CHARS}
        # Cleanup hooks (SIGINT/SIGTERM/atexit) — registrados en __enter__, removidos
        # en __exit__. Guardamos los previous handlers para restaurar (no pisar
        # global state si el caller tiene los suyos).
        self._prev_sigint = None
        self._prev_sigterm = None
        self._atexit_registered = False
        self._cleanup_done = False  # guard contra double-cleanup

    @staticmethod
    def scan(timeout: float = 10.0, all_devices: bool = False) -> list[BLEDevice]:
        """Sync wrapper sobre BleakScanner.discover. Filtra por SERVICE_UUID si all_devices=False."""
        log.info("scanning %.1fs%s", timeout, " (all)" if all_devices else f" (filter {SERVICE_UUID})")

        async def _do_scan():
            return await BleakScanner.discover(timeout=timeout, return_adv=True)

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(_do_scan())
        finally:
            loop.close()

        out: list[BLEDevice] = []
        for addr, (dev, adv) in results.items():
            if all_devices:
                out.append(dev)
                continue
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if SERVICE_UUID.lower() in uuids:
                out.append(dev)
        return out

    def __enter__(self) -> "QixTransport":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        try:
            # Must cover the full bounded retry loop in _connect: up to
            # QIX_CONNECT_ATTEMPTS rounds of scan (15s, skipped when
            # QIX_CONNECT_BY_ADDR=1) + connect (QIX_CONNECT_TIMEOUT) + setup +
            # backoff (min(2*attempt,6)s). Computed so a raised QIX_CONNECT_TIMEOUT
            # (e.g. 120s for a bluetoothctl-equivalent wait) isn't cut short by this
            # outer budget; floored at 180s for the default case.
            _attempts = self._connect_attempts()
            _scan = 0 if os.environ.get("QIX_CONNECT_BY_ADDR", "0") != "0" else 15
            _budget = _attempts * (_scan + self._connect_timeout() + 8) + 20
            fut.result(timeout=max(180.0, _budget))
        except Exception as e:
            self._stop_loop()
            raise BleConnectionError(f"connect failed: {e}") from e
        # Conexión OK — registrar cleanup hooks ahora (no antes, sino un fail
        # de connect dispararía cleanup sobre un transport sin loop).
        self._install_cleanup_hooks()
        log.info("connected to %s", self.mac)
        return self

    def __exit__(self, *_) -> None:
        self._remove_cleanup_hooks()
        self._do_cleanup(source="__exit__")

    def _install_cleanup_hooks(self) -> None:
        """SIGINT/SIGTERM + atexit. Cubre Ctrl+C y sys.exit sin pasar por __exit__.

        Limitación conocida: signal handlers solo se pueden instalar desde el
        main thread (Python restriction). Si QixTransport se usa desde un thread
        secundario, silenciosamente saltamos los signal hooks pero el atexit
        sigue activo."""
        try:
            self._prev_sigint = signal.signal(signal.SIGINT, self._signal_handler)
            self._prev_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)
        except (ValueError, OSError) as e:
            log.debug("signal handlers no instalables (probable non-main thread): %s", e)
            self._prev_sigint = None
            self._prev_sigterm = None
        atexit.register(self._atexit_handler)
        self._atexit_registered = True

    def _remove_cleanup_hooks(self) -> None:
        if self._prev_sigint is not None:
            try:
                signal.signal(signal.SIGINT, self._prev_sigint)
            except (ValueError, OSError):
                pass
            self._prev_sigint = None
        if self._prev_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, self._prev_sigterm)
            except (ValueError, OSError):
                pass
            self._prev_sigterm = None
        if self._atexit_registered:
            try:
                atexit.unregister(self._atexit_handler)
            except Exception:
                pass
            self._atexit_registered = False

    def _signal_handler(self, signum, frame):
        log.warning("recibido signal %d — cleanup BLE antes de salir", signum)
        self._do_cleanup(source=f"signal-{signum}")
        # Re-raise el signal con el handler original (KeyboardInterrupt para SIGINT,
        # default terminate para SIGTERM) para no enmascarar el flow normal.
        prev = self._prev_sigint if signum == signal.SIGINT else self._prev_sigterm
        if callable(prev):
            prev(signum, frame)
        else:
            # Default handler: re-raise como KeyboardInterrupt si SIGINT
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
            import sys
            sys.exit(128 + signum)

    def _atexit_handler(self):
        self._do_cleanup(source="atexit")

    def _do_cleanup(self, *, source: str) -> None:
        """Disconnect idempotente. Cubre todos los exit paths (normal/signal/atexit).

        Orden:
          1. BleakClient.disconnect via asyncio loop (graceful, libera el slot
             del badge desde el lado controller).
          2. Stop asyncio loop + join thread.
          3. BlueZ DBus Disconnect via bluez_cleanup (cubre caso donde bleak
             quedó hung y BlueZ todavía tiene Connected=yes cacheado).
        """
        if self._cleanup_done:
            return
        self._cleanup_done = True
        log.debug("cleanup BLE (source=%s)", source)
        if self._loop and self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
                fut.result(timeout=5.0)
            except Exception as e:
                log.warning("bleak disconnect error: %s", e)
            self._stop_loop()
        # Cleanup final via dbus por si bleak no liberó el state en BlueZ.
        # Idempotente: si ya está limpio no hace nada.
        try:
            bluez_force_disconnect(self.mac, timeout=1.5)
        except Exception as e:
            log.debug("final bluez_force_disconnect failed (continuing): %s", e)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _stop_loop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=2.0)

    @staticmethod
    def _connect_attempts() -> int:
        """Bounded connect-retry budget. Env QIX_CONNECT_ATTEMPTS (default 4)."""
        try:
            n = int(os.environ.get("QIX_CONNECT_ATTEMPTS", "4"))
        except (TypeError, ValueError):
            n = 4
        return max(1, n)

    @staticmethod
    def _connect_timeout() -> float:
        """Per-attempt cap on bleak's Device1.Connect(). Env QIX_CONNECT_TIMEOUT
        (default 20s). The OEM E87 advertises only intermittently (in its update
        screen / for a window after wake) and is single-link, so a fixed 20s cap
        can expire before an advert window arrives — while `bluetoothctl connect`
        (no cap) latches the next one. Raise this (e.g. 120) with
        QIX_CONNECT_BY_ADDR=1 + QIX_CONNECT_ATTEMPTS=1 for a bluetoothctl-equivalent
        single long wait."""
        try:
            t = float(os.environ.get("QIX_CONNECT_TIMEOUT", "20"))
        except (TypeError, ValueError):
            t = 20.0
        return max(1.0, t)

    def _services_resolved(self) -> bool:
        """True iff bleak resolved the GATT table AND an OTA write char is present.

        bleak can report `is_connected` with an EMPTY services table when the badge's
        HID-bond drops during service discovery (surfaces as a BleakError "failed to
        discover services" / "device disconnected", NOT a TimeoutError). In that state
        `services.get_characteristic()` returns None for every char — so we treat a
        missing OTA write char (FD02 for Qix, AE01 for RCSP) as an unusable link."""
        client = self._client
        if client is None:
            return False
        try:
            services = client.services
        except Exception:
            return False
        if services is None:
            return False
        for char_uuid in (CHAR_FD02_WRITE, CHAR_AE01_WRITE):
            try:
                if services.get_characteristic(char_uuid) is not None:
                    return True
            except Exception:
                continue
        return False

    async def _connect(self):
        # No pre-flight force_disconnect here: it ran in an executor with its own loop,
        # concurrent with the main-loop connect. Its late Device1.Disconnect() landed on
        # the freshly established link and tore it down (Connected:True -> Battery1 ->
        # Connected:False, services never resolved). The bounded retry below
        # (BleakClient.disconnect between attempts) covers the zombie case without that race.
        #
        # Retry loop wraps scan → connect → VERIFY-services-resolved → post-connect setup
        # (MTU/pair/notify). "failed to discover services" / "device disconnected" (a
        # BleakError, not a TimeoutError) used to abort with no retry — now it's a
        # RETRYABLE failure like a plain timeout. Gated by QIX_CONNECT_ATTEMPTS (default 4),
        # backoff min(2*attempt, 6)s, cleaning the link with BleakClient.disconnect()
        # between attempts (NOT the DBus force_disconnect — see the race note above).
        attempts = self._connect_attempts()
        last_exc: Exception | None = None
        # Custom-firmware badges (EC-BADGE) often never surface in bleak's scan on some
        # hosts (the adv is intermittently missed), so find_device_by_address returns None
        # and we burn 15s/attempt before the by-MAC fallback even runs. With
        # QIX_CONNECT_BY_ADDR=1 we skip the scan and connect straight by address — exactly
        # what `qix bond` does (BlueZ resolves the cached/bonded/trusted Device1 directly,
        # no fresh advert needed). Recommended with BR/EDR off on the adapter. Left unset
        # (default) → original scan-first behavior, so the OEM path is unchanged.
        connect_by_addr = os.environ.get("QIX_CONNECT_BY_ADDR", "0") != "0"
        # DETERMINISTIC connect (QIX_CONNECT_VIA_DBUS=1): the decisive fix for the
        # CUSTOM EC-BADGE, whose advert bleak's scanner surfaces only intermittently on
        # some hosts. bleak's connect ALWAYS runs find_device_by_address when the client
        # was built from a bare MAC (BlueZ backend, connect() line ~133: `if
        # self._device_path is None: find_device_by_address(...)`), so BOTH scan-first
        # and "connect_by_addr" actually depend on that flaky scan. Here we instead (1)
        # bring the link up with a raw DBus Device1.Connect() — the same call
        # bluetoothctl/qix bond use, which is reliable — and (2) hand bleak the device
        # path directly so it SKIPS its scan and just attaches to the live link
        # (connect() then hits `manager.is_connected(path)` → True → no re-Connect →
        # reads services). Requires the device cached in BlueZ (a prior scan/bond;
        # Trusted devices persist). Recommended with BR/EDR off + QIX_FLASH_PAIR=1.
        via_dbus = os.environ.get("QIX_CONNECT_VIA_DBUS", "0") != "0"
        for attempt in range(1, attempts + 1):
            try:
                if via_dbus:
                    from qix_ble.bluez_cleanup import dbus_connect_async, _device_path
                    await dbus_connect_async(self.mac, timeout=self._connect_timeout())
                    self._client = BleakClient(self.mac)
                    # Pre-seed the BlueZ backend device path so bleak's connect() does
                    # NOT call find_device_by_address (the intermittent scan).
                    try:
                        self._client._backend._device_path = _device_path(self.mac)
                    except Exception as e:
                        log.debug("could not pre-seed _device_path (%s)", e)
                    await asyncio.wait_for(self._client.connect(),
                                           timeout=self._connect_timeout())
                else:
                    if connect_by_addr:
                        # Connect-by-address: BlueZ resolves the cached dev_XX_.. Device1
                        # object directly (like bond's call_connect), no scan needed.
                        target = None
                    else:
                        # Resolve a fresh LE BLEDevice via scan before connecting: connecting by
                        # bare MAC leaves BlueZ without an LE object and can fall back to BR/EDR
                        # (page timeout) or "not available". The scan repopulates it as LE.
                        target = await BleakScanner.find_device_by_address(self.mac, timeout=15.0)
                    self._client = BleakClient(target or self.mac)
                    await asyncio.wait_for(self._client.connect(),
                                           timeout=self._connect_timeout())
                # VERIFY discovery: a "connected" client with an empty GATT table is
                # useless — treat it as a retryable failure (dropped HID-bond).
                if not self._services_resolved():
                    raise BleConnectionError(
                        "connected but OTA services not resolved (empty GATT table)"
                    )
                await self._post_connect_setup()
                if attempt > 1:
                    log.info("connect succeeded on attempt %d/%d", attempt, attempts)
                return
            except Exception as e:
                last_exc = e
                log.warning("connect attempt %d/%d failed: %s", attempt, attempts, e)
                # Clean the link on the bleak side before retrying (never the DBus
                # force_disconnect, which races the next fresh link).
                if self._client is not None:
                    try:
                        await asyncio.wait_for(self._client.disconnect(), timeout=5.0)
                    except Exception as de:
                        log.debug("cleanup disconnect raised (ignoring): %s", de)
                if attempt < attempts:
                    backoff = min(2 * attempt, 6)
                    log.info("retrying connect in %ds", backoff)
                    await asyncio.sleep(backoff)
        raise BleConnectionError(
            f"connect failed after {attempts} attempts: {last_exc}"
        ) from last_exc

    async def _post_connect_setup(self):
        # MTU exchange explícito (port de community _request_mtu_if_supported).
        # En BlueZ antiguos no se negocia automáticamente — quedamos con MTU 23
        # que para OTA header27 ya es problemático.
        try:
            backend = getattr(self._client, "_backend", self._client)
            if hasattr(backend, "_acquire_mtu"):
                await backend._acquire_mtu()
                log.debug("MTU acquired")
        except Exception as e:
            log.debug("MTU exchange skipped (%s)", e)
        # The badge is BLE-HID: BlueZ bonds on discovering the HID service, and the
        # badge's SM only reaches `encryption change=0` after pairing. It clears its key
        # on a new connection, so a stored bond breaks the reconnect (PIN_KEY_MISS) —
        # instead we pair FRESH on THIS connection and run the OTA on it (no reconnect).
        # Needs a Just-Works agent up (qix.bond.start_persistent_agent); without one
        # bleak's pair() would fail AuthenticationFailed. Gated by QIX_PAIR so non-RCSP
        # paths are unaffected.
        if os.environ.get("QIX_PAIR", "0") != "0":
            try:
                await asyncio.wait_for(self._client.pair(), timeout=25.0)
                log.info("paired (encrypted link)")
            except Exception as e:
                log.warning("pair() on this connection failed/skipped: %s", e)
        for char_uuid in ALL_NOTIFY_CHARS:
            try:
                await self._client.start_notify(char_uuid, self._on_notify)
                log.debug("subscribed to %s", char_uuid)
            except Exception as e:
                log.warning("no se pudo subscribir a %s: %s", char_uuid, e)

    async def _disconnect(self):
        if self._client and self._client.is_connected:
            for char_uuid in ALL_NOTIFY_CHARS:
                try:
                    await self._client.stop_notify(char_uuid)
                except Exception:
                    pass
            await self._client.disconnect()

    def disconnect(self) -> None:
        """Drop the BLE link but KEEP the loop alive (so we can reconnect later).

        Single-bank OTA needs this: the badge arms the loader-boot and resets into the
        loader only when its BLE reaches BLE_ST_IDLE (rcsp_manage.c) with the update
        flag set — i.e. we must DISCONNECT after the loader download, not send E7. E7's
        handler does a plain cpu_reset() that preempts the arming, so the badge boots
        the old app instead of the loader."""
        if self._loop and self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
                fut.result(timeout=6.0)
            except Exception as e:
                log.warning("disconnect error: %s", e)

    def reconnect(self, timeout: float = 180.0) -> None:
        """Re-establish the BLE link on the SAME loop (symmetric to disconnect()).

        Runs _connect() again via the existing loop thread, so it inherits its full
        retry+backoff + service-discovery verification. Used to resume an OTA whose link
        dropped mid-transfer (device-driven offset flows re-issue REQ_UPDATE afterwards).
        No-op-with-warning if the loop isn't running (nothing to reconnect on)."""
        if not (self._loop and self._loop.is_running()):
            log.warning("reconnect() called but asyncio loop is not running — skipping")
            return
        fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        fut.result(timeout=timeout)
        log.info("reconnected to %s", self.mac)

    def _on_notify(self, sender, data: bytearray):
        """Callback bleak. Route por char UUID:
        - AE02 (RCSP): bytes raw → _ae02_rx_queue (1 notify = 1 item).
        - FD01/FD03 (Qix): stream parse → _rx_queue.
        """
        char_uuid = getattr(sender, "uuid", "").lower()
        if char_uuid in self._rcsp_notify_chars_lower:
            raw = bytes(data)
            log.debug("RX ae02 raw len=%d payload=%s", len(raw), raw.hex())
            self._ae02_rx_queue.put(raw)
            return
        with self._rx_lock:
            self._rx_buffer.extend(data)
            while True:
                frame, remainder = QixFrame.from_bytes_stream(self._rx_buffer)
                self._rx_buffer = remainder
                if frame is None:
                    break
                log.debug("RX qix cmd=0x%02X flags=0x%02X len=%d payload=%s",
                          frame.cmd, frame.flags, len(frame.payload), frame.payload.hex())
                self._rx_queue.put(frame)

    def send_raw(self, frame: QixFrame, response: bool = False) -> None:
        """Write FD02 con frame.encode(). No espera respuesta del state machine.

        `response`: True = BLE Write-With-Response (ACKed L2CAP, slower pero
        reliable). Default False (Write-Without-Response, fastest, lo que
        usa la mayoría de cmds Qix). OTA UpdateManager pasa True per community.
        """
        if not self._client or not self._client.is_connected:
            raise BleConnectionError("not connected")
        encoded = frame.encode()
        log.debug("TX cmd=0x%02X flags=0x%02X len=%d ack=%s payload=%s",
                  frame.cmd, frame.flags, len(frame.payload), response, frame.payload.hex())
        fut = asyncio.run_coroutine_threadsafe(
            self._client.write_gatt_char(CHAR_FD02_WRITE, encoded, response=response),
            self._loop,
        )
        # A mid-transfer link drop surfaces here as a bleak BleakError (write
        # failed / disconnected) or, if the write just hangs, a builtin
        # concurrent.futures.TimeoutError. Neither is BleConnectionError, so the
        # OTA resume handler (update_manager) wouldn't catch them → the transfer
        # would hard-fail on a drop that happened on the OUTGOING chunk write.
        # Normalise both to BleConnectionError so resume can reconnect + retry.
        try:
            fut.result(timeout=5.0)
        except (BleakError, concurrent.futures.TimeoutError) as e:
            raise BleConnectionError(f"FD02 write failed (link drop?): {e}") from e

    def write_fd02_raw(self, data: bytes) -> None:
        """Write bytes verbatim a FD02 sin validar como QixFrame.

        Usado por bootstrap dance: el community manda secuencias hardcoded con
        checksums pre-computados (algunos inválidos pero el FW las tolera).
        Bypass QixFrame.encode() para mandar bytes exactos.
        """
        if not self._client or not self._client.is_connected:
            raise BleConnectionError("not connected")
        log.debug("TX fd02 raw len=%d payload=%s", len(data), data.hex())
        fut = asyncio.run_coroutine_threadsafe(
            self._client.write_gatt_char(CHAR_FD02_WRITE, data, response=False),
            self._loop,
        )
        fut.result(timeout=5.0)

    def send_command(
        self,
        cmd: int,
        payload: bytes = b"",
        flags: int = 0,
        expect_cmd: int | None = None,
        timeout: float = 5.0,
        response: bool = False,
    ) -> QixFrame:
        """Send + wait. expect_cmd=None retorna primer frame; si especificado, descarta los que no matchean.

        `response`: BLE Write-With-Response (ACKed). Default False. OTA path
        debe pasarlo True per community impl.
        """
        self.send_raw(QixFrame(flags=flags, cmd=cmd, payload=payload), response=response)
        return self._wait_for_cmd(expect_cmd, timeout)

    def _wait_for_cmd(self, expect_cmd, timeout: float) -> QixFrame:
        """expect_cmd: None (acepta cualquier frame), int (un cmd específico),
        o iterable de ints (acepta el primero que matchee — útil para
        flows ambiguos como el último chunk OTA que puede llegar como
        0xC3 RET_UPDATE_DATA o 0xC5 RET_UPDATE_RESULT)."""
        import time
        deadline = time.monotonic() + timeout
        if isinstance(expect_cmd, int):
            wanted = (expect_cmd,)
        elif expect_cmd is None:
            wanted = None
        else:
            wanted = tuple(expect_cmd)
        label = ("(expect=" + ",".join(f"0x{c:02x}" for c in wanted) + ")") if wanted else ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no se recibió frame {label} en {timeout}s")
            try:
                frame = self._rx_queue.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"queue.get {label} timeout @ {timeout}s")
            if wanted is None or frame.cmd in wanted:
                return frame
            log.debug("descarto frame cmd=0x%02X (no matchea expect=%s)",
                      frame.cmd, ",".join(f"0x{c:02x}" for c in wanted))

    def recv_frame(self, timeout: float = 5.0) -> QixFrame | None:
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait_for_cmd(self, expect_cmd: int, timeout: float = 30.0) -> QixFrame:
        """Espera un frame con cmd específico (descartando otros) sin enviar nada.
        Útil para responses unsolicited del badge (ej. 0xC5 RET_UPDATE_RESULT)."""
        return self._wait_for_cmd(expect_cmd, timeout)

    def drain(self) -> list[QixFrame]:
        out = []
        while True:
            try:
                out.append(self._rx_queue.get_nowait())
            except queue.Empty:
                break
        return out

    # ─── RCSP AE00 (raw API — usado por AuthSession para el handshake) ───────

    def send_to_ae01(self, data: bytes) -> None:
        """Raw write a AE01 (Write Without Response). Usado por el handshake
        auth (frames chicos) y por RCSP (incl. OTA E5 data responses, que pueden
        exceder el MTU).

        AE01 es write-without-response → cada write BLE debe caber en (MTU-3)
        bytes. Frames RCSP grandes se FRAGMENTAN en varios writes; el device los
        reensambla por el campo de longitud del frame (FE-DC-BA … len … EF).
        Sin esto, WinRT rechaza el write con WinError -2147024809."""
        if not self._client or not self._client.is_connected:
            raise BleConnectionError("not connected")
        mtu = getattr(self._client, "mtu_size", 0) or 23
        max_chunk = max(20, mtu - 3)
        if len(data) <= max_chunk:
            log.debug("TX ae01 raw len=%d payload=%s", len(data), data.hex())
            fut = asyncio.run_coroutine_threadsafe(
                self._client.write_gatt_char(CHAR_AE01_WRITE, data, response=False),
                self._loop,
            )
            fut.result(timeout=5.0)
            return
        log.debug("TX ae01 raw len=%d -> %d fragments of <=%d (mtu=%d)",
                  len(data), -(-len(data) // max_chunk), max_chunk, mtu)
        for i in range(0, len(data), max_chunk):
            piece = data[i:i + max_chunk]
            fut = asyncio.run_coroutine_threadsafe(
                self._client.write_gatt_char(CHAR_AE01_WRITE, piece, response=False),
                self._loop,
            )
            fut.result(timeout=5.0)

    def recv_ae02_raw(self, timeout: float = 5.0) -> bytes:
        """Pop next raw notification chunk de AE02. Cada notify = 1 item.

        Raises TimeoutError si no llega nada en `timeout` segundos.
        """
        try:
            return self._ae02_rx_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"no se recibió notify de AE02 en {timeout}s")

    def drain_ae02_raw(self) -> list[bytes]:
        """Vacía el queue AE02 sin esperar. Útil para limpiar antes del handshake."""
        out: list[bytes] = []
        while True:
            try:
                out.append(self._ae02_rx_queue.get_nowait())
            except queue.Empty:
                break
        return out

    # ─── RCSP high-level helpers (usado por RcspSession) ─────────────────

    def send_rcsp_frame(self, frame) -> None:
        """Encode + write a AE01. `frame` debe ser RcspFrame (import diferido
        para evitar circular en transport import time)."""
        self.send_to_ae01(frame.encode())

    def recv_rcsp_frame(self, timeout: float = 5.0):
        """Pop next AE02 raw chunk + parse como RcspFrame.

        Single notify = single frame (caso browse + small_file, frames fit en MTU).
        Para chunked/fragmented (read_large_file), usar recv_ae02_raw + buffer
        + RcspFrame.from_bytes_stream a mano.

        Raises:
            TimeoutError: si no llega nada en `timeout` segundos.
            InvalidFrameError: si el chunk no decodifica como RCSP frame válido.
        """
        from qix_ble.rcsp_frame import RcspFrame  # import diferido
        raw = self.recv_ae02_raw(timeout=timeout)
        return RcspFrame.decode(raw)
