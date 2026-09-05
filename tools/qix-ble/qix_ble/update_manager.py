"""State machine OTA Qix (com.qix.library.sdk.UpdateManager decompiled).

Flow:
  1. Validate wrapper Qix (magic, type, size, CRC, JLUFW footer)
  2. Send 0xC0 REQ_UPDATE con header27 → wait 0xC1
  3. Loop chunks: send 0xC2 SEND_UPDATE_DATA → wait 0xC3
  4. Wait 0xC5 RET_UPDATE_RESULT
"""
from __future__ import annotations

import binascii
import logging
import struct
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from qix_ble.frame import QixFrame
from qix_ble.errors import BadgeRejected, UfwInvalid, BleConnectionError, TimeoutError

log = logging.getLogger("qix_ble.update_manager")


# Constantes (no via opcodes.py para evitar circular import si testeamos aislado)
CMD_REQ_UPDATE = 0xC0
CMD_RET_UPDATE = 0xC1
CMD_SEND_UPDATE_DATA = 0xC2
CMD_RET_UPDATE_DATA = 0xC3
CMD_RET_UPDATE_RESULT = 0xC5

QIX_MAGIC_BYTES = b"\xbc\xaf"
WRAPPER_LEN = 27
JLUFW_MAGIC = b"JLUFW"


# OTA-specific flag computation (port byte-exact de community ota_update.py:37-43).
# Distinto del bind flag (que usa `| 0x02`) — OTA usa `| 0x01`.
_OTA_SERIAL = 0


def _next_ota_flag(is_long: bool) -> int:
    global _OTA_SERIAL
    s = _OTA_SERIAL
    if s == 16:
        s = 0
    _OTA_SERIAL = s + 1
    return ((s << 3) | (int(is_long) << 2) | 1) & 0xFF


def _reset_ota_serial() -> None:
    """Test helper."""
    global _OTA_SERIAL
    _OTA_SERIAL = 0


@dataclass(frozen=True)
class ProbeResult:
    """Resultado de QixUpdater.probe() — REQ_UPDATE handshake only, zero flash write.

    Campos:
        state: state_byte del RET_UPDATE (1 = badge ready for data, otro = rechazo)
        allow_len: bytes que el badge puede aceptar per chunk (RET_UPDATE BE32 word 2)
        offset: offset desde el que badge espera data (RET_UPDATE BE32 word 3). 0 = fresh,
                >0 = partial OTA pendiente (warning).
        dt_ms: latencia REQ_UPDATE → RET_UPDATE en ms.
        accepted: True iff state == 1 AND offset == 0 — significa "clean accept".
    """
    state: int
    allow_len: int
    offset: int
    dt_ms: float
    accepted: bool


class UpdaterState(Enum):
    IDLE = 0
    SENT_REQ = 1
    SENDING_CHUNKS = 2
    AWAITING_RESULT = 3
    DONE = 4
    FAILED = 5


class TransportProtocol(Protocol):
    def send_command(self, cmd: int, payload: bytes = b"", flags: int = 0,
                     expect_cmd: int | None = None, timeout: float = 5.0,
                     response: bool = False) -> QixFrame: ...
    def wait_for_cmd(self, expect_cmd: int, timeout: float = 30.0) -> QixFrame: ...
    def drain(self) -> list[QixFrame]: ...


def validate_qix_wrapper(ufw: bytes) -> None:
    """Re-implementación de wrap_qix.py logic. Raise UfwInvalid si algo falla."""
    if len(ufw) < WRAPPER_LEN + 16:
        raise UfwInvalid(f"ufw demasiado corto: {len(ufw)} bytes")
    if ufw[:2] != QIX_MAGIC_BYTES:
        raise UfwInvalid(f"magic Qix inválido: {ufw[:2].hex()} != bcaf")
    if ufw[2] != 0x01:
        raise UfwInvalid(f"type inválido: 0x{ufw[2]:02x} != 0x01")

    declared_size = int.from_bytes(ufw[13:17], "little")
    actual_size = len(ufw) - WRAPPER_LEN
    if declared_size != actual_size:
        raise UfwInvalid(f"size mismatch: header dice {declared_size}, actual {actual_size}")

    payload = ufw[WRAPPER_LEN:]
    expected_crc = binascii.crc_hqx(payload, 0xFFFF) & 0xFFFF
    actual_crc = int.from_bytes(ufw[25:27], "little")
    if expected_crc != actual_crc:
        raise UfwInvalid(f"CRC mismatch: header 0x{actual_crc:04x} vs computed 0x{expected_crc:04x}")

    # JLUFW magic: en self-built/2.3.6 OEM aparece en los últimos 32B; en V1.0.3 oficial
    # del cloud aparece en offset interno (0x102210). Buscar en todo el payload, no solo footer.
    if JLUFW_MAGIC not in payload:
        raise UfwInvalid("JLUFW magic no encontrado en payload")


class QixUpdater:
    def __init__(self, transport: TransportProtocol):
        self.transport = transport
        self.state = UpdaterState.IDLE

    def probe(self, ufw_bytes: bytes, timeout: float = 5.0) -> ProbeResult:
        """REQ_UPDATE handshake → parse RET_UPDATE → return. ZERO flash write.

        Invariant: NUNCA envía 0xC2. Si llegamos a la phase chunk loop, hay un bug.

        Args:
            ufw_bytes: el .ufw completo (header27 + payload). Solo el header27 se envía.
            timeout: segundos para esperar la respuesta 0xC1.

        Returns:
            ProbeResult con state/allow_len/offset/dt_ms/accepted.

        Raises:
            UfwInvalid: wrapper Qix local inválido (no se inicia BLE write).
            TimeoutError: badge no respondió 0xC1 en `timeout` segundos.
            BadgeRejected: response 0xC1 malformado (payload <9 bytes).
        """
        validate_qix_wrapper(ufw_bytes)
        self.transport.drain()
        header27 = ufw_bytes[:WRAPPER_LEN]

        # Community usa flag con serial rotating + is_long bit + 0x01 trailing,
        # Y BLE Write-With-Response. Verificado contra ota_update.py:46-48,120.
        is_long = (len(header27) + 6) > 20  # 27+6=33 > 20 → True siempre
        flag = _next_ota_flag(is_long)
        log.info("PROBE REQ_UPDATE: flag=0x%02x header=%s", flag, header27.hex())
        t0 = time.monotonic()
        resp = self.transport.send_command(
            CMD_REQ_UPDATE, header27, flags=flag,
            expect_cmd=CMD_RET_UPDATE, timeout=timeout, response=True,
        )
        dt_ms = (time.monotonic() - t0) * 1000.0

        if len(resp.payload) < 9:
            raise BadgeRejected(
                f"RET_UPDATE payload corto: {len(resp.payload)} bytes",
                cmd=CMD_REQ_UPDATE,
            )
        state, allow_len, offset = struct.unpack(">BII", resp.payload[:9])
        log.info("PROBE RET_UPDATE: state=%d allow=%d offset=%d dt=%.1fms",
                 state, allow_len, offset, dt_ms)
        return ProbeResult(
            state=state,
            allow_len=allow_len,
            offset=offset,
            dt_ms=dt_ms,
            accepted=(state == 1 and offset == 0),
        )

    def flash(
        self,
        ufw_bytes: bytes,
        is_firmware_update: bool = True,
        on_progress: Callable[[float], None] | None = None,
        timeout_per_chunk: float = 5.0,
        timeout_final: float = 30.0,
        timeout_req: float | None = None,
        on_reconnect: Callable[[], None] | None = None,
        max_reconnects: int = 4,
    ) -> None:
        # OEM firmware reacts slowly to REQ_UPDATE (~6 s: shows "actualizando",
        # may change GATT services) — give RET_UPDATE its own, longer timeout so the
        # first handshake doesn't false-timeout. Our own stager firmware replies fast.
        timeout_req = timeout_req if timeout_req is not None else timeout_per_chunk
        validate_qix_wrapper(ufw_bytes)

        # Limpiar frames RX pendientes (stale BLE notifications de operaciones previas)
        self.transport.drain()

        header27 = ufw_bytes[:WRAPPER_LEN]
        payload = ufw_bytes[WRAPPER_LEN:]

        # Step 4: REQ_UPDATE con header27
        is_long = (len(header27) + 6) > 20
        flag = _next_ota_flag(is_long)
        log.info("REQ_UPDATE: flag=0x%02x payload=%d bytes, header=%s",
                 flag, len(payload), header27.hex())
        self.state = UpdaterState.SENT_REQ
        resp = self.transport.send_command(
            CMD_REQ_UPDATE, header27, flags=flag,
            expect_cmd=CMD_RET_UPDATE, timeout=timeout_req, response=True,
        )
        if len(resp.payload) < 9:
            self.state = UpdaterState.FAILED
            raise BadgeRejected(f"RET_UPDATE payload corto: {len(resp.payload)} bytes",
                                cmd=CMD_REQ_UPDATE)
        state_byte, allow_len, offset_pos = struct.unpack(">BII", resp.payload[:9])
        log.info("RET_UPDATE: state=%d allow=%d offset=%d", state_byte, allow_len, offset_pos)
        if state_byte != 1:
            self.state = UpdaterState.FAILED
            raise BadgeRejected(f"REQ_UPDATE rejected, state={state_byte}",
                                cmd=CMD_REQ_UPDATE, state=state_byte)
        if allow_len == 0:
            self.state = UpdaterState.FAILED
            raise BadgeRejected("RET_UPDATE allow_len=0 (badge halt)",
                                cmd=CMD_REQ_UPDATE, state=state_byte)

        # Step 5: Loop chunks
        # NOTA 2026-05-16 night: el badge dice `allow_len` con un valor abstract
        # (window size acumulativo? hint del firmware buffer?), pero el wire frame
        # Qix tiene max payload 65535B (len field LE16). ATT MTU 517 → fragmenta
        # internamente vía GATT long write (bleak lo hace transparente).
        #
        # 2026-05-16 evening UPDATE (btsnoop del app ZRun real exitoso):
        #   - App usa **chunk_size = 1024 bytes** (8B header + 1024B data = 1032B Qix payload)
        #     → 2-3 ATT writes via long-write per chunk. Empíricamente sostenible.
        #   - App usa **BLE WriteWithoutResponse** (WRITE_CMD op=0x52) — saltea
        #     link-layer ACK wait, app-level flow control via cmd 0xC3 sí espera.
        #   - C2→C3 cycle: ~75ms (vs 175ms con WriteWithResponse + chunk 480).
        # Resultado: 4.5x faster end-to-end vs config conservative anterior.
        MAX_WIRE_CHUNK = 1024  # bytes de UFW data per Qix frame (sin contar 8B header chunk)
        self.state = UpdaterState.SENDING_CHUNKS
        offset = offset_pos
        total = len(payload)
        final_resp = None  # capture 0xC5 si llega inline en el último chunk
        reconnects = 0     # count of mid-transfer link drops recovered so far
        while offset < total:
            chunk_size = min(allow_len, MAX_WIRE_CHUNK, total - offset)
            chunk_payload = (
                chunk_size.to_bytes(4, "little")
                + offset.to_bytes(4, "little")
                + payload[offset : offset + chunk_size]
            )
            chunk_is_long = (len(chunk_payload) + 6) > 20
            chunk_flag = _next_ota_flag(chunk_is_long)
            # Empírico 2026-05-16: en el último chunk el badge omite el `0xC3
            # RET_UPDATE_DATA` y emite directamente `0xC5 RET_UPDATE_RESULT`.
            # En lugar de filtrar estricto por 0xC3 (causa false-timeout @ 99%),
            # aceptamos ambos cmds en el último chunk y procesamos según el que llegue.
            is_last_chunk = (offset + chunk_size) >= total
            expect = ((CMD_RET_UPDATE_DATA, CMD_RET_UPDATE_RESULT)
                      if is_last_chunk else CMD_RET_UPDATE_DATA)
            try:
                resp = self.transport.send_command(
                    CMD_SEND_UPDATE_DATA, chunk_payload, flags=chunk_flag,
                    expect_cmd=expect, timeout=timeout_per_chunk,
                    response=True,  # 2026-05-16 night: revertido de False — WriteWithoutResponse
                                    # causaba BleakDBusError "Failed to initiate write" en wrapper-tampered
                                    # UFW post-REQ_UPDATE. Conservative WriteWithResponse para debugging.
                )
            except (BleConnectionError, TimeoutError) as e:
                # Link dropped / stalled mid-transfer (the classic sustained-~1MB PC
                # BlueZ drop near the end). Resume WITHOUT re-issuing REQ_UPDATE: the
                # badge keeps qix_ota_offset + qix_ota_active in RAM across a BLE
                # disconnect, and 0xC0 REQ_UPDATE RE-ERASES the staging span (firmware
                # qix_ota_server.c) which would restart from 0 and hit the same drop
                # forever. FD02 is cleartext (no re-auth needed), so we just reconnect
                # the LINK and continue the chunk loop from the current offset — the
                # badge accepts the next SEND_DATA because chunk_off == its preserved
                # qix_ota_offset. Only if the badge then rejects the resumed chunk
                # (offset mismatch → it DID lose state) do we fall back to REQ_UPDATE.
                if on_reconnect is None or reconnects >= max_reconnects:
                    self.state = UpdaterState.FAILED
                    raise
                reconnects += 1
                log.warning("chunk @ offset %d dropped (%s) — reconnect %d/%d + resume "
                            "(continue @ %d, no REQ_UPDATE)", offset, e, reconnects,
                            max_reconnects, offset)
                on_reconnect()
                self.transport.drain()
                # Keep offset/allow_len as-is and retry the SAME chunk. If the badge
                # lost state, its ret_data will carry state!=0 and we recover below.
                continue
            if resp.cmd == CMD_RET_UPDATE_RESULT:
                # Badge salteó 0xC3 del último chunk y mandó directo 0xC5.
                log.info("último chunk: badge respondió 0xC5 directo (skipped 0xC3)")
                final_resp = resp
                if on_progress:
                    on_progress(1.0)
                break
            if len(resp.payload) < 5:
                self.state = UpdaterState.FAILED
                raise BadgeRejected(f"RET_UPDATE_DATA payload corto: {len(resp.payload)}",
                                    cmd=CMD_SEND_UPDATE_DATA)
            # NOTA 2026-05-16 night: endian del next_offset en 0xC3 es LE32, NO BE32.
            # Inconsistente con 0xC1 RET_UPDATE (que sí es BE32 para allow_len + offset).
            # Empíricamente verificado: badge echo nuestro size=480 (E0 01 00 00) y
            # interpretado BE32 da 0xE0010000 (3.76B); interpretado LE32 da 480 correcto.
            # ebadge-python-cli/ota_update.py:161 usa BE32 pero probably nunca testearon
            # chunks reales por el TEST_MODE silent reject de community samples.
            chunk_state, next_offset = struct.unpack("<BI", resp.payload[:5])
            if chunk_state != 0:
                self.state = UpdaterState.FAILED
                raise BadgeRejected(f"chunk failed @ offset {offset}, state={chunk_state}",
                                    cmd=CMD_SEND_UPDATE_DATA, state=chunk_state)
            offset = next_offset
            if on_progress:
                on_progress(offset / total)

        # Step 6: Final result. Si ya lo capturamos inline en el último chunk lo reusamos;
        # sino lo esperamos via notify unsolicited.
        self.state = UpdaterState.AWAITING_RESULT
        if final_resp is None:
            log.info("waiting RET_UPDATE_RESULT (timeout=%.0fs)", timeout_final)
            final_resp = self.transport.wait_for_cmd(CMD_RET_UPDATE_RESULT, timeout=timeout_final)
        if not final_resp.payload:
            self.state = UpdaterState.FAILED
            raise BadgeRejected("RET_UPDATE_RESULT payload vacío", cmd=CMD_RET_UPDATE_RESULT)
        status = final_resp.payload[0]
        log.info("RET_UPDATE_RESULT: status=%d", status)
        if status != 0:
            self.state = UpdaterState.FAILED
            raise BadgeRejected(f"final status={status}", cmd=CMD_RET_UPDATE_RESULT, state=status)
        self.state = UpdaterState.DONE

    def _reissue_req_update(self, header27: bytes, timeout_req: float) -> tuple[int, int]:
        """Re-emit REQ_UPDATE after a mid-transfer reconnect and return (allow_len,
        offset). The OTA is device-driven by offset: the badge replies RET_UPDATE with
        the byte offset it expects next, so we resume the chunk loop from there."""
        flag = _next_ota_flag((len(header27) + 6) > 20)
        log.info("REQ_UPDATE (resume): flag=0x%02x header=%s", flag, header27.hex())
        resp = self.transport.send_command(
            CMD_REQ_UPDATE, header27, flags=flag,
            expect_cmd=CMD_RET_UPDATE, timeout=timeout_req, response=True,
        )
        if len(resp.payload) < 9:
            self.state = UpdaterState.FAILED
            raise BadgeRejected(f"RET_UPDATE (resume) payload corto: {len(resp.payload)} bytes",
                                cmd=CMD_REQ_UPDATE)
        state_byte, allow_len, offset_pos = struct.unpack(">BII", resp.payload[:9])
        log.info("RET_UPDATE (resume): state=%d allow=%d offset=%d",
                 state_byte, allow_len, offset_pos)
        if state_byte != 1:
            self.state = UpdaterState.FAILED
            raise BadgeRejected(f"REQ_UPDATE (resume) rejected, state={state_byte}",
                                cmd=CMD_REQ_UPDATE, state=state_byte)
        if allow_len == 0:
            self.state = UpdaterState.FAILED
            raise BadgeRejected("RET_UPDATE (resume) allow_len=0 (badge halt)",
                                cmd=CMD_REQ_UPDATE, state=state_byte)
        return allow_len, offset_pos
