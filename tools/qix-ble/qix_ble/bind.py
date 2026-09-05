"""Bind / unbind protocol via Qix frame FD00.

Port byte-exact de ebadge-python-cli/ebadge_cli/{bind_response.py, cli.py}.

Wire-format bind:
  TX QixFrame(flag=_next_flag(13), cmd=0x60, payload=13B)
      payload = [header 1B][device_id 6B LE][device_id 6B LE]
      header = (hour_flag << 2) | (lang_flag << 1)
        - hour_flag: 0=24h, 1=12h
        - lang_flag: 0=zh, 1=en
  RX QixFrame(cmd=0x61, payload≥39B) — BindResponse parsed con fields:
        state(1B) pact_version(3B str) firmwa_version(10B str)
        platform(4B BE) serial_number(4B BE)
        function_config(4B BE) function_config1(4B BE)
        function_config2(8B BE) ui_version(5B str)
        function_bytes(rest)

Wire-format unbind:
  TX QixFrame(flag=0x00, cmd=0x62, payload=b"\\x01")
  No response esperado (fire-and-forget).

Notas:
- Memoria: `cmd 0x60 → status 0x02` (gate) en E87 build production. La cmd
  está implementada en el firmware pero retorna rechazo. Implementamos paridad
  con community por si la flippea Vector B (firmware modify).
- El "device_id" default identifica a la PHONE (no al badge). Community usa
  hash de `platform.uname()`. Nosotros igual + opción override.
"""
from __future__ import annotations

import logging
import platform as _platform
from dataclasses import dataclass

from qix_ble.errors import BadgeRejected
from qix_ble.frame import QixFrame

log = logging.getLogger("qix_ble.bind")

CMD_BIND_REQ: int = 0x60
CMD_BIND_RESP: int = 0x61
CMD_UNBIND: int = 0x62

_SERIAL_COUNTER: int = 0


def _next_flag(payload_len: int) -> int:
    """Compute Qix flag byte byte-exact con community.

    flag = (serial 4bit << 3) | (is_long 1bit << 2) | (1 << 1)
    where serial increments mod 16, is_long = (payload_len + 6) > 20.
    """
    global _SERIAL_COUNTER
    serial = _SERIAL_COUNTER
    if serial == 16:
        serial = 0
    _SERIAL_COUNTER = serial + 1
    is_long = (payload_len + 6) > 20
    return ((serial << 3) | (int(is_long) << 2) | (1 << 1)) & 0xFF


def _reset_flag_counter() -> None:
    """Util para tests — resetear el counter global del serial."""
    global _SERIAL_COUNTER
    _SERIAL_COUNTER = 0


def _java_string_hash(value: str) -> int:
    """Match Java String.hashCode() (signed 32-bit)."""
    result = 0
    for char in value:
        result = (31 * result + ord(char)) & 0xFFFFFFFF
    if result & 0x80000000:
        result -= 0x100000000
    return result


def default_device_id() -> int:
    """Generate stable per-host device_id (matches community impl)."""
    parts = _platform.uname()
    signature = "35" + "".join(
        str(len(part) % 10)
        for part in [parts.system, parts.node, parts.release,
                     parts.version, parts.machine, parts.processor]
    )
    return _java_string_hash(signature)


def _long_to_6_bytes_le(value: int) -> bytes:
    """6-byte little-endian encoding del device_id (signed long compatible)."""
    if value < 0:
        value = (1 << 64) + value
    return bytes((value >> (8 * i)) & 0xFF for i in range(6))


def build_bind_payload(lang: str = "en", hour12: bool = False,
                       device_id: int | None = None) -> bytes:
    """Encode el 13-byte payload del bind request."""
    if lang not in ("zh", "en"):
        raise ValueError(f"lang debe ser 'zh' o 'en', got {lang!r}")
    lang_flag = 0 if lang == "zh" else 1
    hour_flag = 1 if hour12 else 0
    header = ((hour_flag << 2) | (lang_flag << 1)) & 0xFF
    if device_id is None:
        device_id = default_device_id()
    body_6 = _long_to_6_bytes_le(device_id)
    return bytes([header]) + body_6 + body_6


# ── Response parsing ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BindResponse:
    state: int
    pact_version: str
    firmwa_version: str
    platform: int
    serial_number: int
    function_config: int
    function_config1: int
    function_config2: int
    ui_version: str
    function_bytes: bytes


def _bytes_to_int_be(data: bytes, offset: int, length: int) -> int:
    if offset + length > len(data):
        raise ValueError(f"cannot read {length}B at offset {offset} de {len(data)}B")
    return int.from_bytes(data[offset:offset + length], "big")


def _bytes_to_string_cstr(data: bytes, offset: int, max_length: int) -> str:
    """ASCII, terminated por NUL o max_length, lo que llegue primero."""
    if offset >= len(data):
        return ""
    actual = min(max_length, len(data) - offset)
    end = offset + actual
    nul = data.find(b"\x00", offset, end)
    if nul != -1:
        end = nul
    return data[offset:end].decode("ascii", errors="ignore")


# Status codes observados en short bind responses (gated firmware)
BIND_STATUS_NAMES: dict[int, str] = {
    0x00: "SUCCESS",
    0x01: "UNKNOWN_ERROR",
    0x02: "NOT_SUPPORTED",
    0x03: "DATA_ERROR",
    0x04: "TIMEOUT",
    0x05: "REJECTED",
}


def parse_bind_response(payload: bytes) -> BindResponse | None:
    """Parse bind response. Tolerant a respuestas parciales.

    El E87 OEM FW production retorna 30B post-bootstrap (vs los 43+B canonical).
    Los primeros 30B contienen state+pact_ver+firmwa_ver+platform+serial+
    function_config+function_config1, que son los campos MÁS importantes
    (especialmente firmwa_version y serial_number). Returns None solo si
    payload < 14B (no hay siquiera firmwa_version).
    """
    if len(payload) < 14:
        return None
    return BindResponse(
        state=payload[0],
        pact_version=_bytes_to_string_cstr(payload, 1, 3),
        firmwa_version=_bytes_to_string_cstr(payload, 4, 10),
        platform=_bytes_to_int_be(payload, 14, 4) if len(payload) >= 18 else 0,
        serial_number=_bytes_to_int_be(payload, 18, 4) if len(payload) >= 22 else 0,
        function_config=_bytes_to_int_be(payload, 22, 4) if len(payload) >= 26 else 0,
        function_config1=_bytes_to_int_be(payload, 26, 4) if len(payload) >= 30 else 0,
        function_config2=_bytes_to_int_be(payload, 30, 8) if len(payload) >= 38 else 0,
        ui_version=_bytes_to_string_cstr(payload, 38, 5) if len(payload) >= 39 else "",
        function_bytes=bytes(payload[43:]) if len(payload) > 43 else b"",
    )


# ── High-level senders ───────────────────────────────────────────────────────

def bind(transport, lang: str = "en", hour12: bool = False,
         device_id: int | None = None, timeout: float = 5.0) -> BindResponse:
    """Send cmd 0x60 + wait response 0x61, parse + return.

    Raises:
        BadgeRejected: response no parsea como BindResponse válido.
        TimeoutError: badge no respondió en `timeout` segundos.
    """
    payload = build_bind_payload(lang=lang, hour12=hour12, device_id=device_id)
    flag = _next_flag(len(payload))
    log.info("bind: TX cmd=0x60 flag=0x%02x payload=%s", flag, payload.hex())
    transport.drain()
    resp = transport.send_command(
        CMD_BIND_REQ, payload, flags=flag,
        expect_cmd=CMD_BIND_RESP, timeout=timeout,
    )
    # Algunos firmwares (E87 cuando bonded previo, ZRun app real) responden
    # primero con un preamble de 1B (status code) y a continuación con el
    # full BindResponse (≥14B). Si recibimos preamble, esperar el siguiente
    # frame 0x61 con el data real. Empíricamente verificado 2026-05-16.
    if 1 <= len(resp.payload) < 14:
        status = resp.payload[0]
        status_name = BIND_STATUS_NAMES.get(status, f"0x{status:02x}")
        log.info("bind: preamble 1B status=0x%02x (%s) — esperando full response",
                 status, status_name)
        try:
            resp = transport.wait_for_cmd(CMD_BIND_RESP, timeout=timeout)
        except Exception as e:
            raise BadgeRejected(
                f"bind: preamble status=0x{status:02x} ({status_name}) recibido, "
                f"pero no llegó full response: {e}",
                cmd=CMD_BIND_REQ, state=status,
            )
    parsed = parse_bind_response(resp.payload)
    if parsed is None:
        if len(resp.payload) >= 1:
            status = resp.payload[0]
            status_name = BIND_STATUS_NAMES.get(status, f"0x{status:02x}")
            raise BadgeRejected(
                f"bind: response too short ({len(resp.payload)}B < 14 min). "
                f"status=0x{status:02x} ({status_name}). "
                f"Probablemente badge gated sin bootstrap previo — try bind con --bootstrap.",
                cmd=CMD_BIND_REQ, state=status,
            )
        raise BadgeRejected(
            f"bind: response payload vacío (badge silently dropped)",
            cmd=CMD_BIND_REQ,
        )
    if len(resp.payload) < 39:
        log.warning(
            "bind: partial response %dB (< 39B canonical). Parsed firmwa_version=%r "
            "serial=0x%08x — function_config2/ui_version/function_bytes vacíos.",
            len(resp.payload), parsed.firmwa_version, parsed.serial_number,
        )
    log.info("bind OK: state=%d serial=%d fw=%s ui=%s",
             parsed.state, parsed.serial_number,
             parsed.firmwa_version, parsed.ui_version)
    return parsed


def unbind(transport) -> None:
    """Send cmd 0x62 payload=[0x01]. Fire-and-forget — no espera response."""
    log.info("unbind: TX cmd=0x62")
    transport.send_raw(QixFrame(flags=0x00, cmd=CMD_UNBIND, payload=b"\x01"))
