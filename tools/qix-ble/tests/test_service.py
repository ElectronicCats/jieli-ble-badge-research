"""Tests de las constantes del módulo service."""
from qix_ble.service import (
    SERVICE_UUID,
    CHAR_FD01_NOTIFY, CHAR_FD02_WRITE, CHAR_FD03_CTRL,
    NOTIFY_CHARS,
    RCSP_SERVICE_UUID,
    CHAR_AE01_WRITE, CHAR_AE02_NOTIFY,
    RCSP_NOTIFY_CHARS,
    ALL_NOTIFY_CHARS,
)


def test_qix_service_uuids_unchanged():
    """Backwards compat: las constantes Qix existentes no cambiaron."""
    assert SERVICE_UUID.upper() == "C2E6FD00-E966-1000-8000-BEF9C223DF6A"
    assert CHAR_FD02_WRITE.upper() == "C2E6FD02-E966-1000-8000-BEF9C223DF6A"


def test_rcsp_service_uuid_is_jieli_16bit_full_form():
    """RCSP service AE00: alias 0xAE00 expandido a UUID 128-bit standard."""
    # JieLi usa el SIG base UUID 00000000-0000-1000-8000-00805F9B34FB con AE00 en posición 16-bit.
    assert RCSP_SERVICE_UUID.upper() == "0000AE00-0000-1000-8000-00805F9B34FB"
    assert CHAR_AE01_WRITE.upper() == "0000AE01-0000-1000-8000-00805F9B34FB"
    assert CHAR_AE02_NOTIFY.upper() == "0000AE02-0000-1000-8000-00805F9B34FB"


def test_rcsp_notify_chars_contains_ae02():
    assert CHAR_AE02_NOTIFY in RCSP_NOTIFY_CHARS


def test_all_notify_chars_combines_both_services():
    """ALL_NOTIFY_CHARS = NOTIFY_CHARS (Qix) + RCSP_NOTIFY_CHARS."""
    assert set(NOTIFY_CHARS).issubset(set(ALL_NOTIFY_CHARS))
    assert set(RCSP_NOTIFY_CHARS).issubset(set(ALL_NOTIFY_CHARS))
    assert len(ALL_NOTIFY_CHARS) == len(NOTIFY_CHARS) + len(RCSP_NOTIFY_CHARS)
