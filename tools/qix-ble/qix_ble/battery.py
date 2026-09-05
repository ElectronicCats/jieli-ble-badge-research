"""Battery query — paths según el firmware.

**Path correcto para FW E87 OEM production: device push** (community PROTOCOL.md §6):
  1. Run bootstrap Phase 1-5 (cmd 0x06 + time + cmd 0x29 etc.)
  2. Después del Phase 5 (`9E B5 0B 29 01 00 80` write a FD02), el badge
     hace PUSH spontáneo de cmd 0x27 payload=[charge_mode, percent].
  3. Listen passive en FD01 hasta capturar el frame 0x27.

**Path NUNCA funciona en este FW:**
  - `query_battery_legacy()`: standalone cmd 0x27 query — no response.
  - `query_battery_via_sysinfo()`: sys_info attr 0 — **siempre retorna 0x00
    FAKE VALUE** per community docs. Lo dejamos en código solo para detectar
    si tenemos un FW distinto que sí lo populate.
  - BLE std Battery Service 0x180F/0x2A19: read empty, no notify push.

Default `query_battery()` usa device push path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from qix_ble.errors import BadgeRejected

log = logging.getLogger("qix_ble.battery")

# Legacy path (cmd 0x27)
CMD_BATTERY: int = 0x27
BATTERY_REQ_PAYLOAD: bytes = b"\x01"

# Sys_info path (RCSP attr type 0)
SYS_INFO_ATTR_BATTERY: int = 0


@dataclass(frozen=True)
class BatteryStatus:
    """percent 0..100. mode: 0=discharging, 1=charging probable per community."""
    percent: int
    mode: int = 0


def query_battery(transport, listen_timeout: float = 3.0) -> BatteryStatus:
    """Battery via device push post-bootstrap (FW E87 OEM production path).

    Pre-condition: auth handshake + bootstrap Phase 1-5 ya corrido.
    Bootstrap Phase 5 (write FD02 `9E B5 0B 29 01 00 80`) triggera al device
    a hacer push de cmd 0x27 con `[charge_mode, percent]`.

    Args:
        transport: QixTransport con auth+bootstrap completados.
        listen_timeout: segundos para esperar el device push.

    Raises:
        BadgeRejected: timeout sin recibir push (FW probablemente no soporta).
    """
    import time
    from qix_ble.errors import TimeoutError as QixTimeoutError
    deadline = time.monotonic() + listen_timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            frame = transport.recv_frame(timeout=remaining)
        except QixTimeoutError:
            continue
        if frame is None:
            continue
        if frame.cmd == CMD_BATTERY and len(frame.payload) >= 2:
            mode = frame.payload[0]
            percent = frame.payload[1]
            log.info("battery push received: mode=0x%02x percent=%d", mode, percent)
            return BatteryStatus(percent=percent, mode=mode)
        log.debug("battery: descarto frame cmd=0x%02x (esperando 0x27 push)", frame.cmd)
    raise BadgeRejected(
        f"battery: no se recibió device push 0x27 en {listen_timeout}s — "
        "FW probablemente no soporta battery query en este path",
        cmd=CMD_BATTERY,
    )


def query_battery_via_sysinfo(rcsp_session, timeout: float = 5.0) -> BatteryStatus:
    """Battery via RCSP sys_info attr 0. **DEPRECATED para E87 OEM FW**.

    Community PROTOCOL.md §6 documenta que attr 0 retorna 0x00 fake value
    siempre en este FW. Función queda para detectar si tenemos otro FW
    donde sí sea populated.
    """
    from qix_ble.sysinfo import get_sys_info
    sys_info = get_sys_info(rcsp_session, timeout=timeout)
    for attr in sys_info.attrs:
        if attr.type == SYS_INFO_ATTR_BATTERY and attr.data:
            percent = attr.data[0]
            log.warning("battery via sys_info attr 0 returned %d "
                        "(community says este es fake value 0x00 en E87 OEM)", percent)
            return BatteryStatus(percent=percent, mode=0)
    raise BadgeRejected(
        f"battery: sys_info no incluyó attr 0 (got {len(sys_info.attrs)} attrs)",
        cmd=0x07,
    )


def query_battery_legacy(transport, timeout: float = 5.0) -> BatteryStatus:
    """Battery via Qix cmd 0x27 (community CLI compat).

    ROTO en FW E87 production 2026. Solo funciona en SDKs antiguos.
    Ver memory feedback_qix_battery_cmd_wrong_path.
    """
    transport.drain()
    resp = transport.send_command(
        CMD_BATTERY, BATTERY_REQ_PAYLOAD, expect_cmd=CMD_BATTERY, timeout=timeout
    )
    if len(resp.payload) < 2:
        raise BadgeRejected(
            f"battery response payload corto: {len(resp.payload)} bytes",
            cmd=CMD_BATTERY,
        )
    mode, percent = resp.payload[0], resp.payload[1]
    log.info("battery mode=0x%02x percent=%d", mode, percent)
    return BatteryStatus(mode=mode, percent=percent)
