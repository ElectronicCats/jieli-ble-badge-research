"""Tests del cipher JieLi (qix_ble.auth.JliCipher).

KAT vectors:
- KAT 0: built-in en jl_auth_v3.py upstream (un E87 de otro usuario).
- KAT 1 + 2: nuestro E87 real (MAC AA:BB:CC:DD:EE:FF), extraídos del
  btsnoop hw-sessions/2026-05-14/zrun/btsnoop.pcap (packets 562/565 + 571/573).

Detalles en hw-sessions/2026-05-14/zrun/kat-vectors.md (gitignored).

NOTA: AuthSession state machine se testea en test_auth_session.py (Task 7)
una vez que MockTransport tenga la API raw AE01/AE02.
"""
from qix_ble.auth import (
    JliCipher,
    STATIC_KEY, MAGIC, MASK,
    SBOX, ISBOX, KS_TABLE,
    get_random_auth_data,
)


def test_constants_match_day1_findings():
    """Day 1 documentó las constantes vía RE de libjl_auth.so + repo comunitario."""
    assert STATIC_KEY == bytes.fromhex("06775F87918DD423005DF1D8CF0C142B")
    assert MAGIC == bytes.fromhex("112233332211")
    assert MASK == 0x9999


def test_sbox_isbox_are_inverse_permutations():
    """SBOX[ISBOX[x]] == x ∀ x, y ambas son permutations completas de [0, 256)."""
    assert len(SBOX) == 256
    assert len(ISBOX) == 256
    assert sorted(SBOX) == list(range(256))
    assert sorted(ISBOX) == list(range(256))
    assert all(SBOX[ISBOX[x]] == x for x in range(256))
    assert all(ISBOX[SBOX[x]] == x for x in range(256))


def test_ks_table_has_correct_length():
    assert len(KS_TABLE) == 256


def test_cipher_kat0_upstream_builtin():
    """KAT 0: vector built-in en jl_auth_v3.py — independiente del HW nuestro.

    Verifica que el port funcionaliza correctamente, en general (otro badge).
    """
    challenge_inner = bytes.fromhex("b6e080ecaff32291" "6d88fad5aa34c2ac")
    expected_inner = bytes.fromhex("1d8897ac4604d332" "e8175e81bb292524")
    c = JliCipher()
    assert c.encrypt(challenge_inner) == expected_inner


def test_cipher_kat1_our_e87_step1_to_step2():
    """KAT 1: nuestro E87 — packet 562 (phone random) → packet 565 (device encrypted)."""
    phone_random = bytes.fromhex("a53057d376fc0476" "e5dc68273b5e791e")
    device_encrypted = bytes.fromhex("b3242d82e1df6c54" "5895ecae13b44fea")
    c = JliCipher()
    assert c.encrypt(phone_random) == device_encrypted


def test_cipher_kat2_our_e87_step4_to_step5():
    """KAT 2: nuestro E87 — packet 571 (device challenge) → packet 573 (phone encrypted)."""
    device_challenge = bytes.fromhex("384ac9d8e481e5ca" "aeb8cead20ee8f9e")
    phone_encrypted = bytes.fromhex("fe1b5b643a790fdc" "f585e354dd377b0f")
    c = JliCipher()
    assert c.encrypt(device_challenge) == phone_encrypted


def test_cipher_input_size_validated():
    """encrypt() exige exactly 16 bytes."""
    c = JliCipher()
    try:
        c.encrypt(b"\x00" * 15)
    except ValueError:
        pass
    else:
        raise AssertionError("encrypt(15B) should have raised")
    try:
        c.encrypt(b"\x00" * 17)
    except ValueError:
        pass
    else:
        raise AssertionError("encrypt(17B) should have raised")


def test_get_random_auth_data_returns_16_bytes():
    """get_random_auth_data() — 16B random, sin marker (el marker lo añade AuthSession)."""
    r = get_random_auth_data()
    assert isinstance(r, bytes)
    assert len(r) == 16
    # Sanity: dos calls consecutivas no deberían ser iguales (extremadamente improbable)
    r2 = get_random_auth_data()
    assert r != r2


# ────────────────────────────────────────────────────────────────────────────
# AuthSession state machine tests (con MockTransport raw AE01/AE02)
# ────────────────────────────────────────────────────────────────────────────

import pytest
from tests.conftest import MockTransport
from qix_ble.auth import AuthSession
from qix_ble.errors import BadgeRejected


def test_auth_session_happy_path_completes_6_steps(monkeypatch):
    """Simular el handshake completo con responses correctas del device."""
    # Forzar random determinista — uso los bytes reales del packet 562 (nuestro E87)
    fixed_random = bytes.fromhex("a53057d376fc0476e5dc68273b5e791e")
    monkeypatch.setattr("qix_ble.auth.get_random_auth_data", lambda: fixed_random)

    t = MockTransport()
    c = JliCipher()

    # Step 2: device cifra nuestro random (sabemos el resultado por KAT 1)
    t.queue_ae02_raw(b"\x01" + c.encrypt(fixed_random))
    # Step 4: device envía su challenge (uso los bytes del packet 571)
    device_challenge = bytes.fromhex("384ac9d8e481e5caaeb8cead20ee8f9e")
    t.queue_ae02_raw(b"\x00" + device_challenge)
    # Step 6: device acks
    t.queue_ae02_raw(b"\x02pass")

    AuthSession(t).do_handshake()  # should not raise

    # Verify writes: 3 packets a AE01
    assert len(t.ae01_sent) == 3
    assert t.ae01_sent[0] == b"\x00" + fixed_random              # step 1
    assert t.ae01_sent[1] == b"\x02pass"                          # step 3
    assert t.ae01_sent[2] == b"\x01" + c.encrypt(device_challenge)  # step 5


def test_auth_session_step2_wrong_encryption_raises():
    """Step 2: si device devuelve cifrado incorrecto → BadgeRejected."""
    t = MockTransport()
    # Garbage en lugar del cifrado correcto
    t.queue_ae02_raw(b"\x01" + b"\x00" * 16)

    with pytest.raises(BadgeRejected, match="step 2"):
        AuthSession(t).do_handshake()


def test_auth_session_step2_wrong_marker_raises():
    """Step 2: si payload[0] != 0x01 → BadgeRejected (marker mismatch)."""
    t = MockTransport()
    t.queue_ae02_raw(b"\x99" + b"\x00" * 16)
    with pytest.raises(BadgeRejected, match="step 2"):
        AuthSession(t).do_handshake()


def test_auth_session_step4_wrong_marker_raises(monkeypatch):
    """Step 4: si device no envía 00 ‖ 16B challenge → BadgeRejected."""
    fixed_random = b"\x00" * 16
    monkeypatch.setattr("qix_ble.auth.get_random_auth_data", lambda: fixed_random)
    c = JliCipher()
    t = MockTransport()
    t.queue_ae02_raw(b"\x01" + c.encrypt(fixed_random))   # step 2 OK
    t.queue_ae02_raw(b"\x99" + b"\x42" * 16)              # step 4 wrong marker
    with pytest.raises(BadgeRejected, match="step 4"):
        AuthSession(t).do_handshake()


def test_auth_session_step6_wrong_final_raises(monkeypatch):
    """Step 6: si device no responde "\\x02pass" exact → BadgeRejected."""
    fixed_random = b"\x00" * 16
    monkeypatch.setattr("qix_ble.auth.get_random_auth_data", lambda: fixed_random)
    c = JliCipher()
    t = MockTransport()
    t.queue_ae02_raw(b"\x01" + c.encrypt(fixed_random))   # step 2 OK
    t.queue_ae02_raw(b"\x00" + b"\x42" * 16)              # step 4 OK shape
    t.queue_ae02_raw(b"\x03not_pass")                     # step 6 wrong
    with pytest.raises(BadgeRejected, match="step 6"):
        AuthSession(t).do_handshake()


def test_auth_session_sets_auth_done_flag_on_success(monkeypatch):
    """Post-handshake, transport._auth_done debe ser True."""
    fixed_random = bytes.fromhex("00" * 16)
    monkeypatch.setattr("qix_ble.auth.get_random_auth_data", lambda: fixed_random)
    c = JliCipher()
    t = MockTransport()
    assert t._auth_done is False  # initial state
    t.queue_ae02_raw(b"\x01" + c.encrypt(fixed_random))
    t.queue_ae02_raw(b"\x00" + b"\x42" * 16)
    t.queue_ae02_raw(b"\x02pass")
    AuthSession(t).do_handshake()
    assert t._auth_done is True
