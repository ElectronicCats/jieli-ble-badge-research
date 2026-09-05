"""Tests para commands/query.py — cmd 26 getSetInfoByKey."""
from baji_ble.commands.query import build_query, build_query_mac, build_query_name


def test_build_query_mac_golden_bytes():
    """getSetInfoByKey(10) — BD_ADDR."""
    out = build_query_mac()
    # cmd=26 (0x1A), sub=10 (0x0A), no payload → 8B total
    assert out == bytes.fromhex("cd00051a01 0a 0000".replace(" ", ""))


def test_build_query_name_uses_sub_12():
    assert build_query_name()[5] == 0x0C


def test_build_query_arbitrary_key():
    out = build_query(20)
    assert out[3] == 26
    assert out[5] == 20
    assert out[6:8] == bytes.fromhex("0000")
