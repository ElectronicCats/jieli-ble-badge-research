"""Probe sysinfo del DG01: handshake AE00 + RCSP get_target_info + get_sys_info.

Sucesor lógico de ae00_probe: una vez confirmado el handshake (2026-05-16 night),
el próximo paso es leer atributos del firmware via RCSP standard (cmds 0x03 y 0x07)
para extraer firmware_version, MAC, vid_pid, battery, modo, etc.

NO corre bootstrap completo del E87 (Phase 1-5 mezclan FD00 que DG01 no tiene).
Solo las partes RCSP-puras (sysinfo + target_info) que sí aplican a AE00-only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from qix_ble.auth import AuthSession
from qix_ble.rcsp_session import RcspSession
from qix_ble.sysinfo import get_sys_info, get_target_info, SysInfoResult, TargetInfoResult

from baji_ble.ae00_dbus_transport import Ae00DbusTransport

log = logging.getLogger("baji_ble.sysinfo_probe")


@dataclass(frozen=True)
class SysinfoProbeResult:
    success: bool
    message: str
    handshake_ok: bool = False
    target_info: TargetInfoResult | None = None
    sys_info: SysInfoResult | None = None


def probe_sysinfo(
    mac: str,
    *,
    handshake_timeout: float = 8.0,
    rcsp_timeout: float = 10.0,
    target_info_mask: int = 0xFFFFFFFF,
    sys_info_mask: int = 0xFFFFFFFF,
) -> SysinfoProbeResult:
    """Connect → handshake → RCSP target_info + sys_info. Captura excepciones."""
    log.info("sysinfo probe against %s", mac)
    handshake_ok = False
    target_info: TargetInfoResult | None = None
    sys_info: SysInfoResult | None = None
    try:
        with Ae00DbusTransport(mac) as t:
            log.info("step 1/3: handshake")
            AuthSession(t, step_timeout=handshake_timeout).do_handshake()
            handshake_ok = True

            sess = RcspSession(t)

            log.info("step 2/3: RCSP get_target_info (cmd 0x03)")
            target_info = get_target_info(sess, mask=target_info_mask, timeout=rcsp_timeout)

            log.info("step 3/3: RCSP get_sys_info (cmd 0x07)")
            sys_info = get_sys_info(sess, mask=sys_info_mask, timeout=rcsp_timeout)

        return SysinfoProbeResult(
            success=True,
            message="sysinfo probe complete",
            handshake_ok=True,
            target_info=target_info,
            sys_info=sys_info,
        )
    except Exception as e:  # noqa: BLE001 — probe captura todo
        log.warning("sysinfo probe failed: %s", e)
        return SysinfoProbeResult(
            success=False,
            message=str(e),
            handshake_ok=handshake_ok,
            target_info=target_info,
            sys_info=sys_info,
        )
