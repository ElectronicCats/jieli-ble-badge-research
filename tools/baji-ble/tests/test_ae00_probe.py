"""Tests para baji_ble.ae00_probe."""
import pytest
from unittest.mock import MagicMock, patch
from baji_ble.ae00_probe import probe_ae00, AE00ProbeResult


def test_probe_ae00_success_returns_handshake_done():
    """Mock QixTransport + AuthSession.do_handshake → success."""
    mock_transport_class = MagicMock()
    mock_transport_inst = MagicMock()
    mock_transport_class.return_value.__enter__.return_value = mock_transport_inst
    mock_transport_class.return_value.__exit__.return_value = False
    mock_auth_class = MagicMock()
    mock_auth_inst = MagicMock()
    mock_auth_class.return_value = mock_auth_inst
    mock_auth_inst.do_handshake.return_value = None

    with patch("baji_ble.ae00_probe.Ae00DbusTransport", mock_transport_class), \
         patch("baji_ble.ae00_probe.AuthSession", mock_auth_class):
        result = probe_ae00("AA:BB:CC:DD:EE:FF", timeout=2.0)

    assert isinstance(result, AE00ProbeResult)
    assert result.success is True
    assert "handshake" in result.message.lower()
    mock_auth_inst.do_handshake.assert_called_once()


def test_probe_ae00_failure_captures_exception():
    """Si do_handshake raises, retorna success=False con mensaje."""
    mock_transport_class = MagicMock()
    mock_transport_inst = MagicMock()
    mock_transport_class.return_value.__enter__.return_value = mock_transport_inst
    mock_transport_class.return_value.__exit__.return_value = False
    mock_auth_class = MagicMock()
    mock_auth_inst = MagicMock()
    mock_auth_class.return_value = mock_auth_inst
    mock_auth_inst.do_handshake.side_effect = RuntimeError("step 3 timeout")

    with patch("baji_ble.ae00_probe.Ae00DbusTransport", mock_transport_class), \
         patch("baji_ble.ae00_probe.AuthSession", mock_auth_class):
        result = probe_ae00("AA:BB:CC:DD:EE:FF", timeout=2.0)

    assert result.success is False
    assert "step 3 timeout" in result.message
