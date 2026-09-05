"""GATT reads de DIS (0x180A) + BAS (0x180F) — Bleak nativo, sin framing Baji."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from baji_ble.service import (
    CHAR_MANUFACTURER, CHAR_MODEL, CHAR_SERIAL,
    CHAR_FW_REV, CHAR_HW_REV, CHAR_SW_REV, CHAR_SYSTEM_ID,
    CHAR_IEEE_CERT, CHAR_PNP_ID, CHAR_BATTERY_LEVEL,
)


@dataclass(frozen=True)
class DeviceInfo:
    manufacturer: str
    model: str
    serial: str
    firmware_revision: str
    hardware_revision: str
    software_revision: str
    system_id: bytes
    ieee_cert: str
    pnp_id: bytes


def _utf8(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="replace")


def read_device_info(client) -> DeviceInfo:
    """Lee los 9 chars DIS de un BleakClient conectado. Sync wrapper."""
    async def _do():
        return DeviceInfo(
            manufacturer=_utf8(await client.read_gatt_char(CHAR_MANUFACTURER)),
            model=_utf8(await client.read_gatt_char(CHAR_MODEL)),
            serial=_utf8(await client.read_gatt_char(CHAR_SERIAL)),
            firmware_revision=_utf8(await client.read_gatt_char(CHAR_FW_REV)),
            hardware_revision=_utf8(await client.read_gatt_char(CHAR_HW_REV)),
            software_revision=_utf8(await client.read_gatt_char(CHAR_SW_REV)),
            system_id=await client.read_gatt_char(CHAR_SYSTEM_ID),
            ieee_cert=_utf8(await client.read_gatt_char(CHAR_IEEE_CERT)),
            pnp_id=await client.read_gatt_char(CHAR_PNP_ID),
        )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_do())
    fut = asyncio.run_coroutine_threadsafe(_do(), loop)
    return fut.result(timeout=10.0)


def read_battery_level(client) -> int:
    """Lee BAS 0x2A19 — primer octeto es % (DG01 devuelve [pct, 0x00])."""
    async def _do():
        raw = await client.read_gatt_char(CHAR_BATTERY_LEVEL)
        return raw[0] if raw else 0
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_do())
    fut = asyncio.run_coroutine_threadsafe(_do(), loop)
    return fut.result(timeout=10.0)
