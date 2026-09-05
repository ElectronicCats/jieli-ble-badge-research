"""Tests de los helpers high-level send_rcsp_frame/recv_rcsp_frame.

Estos NO testean el transport real (HW required). Testean el wiring:
- send_rcsp_frame(f) → llama transport.send_to_ae01(f.encode())
- recv_rcsp_frame() → pop AE02 raw chunk + parse como RcspFrame.

QixTransport real se valida en HW (Task 11+).
"""
import pytest
from qix_ble.rcsp_frame import RcspFrame
from qix_ble.errors import InvalidFrameError, TimeoutError as QixTimeoutError


class FakeRawTransport:
    """Implementa el subset del API que send_rcsp_frame/recv_rcsp_frame usan."""
    def __init__(self):
        self.ae01_sent: list[bytes] = []
        self.ae02_to_return: list[bytes] = []

    def send_to_ae01(self, data: bytes) -> None:
        self.ae01_sent.append(bytes(data))

    def recv_ae02_raw(self, timeout: float = 5.0) -> bytes:
        if not self.ae02_to_return:
            raise QixTimeoutError("no data scripted")
        return self.ae02_to_return.pop(0)


def make_send_recv(transport):
    """Helper que retorna closures que llaman a los métodos del transport.
    Reusamos los unbound methods, los bindeamos a nuestro fake — evita
    necesitar un QixTransport completo (que requiere bleak + threading)."""
    from qix_ble.transport import QixTransport
    return (
        lambda f: QixTransport.send_rcsp_frame(transport, f),
        lambda t=5.0: QixTransport.recv_rcsp_frame(transport, t),
    )


def test_send_rcsp_frame_encodes_and_writes_to_ae01():
    t = FakeRawTransport()
    send, _ = make_send_recv(t)
    f = RcspFrame(flag=0xc0, cmd=0x03, payload=b"\x74\xff\xff\xff\xff\x00")
    send(f)
    assert len(t.ae01_sent) == 1
    assert t.ae01_sent[0] == bytes.fromhex("fedcbac003000674ffffffff00ef")


def test_recv_rcsp_frame_parses_raw_chunk():
    t = FakeRawTransport()
    _, recv = make_send_recv(t)
    # Simular notify chunk = RCSP frame ack del browse
    t.ae02_to_return.append(bytes.fromhex("fedcba000c00027600ef"))
    f = recv(2.0)
    assert f.flag == 0x00
    assert f.cmd == 0x0c
    assert f.payload == bytes.fromhex("7600")


def test_recv_rcsp_frame_propagates_timeout():
    t = FakeRawTransport()
    _, recv = make_send_recv(t)
    with pytest.raises(QixTimeoutError):
        recv(0.5)


def test_recv_rcsp_frame_raises_invalid_on_garbage():
    t = FakeRawTransport()
    _, recv = make_send_recv(t)
    t.ae02_to_return.append(b"\x00\x11\x22")  # no head FEDCBA
    with pytest.raises(InvalidFrameError):
        recv(2.0)


def test_mock_queue_rcsp_response():
    """Helper queue_rcsp_response encodea + mete al queue ae02 raw."""
    from tests.conftest import MockTransport
    m = MockTransport()
    m.queue_rcsp_response(RcspFrame(flag=0x00, cmd=0x0c, payload=b"\x76\x00"))
    chunk = m.recv_ae02_raw()
    assert chunk == bytes.fromhex("fedcba000c00027600ef")


def test_mock_recv_rcsp_frame_via_helper():
    """MockTransport.recv_rcsp_frame parses raw queue como RcspFrame."""
    from tests.conftest import MockTransport
    m = MockTransport()
    m.queue_rcsp_response(RcspFrame(flag=0x80, cmd=0x01, payload=b"hello"))
    f = m.recv_rcsp_frame()
    assert f.flag == 0x80
    assert f.cmd == 0x01
    assert f.payload == b"hello"
