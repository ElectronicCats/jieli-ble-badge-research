"""Boot animation push para el badge E87 (JieLi AC707N).

Per RE smali del app ZRun (`DialTool.smali:447 sendBootAniFile`):
  El boot ani = un blob `[27B file_hdr type=0x0B][8B BM hdr][RGB565 LE raw pixels]`
  subido al device usando la misma pipeline RcspUploader del image push.

A diferencia del image push (JPEG comprimido), el boot ani envía **raw RGB565
little-endian** sin compresión — el tamaño es siempre `width × height × 2` bytes
+ 35 bytes de headers.

Caveat: para E87 (360×360) el blob queda ~253KB que **excede** el threshold
empírico de 200KB seguros para uploads RCSP. Mitigación: usar 240×240 (~115KB).

⚠️ EXPERIMENTAL: NO validado contra HW. La implementación es RE-based del smali
sin captura wire del path real. El primer test debería ser `query()` (read-only,
sin escritura al device) antes de cualquier push.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

from qix_ble.image import _require_pil, transform_to_badge
from qix_ble.opcodes import CMD_SET_BOOT_ANI

log = logging.getLogger("qix_ble.bootanim")

BOOT_ANI_FILE_TYPE: int = 0x0B  # type byte en el file header (NO el cmd wire)


@dataclass(frozen=True)
class BootAnimationInfo:
    """Response del badge a get_boot_ani_info (cmd 0x8A standalone, 5 byte reply)."""
    width: int   # LE16
    height: int  # LE16
    fmt_type: int  # 1 byte — flag de formato esperado


def query_boot_ani_info(transport, timeout: float = 5.0) -> BootAnimationInfo:
    """Query badge metadata del boot ani actual.

    Envía cmd 0x8A SET_BOOT_ANI con payload vacío (= "get info") y parsea reply.
    Read-only — ZERO escritura al device. Útil para validar feature support
    antes de cualquier push experimental.
    """
    resp = transport.send_command(
        CMD_SET_BOOT_ANI, b"",
        expect_cmd=CMD_SET_BOOT_ANI, timeout=timeout, response=True,
    )
    if len(resp.payload) < 5:
        raise ValueError(
            f"boot_ani_info reply corto: {len(resp.payload)} bytes "
            f"(esperado ≥5) — feature posiblemente no soportada en este FW"
        )
    width, height, fmt_type = struct.unpack("<HHB", resp.payload[:5])
    return BootAnimationInfo(width=width, height=height, fmt_type=fmt_type)


def encode_rgb565_le(pil_img) -> bytes:
    """RGB888 → RGB565 little-endian per smali `ImageCacheUtils.bitmap2RGB`.

    Formula:
      px565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    Stored LE16.
    """
    _require_pil()
    import numpy as np
    rgb = np.array(pil_img.convert("RGB"), dtype=np.uint16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    px = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return px.astype("<u2").tobytes()


def _crc16_xmodem(data: bytes, seed: int = 0xFFFF) -> int:
    """CRC16-CCITT/XMODEM with seed=0xFFFF (JieLi `getCRC16` per DialTool.smali:311).

    NOTA: distinto del default `crc16_ccitt` en upload.py que usa seed=0.
    """
    crc = seed & 0xFFFF
    for byte in data:
        crc = (crc ^ (byte << 8)) & 0xFFFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_boot_ani_blob(pil_img, *, mode: int = 0) -> bytes:
    """Construye `[27B file_hdr][8B BM hdr][RGB565 LE pixels]` byte-exact con
    `sendBootAniFile + fileToBytes` del smali.

    Layout file header (27B):
        [0:2]   magic 0xBC 0xAF
        [2]     type = 0x0B (BOOT_ANI)
        [3:5]   mode LE16 (default 0)
        [5:13]  zeros
        [13:17] total_len BE32 (= 8 + w*h*2)
        [17:25] zeros
        [25:27] CRC16 LE16 over bytes [27:] (BM hdr + pixels), seed=0xFFFF

    Layout BM header (8B):
        [0:2]   magic 0x42 0x4D ('B','M')
        [2:4]   width LE16
        [4:6]   height LE16
        [6]     bpp = 0x10 (16)
        [7]     flag = 0x80 (RGB565)
    """
    w, h = pil_img.size
    pixels = encode_rgb565_le(pil_img)
    bm_hdr = bytes([
        0x42, 0x4D,
        w & 0xFF, (w >> 8) & 0xFF,
        h & 0xFF, (h >> 8) & 0xFF,
        0x10, 0x80,
    ])
    img_block = bm_hdr + pixels  # 8 + w*h*2 bytes
    crc = _crc16_xmodem(img_block, seed=0xFFFF)

    fhdr = bytearray(27)
    fhdr[0] = 0xBC
    fhdr[1] = 0xAF
    fhdr[2] = BOOT_ANI_FILE_TYPE
    fhdr[3] = mode & 0xFF
    fhdr[4] = (mode >> 8) & 0xFF
    fhdr[13:17] = len(img_block).to_bytes(4, "big")  # BE32 per JieLi intToBytes
    fhdr[25] = crc & 0xFF
    fhdr[26] = (crc >> 8) & 0xFF
    return bytes(fhdr) + img_block


def prepare_bootanim(file_path, *, target_size: tuple[int, int] = (360, 360),
                     fit: str = "cover") -> bytes:
    """Pipeline completo: load image → resize → encode → wrap headers.

    Returns blob listo para push (incluye file header). Total size:
      27 + 8 + (w * h * 2) bytes.

    Para E87 (360x360): 27 + 8 + 259200 = 259235 bytes (~253 KB).
    Para 240x240 (recomendado por <200KB threshold): 27 + 8 + 115200 = 115235.
    """
    _require_pil()
    from PIL import Image
    img = Image.open(file_path)
    img = transform_to_badge(img, target_size=target_size, fit=fit)
    blob = build_boot_ani_blob(img)
    log.info("prepared bootanim blob: %d bytes (target %dx%d)",
             len(blob), target_size[0], target_size[1])
    return blob
