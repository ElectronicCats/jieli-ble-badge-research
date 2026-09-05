"""Pure-Python MJPG AVI container builder.

Port byte-exact de community/ebadge-python-cli/ebadge_cli/avi_builder.py
(que a su vez es port del web-bluetooth-e87/web/src/avi-builder.ts).

Build RIFF/AVI con frames MJPG-comprimidos. NO requiere PIL — solo packing
binario. PIL es solo necesario para CREAR los JPEG frames (ver qix_ble.image).

Layout (video-only):
  RIFF 'AVI '
    LIST 'hdrl'
      avih          (56B)
      LIST 'strl'
        strh        (56B — vids, handler=MJPG)
        strf        (40B — BITMAPINFOHEADER)
    LIST 'movi'
      00dc frame0
      00dc frame1
      ...
    idx1            (16B per frame)

Target del E87: pantalla redonda 360×360. Default width/height del community
es 368 (cuadrado encerrando el círculo). Verificado en memory `display+touch`.
"""
from __future__ import annotations

import struct
from typing import Iterable


def _fourcc(s: str) -> bytes:
    return s.encode("ascii")[:4].ljust(4, b"\x00")


def _u32le(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def _u16le(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def _pad_even(data: bytes) -> bytes:
    """RIFF chunks deben ser word-aligned."""
    return data + b"\x00" if len(data) & 1 else data


def _chunk(chunk_id: str, data: bytes) -> bytes:
    return _fourcc(chunk_id) + _u32le(len(data)) + _pad_even(data)


def _list_chunk(list_type: str, *children: bytes) -> bytes:
    inner = _fourcc(list_type) + b"".join(children)
    return _fourcc("LIST") + _u32le(len(inner)) + inner


def build_mjpg_avi(
    frames: list[bytes] | Iterable[bytes],
    *,
    width: int = 360,
    height: int = 360,
    fps: int = 12,
) -> bytes:
    """Construir AVI con frames MJPG. Each frame = raw JPEG bytes (FFD8...FFD9).

    Args:
        frames: lista o iterable de JPEG bytes per frame
        width, height: pixel dimensions. Default 360 (E87 display nativo).
            Community usa 368 para encerrar el círculo del LCD ST77916.
        fps: frames per second (default 12).

    Returns: full AVI file as bytes.

    Raises:
        ValueError: si frames está vacío.
    """
    frames = list(frames)
    if not frames:
        raise ValueError("At least one frame is required")

    total_frames = len(frames)
    usec_per_frame = round(1_000_000 / fps) if fps > 0 else 1_000_000
    max_frame_size = max(len(f) for f in frames)

    # ── avih (main AVI header, 56 bytes) ──
    avih_data = struct.pack(
        "<IIIIIIIIIIII" + "hh",
        usec_per_frame,           # dwMicroSecPerFrame
        0,                        # dwMaxBytesPerSec (0 = unconstrained)
        0,                        # dwPaddingGranularity
        0x0110,                   # dwFlags: AVIF_HASINDEX | AVIF_TRUSTCKTYPE
        total_frames,             # dwTotalFrames
        0,                        # dwInitialFrames
        1,                        # dwStreams (video only)
        max_frame_size + 8,       # dwSuggestedBufferSize
        width, height,
        0, 0, 0, 0,               # dwReserved[0..3]
    )
    avih = _chunk("avih", avih_data)

    # ── strh (stream header, 56 bytes) ──
    strh_data = (
        _fourcc("vids")           # fccType
        + _fourcc("MJPG")         # fccHandler
        + _u32le(0)               # dwFlags
        + _u16le(0)               # wPriority
        + _u16le(0)               # wLanguage
        + _u32le(0)               # dwInitialFrames
        + _u32le(1)               # dwScale
        + _u32le(fps)             # dwRate
        + _u32le(0)               # dwStart
        + _u32le(total_frames)    # dwLength
        + _u32le(max_frame_size + 8)  # dwSuggestedBufferSize
        + _u32le(0)               # dwQuality
        + _u32le(0)               # dwSampleSize
        + _u16le(0) + _u16le(0)   # rcFrame.left, .top
        + _u16le(width) + _u16le(height)  # rcFrame.right, .bottom
    )
    strh = _chunk("strh", strh_data)

    # ── strf (BITMAPINFOHEADER, 40 bytes) ──
    strf_data = struct.pack(
        "<IiiHHIIiiII",
        40,                       # biSize
        width, height,            # biWidth, biHeight (positive = bottom-up)
        1,                        # biPlanes
        24,                       # biBitCount
        0x47504A4D,               # biCompression = 'MJPG' as LE32
        width * height * 3,       # biSizeImage
        0, 0,                     # biXPelsPerMeter, biYPelsPerMeter
        0, 0,                     # biClrUsed, biClrImportant
    )
    strf = _chunk("strf", strf_data)

    strl = _list_chunk("strl", strh, strf)
    hdrl = _list_chunk("hdrl", avih, strl)

    # ── movi list + idx1 ──
    movi_parts: list[bytes] = []
    idx1_entries: list[bytes] = []
    movi_offset = 4  # offset from start of movi list data (after 'movi' fourcc)

    for frame_data in frames:
        padded = _pad_even(frame_data)
        chunk_header = _fourcc("00dc") + _u32le(len(frame_data))
        movi_parts.append(chunk_header + padded)
        idx1_entries.append(
            _fourcc("00dc")
            + _u32le(0x10)        # AVIIF_KEYFRAME
            + _u32le(movi_offset)
            + _u32le(len(frame_data))
        )
        movi_offset += 8 + len(padded)

    movi_data = _fourcc("movi") + b"".join(movi_parts)
    movi = _fourcc("LIST") + _u32le(len(movi_data)) + movi_data

    idx1 = _chunk("idx1", b"".join(idx1_entries))

    riff_data = _fourcc("AVI ") + hdrl + movi + idx1
    return _fourcc("RIFF") + _u32le(len(riff_data)) + riff_data
