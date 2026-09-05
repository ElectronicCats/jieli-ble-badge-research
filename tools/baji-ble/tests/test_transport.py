"""Tests para baji_ble.transport — BajiTransport con mock BLE."""
import queue
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from baji_ble.transport import BajiTransport
from baji_ble.errors import BajiError


@pytest.fixture
def mock_bleak_client():
    """Mock BleakClient con write_gatt_char + start_notify capturados."""
    client = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock(return_value=True)
    client.is_connected = True
    client.write_gatt_char = AsyncMock(return_value=None)
    client.start_notify = AsyncMock(return_value=None)
    client.stop_notify = AsyncMock(return_value=None)
    return client


def test_transport_writes_to_nus_tx(mock_bleak_client):
    """Validate que send_raw_to_nus llama write_gatt_char con NUS_TX_WRITE."""
    from baji_ble.service import NUS_TX_WRITE
    with patch("baji_ble.transport.BleakClient", return_value=mock_bleak_client):
        with BajiTransport("AA:BB:CC:DD:EE:FF") as t:
            t.send_raw_to_nus(b"\xcd\x00\x05\x12\x01\x0b\x00\x00")
    calls = mock_bleak_client.write_gatt_char.call_args_list
    assert len(calls) == 1
    assert calls[0].args[0] == NUS_TX_WRITE
    assert calls[0].args[1] == b"\xcd\x00\x05\x12\x01\x0b\x00\x00"


def test_transport_receives_complete_frame_via_assembler(mock_bleak_client):
    """Llamada al callback notify con frame completo → recv_frame lo devuelve."""
    captured_callback = {}

    async def capture_start_notify(uuid, cb):
        captured_callback[uuid] = cb

    mock_bleak_client.start_notify = AsyncMock(side_effect=capture_start_notify)
    with patch("baji_ble.transport.BleakClient", return_value=mock_bleak_client):
        with BajiTransport("AA:BB:CC:DD:EE:FF") as t:
            from baji_ble.service import NUS_RX_NOTIFY
            cb = captured_callback[NUS_RX_NOTIFY]
            sender = MagicMock()
            sender.uuid = NUS_RX_NOTIFY
            full = bytes.fromhex("cd00051201 0b00 00".replace(" ", ""))
            cb(sender, bytearray(full))
            got = t.recv_frame(timeout=1.0)
            assert got == full


def test_transport_assembles_split_notify(mock_bleak_client):
    captured_callback = {}

    async def capture_start_notify(uuid, cb):
        captured_callback[uuid] = cb

    mock_bleak_client.start_notify = AsyncMock(side_effect=capture_start_notify)
    with patch("baji_ble.transport.BleakClient", return_value=mock_bleak_client):
        with BajiTransport("AA:BB:CC:DD:EE:FF") as t:
            from baji_ble.service import NUS_RX_NOTIFY
            cb = captured_callback[NUS_RX_NOTIFY]
            sender = MagicMock()
            sender.uuid = NUS_RX_NOTIFY
            full = bytes([0xCD, 0x00, 0x1b]) + b"\x00" * 27  # 30B frame
            cb(sender, bytearray(full[:20]))
            cb(sender, bytearray(full[20:]))
            got = t.recv_frame(timeout=1.0)
            assert got == full


def test_send_raw_to_nus_wraps_timeout_in_qix_timeout_error():
    """Issue 2 fix: stdlib concurrent.futures.TimeoutError must be re-raised as qix_ble.TimeoutError."""
    import concurrent.futures
    from qix_ble.errors import TimeoutError as QixTimeoutError

    mock_client = MagicMock()
    mock_client.connect = AsyncMock(return_value=True)
    mock_client.disconnect = AsyncMock(return_value=True)
    mock_client.is_connected = True
    mock_client.write_gatt_char = AsyncMock(return_value=None)
    mock_client.start_notify = AsyncMock(return_value=None)
    mock_client.stop_notify = AsyncMock(return_value=None)

    with patch("baji_ble.transport.BleakClient", return_value=mock_client):
        with BajiTransport("AA:BB:CC:DD:EE:FF") as t:
            # Force fut.result to raise concurrent.futures.TimeoutError via mock
            with patch("asyncio.run_coroutine_threadsafe") as mock_rcs:
                mock_future = MagicMock()
                mock_future.result.side_effect = concurrent.futures.TimeoutError()
                mock_rcs.return_value = mock_future
                with pytest.raises(QixTimeoutError, match="timeout"):
                    t.send_raw_to_nus(b"\x00")
