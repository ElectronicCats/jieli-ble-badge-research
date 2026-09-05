"""Tests para commands/upload.py — FSM dial upload con FakeTransport."""
import pytest
from baji_ble.commands.upload import upload_dial, UploadError
from baji_ble.errors import BajiStatusError


def _make_status_frame(*, cmd: int, sub: int, status: int) -> bytes:
    """Construye un frame CD cmd/sub con primeros 4B del payload = status BE i32."""
    payload = status.to_bytes(4, "big", signed=True)
    total_minus_3 = 5 + len(payload)
    head = bytes([0xCD]) + total_minus_3.to_bytes(2, "big") + bytes([cmd, 0x01, sub]) + len(payload).to_bytes(2, "big")
    return head + payload


def test_upload_dial_3_chunks_happy_path(fake_transport):
    """File 350B → chunk_size 200 → 2 chunks (200 + 150). Status 1000 → 1001 → 1002 → 2."""
    file = bytes(range(256)) + bytes(range(94))  # 350B determinístico
    fake_transport.queue_response(_make_status_frame(cmd=31, sub=2, status=1000))    # start ACK
    fake_transport.queue_response(_make_status_frame(cmd=32, sub=1, status=1001))    # chunk 1 ACK
    fake_transport.queue_response(_make_status_frame(cmd=32, sub=1, status=1002))    # chunk 2 ACK
    fake_transport.queue_response(_make_status_frame(cmd=32, sub=1, status=2))       # finish OK

    upload_dial(fake_transport, file=file, chunk_size=200,
                font_position=0, custom=0, r=255, g=255, b=255, timeout=1.0)

    # 4 TX: start + 2 chunks + finish
    assert len(fake_transport.tx_log) == 4
    assert fake_transport.tx_log[0][3] == 31 and fake_transport.tx_log[0][5] == 2  # start
    assert fake_transport.tx_log[1][3] == 31 and fake_transport.tx_log[1][5] == 1  # chunk 1
    assert fake_transport.tx_log[2][3] == 31 and fake_transport.tx_log[2][5] == 1  # chunk 2
    assert fake_transport.tx_log[3][3] == 31 and fake_transport.tx_log[3][5] == 3  # finish


def test_upload_dial_aborts_on_fatal_battery_status(fake_transport):
    """Status 3 = battery low → BajiStatusError."""
    fake_transport.queue_response(_make_status_frame(cmd=31, sub=2, status=3))
    with pytest.raises(BajiStatusError) as exc:
        upload_dial(fake_transport, file=b"\x00" * 100, chunk_size=200,
                    font_position=0, custom=0, r=255, g=255, b=255, timeout=1.0)
    assert exc.value.code == 3


def test_upload_dial_uses_120b_chunk_when_specified(fake_transport):
    """Per APK config bit, algunos firmwares quieren chunks de 120B."""
    file = bytes(240)
    fake_transport.queue_response(_make_status_frame(cmd=31, sub=2, status=1000))
    fake_transport.queue_response(_make_status_frame(cmd=32, sub=1, status=1001))
    fake_transport.queue_response(_make_status_frame(cmd=32, sub=1, status=1002))
    fake_transport.queue_response(_make_status_frame(cmd=32, sub=1, status=2))
    upload_dial(fake_transport, file=file, chunk_size=120,
                font_position=0, custom=0, r=255, g=255, b=255, timeout=1.0)
    # 2 chunks de 120B esperados
    assert len(fake_transport.tx_log) == 4
