"""cmd 18 (settings) — SwitchProtocol family per APK SendData."""
from baji_ble.frame import encode_switch_protocol, encode_no_value
from baji_ble.opcodes import CMD_SETTINGS, SUB_FIND_ME, KEY_ENTER_OTA_MODE


def build_find_me_on() -> bytes:
    return encode_switch_protocol(cmd=CMD_SETTINGS, sub=SUB_FIND_ME, value=1)


def build_find_me_off() -> bytes:
    return encode_switch_protocol(cmd=CMD_SETTINGS, sub=SUB_FIND_ME, value=0)


def build_enter_ota_mode() -> bytes:
    """getEnterOtaMode — preflight hint, NO ejecuta firmware push."""
    return encode_no_value(cmd=CMD_SETTINGS, sub=KEY_ENTER_OTA_MODE)
