"""Transport BLE Baji: bleak wrapper sync (NUS 7e40 + CdNotifyAssembler)."""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import threading

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from baji_ble.frame import CdNotifyAssembler
from baji_ble.service import (
    NUS_SERVICE_UUID, NUS_TX_WRITE, NUS_RX_NOTIFY, NUS_AUX_NOTIFY,
)
from qix_ble.errors import BleConnectionError, TimeoutError

log = logging.getLogger("baji_ble.transport")

_NOTIFY_CHARS = (NUS_RX_NOTIFY, NUS_AUX_NOTIFY)


class BajiTransport:
    """Cliente BLE sync sobre bleak para path Baji NUS. Use as context manager."""

    def __init__(self, mac: str, *, mtu_hint: int = 247):
        self.mac = mac
        self.mtu_hint = mtu_hint
        self._client: BleakClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._rx_queue: queue.Queue[bytes] = queue.Queue()
        self._assembler = CdNotifyAssembler()
        self._asm_lock = threading.Lock()

    @staticmethod
    def scan(timeout: float = 10.0, *, name_filter: str | None = "DG01") -> list[BLEDevice]:
        async def _do_scan():
            return await BleakScanner.discover(timeout=timeout, return_adv=True)
        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(_do_scan())
        finally:
            loop.close()
        out: list[BLEDevice] = []
        for addr, (dev, adv) in results.items():
            if name_filter and (dev.name or "") != name_filter:
                continue
            out.append(dev)
        return out

    def __enter__(self) -> "BajiTransport":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        try:
            fut.result(timeout=15.0)
        except Exception as e:
            self._stop_loop()
            raise BleConnectionError(f"connect failed: {e}") from e
        log.info("connected to %s", self.mac)
        return self

    def __exit__(self, *_) -> None:
        try:
            if self._loop and self._loop.is_running():
                try:
                    fut = asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
                    fut.result(timeout=5.0)
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
                self._thread.join(timeout=2.0)

    async def _connect(self):
        self._client = BleakClient(self.mac)
        await asyncio.wait_for(self._client.connect(), timeout=10.0)
        for char_uuid in _NOTIFY_CHARS:
            try:
                await self._client.start_notify(char_uuid, self._on_notify)
                log.debug("subscribed to %s", char_uuid)
            except Exception as e:
                log.warning("subscribe %s failed: %s", char_uuid, e)

    async def _disconnect(self):
        if self._client and self._client.is_connected:
            for char_uuid in _NOTIFY_CHARS:
                try:
                    await self._client.stop_notify(char_uuid)
                except Exception:
                    pass
            await self._client.disconnect()

    def _on_notify(self, sender, data: bytearray):
        raw = bytes(data)
        log.debug("RX raw len=%d hex=%s", len(raw), raw.hex())
        with self._asm_lock:
            frames = self._assembler.push(raw)
        for f in frames:
            self._rx_queue.put(f)

    def send_raw_to_nus(self, data: bytes, *, response: bool = False) -> None:
        if not self._client or not self._client.is_connected:
            raise BleConnectionError("not connected")
        log.debug("TX nus len=%d hex=%s", len(data), data.hex())
        fut = asyncio.run_coroutine_threadsafe(
            self._client.write_gatt_char(NUS_TX_WRITE, data, response=response),
            self._loop,
        )
        try:
            fut.result(timeout=5.0)
        except concurrent.futures.TimeoutError as e:
            raise TimeoutError("write_gatt_char timeout after 5s") from e

    def recv_frame(self, timeout: float = 5.0) -> bytes | None:
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> list[bytes]:
        out = []
        while True:
            try:
                out.append(self._rx_queue.get_nowait())
            except queue.Empty:
                break
        return out
