"""Bootstrap dance pre-upload (Phase 1-5 del community).

Port byte-exact de community/ebadge-python-cli/ebadge_cli/rcsp_transfer.py
lines 332-414, que prepara el badge para aceptar SESSION_OPEN (cmd 0x21).

El badge AC707N en build OEM gatea las operaciones post-auth si no se le
manda esta secuencia previa. Empíricamente verificado:
- cmd 0xC6 (screen info) funciona pre-bootstrap → working
- cmd 0x03 RCSP (target_info) funciona post-auth pre-bootstrap → working
- cmd 0x27 (battery via Qix) DROPPED pre-bootstrap → silently ignored.
  Probable conflicto: 0x27 es también XFER_PARAMS — el FW espera
  contexto de sesión.

Fases (ack-tolerant — cada wait usa try/except, continúa si timeout):

  Phase 1: RCSP cmd 0x06 (reset auth flag, body [0x02, 0x00, 0x01]) +
           FD02 raw bind-like trigger (verbatim community bytes, checksum
           inválido pero badge lo tolera) → wait cmd 0x06 ack (3s tol).

  Phase 2: FD02 set time (Qix cmd 0x02 7B payload year/month/day/hr/min)
           + cmd 0x16 [0x01] (display on?) + cmd 0x29 [0x80] (init?). Sleep.

  Phase 3: RCSP cmd 0x03 getTargetInfo + FD02 cmd 0xC6 + cmd 0x20 → wait
           cmd 0x03 ack (3s tol).

  Phase 4: RCSP cmd 0x07 getSysInfo + FD02 cmd 0xFF [0x22,0x00] + cmd 0xFF
           [0x24,0x00] → wait cmd 0x07 ack (3s tol).

  Phase 5: FD02 cmd 0x29 [0x80] replay (0.4s sleep) + cmd 0xC6 → wait
           any Qix frame with cmd 0xC7 (3s tol) + cmd 0xDC [0x0C] → wait
           any FD03 frame (3s tol, "ready signal").
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional, Protocol

from qix_ble.errors import BadgeRejected, TimeoutError as QixTimeoutError
from qix_ble.frame import QixFrame
from qix_ble.rcsp_frame import RcspFrame

log = logging.getLogger("qix_ble.bootstrap")

# RCSP opcodes
CMD_RESET_AUTH: int = 0x06
CMD_GET_TARGET_INFO: int = 0x03
CMD_GET_SYS_INFO: int = 0x07
FLAG_PHONE_REQ: int = 0xC0

# Qix opcodes (FD00 control)
QIX_CMD_SET_TIME: int = 0x02
QIX_CMD_DISPLAY: int = 0x16
QIX_CMD_INIT_29: int = 0x29
QIX_CMD_SCREEN_INFO: int = 0xC6
QIX_CMD_SCREEN_INFO_RESP: int = 0xC7
QIX_CMD_GENERIC_20: int = 0x20
QIX_CMD_FF: int = 0xFF
QIX_CMD_DC: int = 0xDC

# Verbatim community frame (Phase 1 trigger, checksum inválido pero badge lo
# tolera per try/except wrap del community).
COMMUNITY_PHASE1_TRIGGER: bytes = bytes([0x9E, 0xBD, 0x0B, 0x60, 0x0D, 0x00, 0x03])


class BootstrapTransport(Protocol):
    """Interface mínima del transport. Cubre QixTransport + MockTransport."""
    _auth_done: bool
    def send_rcsp_frame(self, frame: RcspFrame) -> None: ...
    def recv_rcsp_frame(self, timeout: float = 5.0) -> RcspFrame: ...
    def send_raw(self, frame: QixFrame) -> None: ...
    def write_fd02_raw(self, data: bytes) -> None: ...
    def wait_for_cmd(self, expect_cmd: int, timeout: float = 30.0) -> QixFrame: ...
    def drain(self) -> list[QixFrame]: ...


def run_bootstrap(transport: BootstrapTransport,
                  *, sleep: Optional[callable] = None,
                  phase_ack_timeout: float = 3.0) -> dict:
    """Ejecutar Phase 1-5 ack-tolerant. Returns dict con cada phase result.

    Args:
        transport: QixTransport o equivalente, post-auth.
        sleep: function(seconds) — usa time.sleep por default. Override en tests.
        phase_ack_timeout: timeout per wait. Community usa 3000ms.

    Returns: dict con keys phase_1, phase_3, phase_4, phase_5_c7,
             phase_5_fd03 → bool (True si ack recibido).
    """
    if not getattr(transport, "_auth_done", False):
        raise BadgeRejected(
            "bootstrap requires prior auth — call AuthSession.do_handshake() first"
        )
    _sleep = sleep if sleep is not None else time.sleep
    seq = 0
    results = {}

    # ── PHASE 1: cmd 0x06 reset auth flag ──
    log.info("bootstrap phase 1: cmd 0x06 reset_auth")
    transport.send_rcsp_frame(RcspFrame(
        flag=FLAG_PHONE_REQ, cmd=CMD_RESET_AUTH,
        payload=bytes([0x02, 0x00, 0x01]),
    ))
    seq = 1
    # Trigger verbatim community (some FWs need it, others ignore)
    try:
        transport.write_fd02_raw(COMMUNITY_PHASE1_TRIGGER)
    except Exception as e:
        log.debug("phase 1 trigger write failed (ignoring): %s", e)
    results["phase_1"] = _wait_rcsp_tolerant(
        transport, CMD_RESET_AUTH, phase_ack_timeout, "cmd 0x06 ack (RCSP)",
    )

    # ── PHASE 2: FD02 control writes (set time + display + init) ──
    log.info("bootstrap phase 2: FD02 control writes")
    now = datetime.now()
    time_payload = bytes([
        now.year & 0xFF, (now.year >> 8) & 0xFF,
        now.month, now.day, 0x00,
        now.hour, now.minute,
    ])
    transport.send_raw(QixFrame(flags=0x08, cmd=QIX_CMD_SET_TIME, payload=time_payload))
    _sleep(0.02)
    transport.send_raw(QixFrame(flags=0x08, cmd=QIX_CMD_DISPLAY, payload=b"\x01"))
    _sleep(0.02)
    transport.send_raw(QixFrame(flags=0x0B, cmd=QIX_CMD_INIT_29, payload=b"\x80"))
    _sleep(0.2)

    # ── PHASE 3: RCSP cmd 0x03 + FD02 0xC6 + 0x20 ──
    log.info("bootstrap phase 3: cmd 0x03 getTargetInfo")
    transport.send_rcsp_frame(RcspFrame(
        flag=FLAG_PHONE_REQ, cmd=CMD_GET_TARGET_INFO,
        payload=bytes([seq, 0xFF, 0xFF, 0xFF, 0xFF, 0x01]),
    ))
    seq += 1
    transport.send_raw(QixFrame(flags=0x0B, cmd=QIX_CMD_SCREEN_INFO, payload=b"\x01"))
    _sleep(0.02)
    transport.send_raw(QixFrame(flags=0x08, cmd=QIX_CMD_GENERIC_20, payload=b"\xff\x07"))
    results["phase_3"] = _wait_rcsp_tolerant(
        transport, CMD_GET_TARGET_INFO, phase_ack_timeout, "cmd 0x03 ack (RCSP)",
    )

    # ── PHASE 4: RCSP cmd 0x07 + FD02 0xFF×2 ──
    log.info("bootstrap phase 4: cmd 0x07 getSysInfo")
    transport.send_rcsp_frame(RcspFrame(
        flag=FLAG_PHONE_REQ, cmd=CMD_GET_SYS_INFO,
        payload=bytes([seq, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]),
    ))
    seq += 1
    transport.send_raw(QixFrame(flags=0x08, cmd=QIX_CMD_FF, payload=b"\x22\x00"))
    _sleep(0.04)
    transport.send_raw(QixFrame(flags=0x08, cmd=QIX_CMD_FF, payload=b"\x24\x00"))
    results["phase_4"] = _wait_rcsp_tolerant(
        transport, CMD_GET_SYS_INFO, phase_ack_timeout, "cmd 0x07 ack (RCSP)",
    )

    # ── PHASE 5: replay cmd 0x29 + 0xC6 + wait 0xC7 + 0xDC + wait FD03 ──
    log.info("bootstrap phase 5: FD02 ready-signal dance")
    transport.send_raw(QixFrame(flags=0x0B, cmd=QIX_CMD_INIT_29, payload=b"\x80"))
    _sleep(0.4)
    transport.send_raw(QixFrame(flags=0x0B, cmd=QIX_CMD_SCREEN_INFO, payload=b"\x01"))
    results["phase_5_c7"] = _wait_tolerant(
        transport, QIX_CMD_SCREEN_INFO_RESP, phase_ack_timeout, "FD01 C7 (screen info resp)",
    )
    transport.send_raw(QixFrame(flags=0x0B, cmd=QIX_CMD_DC, payload=b"\x0c"))
    # FD03 ready signal — community waits for ANY Qix frame with checksum 0xE6,
    # nuestro transport ya combina FD01+FD03 en _rx_queue. Esperamos cualquier
    # cmd que llegue post-DC. Tolerant.
    results["phase_5_fd03"] = _wait_any_tolerant(
        transport, phase_ack_timeout, "FD03 ready signal (any frame)",
    )

    log.info("bootstrap COMPLETE: %s", results)
    return results


def _wait_tolerant(transport: BootstrapTransport, expect_cmd: int,
                   timeout: float, label: str) -> bool:
    """Wait + log + ack-tolerant. True si recibió, False si timeout. Qix queue (FD00)."""
    try:
        transport.wait_for_cmd(expect_cmd, timeout=timeout)
        log.debug("bootstrap: ack %s", label)
        return True
    except QixTimeoutError:
        log.info("bootstrap: %s timeout (continuing)", label)
        return False


def _wait_rcsp_tolerant(transport: BootstrapTransport, expect_cmd: int,
                        timeout: float, label: str) -> bool:
    """Like _wait_tolerant pero polea AE02 RCSP queue, descartando otros cmds.

    Necesario para phases 1/3/4 que esperan RCSP response (cmd 0x06/0x03/0x07).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        try:
            frame = transport.recv_rcsp_frame(timeout=remaining)
        except QixTimeoutError:
            break
        except Exception as e:
            log.debug("bootstrap: %s recv error (ignored): %s", label, e)
            continue
        if frame.cmd == expect_cmd:
            log.debug("bootstrap: ack %s", label)
            return True
        log.debug("bootstrap: %s skip frame cmd=0x%02x (esperando 0x%02x)",
                  label, frame.cmd, expect_cmd)
    log.info("bootstrap: %s timeout (continuing)", label)
    return False


def _wait_any_tolerant(transport: BootstrapTransport, timeout: float,
                       label: str) -> bool:
    """Wait for any Qix frame. Used when expected cmd is uncertain (FD03 ready)."""
    try:
        # Drain expects an iterable; we poll for first frame con timeout corto
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # recv_frame retorna None si no hay → seguimos polling rápido
            f = transport.recv_frame(timeout=max(0.05, deadline - time.monotonic())) \
                if hasattr(transport, "recv_frame") else None
            if f is not None:
                log.debug("bootstrap: %s rx cmd=0x%02x", label, f.cmd)
                return True
        log.info("bootstrap: %s timeout (continuing)", label)
        return False
    except QixTimeoutError:
        log.info("bootstrap: %s timeout (continuing)", label)
        return False
