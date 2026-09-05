"""RCSP upload de archivos al badge (windowed flow control sobre AE00).

Port del CORE upload protocol de community/ebadge-python-cli/ebadge_cli/
rcsp_transfer.py. NO incluye el bootstrap pre-upload (phases 1-5) que es
ritual específico del app ZRun — esos comandos no parecen requeridos por
todos los firmware revs.

Protocol core (phases 6-10):

  Phase 6 — Session open:
    TX cmd 0x21 body=[seq, 0x00]                → ack cmd 0x21
  Phase 7 — Transfer params:
    TX cmd 0x27 body=[seq, 0,0,0,0, 0x02, 0x01] → ack cmd 0x27
  Phase 8 — File metadata:
    TX cmd 0x1b body=[seq, size_BE32, crc_BE16, rand1, rand2, name_ascii, 0]
      → ack cmd 0x1b con [_, _, chunk_size_BE16, ...] (device propone chunk)
  Phase 9 — Windowed data:
    Loop:
      RX cmd 0x1d flag=0x80 (WIN_ACK)
          body=[ack_seq, status, win_size_BE16, next_offset_BE32]
      Para cada chunk de la window:
        TX cmd 0x01 flag=0x80 body=[seq, 0x1d, slot, crc_BE16, payload]
  Phase 10 — Completion:
    RX cmd 0x20 flag=0xc0 (FILE_COMPLETE)
      → TX cmd 0x20 body=[0, seq, utf16le_path, 0, 0]
    RX cmd 0x1c flag=0xc0 (SESSION_CLOSE)
      → TX cmd 0x1c body=[0, seq]

Uso típico:
  with QixTransport(mac) as t:
    AuthSession(t).do_handshake()
    RcspUploader(t).upload(data, "my.jpg", mode="image")

Pre-requisito: AuthSession.do_handshake() exitoso. RcspUploader chequea
`transport._auth_done` antes de mandar nada.
"""
from __future__ import annotations

import logging
import random
import struct
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Optional

from qix_ble.errors import BadgeRejected, TimeoutError as QixTimeoutError
from qix_ble.rcsp_frame import RcspFrame

log = logging.getLogger("qix_ble.upload")

UploadMode = Literal["image", "avi", "qr", "bootanim"]


# ── Constants (port byte-exact del community) ────────────────────────────────

CMD_DATA_PUSH: int = 0x01
CMD_FILE_METADATA: int = 0x1B
CMD_SESSION_CLOSE: int = 0x1C
CMD_WIN_ACK: int = 0x1D
CMD_FILE_COMPLETE: int = 0x20
CMD_SESSION_OPEN: int = 0x21
CMD_XFER_PARAMS: int = 0x27

FLAG_PHONE_REQ: int = 0xC0
FLAG_DEVICE_RESP: int = 0x00
FLAG_PUSH: int = 0x80  # used for cmd 0x01 (phone push data) and 0x1d (device win-ack)

DEFAULT_CHUNK_SIZE: int = 490  # fallback si device no propone

# Small file ops (cmd 0x28)
CMD_SMALL_FILE_OP: int = 0x28
SMALL_FILE_OP_QUERY: int = 0x00
SMALL_FILE_OP_READ: int = 0x01
SMALL_FILE_OP_DELETE: int = 0x04


def crc16_ccitt(data: bytes, seed: int = 0x0000) -> int:
    """CRC16-CCITT poly 0x1021 con seed inicial parameter.

    Community default seed = 0. NOTA: jl_crc16 del JieLi SDK usa seed 0xFFFF
    (XMODEM); este NO es el mismo. Ojo si los outputs no concuerdan con el
    device — probar con 0xFFFF.
    """
    crc = seed & 0xFFFF
    for byte in data:
        crc = crc ^ (byte << 8) & 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


@dataclass
class UploadResult:
    success: bool
    bytes_sent: int
    chunk_size: int
    duration_s: float
    error: Optional[str] = None


def _build_file_path_response(device_seq: int, upload_mode: UploadMode) -> bytes:
    """UTF-16LE path response para cmd 0x20 FILE_COMPLETE.

    Path string uses U+555C prefix (port exacto de web-bluetooth-e87).
    """
    now = datetime.now()
    date_str = now.strftime("%Y%m%d%H%M%S")
    if upload_mode in ("image", "qr"):
        ext = ".jpg"
    elif upload_mode == "bootanim":
        ext = ".bin"
    else:
        ext = ".avi"
    device_path = f"啜{date_str}{ext}"
    path_utf16 = device_path.encode("utf-16-le") + b"\x00\x00"
    return bytes([0x00, device_seq]) + path_utf16


def _build_metadata_body(seq: int, file_size: int, file_crc: int,
                         name: str) -> bytes:
    """Encode cmd 0x1b body byte-exact con community."""
    name_bytes = name.encode("ascii")
    body = bytearray([seq & 0xFF])
    body += file_size.to_bytes(4, "big")
    body += (file_crc & 0xFFFF).to_bytes(2, "big")
    body.append(random.randint(0, 255))
    body.append(random.randint(0, 255))
    body += name_bytes
    body.append(0x00)
    return bytes(body)


class RcspUploader:
    """High-level upload state machine. Stateless excepto seq counter."""

    def __init__(self, transport):
        self.transport = transport
        self._seq: int = 0

    def _check_auth(self) -> None:
        if not getattr(self.transport, "_auth_done", False):
            raise BadgeRejected(
                "RcspUploader requires prior auth — call AuthSession.do_handshake() first"
            )

    def _next_seq(self) -> int:
        s = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return s

    def _send_and_wait(self, cmd: int, payload: bytes,
                       expect_cmd: int, timeout: float,
                       flag: int = FLAG_PHONE_REQ) -> RcspFrame:
        """Send + receive ack with matching cmd."""
        self.transport.send_rcsp_frame(RcspFrame(flag=flag, cmd=cmd, payload=payload))
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.01, deadline - time.monotonic())
            resp = self.transport.recv_rcsp_frame(timeout=remaining)
            if resp.cmd == expect_cmd:
                return resp
            log.debug("upload: descarto frame flag=0x%02x cmd=0x%02x (esperando 0x%02x)",
                      resp.flag, resp.cmd, expect_cmd)

    # ── Core phases ──────────────────────────────────────────────────────────

    def open_session(self, timeout: float = 8.0) -> RcspFrame:
        seq = self._next_seq()
        log.info("session_open: seq=0x%02x", seq)
        return self._send_and_wait(
            CMD_SESSION_OPEN, bytes([seq, 0x00]),
            expect_cmd=CMD_SESSION_OPEN, timeout=timeout,
        )

    def xfer_params(self, timeout: float = 8.0) -> RcspFrame:
        seq = self._next_seq()
        log.info("xfer_params: seq=0x%02x", seq)
        # body[5]=0x02, body[6]=0x01 → "image upload" magic del community
        return self._send_and_wait(
            CMD_XFER_PARAMS, bytes([seq, 0, 0, 0, 0, 0x02, 0x01]),
            expect_cmd=CMD_XFER_PARAMS, timeout=timeout,
        )

    def send_metadata(self, file_size: int, file_crc: int, name: str,
                      timeout: float = 8.0) -> int:
        """Send cmd 0x1b + parse chunk_size del response. Returns chunk_size.

        Raises BadgeRejected si response payload[0] != 0x00 (status).
        """
        seq = self._next_seq()
        body = _build_metadata_body(seq, file_size, file_crc, name)
        log.info("metadata: seq=0x%02x size=%d crc=0x%04x name=%s",
                 seq, file_size, file_crc, name)
        resp = self._send_and_wait(
            CMD_FILE_METADATA, body,
            expect_cmd=CMD_FILE_METADATA, timeout=timeout,
        )
        # Check status byte (body[0])
        if resp.payload and resp.payload[0] != 0x00:
            status = resp.payload[0]
            raise BadgeRejected(
                f"metadata rejected: status=0x{status:02x} "
                f"(see RCSP_STATUS table: 0x01=UNKNOWN_ERROR, 0x02=NOT_SUPPORTED, "
                f"0x03=DATA_ERROR, 0x05=REJECTED)",
                cmd=CMD_FILE_METADATA, state=status,
            )
        chunk_size = DEFAULT_CHUNK_SIZE
        if len(resp.payload) >= 4:
            proposed = int.from_bytes(resp.payload[2:4], "big")
            if 0 < proposed <= 4096:
                chunk_size = proposed
            else:
                log.warning("metadata: device propuso chunk=%d (fuera de rango), "
                            "usando default %d", proposed, DEFAULT_CHUNK_SIZE)
        log.info("metadata: chunk_size negociado = %d", chunk_size)
        return chunk_size

    def upload(self, data: bytes, name: str, mode: UploadMode = "image",
               on_progress: Callable[[float], None] | None = None,
               inter_chunk_delay_ms: int = 0,
               window_timeout: float = 15.0,
               bootstrap: bool = True) -> UploadResult:
        """End-to-end upload. Returns UploadResult.

        Args:
            bootstrap: si True (default), ejecuta Phase 1-5 community pre-upload.
                El badge OEM AC707N parece requerirlo para aceptar SESSION_OPEN.
        """
        self._check_auth()
        self.transport.drain_ae02_raw()
        t0 = time.monotonic()

        # Warning: empíricamente AVI ≥280KB rechaza con DATA_ERROR en SESSION_CLOSE.
        # ~200KB es zona segura. Ver feedback_qix_avi_size_limit memory.
        if len(data) > 200_000:
            log.warning(
                "upload size %d bytes > 200KB threshold — badge puede rechazar "
                "con DATA_ERROR en SESSION_CLOSE (límite empírico observado ~280KB). "
                "Considerar reducir duration/fps/resolución.",
                len(data),
            )

        if bootstrap:
            from qix_ble.bootstrap import run_bootstrap
            log.info("running pre-upload bootstrap (Phase 1-5)")
            run_bootstrap(self.transport)
            self.transport.drain()
            self.transport.drain_ae02_raw()

        # Phases 6, 7, 8 (sequential)
        self.open_session()
        self.xfer_params()
        file_crc = crc16_ccitt(data)
        # CRITICAL: durante upload, name DEBE ser un random hex .tmp filename.
        # El badge rechaza .avi/.jpg con UNKNOWN_ERROR en metadata response.
        # El nombre real (con .avi/.jpg) va en el FILE_COMPLETE path response.
        temp_name = f"{random.randint(0, 0xFFFFFF):06x}.tmp"
        log.debug("metadata: using temp name %s (real name %s sent at FILE_COMPLETE)",
                  temp_name, name)
        chunk_size = self.send_metadata(len(data), file_crc, temp_name)

        # Phase 9: Windowed data
        file_offset = 0
        seq = self._next_seq()
        sent_chunks = 0
        done = False

        # Wait for initial WIN_ACK
        try:
            current_ack = self._wait_for_cmd(CMD_WIN_ACK, timeout=10.0,
                                              expect_flag=FLAG_PUSH)
        except QixTimeoutError as e:
            return UploadResult(success=False, bytes_sent=0, chunk_size=chunk_size,
                                duration_s=time.monotonic() - t0,
                                error=f"no initial WIN_ACK: {e}")

        while not done:
            if current_ack and len(current_ack.payload) >= 8:
                body = current_ack.payload
                ack_seq = body[0]
                ack_status = body[1]
                win_size = int.from_bytes(body[2:4], "big")
                next_offset = int.from_bytes(body[4:8], "big")
                log.info("WIN_ACK seq=%d status=%d win=%d next_offset=%d",
                         ack_seq, ack_status, win_size, next_offset)

                if ack_status != 0:
                    log.warning("WIN_ACK status non-zero: 0x%02x", ack_status)

                file_offset = next_offset
                if win_size == 0 and file_offset >= len(data):
                    log.info("final WIN_ACK reached (transfer complete)")
                    done = True
                    break

                # Send chunks within this window
                slot = 0
                window_bytes_sent = 0
                while window_bytes_sent < win_size and file_offset < len(data):
                    chunk_len = min(chunk_size, len(data) - file_offset,
                                    win_size - window_bytes_sent)
                    payload = data[file_offset:file_offset + chunk_len]
                    chunk_crc = crc16_ccitt(payload)
                    chunk_body = bytes([
                        seq & 0xFF, 0x1D, slot & 0xFF,
                        (chunk_crc >> 8) & 0xFF, chunk_crc & 0xFF,
                    ]) + payload
                    self.transport.send_rcsp_frame(
                        RcspFrame(flag=FLAG_PUSH, cmd=CMD_DATA_PUSH, payload=chunk_body)
                    )
                    sent_chunks += 1
                    file_offset += chunk_len
                    seq = (seq + 1) & 0xFF
                    slot = (slot + 1) & 0x07
                    window_bytes_sent += chunk_len

                    if on_progress and len(data) > 0:
                        on_progress(file_offset / len(data))
                    if inter_chunk_delay_ms > 0:
                        time.sleep(inter_chunk_delay_ms / 1000.0)

            # Wait for next event: WIN_ACK or device-initiated 0x20/0x1c
            try:
                next_event = self._wait_for_any(
                    [(FLAG_PUSH, CMD_WIN_ACK),
                     (FLAG_PHONE_REQ, CMD_FILE_COMPLETE),
                     (FLAG_PHONE_REQ, CMD_SESSION_CLOSE)],
                    timeout=window_timeout,
                )
            except QixTimeoutError as e:
                return UploadResult(success=False, bytes_sent=file_offset,
                                    chunk_size=chunk_size,
                                    duration_s=time.monotonic() - t0,
                                    error=f"timeout in window phase: {e}")

            if next_event.cmd == CMD_WIN_ACK:
                current_ack = next_event
                continue
            if next_event.cmd == CMD_FILE_COMPLETE:
                self._respond_file_complete(next_event, mode)
                # Wait for 0x1c finalize
                try:
                    final = self._wait_for_cmd(CMD_SESSION_CLOSE, timeout=10.0)
                    status_byte = final.payload[1] if len(final.payload) >= 2 else 0xFF
                    self._respond_session_close(final)
                    if status_byte == 0:
                        return UploadResult(success=True, bytes_sent=len(data),
                                            chunk_size=chunk_size,
                                            duration_s=time.monotonic() - t0)
                    return UploadResult(success=False, bytes_sent=len(data),
                                        chunk_size=chunk_size,
                                        duration_s=time.monotonic() - t0,
                                        error=f"final status=0x{status_byte:02x}")
                except QixTimeoutError:
                    return UploadResult(success=True, bytes_sent=len(data),
                                        chunk_size=chunk_size,
                                        duration_s=time.monotonic() - t0,
                                        error="0x1c not received but 0x20 handled")
            if next_event.cmd == CMD_SESSION_CLOSE:
                status_byte = next_event.payload[1] if len(next_event.payload) >= 2 else 0xFF
                self._respond_session_close(next_event)
                return UploadResult(success=(status_byte == 0),
                                    bytes_sent=file_offset,
                                    chunk_size=chunk_size,
                                    duration_s=time.monotonic() - t0,
                                    error=None if status_byte == 0
                                    else f"session_close status=0x{status_byte:02x}")

        return UploadResult(success=True, bytes_sent=len(data),
                            chunk_size=chunk_size,
                            duration_s=time.monotonic() - t0)

    def _wait_for_cmd(self, cmd: int, timeout: float,
                      expect_flag: int | None = None) -> RcspFrame:
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.01, deadline - time.monotonic())
            f = self.transport.recv_rcsp_frame(timeout=remaining)
            if f.cmd == cmd and (expect_flag is None or f.flag == expect_flag):
                return f

    def _wait_for_any(self, options: list[tuple[int, int]],
                      timeout: float) -> RcspFrame:
        """Wait for the first frame matching any (flag, cmd) tuple."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.01, deadline - time.monotonic())
            f = self.transport.recv_rcsp_frame(timeout=remaining)
            for flag, cmd in options:
                if f.cmd == cmd and (flag is None or f.flag == flag):
                    return f

    def _respond_file_complete(self, frame: RcspFrame, mode: UploadMode) -> None:
        device_seq = frame.payload[0] if frame.payload else 0
        resp_body = _build_file_path_response(device_seq, mode)
        self.transport.send_rcsp_frame(
            RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_FILE_COMPLETE, payload=resp_body)
        )
        log.info("FILE_COMPLETE acked seq=%d mode=%s", device_seq, mode)

    def _respond_session_close(self, frame: RcspFrame) -> None:
        device_seq = frame.payload[0] if frame.payload else 0
        self.transport.send_rcsp_frame(
            RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_SESSION_CLOSE,
                      payload=bytes([0x00, device_seq]))
        )
        log.info("SESSION_CLOSE acked seq=%d", device_seq)


# ── Small file ops (cmd 0x28) — quick helpers ────────────────────────────────

@dataclass(frozen=True)
class SmallFileEntry:
    type: int
    type_name: str
    id: int
    size: int


SMALL_FILE_TYPES: dict[int, str] = {
    1: "contacts", 2: "sports_record", 3: "heart_rate",
    4: "blood_oxygen", 5: "sleep", 6: "message_sync",
    7: "weather", 8: "call_log", 9: "step",
}


def list_small_files(rcsp_session, timeout: float = 7.0) -> list[SmallFileEntry]:
    """Query cmd 0x28 op=0x00 para cada small file type. Returns combined list."""
    rcsp_session._check_auth()
    all_entries: list[SmallFileEntry] = []
    for type_id, type_name in SMALL_FILE_TYPES.items():
        seq = rcsp_session._next_seq()
        body = bytes([seq, SMALL_FILE_OP_QUERY, type_id])
        frame = RcspFrame(flag=FLAG_PHONE_REQ, cmd=CMD_SMALL_FILE_OP, payload=body)
        rcsp_session.transport.send_rcsp_frame(frame)
        try:
            resp = rcsp_session.transport.recv_rcsp_frame(timeout=timeout)
        except QixTimeoutError:
            log.debug("list_small_files type=%s: no response", type_name)
            continue
        if resp.cmd != CMD_SMALL_FILE_OP:
            continue
        # Status check: response[0] = status (when present); skip if non-zero.
        if not resp.payload:
            continue
        entries = _parse_small_file_query(type_id, type_name, bytes(resp.payload))
        all_entries.extend(entries)
    return sorted(all_entries, key=lambda e: (e.type, e.id))


def _parse_small_file_query(type_id: int, type_name: str,
                            body: bytes) -> list[SmallFileEntry]:
    """Parse cmd 0x28 op=0 response body. Layout: [start_offset?][id_hi id_lo size_hi size_lo]*

    Community probes 3 possible start offsets (1, 2, 0) to handle different
    firmware revs that prepend [status] or [status][op_echo].
    """
    for start in (1, 2, 0):
        rest = len(body) - start
        if rest <= 0 or rest % 4 != 0:
            continue
        out: list[SmallFileEntry] = []
        for i in range(start, len(body) - 3, 4):
            entry_id = (body[i] << 8) | body[i + 1]
            entry_size = (body[i + 2] << 8) | body[i + 3]
            if entry_id == 0 and entry_size == 0:
                continue
            out.append(SmallFileEntry(
                type=type_id, type_name=type_name,
                id=entry_id, size=entry_size,
            ))
        if out or rest == 0:
            return out
    return []


def delete_small_file(rcsp_session, entry: SmallFileEntry,
                      timeout: float = 7.0) -> None:
    """cmd 0x28 op=0x04 — borra entry. Raises BadgeRejected si ret!=0."""
    rcsp_session._check_auth()
    seq = rcsp_session._next_seq()
    body = bytes([seq, SMALL_FILE_OP_DELETE, entry.type,
                  (entry.id >> 8) & 0xFF, entry.id & 0xFF])
    frame = RcspFrame(flag=FLAG_PHONE_REQ, cmd=CMD_SMALL_FILE_OP, payload=body)
    rcsp_session.transport.send_rcsp_frame(frame)
    resp = rcsp_session.transport.recv_rcsp_frame(timeout=timeout)
    if resp.cmd != CMD_SMALL_FILE_OP:
        raise BadgeRejected(f"delete: unexpected cmd 0x{resp.cmd:02x}",
                            cmd=CMD_SMALL_FILE_OP)
    ret = resp.payload[0] if resp.payload else 0xFF
    if ret != 0:
        raise BadgeRejected(
            f"delete: type={entry.type_name} id={entry.id} ret=0x{ret:02x}",
            cmd=CMD_SMALL_FILE_OP, state=ret,
        )
    log.info("deleted small file type=%s id=%d", entry.type_name, entry.id)
