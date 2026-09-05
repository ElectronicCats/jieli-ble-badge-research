"""Probe del path AE00 RCSP del DG01 reusando qix-ble auth (vendor-wide JieLi).

Solo handshake 6-step. NO bootstrap, NO sysinfo write, NO update_manager.
Sirve para cerrar el TBD #1 del Day 1 DG01: ¿AE00 advertised acepta el mismo
handshake byte-exact que el E87?
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from qix_ble.auth import AuthSession

from baji_ble.ae00_dbus_transport import Ae00DbusTransport

log = logging.getLogger("baji_ble.ae00_probe")


@dataclass(frozen=True)
class AE00ProbeResult:
    success: bool
    message: str


def probe_ae00(mac: str, *, timeout: float = 5.0) -> AE00ProbeResult:
    """Intenta el handshake JieLi RCSP 6-step contra AE00/AE01/AE02 del DG01.

    Retorna AE00ProbeResult — NO levanta excepciones (las captura en .message).

    Usa Ae00DbusTransport (BlueZ DBus directo via dbus-fast) en lugar de bleak,
    porque bleak 3.0.1 hangs en `client.connect()` contra DG01 esperando
    ServicesResolved=true que el firmware no emite (per Day 1 bitacora).
    """
    log.info("probe AE00 handshake against %s (timeout per step=%.1fs)", mac, timeout)
    try:
        with Ae00DbusTransport(mac) as t:
            session = AuthSession(t, step_timeout=timeout)
            session.do_handshake()
        return AE00ProbeResult(
            success=True,
            message="handshake completed — DG01 AE00 path is functional",
        )
    except Exception as e:  # noqa: BLE001 — probe quiere capturar todo
        log.warning("probe failed: %s", e)
        return AE00ProbeResult(success=False, message=str(e))
