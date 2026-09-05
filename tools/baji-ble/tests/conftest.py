"""Fixtures compartidas para baji-ble tests."""
import queue
import pytest


class FakeTransport:
    """Mock de BajiTransport: send_raw_to_nus se acumula en .tx_log; las responses
    se preconfiguran con queue_response(bytes)."""
    def __init__(self):
        self.tx_log: list[bytes] = []
        self._rx: queue.Queue[bytes] = queue.Queue()

    def send_raw_to_nus(self, data: bytes, *, response: bool = False) -> None:
        self.tx_log.append(data)

    def recv_frame(self, timeout: float = 5.0) -> bytes | None:
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None

    def queue_response(self, frame: bytes) -> None:
        self._rx.put(frame)

    def drain(self) -> list[bytes]:
        out = []
        while True:
            try:
                out.append(self._rx.get_nowait())
            except queue.Empty:
                break
        return out


@pytest.fixture
def fake_transport():
    return FakeTransport()
