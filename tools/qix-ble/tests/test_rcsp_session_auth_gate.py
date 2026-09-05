"""Tests del pre-condition check de RcspSession.

RcspSession verifica transport._auth_done al iniciar cualquier operación.
Si False, raises BadgeRejected explicando que necesita AuthSession primero.
"""
import pytest
from tests.conftest import MockTransport
from qix_ble.rcsp_session import RcspSession
from qix_ble.errors import BadgeRejected


def test_rcsp_session_constructs_without_auth():
    """Constructor NO chequea auth — el check es lazy en cada op (más flexible)."""
    t = MockTransport()
    assert t._auth_done is False
    sess = RcspSession(t)
    assert sess.transport is t


def test_rcsp_session_browse_raises_without_auth():
    """browse() sin auth previo → BadgeRejected con mensaje claro."""
    t = MockTransport()  # _auth_done = False
    sess = RcspSession(t)
    with pytest.raises(BadgeRejected, match="requires prior auth"):
        sess.browse()


def test_rcsp_session_read_small_file_raises_without_auth():
    """read_small_file() sin auth → mismo BadgeRejected."""
    from qix_ble.rcsp_session import FileEntry
    t = MockTransport()
    entry = FileEntry(type=0, type_name="USB", id=0, size=10, cluster=0, name="x")
    sess = RcspSession(t)
    with pytest.raises(BadgeRejected, match="requires prior auth"):
        sess.read_small_file(entry)


def test_rcsp_session_skips_check_if_auth_done():
    """Si _auth_done=True, el check pasa y la op procede (aunque luego falle por otra razón)."""
    t = MockTransport()
    t._auth_done = True
    sess = RcspSession(t)
    # browse sin scripted response → TimeoutError, NO BadgeRejected
    from qix_ble.errors import TimeoutError as QixTimeoutError
    with pytest.raises(QixTimeoutError):
        sess.browse()
