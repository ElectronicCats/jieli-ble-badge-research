"""cmd 26 getSetInfoByKey — query device info por sub-key."""
from baji_ble.frame import encode_no_value
from baji_ble.opcodes import CMD_QUERY, SUB_QUERY_MAC, SUB_QUERY_NAME


def build_query(key: int) -> bytes:
    return encode_no_value(cmd=CMD_QUERY, sub=key)


def build_query_mac() -> bytes:
    return build_query(SUB_QUERY_MAC)


def build_query_name() -> bytes:
    return build_query(SUB_QUERY_NAME)
