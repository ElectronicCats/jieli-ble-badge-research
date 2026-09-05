"""Tests para baji_ble.errors."""
import pytest
from qix_ble.errors import QixError
from baji_ble.errors import BajiError, BajiFrameError, BajiStatusError


def test_baji_error_inherits_qix_error():
    assert issubclass(BajiError, QixError)


def test_baji_frame_error_inherits_baji_error():
    assert issubclass(BajiFrameError, BajiError)


def test_baji_status_error_carries_code_and_message():
    err = BajiStatusError("battery too low", code=3)
    assert err.code == 3
    assert "battery too low" in str(err)
