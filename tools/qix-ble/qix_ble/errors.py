"""Jerarquía de excepciones del cliente Qix BLE."""


class QixError(Exception):
    """Base de toda excepción específica del cliente Qix."""


class InvalidFrameError(QixError):
    """Frame Qix malformado (magic incorrecto, length truncado, etc.)."""


class ChecksumError(InvalidFrameError):
    """Checksum del frame no matchea con el computado."""


class BleConnectionError(QixError):
    """bleak falló: scan timeout, connect refused, write error."""


class TimeoutError(QixError):
    """Response no llegó al RX queue dentro del timeout configurado."""


class BadgeRejected(QixError):
    """El badge respondió pero con state error (no ACK del comando esperado)."""

    def __init__(self, msg: str, *, cmd: int | None = None, state: int | None = None):
        super().__init__(msg)
        self.cmd = cmd
        self.state = state


class UfwInvalid(QixError):
    """Validación pre-flash falló: wrapper Qix mal formado o CRC inválido."""
