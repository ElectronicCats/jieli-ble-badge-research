"""Tests para baji_ble.opcodes."""
from baji_ble.opcodes import (
    CMD_SETTINGS, CMD_QUERY, CMD_DIAL_TRANSFER, CMD_DIAL_NOTIFY, CMD_FILE_UART,
    SUB_FIND_ME, SUB_DIAL_START, SUB_DIAL_FILE, SUB_DIAL_FINISH,
    SUB_QUERY_MAC, SUB_QUERY_NAME, KEY_ENTER_OTA_MODE,
    cmd_name, query_key_label, is_fatal_status, fatal_status_message,
)


def test_cmd_constants_match_apk():
    assert CMD_SETTINGS == 18
    assert CMD_QUERY == 26
    assert CMD_DIAL_TRANSFER == 31
    assert CMD_DIAL_NOTIFY == 32
    assert CMD_FILE_UART == 34


def test_sub_constants_match_apk():
    assert SUB_FIND_ME == 11
    assert SUB_DIAL_START == 2
    assert SUB_DIAL_FILE == 1
    assert SUB_DIAL_FINISH == 3
    assert SUB_QUERY_MAC == 10
    assert SUB_QUERY_NAME == 12
    assert KEY_ENTER_OTA_MODE == 25


def test_cmd_name_known():
    assert cmd_name(18) == "settings"
    assert cmd_name(31) == "dial_transfer"


def test_cmd_name_unknown_returns_question_mark():
    assert cmd_name(0xFE) == "?"


def test_query_key_label_known():
    assert "address" in query_key_label(10).lower()
    assert "product" in query_key_label(20).lower()


def test_query_key_label_unknown_returns_none():
    assert query_key_label(0xFE) is None


def test_is_fatal_status_battery_low():
    assert is_fatal_status(3) is True


def test_is_fatal_status_not_fatal_for_ack_codes():
    assert is_fatal_status(1000) is False
    assert is_fatal_status(1001) is False


def test_fatal_status_message_battery():
    msg = fatal_status_message(3)
    assert msg is not None and "battery" in msg.lower()


def test_fatal_status_message_unknown_returns_none():
    assert fatal_status_message(999) is None
