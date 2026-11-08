import struct
from qix_ble.frame import QixFrame
from qix_ble.update_manager import QixUpdater
from tests.conftest import MockTransport
from tests.test_update_manager import make_valid_ufw


def test_windows_ota_never_exceeds_write_response_limit(monkeypatch):
    monkeypatch.setattr("qix_ble.update_manager.sys.platform", "win32")
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0xc1, payload=struct.pack(">BII", 1, 1024, 0)))
    for offset in (480, 960, 1000):
        transport.queue_response(QixFrame(flags=0, cmd=0xc3, payload=struct.pack("<BI", 0, offset)))
    transport.queue_response(QixFrame(flags=0, cmd=0xc5, payload=b"\x00"))
    QixUpdater(transport).flash(make_valid_ufw(1000))
    frames = [QixFrame(flags=flags, cmd=cmd, payload=data).encode()
              for cmd, data, flags in transport.sent if cmd == 0xc2]
    assert len(frames) == 3
    assert max(map(len, frames)) == 494
    assert all(len(frame) <= 512 for frame in frames)
