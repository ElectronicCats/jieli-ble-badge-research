"""Image pipeline para E87 badge (PIL-based).

Port slim de community/ebadge-python-cli/ebadge_cli/image_converter.py.

Solo PIL — sin ffmpeg fallbacks ni qrcode dep. Para ffmpeg/QR el usuario
debe usar el flujo community o agregar las deps a su gusto.

Funciones core:
  - transform_to_badge: resize/fit/zoom a target_size con bg color
  - encode_badge_jpeg: JPEG quality bracketing → ≤16KB (single image limit del E87)
  - encode_video_jpeg: JPEG quality fijo (default 85) para frames de AVI
  - prepare_image: path → JPEG bytes (crop + resize + quality bracketing)
  - prepare_video: GIF/APNG/WebP/PNG animated → AVI MJPG bytes

Constantes del display E87 (per memory `display+touch`):
  - LCD = Sitronix ST77916, 360×360 QSPI RGB565
  - Touch radius 180 (round bezel)
  - FPS native 90, comfortable 12-30 para BLE push

Community usa 368×368 (square envelope del round LCD). Default aquí 360×360
(native LCD), pero override-able.

Dependency: instalar via `pip install qix-ble[media]` → PIL + numpy.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Literal, Optional

from qix_ble.avi import build_mjpg_avi

log = logging.getLogger("qix_ble.image")

# E87 single-image upload byte budget (per quality bracketing en community)
E87_TARGET_IMAGE_BYTES: int = 16_000

# Default device resolution
DEFAULT_TARGET_SIZE: tuple[int, int] = (360, 360)

# Quality steps for single-image bracketing: highest first, lowest fallback.
JPEG_QUALITY_STEPS: tuple[int, ...] = (88, 80, 72, 64, 56, 48, 40, 34)

FitMode = Literal["cover", "contain", "stretch"]


def _require_pil():
    """Lazy import + helpful error si media extras no instaladas."""
    try:
        from PIL import Image, ImageDraw  # noqa: F401
        return True
    except ImportError as e:
        raise ImportError(
            "qix_ble.image requires Pillow. Install with: "
            "pip install 'qix-ble[media]'  o  pip install Pillow numpy"
        ) from e


def transform_to_badge(pil_img, *,
                       fit: FitMode = "cover",
                       zoom: float = 1.0,
                       target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                       bg_color: tuple[int, int, int] = (0, 0, 0)):
    """Resize source PIL image a target_size con mode `fit`.

    fit:
      - cover   : llena target (crop excess) — default
      - contain : conserva todo (letterbox)
      - stretch : ignora aspect ratio
    zoom: multiplicador post-fit (>1 zoom in, <1 zoom out)
    """
    _require_pil()
    from PIL import Image

    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    tw, th = target_size
    out = Image.new("RGB", (tw, th), bg_color)
    src_w, src_h = pil_img.size

    if fit == "stretch":
        scaled = pil_img.resize((max(1, int(tw * zoom)), max(1, int(th * zoom))),
                                Image.Resampling.LANCZOS)
    elif fit == "contain":
        ratio = min(tw / src_w, th / src_h) * zoom
        scaled = pil_img.resize((max(1, int(src_w * ratio)),
                                 max(1, int(src_h * ratio))),
                                Image.Resampling.LANCZOS)
    else:  # cover
        ratio = max(tw / src_w, th / src_h) * zoom
        scaled = pil_img.resize((max(1, int(src_w * ratio)),
                                 max(1, int(src_h * ratio))),
                                Image.Resampling.LANCZOS)

    ox = (tw - scaled.width) // 2
    oy = (th - scaled.height) // 2
    out.paste(scaled, (ox, oy))
    return out


def encode_badge_jpeg(pil_img,
                      max_bytes: int = E87_TARGET_IMAGE_BYTES,
                      quality_steps: tuple[int, ...] = JPEG_QUALITY_STEPS) -> bytes:
    """JPEG encode con quality bracketing — para single image (≤16KB)."""
    _require_pil()
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    result = b""
    for q in quality_steps:
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=q)
        result = buf.getvalue()
        if len(result) <= max_bytes:
            log.debug("encode_badge_jpeg: quality=%d size=%d B (fit budget)", q, len(result))
            return result
    log.warning("encode_badge_jpeg: lowest quality %d still produced %d B (>%d)",
                quality_steps[-1], len(result), max_bytes)
    return result


def encode_video_jpeg(pil_img, quality: int = 85) -> bytes:
    """JPEG encode con quality fija — para AVI frames.

    Device E87 toleraba quality alta para video, pero quality muy baja
    da "Device error 0x01" (per community).
    """
    _require_pil()
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def prepare_image(file_path: str | Path,
                  target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                  fit: FitMode = "cover",
                  zoom: float = 1.0) -> bytes:
    """Cargar imagen → crop/resize → JPEG bytes (≤16KB)."""
    _require_pil()
    from PIL import Image

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"file not found: {path}")

    ext = path.suffix.lower()
    if ext in (".avi", ".mjpeg"):
        return path.read_bytes()

    with Image.open(path) as img:
        transformed = transform_to_badge(
            img, fit=fit, zoom=zoom, target_size=target_size,
        )
        return encode_badge_jpeg(transformed)


def prepare_video(file_path: str | Path,
                  target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                  fps: int = 12,
                  duration: Optional[float] = None,
                  fit: FitMode = "cover",
                  zoom: float = 1.0,
                  jpeg_quality: int = 85) -> bytes:
    """Animated image (GIF/APNG/WebP/PNG) → AVI MJPG bytes.

    Para video files (mp4, avi, etc.) usar ffmpeg externamente — este module
    deliberadamente NO depende de ffmpeg.

    Args:
        duration: tope en segundos. None = todo el animation.
    """
    _require_pil()
    from PIL import Image

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"file not found: {path}")

    ext = path.suffix.lower()
    if ext not in (".gif", ".apng", ".webp", ".png"):
        raise ValueError(
            f"prepare_video soporta solo GIF/APNG/WebP/PNG animated. "
            f"Para {ext}, encode con ffmpeg externamente."
        )

    jpeg_frames: list[bytes] = []
    with Image.open(path) as img:
        n_frames = getattr(img, "n_frames", 1)
        if n_frames < 2:
            raise ValueError(f"{path} no parece ser animated (n_frames={n_frames})")

        max_frames = int(duration * fps) if duration else n_frames
        for i in range(min(n_frames, max_frames)):
            img.seek(i)
            frame_rgb = img.convert("RGB")
            transformed = transform_to_badge(
                frame_rgb, fit=fit, zoom=zoom, target_size=target_size,
            )
            jpeg_frames.append(encode_video_jpeg(transformed, quality=jpeg_quality))

    return build_mjpg_avi(jpeg_frames, width=target_size[0],
                          height=target_size[1], fps=fps)


def prepare_slideshow(file_paths: list[str | Path],
                      target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                      fps: int = 12,
                      duration_per_image: float = 2.0,
                      fit: FitMode = "cover",
                      zoom: float = 1.0,
                      jpeg_quality: int = 85) -> bytes:
    """Múltiples imágenes → AVI MJPG slideshow."""
    _require_pil()
    from PIL import Image

    if not file_paths:
        raise ValueError("file_paths vacío")

    frames_per_image = max(1, int(duration_per_image * fps))
    jpeg_frames: list[bytes] = []

    for fp in file_paths:
        path = Path(fp)
        if not path.is_file():
            raise FileNotFoundError(f"file not found: {path}")
        with Image.open(path) as img:
            transformed = transform_to_badge(
                img, fit=fit, zoom=zoom, target_size=target_size,
            )
            frame_bytes = encode_video_jpeg(transformed, quality=jpeg_quality)
        jpeg_frames.extend([frame_bytes] * frames_per_image)

    return build_mjpg_avi(jpeg_frames, width=target_size[0],
                          height=target_size[1], fps=fps)
