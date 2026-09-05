"""Cmd codes, sub-keys, status codes per APK SuperBand v2.1.23 + PROTOCOL.md community."""

CMD_SETTINGS = 18         # SwitchProtocol family (time, find-me, enter-ota-mode)
CMD_QUERY = 26            # getSetInfoByKey
CMD_DIAL_TRANSFER = 31    # WatchThemeTools: dial upload (start/file/finish)
CMD_DIAL_NOTIFY = 32      # BaseReceiveData: dial ACKs + clock info
CMD_FILE_UART = 34        # BleFileSendTools alt file pipe (no impl en v0.1)

SUB_FIND_ME = 11
SUB_TIME_SYNC = 1
SUB_DIAL_START = 2
SUB_DIAL_FILE = 1
SUB_DIAL_FINISH = 3
SUB_QUERY_MAC = 10
SUB_QUERY_NAME = 12
KEY_ENTER_OTA_MODE = 25   # cmd 18 sub 25 — getEnterOtaMode (preflight hint, no push)

_CMD_NAMES = {
    18: "settings",
    26: "query",
    31: "dial_transfer",
    32: "dial_notify",
    34: "file_uart",
}

_QUERY_KEY_LABELS = {
    1: "personal / profile",
    10: "classic Bluetooth address",
    12: "device name",
    15: "info key 15",
    16: "hardware revision string",
    17: "info key 17",
    20: "product / model string",
    21: "language",
}

_FATAL_STATUS_MESSAGES = {
    1: "check failed (APK ERROR_CHECK 1003 — verify / checksum)",
    3: "battery too low to upgrade (APK ERROR_BATTERY_LOW 1008)",
    4: "charging — device refuses upgrade (APK ERROR_CHARGE_BATTERY 1009)",
    5: "out of memory (APK ERROR_OUT_OF_MEMORY 1010)",
    7: "unknown / not ready (APK ERROR_UNKNOWN 1007)",
}


def cmd_name(cmd: int) -> str:
    return _CMD_NAMES.get(cmd, "?")


def query_key_label(key: int) -> str | None:
    return _QUERY_KEY_LABELS.get(key)


def is_fatal_status(code: int) -> bool:
    """True si el code (<1000) es un error fatal del firmware. >=1000 son ACKs chunk."""
    return code in _FATAL_STATUS_MESSAGES


def fatal_status_message(code: int) -> str | None:
    return _FATAL_STATUS_MESSAGES.get(code)
