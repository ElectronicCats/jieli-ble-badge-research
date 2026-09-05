"""Tests del state machine OTA UpdateManager."""
import struct
import pytest

from qix_ble.frame import QixFrame
from qix_ble.errors import BadgeRejected, UfwInvalid, TimeoutError as QixTimeoutError
from qix_ble.update_manager import QixUpdater
from tests.conftest import MockTransport


# Helper: construir un .ufw válido mínimo para tests
def make_valid_ufw(payload_size: int = 32) -> bytes:
    """Construye un .ufw con wrapper Qix válido + payload mock con magic JLUFW al final.
    Usa wrap_qix logic externa para garantizar que la validación pase."""
    import binascii
    # Payload: empieza con bytes random, termina con JLUFW + 11B de padding (matching real .ufw OEM)
    inner = b"\x42" * (payload_size - 16) + b"JLUFW\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    assert len(inner) == payload_size

    crc = binascii.crc_hqx(inner, 0xFFFF) & 0xFFFF
    wrapper = (
        b"\xbc\xaf"                              # magic
        + b"\x01"                                # type
        + b"99.0.0.1\x00\x00"                    # version 10B null-padded
        + payload_size.to_bytes(4, "little")     # size LE32
        + b"\x00" * 8                            # reserved
        + crc.to_bytes(2, "little")              # CRC LE16
    )
    assert len(wrapper) == 27
    return wrapper + inner


def test_flash_success_single_chunk():
    """Payload pequeño (32B) que cabe en 1 chunk. Flow: 0xC0 → 0xC1 OK → 0xC2 → 0xC3 OK → 0xC5 OK."""
    ufw = make_valid_ufw(payload_size=32)
    transport = MockTransport()

    # 0xC1 RET_UPDATE: state=1 OK, allowSendLength=1024 (mayor que payload), offset=0
    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=struct.pack(">BII", 1, 1024, 0)))
    # 0xC3 RET_UPDATE_DATA: state=0 OK, nextOffset=32 (final). LE32 per wire empirical (2026-05-16).
    transport.queue_response(QixFrame(flags=0, cmd=0xC3, payload=struct.pack("<BI", 0, 32)))
    # 0xC5 RET_UPDATE_RESULT: status=0 OK
    transport.queue_response(QixFrame(flags=0, cmd=0xC5, payload=b"\x00"))

    progress: list[float] = []
    QixUpdater(transport).flash(ufw, on_progress=progress.append)

    # Verificar comandos enviados: 0xC0 + 0xC2 (1 chunk) → 2 sends + recv del 0xC5
    sent_cmds = [c for c, *_ in transport.sent]
    assert 0xC0 in sent_cmds
    assert 0xC2 in sent_cmds
    assert progress[-1] == pytest.approx(1.0)


def test_flash_two_chunks():
    """Payload 100B con allowSendLength=64 → 2 chunks (64 + 36)."""
    ufw = make_valid_ufw(payload_size=100)
    transport = MockTransport()

    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=struct.pack(">BII", 1, 64, 0)))
    transport.queue_response(QixFrame(flags=0, cmd=0xC3, payload=struct.pack("<BI", 0, 64)))   # ack chunk 1 (LE32)
    transport.queue_response(QixFrame(flags=0, cmd=0xC3, payload=struct.pack("<BI", 0, 100)))  # ack chunk 2 (LE32)
    transport.queue_response(QixFrame(flags=0, cmd=0xC5, payload=b"\x00"))

    progress: list[float] = []
    QixUpdater(transport).flash(ufw, on_progress=progress.append)

    chunk_sends = [s for s in transport.sent if s[0] == 0xC2]
    assert len(chunk_sends) == 2
    assert progress[-1] == pytest.approx(1.0)
    # Progreso monótono creciente
    assert all(progress[i] <= progress[i + 1] for i in range(len(progress) - 1))


def test_flash_invalid_wrapper_does_not_send():
    """CRC inválido en wrapper Qix → raise UfwInvalid antes de tocar transport."""
    ufw = make_valid_ufw(payload_size=32)
    bad_ufw = ufw[:25] + b"\xFF\xFF" + ufw[27:]  # corromper CRC bytes 25-26
    transport = MockTransport()

    with pytest.raises(UfwInvalid):
        QixUpdater(transport).flash(bad_ufw)

    assert len(transport.sent) == 0


def test_flash_badge_rejects_req_update():
    """0xC1 con state != 1 → BadgeRejected, no se envía 0xC2."""
    ufw = make_valid_ufw(payload_size=32)
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=struct.pack(">BII", 0, 0, 0)))  # state=0

    with pytest.raises(BadgeRejected) as exc_info:
        QixUpdater(transport).flash(ufw)
    assert exc_info.value.cmd == 0xC0

    chunk_sends = [s for s in transport.sent if s[0] == 0xC2]
    assert len(chunk_sends) == 0


def test_flash_badge_rejects_chunk():
    """0xC3 con state != 0 → BadgeRejected mid-flow."""
    ufw = make_valid_ufw(payload_size=100)
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=struct.pack(">BII", 1, 64, 0)))
    transport.queue_response(QixFrame(flags=0, cmd=0xC3, payload=struct.pack("<BI", 5, 64)))  # state=5 error (LE32)

    with pytest.raises(BadgeRejected) as exc_info:
        QixUpdater(transport).flash(ufw)
    assert exc_info.value.state == 5


def test_flash_timeout_no_response():
    """Si el badge no responde, raise TimeoutError."""
    ufw = make_valid_ufw(payload_size=32)
    transport = MockTransport()  # sin queue_response → simulará timeout

    with pytest.raises(QixTimeoutError):
        QixUpdater(transport).flash(ufw)


# ─── ProbeResult + QixUpdater.probe() ──────────────────────────────────────
from qix_ble.update_manager import ProbeResult


def test_probe_returns_result_with_state_allow_offset():
    """probe() devuelve ProbeResult con campos parseados del RET_UPDATE."""
    ufw = make_valid_ufw(payload_size=32)
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=struct.pack(">BII", 1, 1024, 0)))

    result = QixUpdater(transport).probe(ufw)

    assert isinstance(result, ProbeResult)
    assert result.state == 1
    assert result.allow_len == 1024
    assert result.offset == 0
    assert result.accepted is True
    assert result.dt_ms >= 0  # timing measured


def test_probe_accepted_false_when_state_not_one():
    """state != 1 → accepted=False, NO raise (es info útil)."""
    ufw = make_valid_ufw(payload_size=32)
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=struct.pack(">BII", 0, 0, 0)))

    result = QixUpdater(transport).probe(ufw)

    assert result.state == 0
    assert result.accepted is False


def test_probe_accepted_false_when_offset_nonzero():
    """offset > 0 (partial OTA pendiente) → accepted=False aunque state=1."""
    ufw = make_valid_ufw(payload_size=32)
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=struct.pack(">BII", 1, 1024, 512)))

    result = QixUpdater(transport).probe(ufw)

    assert result.state == 1
    assert result.offset == 512
    assert result.accepted is False


def test_probe_invariant_no_chunks_sent():
    """probe() NUNCA envía 0xC2 — invariant code-level."""
    ufw = make_valid_ufw(payload_size=100)
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=struct.pack(">BII", 1, 64, 0)))

    QixUpdater(transport).probe(ufw)

    chunk_sends = [s for s in transport.sent if s[0] == 0xC2]
    assert len(chunk_sends) == 0
    # Solo se envió 0xC0
    assert [c for c, *_ in transport.sent] == [0xC0]


def test_probe_validates_wrapper_first():
    """Wrapper inválido → UfwInvalid antes de tocar transport."""
    ufw = make_valid_ufw(payload_size=32)
    bad_ufw = ufw[:25] + b"\xFF\xFF" + ufw[27:]  # corromper CRC
    transport = MockTransport()

    with pytest.raises(UfwInvalid):
        QixUpdater(transport).probe(bad_ufw)

    assert len(transport.sent) == 0


def test_probe_raises_on_malformed_response():
    """RET_UPDATE payload <9B → BadgeRejected."""
    ufw = make_valid_ufw(payload_size=32)
    transport = MockTransport()
    transport.queue_response(QixFrame(flags=0, cmd=0xC1, payload=b"\x01\x00\x00"))  # solo 3B

    with pytest.raises(BadgeRejected, match="payload corto"):
        QixUpdater(transport).probe(ufw)


def test_probe_timeout_propagates():
    """Si badge no responde, raise TimeoutError."""
    ufw = make_valid_ufw(payload_size=32)
    transport = MockTransport()  # sin queue_response

    with pytest.raises(QixTimeoutError):
        QixUpdater(transport).probe(ufw)
