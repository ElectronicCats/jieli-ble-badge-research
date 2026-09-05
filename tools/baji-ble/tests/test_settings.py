"""Tests para commands/settings.py — golden bytes APK SendData."""
from baji_ble.commands.settings import (
    build_find_me_on, build_find_me_off, build_enter_ota_mode,
)


def test_build_find_me_on_golden_bytes():
    """SendData.getSetFindMeValue(true) — del APK_PARITY.md"""
    assert build_find_me_on() == bytes.fromhex("cd00061201 0b00 0101".replace(" ", ""))


def test_build_find_me_off_changes_last_byte():
    on = build_find_me_on()
    off = build_find_me_off()
    assert off[:-1] == on[:-1]
    assert off[-1] == 0x00
    assert on[-1] == 0x01


def test_build_enter_ota_mode_golden_bytes():
    """getEnterOtaMode = getNoValueProtocol(18, 25)."""
    assert build_enter_ota_mode() == bytes.fromhex("cd0005120119 0000".replace(" ", ""))
