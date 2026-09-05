"""RCSP filesystem ops sobre service AE00 (post-auth handshake).

Port de:
- hybridherbst/web-bluetooth-e87/web/src/lib/e87-protocol.ts (simplified framing)
- guan4tou2/ebadge-python-cli/ebadge_cli/{rcsp_frame.py, file_browse.py} (full
  RCSP inner framing + _parse_file_entries entry decoder)

El frame outer (`fe dc ba [flag][cmd][len BE16][body] ef`) lo maneja RcspFrame.
El frame INNER del RCSP tiene estructura distinta según el flag:

  Commands (flag bit 0x80 set):
    body = [op_code_sn 1B][xm_op_code 1B if cmd==0x01][payload]

  Responses (flag bit 0x80 clear, i.e. 0x00):
    body = [status 1B][op_code_sn 1B][xm_op_code 1B if cmd==0x01][payload]

Pre-condición: AuthSession.do_handshake() previamente exitoso, marcado en
transport._auth_done. Cualquier op chequea el flag al inicio.

Detalles wire-format en internal notes (not published) sección "RCSP filesystem".
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from qix_ble.errors import BadgeRejected, TimeoutError as QixTimeoutError
from qix_ble.rcsp_frame import RcspFrame

log = logging.getLogger("qix_ble.rcsp")

# ── Storage device handlers (port de DEV_HANDLER_NAMES del TS) ───────────────
DEV_HANDLER_USB: int = 0
DEV_HANDLER_SD0: int = 1
DEV_HANDLER_SD1: int = 2
DEV_HANDLER_FLASH: int = 3
DEV_HANDLER_NAMES: dict[int, str] = {
    DEV_HANDLER_USB: "USB",
    DEV_HANDLER_SD0: "SD0",
    DEV_HANDLER_SD1: "SD1",
    DEV_HANDLER_FLASH: "Flash",
}

# ── RCSP commands (FE-framed) ────────────────────────────────────────────────
CMD_GET_TARGET_INFO: int = 0x03
CMD_START_FILE_BROWSE: int = 0x0c
CMD_STOP_FILE_BROWSE: int = 0x0d
CMD_FILE_METADATA: int = 0x1b
CMD_SESSION_CLOSE: int = 0x1c
CMD_WIN_ACK: int = 0x1d
CMD_FILE_COMPLETE: int = 0x20
CMD_SESSION_OPEN: int = 0x21
CMD_XFER_PARAMS: int = 0x27
CMD_SMALL_FILE_OP: int = 0x28
CMD_DATA_PUSH: int = 0x01

# Flag bytes en RCSP frame
FLAG_PHONE_REQ: int = 0xc0     # command from phone, expects response (is_command + has_response)
FLAG_DEVICE_RESP: int = 0x00   # response from device (also: phone ack to device push)
FLAG_DEVICE_PUSH: int = 0x80   # command from device, no response expected (push)
FLAG_DEVICE_CMD: int = 0xc0    # command from device, expects response (e.g., StopBrowse)


# ── Inner RCSP framing helpers (port de ebadge_cli/rcsp_frame.py parse()) ────

from dataclasses import field

@dataclass
class _RcspInner:
    """RCSP inner framing decoded del body de un RcspFrame.

    Para commands (flag bit 0x80 set):
      body = [op_code_sn][xm_op_code if cmd==0x01][payload]
    Para responses (flag bit 0x80 clear):
      body = [status][op_code_sn][xm_op_code if cmd==0x01][payload]
    """
    is_command: bool
    has_response: bool
    op_code: int
    op_code_sn: int
    xm_op_code: int | None
    status: int | None
    payload: bytes


def _parse_inner(frame: RcspFrame) -> _RcspInner:
    """Decode inner RCSP framing del body de un RcspFrame."""
    body = frame.payload
    is_command = (frame.flag & 0x80) != 0
    has_response = (frame.flag & 0x40) != 0
    status: int | None = None
    op_code_sn = 0
    xm_op_code: int | None = None
    payload_start = 0
    if not body:
        return _RcspInner(is_command, has_response, frame.cmd, 0, None, None, b"")
    if is_command:
        op_code_sn = body[0]
        payload_start = 1
        if frame.cmd == 0x01 and len(body) >= 2:
            xm_op_code = body[1]
            payload_start = 2
    else:
        status = body[0]
        op_code_sn = body[1] if len(body) > 1 else 0
        payload_start = 2
        if frame.cmd == 0x01 and len(body) >= 3:
            xm_op_code = body[2]
            payload_start = 3
    return _RcspInner(
        is_command=is_command,
        has_response=has_response,
        op_code=frame.cmd,
        op_code_sn=op_code_sn,
        xm_op_code=xm_op_code,
        status=status,
        payload=bytes(body[payload_start:]),
    )


def _build_response_body(cmd: int, op_code_sn: int, status: int,
                         payload: bytes = b"", xm_op_code: int | None = None) -> bytes:
    """Build inner body para un response frame.
    Layout: [status][op_code_sn][xm_op_code if cmd==0x01 and provided][payload].
    """
    body = bytearray([status & 0xff, op_code_sn & 0xff])
    if cmd == 0x01 and xm_op_code is not None:
        body.append(xm_op_code & 0xff)
    body += payload
    return bytes(body)


# ── File entry parser (port de ebadge_cli/file_browse.py _parse_file_entries) ─

def _parse_file_entries(data: bytes, query_type: int) -> list[FileEntry]:
    """Decode accumulated file entry bytes (cross-multiple push frames) en FileEntries.

    Format per entry (port byte-exact del upstream):
      [header 1B]
        bit 0: is_file (1) | is_folder (0)
        bit 1: 0=unicode/UTF-16LE | 1=ascii
        bits 2-6: dev_index (storage device index)
      [cluster 4B BE]
      [file_num 2B BE]
      [name_len 1B]
      [name N bytes]
    """
    entries: list[FileEntry] = []
    offset = 0
    while offset + 7 < len(data):
        header = data[offset]
        is_file = (header & 0x01) != 0
        is_unicode = (header & 0x02) == 0  # bit1=0 means unicode (per upstream)
        dev_index = (header >> 2) & 0x1F

        cluster = (
            (data[offset + 1] << 24)
            | (data[offset + 2] << 16)
            | (data[offset + 3] << 8)
            | data[offset + 4]
        )
        file_num = (data[offset + 5] << 8) | data[offset + 6]
        name_len = data[offset + 7]
        offset += 8

        if offset + name_len > len(data):
            break

        name_bytes = bytes(data[offset:offset + name_len])
        offset += name_len

        if is_unicode and name_len >= 2 and name_len % 2 == 0:
            try:
                name = name_bytes.decode("utf-16-le").rstrip("\x00")
            except UnicodeDecodeError:
                name = name_bytes.hex()
        else:
            try:
                name = name_bytes.decode("ascii", errors="replace").rstrip("\x00")
            except Exception:
                name = name_bytes.hex()

        # Construir FileEntry — type del request, name/cluster del entry,
        # file_num lo guardamos como `id` (lo que small_file_read necesita),
        # size queda 0 (browse no devuelve size; FileMetadata cmd 0x1b lo trae).
        entries.append(FileEntry(
            type=query_type,
            type_name="file" if is_file else "folder",
            id=file_num,
            size=0,
            cluster=cluster,
            name=name,
        ))

    return entries


@dataclass(frozen=True)
class FileEntry:
    """Browse result entry. Field semantics del TS parseFileBrowseResponse."""
    type: int                      # 0=folder, 1=file (queryType del request)
    type_name: str                 # "folder" o "file" — human-readable
    id: int                        # 16-bit id (para small files) o offset within (0 para browse-only)
    size: int                      # bytes; 0 para folders
    cluster: int | None            # 32-bit cluster ID asignado por el firmware
    name: str | None               # ASCII / UTF decoded


class _Ae00RcspTransport(Protocol):
    """Interface del transport requerida por RcspSession."""
    _auth_done: bool
    def send_rcsp_frame(self, frame: RcspFrame) -> None: ...
    def recv_rcsp_frame(self, timeout: float = 5.0) -> RcspFrame: ...
    def recv_ae02_raw(self, timeout: float = 5.0) -> bytes: ...


class RcspSession:
    """High-level FS ops sobre transport con AuthSession previa.

    Stateless excepto por seq counter incrementado per request. Cada
    operación verifica `transport._auth_done` antes de mandar nada.
    """

    def __init__(self, transport: _Ae00RcspTransport):
        self.transport = transport
        self._seq: int = 0

    def _check_auth(self) -> None:
        """Pre-condición: transport debe haber pasado AuthSession.do_handshake()."""
        if not getattr(self.transport, "_auth_done", False):
            raise BadgeRejected(
                "RcspSession requires prior auth — call AuthSession.do_handshake() first"
            )

    def _next_seq(self) -> int:
        s = self._seq
        self._seq = (self._seq + 1) & 0xff
        return s

    # ── Browse ─────────────────────────────────────────────────────────────
    def browse(
        self,
        type: int = 0,
        read_num: int = 10,
        start_index: int = 0,
        dev_handler: int = DEV_HANDLER_FLASH,
        clusters: list[int] | None = None,
        timeout: float = 5.0,
    ) -> list[FileEntry]:
        """Listar archivos/folders del storage device especificado.

        Wire-format del body del request (inner framing):
          [op_code_sn 1B][FileBrowseParam].
        FileBrowseParam = type(1) readNum(1) startIndex(2BE) devHandler(4BE)
                          pathLen(2LE) path(N*4BE).

        Flow contra device (port de ebadge_cli/file_browse.py):
          1. Phone TX command 0xc0 0x0c con FileBrowseParam → device ACK con response 0x00 0x0c (status+sn echo).
          2. Device PUSH commands 0x80 0x01 xm_op=0x0c con entry data chunks → Phone ACK con response 0x00 0x01.
          3. Device CMD 0xc0 0x0d StopFileBrowse → Phone ACK con response 0x00 0x0d.
        """
        self._check_auth()
        clusters = clusters or []

        # Encode FileBrowseParam
        path_bytes = bytearray()
        for c in clusters:
            path_bytes += int(c).to_bytes(4, "big")
        path_len = len(path_bytes)

        param = bytearray()
        param.append(type & 0xff)
        param.append(read_num & 0xff)
        param += int(start_index).to_bytes(2, "big")
        param += int(dev_handler).to_bytes(4, "big")
        param += int(path_len).to_bytes(2, "little")
        param += path_bytes

        # Wrap en inner command framing: [op_code_sn][param]
        sn_req = self._next_seq()
        body = bytes([sn_req]) + bytes(param)
        frame = RcspFrame(flag=FLAG_PHONE_REQ, cmd=CMD_START_FILE_BROWSE, payload=body)
        log.debug("browse: TX sn=0x%02x type=%d readNum=%d startIdx=%d dev=%s clusters=%s",
                  sn_req, type, read_num, start_index,
                  DEV_HANDLER_NAMES.get(dev_handler, f"0x{dev_handler:x}"), clusters)
        self.transport.send_rcsp_frame(frame)

        # Collect: device ack of our 0x0c, then push entries (cmd 0x01 xm_op=0x0c),
        # finally device StopFileBrowse (cmd 0x0d). Accumulate entry data, parse al final.
        entry_data_buf = bytearray()
        got_initial_ack = False

        while True:
            resp = self.transport.recv_rcsp_frame(timeout=timeout)
            inner = _parse_inner(resp)

            if not inner.is_command and inner.op_code == CMD_START_FILE_BROWSE:
                # Device response to our 0x0c request: [status][sn]
                log.debug("browse: RX ack status=0x%02x sn=0x%02x", inner.status, inner.op_code_sn)
                if inner.status != 0x00:
                    raise BadgeRejected(
                        f"browse rejected: status=0x{inner.status:02x} sn=0x{inner.op_code_sn:02x}",
                        cmd=resp.cmd, state=inner.status,
                    )
                got_initial_ack = True
                continue

            if inner.is_command and inner.op_code == CMD_DATA_PUSH and inner.xm_op_code == CMD_START_FILE_BROWSE:
                # Push entry data chunk: [sn][xm_op=0x0c][entry_bytes...]
                log.debug("browse: RX push sn=0x%02x entry_data=%dB",
                          inner.op_code_sn, len(inner.payload))
                entry_data_buf += inner.payload
                # ACK each push with response 0x00 0x01 xm_op=0x0c, same sn, no payload
                ack_body = _build_response_body(
                    cmd=CMD_DATA_PUSH,
                    op_code_sn=inner.op_code_sn,
                    status=0x00,
                    xm_op_code=CMD_START_FILE_BROWSE,
                )
                self.transport.send_rcsp_frame(
                    RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_DATA_PUSH, payload=ack_body)
                )
                continue

            if inner.is_command and inner.op_code == CMD_STOP_FILE_BROWSE:
                # Device sends StopFileBrowse (cmd 0x0d) — phone ACKs and exits.
                log.debug("browse: RX StopFileBrowse sn=0x%02x — exiting", inner.op_code_sn)
                ack_body = _build_response_body(
                    cmd=CMD_STOP_FILE_BROWSE,
                    op_code_sn=inner.op_code_sn,
                    status=0x00,
                )
                self.transport.send_rcsp_frame(
                    RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_STOP_FILE_BROWSE, payload=ack_body)
                )
                break

            log.warning("browse: unexpected frame flag=0x%02x cmd=0x%02x — skipping",
                        resp.flag, resp.cmd)

        # Parse accumulated entry data into FileEntry list
        entries = _parse_file_entries(bytes(entry_data_buf), query_type=type)
        log.info("browse: parsed %d entries from %dB of data",
                 len(entries), len(entry_data_buf))
        return entries

    def browse_all(self, type: int = 0, read_num: int = 10) -> list[FileEntry]:
        """Iterar todos los dev handlers + colectar entries.

        Skip silencioso handlers que devuelven status=0xff (e.g., USB no presente).
        """
        self._check_auth()
        all_entries: list[FileEntry] = []
        for handler in (DEV_HANDLER_USB, DEV_HANDLER_SD0, DEV_HANDLER_SD1, DEV_HANDLER_FLASH):
            try:
                entries = self.browse(
                    type=type,
                    read_num=read_num,
                    start_index=0,
                    dev_handler=handler,
                    clusters=[],
                )
                log.info("browse_all: %s → %d entries",
                         DEV_HANDLER_NAMES[handler], len(entries))
                all_entries.extend(entries)
            except BadgeRejected as e:
                log.info("browse_all: %s → skipped (%s)",
                         DEV_HANDLER_NAMES[handler], e)
                continue
        return all_entries

    # ── Small file read ────────────────────────────────────────────────────
    def read_small_file(self, entry: FileEntry, timeout: float = 9.0) -> tuple[bytes, int]:
        """Single-shot read via cmd 0x28 op=0x01.

        Body: op(1)=01, type(1), id(2BE), offset(2BE), len(2BE), flag(1)=01.
        Response variants (del TS upstream):
          A: [ret][crc_hi][crc_lo][data...]
          B: [op][ret=0][crc_hi][crc_lo][data...]

        Returns:
            tuple of (data, crc16) — CRC reportado por el badge, sin verify.

        Raises:
            ValueError: si entry.size > 0xFFFF.
            BadgeRejected: si status nonzero del badge o cmd response mismatch.
        """
        self._check_auth()
        if entry.size > 0xFFFF:
            raise ValueError(
                f"file too large for small read: {entry.size} bytes > 65535 "
                f"— use read_large_file instead"
            )
        # Per TS: maxLen = min(0xffff, max(entry.size, 256))
        req_len = min(0xFFFF, max(entry.size, 256))

        body = bytearray()
        body.append(0x01)                                # op = read
        body.append(entry.type & 0xff)                   # type
        body += int(entry.id).to_bytes(2, "big")         # id BE
        body += int(0).to_bytes(2, "big")                # offset BE = 0
        body += int(req_len).to_bytes(2, "big")          # len BE
        body.append(0x01)                                # flag = 1

        frame = RcspFrame(flag=FLAG_PHONE_REQ, cmd=CMD_SMALL_FILE_OP, payload=bytes(body))
        log.debug("read_small_file: TX entry.id=0x%04x size=%d req_len=%d",
                  entry.id, entry.size, req_len)
        self.transport.send_rcsp_frame(frame)

        resp = self.transport.recv_rcsp_frame(timeout=timeout)
        if resp.cmd != CMD_SMALL_FILE_OP:
            raise BadgeRejected(
                f"read_small_file: unexpected response cmd=0x{resp.cmd:02x}",
                cmd=resp.cmd,
            )
        p = resp.payload
        if len(p) < 3:
            raise BadgeRejected(f"read_small_file: response too short ({len(p)}B)")

        # Detect variant A vs B (per TS lines 1163-1178)
        if p[0] == 0x00:
            # Variant A: [ret=0][crc_hi][crc_lo][data]
            ret, crc_hi, crc_lo = p[0], p[1], p[2]
            data_start = 3
        elif len(p) >= 4 and p[1] == 0x00:
            # Variant B: [op][ret=0][crc_hi][crc_lo][data]
            ret, crc_hi, crc_lo = p[1], p[2], p[3]
            data_start = 4
        else:
            # Variant A with non-zero ret (error path)
            ret = p[0]
            crc_hi = crc_lo = 0
            data_start = 3

        if ret != 0x00:
            raise BadgeRejected(
                f"read_small_file: badge returned status=0x{ret:02x}",
                cmd=resp.cmd, state=ret,
            )

        crc16 = (crc_hi << 8) | crc_lo
        data = bytes(p[data_start:])
        log.debug("read_small_file: RX %d bytes crc=0x%04x", len(data), crc16)
        return data, crc16

    # ── Large file read (Phase 3 — post-probe) ─────────────────────────────
    def read_large_file(
        self,
        entry: FileEntry,
        output: Path,
        on_progress: Callable[[float], None] | None = None,
    ) -> None:
        """Chunked read via 0x21 SESS_OPEN flow. Streaming write a output.

        IMPLEMENTACIÓN: pendiente Phase 2 probe del plan (Task 11/12).
        """
        self._check_auth()
        raise NotImplementedError("pending Phase 2 probe — see Day 3 plan T11/T12")
