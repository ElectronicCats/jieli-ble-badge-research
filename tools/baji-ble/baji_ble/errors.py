"""Jerarquía de excepciones específicas de baji-ble (extiende qix_ble.errors)."""
from qix_ble.errors import QixError


class BajiError(QixError):
    """Base de toda excepción del cliente Baji."""


class BajiFrameError(BajiError):
    """Frame Baji malformado (magic ≠ CD/DC, length truncado, etc.)."""


class BajiStatusError(BajiError):
    """Firmware respondió con status code <1000 (error fatal)."""

    def __init__(self, msg: str, *, code: int):
        super().__init__(msg)
        self.code = code
