"""Tests para baji_ble.session.BajiSession."""
import pytest
from baji_ble.session import BajiSession
from qix_ble.errors import TimeoutError


def test_send_and_wait_matches_cmd_sub(fake_transport):
    sess = BajiSession(fake_transport)
    response = bytes.fromhex("dc00051a0a 000901".replace(" ", ""))
    fake_transport.queue_response(response)
    out = sess.send_and_wait(
        bytes.fromhex("cd00051a0102 0000".replace(" ", "")),
        expect_cmd=26, expect_sub=10, timeout=1.0,
    )
    assert out == response


def test_send_and_wait_skips_unrelated_frames(fake_transport):
    sess = BajiSession(fake_transport)
    fake_transport.queue_response(bytes.fromhex("dc0005120a 000901".replace(" ", "")))  # cmd 18 — skip
    target = bytes.fromhex("dc00051a0a 000901".replace(" ", ""))
    fake_transport.queue_response(target)
    out = sess.send_and_wait(b"\x00", expect_cmd=26, expect_sub=10, timeout=1.0)
    assert out == target


def test_send_and_wait_timeout_raises(fake_transport):
    sess = BajiSession(fake_transport)
    with pytest.raises(TimeoutError):
        sess.send_and_wait(b"\x00", expect_cmd=26, expect_sub=10, timeout=0.1)


def test_send_writes_to_transport(fake_transport):
    sess = BajiSession(fake_transport)
    fake_transport.queue_response(bytes.fromhex("dc00051a0a 000901".replace(" ", "")))
    sent = bytes.fromhex("cd00051a0102 0000".replace(" ", ""))
    sess.send_and_wait(sent, expect_cmd=26, expect_sub=10, timeout=1.0)
    assert fake_transport.tx_log == [sent]
