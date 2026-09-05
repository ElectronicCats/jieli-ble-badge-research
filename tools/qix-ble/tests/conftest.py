"""Pytest fixtures compartidas."""
from __future__ import annotations
from collections import deque
from qix_ble.frame import QixFrame
from qix_ble.errors import TimeoutError as QixTimeoutError


class MockTransport:
    """Mock para tests del state machine sin BLE real.

    Soporta dos protocols:
    - Qix (FD00): `queue_response`, `send_command`, `wait_for_cmd`, etc.
    - RCSP AE00 raw (handshake auth): `queue_ae02_raw`, `send_to_ae01`, `recv_ae02_raw`.

    Si la cola correspondiente se vacía, raises QixTimeoutError.
    """

    def __init__(self):
        self.scripted: deque[QixFrame] = deque()
        self.sent: list[tuple[int, bytes, int]] = []  # (cmd, payload, flags) Qix tx
        # RCSP raw side
        self.ae02_scripted: deque[bytes] = deque()
        self.ae01_sent: list[bytes] = []
        # Set por AuthSession.do_handshake() — RcspSession lo chequea como pre-cond.
        self._auth_done: bool = False

    def queue_response(self, frame: QixFrame) -> None:
        self.scripted.append(frame)

    def queue_responses(self, *frames: QixFrame) -> None:
        for f in frames:
            self.queue_response(f)

    def drain(self) -> list[QixFrame]:
        # Mock no tiene RX queue real — scripted son inputs futuros, no frames recibidos.
        return []

    def send_raw(self, frame: QixFrame) -> None:
        self.sent.append((frame.cmd, bytes(frame.payload), frame.flags))

    def write_fd02_raw(self, data: bytes) -> None:
        """Capture raw FD02 writes (bootstrap dance)."""
        if not hasattr(self, "fd02_raw_sent"):
            self.fd02_raw_sent = []
        self.fd02_raw_sent.append(bytes(data))

    def send_command(self, cmd, payload=b"", flags=0, expect_cmd=None, timeout=5.0, response=False):
        self.sent.append((cmd, bytes(payload), flags))
        if not self.scripted:
            raise QixTimeoutError(f"MockTransport: no scripted response for cmd=0x{cmd:02X}")
        return self.scripted.popleft()

    def recv_frame(self, timeout=5.0):
        if not self.scripted:
            return None
        return self.scripted.popleft()

    def wait_for_cmd(self, expect_cmd, timeout=30.0):
        if not self.scripted:
            raise QixTimeoutError(
                f"MockTransport: no scripted response for wait_for_cmd(0x{expect_cmd:02X})"
            )
        frame = self.scripted.popleft()
        if frame.cmd != expect_cmd:
            raise QixTimeoutError(
                f"MockTransport: expected 0x{expect_cmd:02X}, got 0x{frame.cmd:02X}"
            )
        return frame

    # ─── RCSP AE00 raw side (usado por AuthSession tests) ────────────────────

    def queue_ae02_raw(self, data: bytes) -> None:
        self.ae02_scripted.append(bytes(data))

    def queue_ae02_responses(self, *chunks: bytes) -> None:
        for c in chunks:
            self.queue_ae02_raw(c)

    def send_to_ae01(self, data: bytes) -> None:
        self.ae01_sent.append(bytes(data))

    def recv_ae02_raw(self, timeout: float = 5.0) -> bytes:
        if not self.ae02_scripted:
            raise QixTimeoutError("MockTransport: no ae02 scripted response")
        return self.ae02_scripted.popleft()

    def drain_ae02_raw(self) -> list[bytes]:
        out = list(self.ae02_scripted)
        self.ae02_scripted.clear()
        return out

    # ─── RcspFrame helpers (azucar sobre queue_ae02_raw, usado por RcspSession tests) ─

    def queue_rcsp_response(self, frame) -> None:
        """Encode un RcspFrame y meterlo al queue AE02. Equivalente a
        queue_ae02_raw(frame.encode())."""
        self.queue_ae02_raw(frame.encode())

    def queue_rcsp_responses(self, *frames) -> None:
        for f in frames:
            self.queue_rcsp_response(f)

    # ─── RCSP frame-level send/recv (espeja QixTransport.send_rcsp_frame/recv) ───

    def send_rcsp_frame(self, frame) -> None:
        """Para tests que usan el API frame-level. Encodea + push a ae01_sent."""
        self.send_to_ae01(frame.encode())

    def recv_rcsp_frame(self, timeout: float = 5.0):
        """Pop next ae02 scripted como RcspFrame."""
        from qix_ble.rcsp_frame import RcspFrame
        raw = self.recv_ae02_raw(timeout=timeout)
        return RcspFrame.decode(raw)
