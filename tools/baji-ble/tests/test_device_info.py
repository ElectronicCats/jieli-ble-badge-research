"""Tests para baji_ble.device_info."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from baji_ble.device_info import read_device_info, read_battery_level, DeviceInfo


@pytest.fixture
def mock_client_with_dis():
    """BleakClient mock que retorna golden DIS bytes del dis_reads.txt del community."""
    client = MagicMock()
    dis_map = {
        "00002a29-0000-1000-8000-00805f9b34fb": b"LJ733",
        "00002a24-0000-1000-8000-00805f9b34fb": b"",
        "00002a25-0000-1000-8000-00805f9b34fb": b"LJ",
        "00002a26-0000-1000-8000-00805f9b34fb": b"V32399",
        "00002a27-0000-1000-8000-00805f9b34fb": b"LJ733_MB_V1.1",
        "00002a28-0000-1000-8000-00805f9b34fb": b"111110110101011000010010000043",
        "00002a23-0000-1000-8000-00805f9b34fb": b"",
        "00002a2a-0000-1000-8000-00805f9b34fb": b"LJ733",
        "00002a50-0000-1000-8000-00805f9b34fb": b"",
        "00002a19-0000-1000-8000-00805f9b34fb": b"\x1a\x00",
    }
    async def fake_read(uuid):
        return dis_map.get(uuid, b"")
    client.read_gatt_char = AsyncMock(side_effect=fake_read)
    return client


def test_read_device_info_parses_strings(mock_client_with_dis):
    info = read_device_info(mock_client_with_dis)
    assert info.manufacturer == "LJ733"
    assert info.firmware_revision == "V32399"
    assert info.hardware_revision == "LJ733_MB_V1.1"
    assert info.ieee_cert == "LJ733"


def test_read_battery_level_returns_first_octet_as_percent(mock_client_with_dis):
    """Battery 0x1a 0x00 → 26%. El trailing 0x00 es OEM padding."""
    pct = read_battery_level(mock_client_with_dis)
    assert pct == 26
