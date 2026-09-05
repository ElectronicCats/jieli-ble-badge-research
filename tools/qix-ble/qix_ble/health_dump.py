"""Dump histórico de health data del badge JieLi E87.

Per RE smali del app ZRun (`BTCommandManager.requestData` + `ReceiveCommandParse`):
  El sync de history es triggered por `cmd 0x29 REQ_DATA` con un mask byte.
  El badge responde con `cmd 0x21 SYNC_STATE state=0` (START), luego empuja
  frames `cmd 0x22..0x27` con los datos per-day, y termina con
  `cmd 0x21 SYNC_STATE state=1` (END).

Cada frame data lleva: `[Y][M][D][N:u8] + N * record_struct` donde Y = year-2000,
M = month 1-12, D = day 1-31, N = entries en el día, record_struct varía por
tipo.

Mask bits (cmd 0x29 byte 0):
    0x01: steps    (cmd 0x22)
    0x02: sleep    (cmd 0x23)
    0x04: hr       (cmd 0x24)
    0x08: pressure (cmd 0x25)
    0x10: oxygen   (cmd 0x26)
    0x20: battery  (cmd 0x27)
    0x40: (skip — cleared in 0xBF default)
    0x80: ext      (pact<25 legacy remap)

App ZRun usa siempre `0xBF` (all except bit 6).

Auth NOT required. BIND post-bootstrap es suficiente per
`AppBleManager$bindResponse$1.java:130 requestData(191)`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from qix_ble.frame import QixFrame

log = logging.getLogger("qix_ble.health_dump")


CMD_REQ_DATA: int = 0x29
CMD_RET_STEP: int = 0x22
CMD_RET_SLEEP: int = 0x23
CMD_RET_HR: int = 0x24
CMD_RET_PRESSURE: int = 0x25
CMD_RET_OXYGEN: int = 0x26
CMD_RET_BATTERY: int = 0x27
CMD_RET_SYNCH_STATE: int = 0x21
CMD_ACK: int = 0xFF


DATA_TYPE_MASK: dict[str, int] = {
    "steps": 0x01,
    "sleep": 0x02,
    "hr": 0x04,
    "pressure": 0x08,
    "oxygen": 0x10,
    "battery": 0x20,
}

CMD_TO_KEY: dict[int, str] = {
    CMD_RET_STEP: "steps",
    CMD_RET_SLEEP: "sleep",
    CMD_RET_HR: "hr",
    CMD_RET_PRESSURE: "pressure",
    CMD_RET_OXYGEN: "oxygen",
    CMD_RET_BATTERY: "battery",
}


@dataclass
class DumpResult:
    """Returns from dump_health(). Each entry is a dict with parsed fields."""
    steps: list[dict[str, Any]] = field(default_factory=list)
    sleep: list[dict[str, Any]] = field(default_factory=list)
    hr: list[dict[str, Any]] = field(default_factory=list)
    pressure: list[dict[str, Any]] = field(default_factory=list)
    oxygen: list[dict[str, Any]] = field(default_factory=list)
    battery: dict[str, Any] | None = None
    raw_frames: int = 0  # total RX frames count for diagnostics


def _date(p: bytes) -> tuple[int, int, int]:
    """Parse Y/M/D prefix from data frame. Y = year-2000."""
    return (2000 + p[0], p[1], p[2])


def parse_steps(p: bytes) -> list[dict[str, Any]]:
    """RET_STEP_DATA: [Y][M][D][N] + N×17B [ts:u16][mode:u8][time:u16]
    [step:u32][dist:u32][cal:u32] (todo LE)."""
    y, m, d = _date(p)
    n = p[3]
    expected_len = 4 + n * 17
    if len(p) != expected_len:
        log.warning("step frame size %d != %d (n=%d)", len(p), expected_len, n)
    out: list[dict[str, Any]] = []
    for i in range(n):
        off = 4 + i * 17
        if off + 17 > len(p):
            break
        ts = p[off] | (p[off + 1] << 8)
        mode = p[off + 2]
        time = p[off + 3] | (p[off + 4] << 8)
        step = int.from_bytes(p[off + 5:off + 9], "little")
        dist = int.from_bytes(p[off + 9:off + 13], "little")
        cal = int.from_bytes(p[off + 13:off + 17], "little")
        out.append({"date": (y, m, d), "ts_min": ts, "mode": mode,
                    "time": time, "step": step, "dist": dist, "cal": cal})
    return out


def parse_sleep(p: bytes) -> list[dict[str, Any]]:
    """RET_SLEEP_DATA: [Y][M][D][N] + N×3B [ts:u16][mode:u8] (LE).
    mode = period start marker (1=light, 2=deep, 3=rem etc. per OEM)."""
    y, m, d = _date(p)
    n = p[3]
    out: list[dict[str, Any]] = []
    for i in range(n):
        off = 4 + i * 3
        if off + 3 > len(p):
            break
        ts = p[off] | (p[off + 1] << 8)
        mode = p[off + 2]
        out.append({"date": (y, m, d), "ts_min": ts, "mode": mode})
    return out


def parse_hr(p: bytes) -> list[dict[str, Any]]:
    """RET_HR_DATA: [Y][M][D][N] + N×3B [ts:u16][bpm:u8] (LE)."""
    y, m, d = _date(p)
    n = p[3]
    out: list[dict[str, Any]] = []
    for i in range(n):
        off = 4 + i * 3
        if off + 3 > len(p):
            break
        ts = p[off] | (p[off + 1] << 8)
        bpm = p[off + 2]
        out.append({"date": (y, m, d), "ts_min": ts, "bpm": bpm})
    return out


def parse_pressure(p: bytes) -> list[dict[str, Any]]:
    """RET_PRESSURE_DATA: [Y][M][D][N] + N×4B [ts:u16][low:u8][high:u8] (LE).
    SDK swap si low > high."""
    y, m, d = _date(p)
    n = p[3]
    out: list[dict[str, Any]] = []
    for i in range(n):
        off = 4 + i * 4
        if off + 4 > len(p):
            break
        ts = p[off] | (p[off + 1] << 8)
        low = p[off + 2]
        high = p[off + 3]
        if low > high:
            low, high = high, low
        out.append({"date": (y, m, d), "ts_min": ts,
                    "systolic": high, "diastolic": low})
    return out


def parse_oxygen(p: bytes) -> list[dict[str, Any]]:
    """RET_OXYGEN_DATA: [Y][M][D][N] + N×3B [ts:u16][spo2:u8] (LE)."""
    y, m, d = _date(p)
    n = p[3]
    out: list[dict[str, Any]] = []
    for i in range(n):
        off = 4 + i * 3
        if off + 3 > len(p):
            break
        ts = p[off] | (p[off + 1] << 8)
        spo2 = p[off + 2]
        out.append({"date": (y, m, d), "ts_min": ts, "spo2": spo2})
    return out


def parse_battery(p: bytes) -> dict[str, Any]:
    """RET_BATTERY_DATA: [charge_mode:u8][percent:u8]."""
    if len(p) < 2:
        return {"raw": p.hex()}
    return {"charge_mode": p[0], "percent": p[1]}


PARSERS: dict[int, Any] = {
    CMD_RET_STEP: parse_steps,
    CMD_RET_SLEEP: parse_sleep,
    CMD_RET_HR: parse_hr,
    CMD_RET_PRESSURE: parse_pressure,
    CMD_RET_OXYGEN: parse_oxygen,
    CMD_RET_BATTERY: parse_battery,
}


def types_to_mask(types: list[str]) -> int:
    """Convert list of type names to mask byte. Unknown types raise KeyError."""
    mask = 0
    for t in types:
        if t not in DATA_TYPE_MASK:
            raise KeyError(f"unknown data type: {t} (valid: {list(DATA_TYPE_MASK)})")
        mask |= DATA_TYPE_MASK[t]
    return mask


def _maybe_ack(transport, frame: QixFrame) -> None:
    """Echo ack via cmd 0xFF [cmd_echo, status=0] si el frame request ack.

    Per `ReceiveCommandParse.flagStatuParse @ 408`: si `flags & 0x02 != 0`
    el SDK auto-emite ack. La app real lo hace para 0x22/0x24/0x27 frames.
    """
    if frame.flags & 0x02:
        ack_payload = bytes([frame.cmd, 0x00])  # echo_cmd + status=ok
        try:
            transport.send_raw(QixFrame(flags=0x00, cmd=CMD_ACK, payload=ack_payload))
            log.debug("auto-acked cmd=0x%02x", frame.cmd)
        except Exception as e:
            log.warning("auto-ack failed for cmd=0x%02x: %s", frame.cmd, e)


def dump_health(transport, types: list[str] | None = None,
                timeout_per_frame: float = 10.0,
                total_timeout: float = 60.0) -> DumpResult:
    """Trigger + collect del sync histórico.

    Args:
        transport: QixTransport con BIND ya realizado (no requiere auth).
        types: lista de strings ['steps','sleep','hr','pressure','oxygen','battery'].
               None = todos (mask 0xBF, equivalente al app real).
        timeout_per_frame: timeout entre frames RX consecutivos.
        total_timeout: timeout total del dump completo.

    Returns:
        DumpResult con todos los entries parseados por tipo.

    Raises:
        TimeoutError si no llega SYNC_START o SYNC_END a tiempo.
    """
    import time
    if types is None:
        mask = 0xBF
    else:
        mask = types_to_mask(types)

    log.info("dump_health: mask=0x%02x (types=%s)", mask, types or "all")

    # Step 1: trigger
    transport.drain()
    transport.send_raw(
        QixFrame(flags=0x00, cmd=CMD_REQ_DATA, payload=bytes([mask])),
    )

    # Step 2: wait SYNC_START (cmd 0x21 state byte == 0)
    deadline = time.monotonic() + total_timeout
    start_frame = transport.wait_for_cmd(
        CMD_RET_SYNCH_STATE,
        timeout=min(timeout_per_frame, max(0.1, deadline - time.monotonic())),
    )
    _maybe_ack(transport, start_frame)
    if not start_frame.payload or start_frame.payload[0] != 0x00:
        raise RuntimeError(
            f"expected SYNC_START (state=0) got state="
            f"{start_frame.payload[0] if start_frame.payload else 'empty'}"
        )
    log.info("SYNC_START received, collecting data frames...")

    # Step 3: collect frames until SYNC_END (cmd 0x21 state == 1) or timeout
    result = DumpResult()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.warning("total_timeout reached, returning partial result")
            break
        wait = min(timeout_per_frame, max(0.1, remaining))
        frame = transport.recv_frame(timeout=wait)
        if frame is None:
            log.warning("no frame received in %.1fs — assuming dump done", wait)
            break

        result.raw_frames += 1

        if frame.cmd == CMD_RET_SYNCH_STATE:
            state = frame.payload[0] if frame.payload else 0xFF
            _maybe_ack(transport, frame)
            if state == 0x01:
                log.info("SYNC_END received — dump complete")
                break
            else:
                log.debug("intermediate SYNCH_STATE state=0x%02x", state)
                continue

        parser = PARSERS.get(frame.cmd)
        if parser is None:
            log.debug("unhandled cmd=0x%02x payload=%s",
                      frame.cmd, frame.payload.hex())
            _maybe_ack(transport, frame)
            continue

        _maybe_ack(transport, frame)
        key = CMD_TO_KEY[frame.cmd]
        parsed = parser(frame.payload)
        if key == "battery":
            result.battery = parsed
        else:
            getattr(result, key).extend(parsed)
        log.info("parsed cmd=0x%02x (%s): %s entries",
                 frame.cmd, key,
                 1 if key == "battery" else len(parsed))

    return result
