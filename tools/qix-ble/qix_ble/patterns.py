"""Pattern + text animation generators (PIL/numpy based).

Port consolidado de:
  - community/ebadge-python-cli/ebadge_cli/image_converter.py: text_frames,
    pattern_frames (gradient/pulse/checker/rainbow/wave), prepare_* wrappers
  - community/web-bluetooth-e87/web/src/pattern-generators.ts: plasma_waves +
    concentric_waves (math-based, port via numpy). Skip los canvas-heavy
    (matrix_rain, game_of_life, hypno_toad) — su valor estético no justifica
    el LOC.

Estilo: cada generador retorna `list[PIL.Image]` (frames). El usuario los
pasa a `qix_ble.image.encode_video_jpeg` + `qix_ble.avi.build_mjpg_avi` para
empaquetar. Helper `frames_to_avi(...)` lo hace en un paso.

LOOPING CONTRACT (per pattern-generators.ts):
  phase = f / total_frames es 0→1. Frame 0 == hipotético frame N para loop sin
  costuras a cualquier FPS del device.

Dependency: PIL + numpy (instalar via `pip install qix-ble[media]`).
"""
from __future__ import annotations

import logging
import math
from typing import Literal, Optional

from qix_ble.avi import build_mjpg_avi

log = logging.getLogger("qix_ble.patterns")

DEFAULT_TARGET_SIZE: tuple[int, int] = (360, 360)
DEFAULT_FPS: int = 12

# Default CJK font search paths (en order de preferencia)
_DEFAULT_CJK_FONTS: tuple[str, ...] = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _require_pil():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        return True
    except ImportError as e:
        raise ImportError(
            "qix_ble.patterns requires Pillow + numpy. Install via: "
            "pip install 'qix-ble[media]'"
        ) from e


def _require_numpy():
    try:
        import numpy as _np  # noqa: F401
        return True
    except ImportError as e:
        raise ImportError(
            "Plasma/wave patterns require numpy. Install via: "
            "pip install 'qix-ble[media]'"
        ) from e


def _load_font(font_path: Optional[str], font_size: int):
    from PIL import ImageFont
    if font_path:
        try:
            return ImageFont.truetype(font_path, font_size)
        except (OSError, IOError):
            pass
    for fp in _DEFAULT_CJK_FONTS:
        try:
            return ImageFont.truetype(fp, font_size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# ═════════════════════════════════════════════════════════════════════════════
# Text animation frames
# ═════════════════════════════════════════════════════════════════════════════

TextMode = Literal["scroll", "static", "pulse", "wave"]


def text_frames(text: str, *,
                mode: TextMode = "scroll",
                target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                fps: int = DEFAULT_FPS,
                duration: float = 5.0,
                font_size: int = 64,
                font_color: tuple[int, int, int] = (255, 255, 255),
                bg_color: tuple[int, int, int] = (0, 0, 0),
                font_path: Optional[str] = None,
                bold: bool = False) -> list:
    """Generate text animation frames. Returns list[PIL.Image].

    modes:
      scroll : right-to-left tickertape (default)
      static : centered, no movement
      pulse  : centered + sinusoidal scale 0.9..1.2
      wave   : centered + vertical sin-wave displacement
    """
    _require_pil()
    from PIL import Image, ImageDraw

    font = _load_font(font_path, font_size)
    w, h = target_size
    dummy = Image.new("RGB", (1, 1))
    bbox = ImageDraw.Draw(dummy).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_y_off = bbox[1]
    n = max(1, int(duration * fps))
    sw = 2 if bold else 0
    frames = []

    if mode == "static":
        img = Image.new("RGB", (w, h), bg_color)
        ImageDraw.Draw(img).text(
            ((w - tw) // 2 - bbox[0], (h - th) // 2 - text_y_off),
            text, fill=font_color, font=font,
            stroke_width=sw, stroke_fill=font_color,
        )
        return [img.copy() for _ in range(n)]

    if mode == "scroll":
        total = w + tw
        y = (h - th) // 2 - text_y_off
        for i in range(n):
            progress = i / max(n - 1, 1)
            x = int(w - total * progress)
            img = Image.new("RGB", (w, h), bg_color)
            ImageDraw.Draw(img).text((x, y), text, fill=font_color, font=font,
                                      stroke_width=sw, stroke_fill=font_color)
            frames.append(img)
        return frames

    if mode == "pulse":
        sprite = Image.new("RGBA", (tw + 4, th + 4), (0, 0, 0, 0))
        ImageDraw.Draw(sprite).text((-bbox[0], -text_y_off), text,
                                     fill=font_color, font=font)
        for i in range(n):
            t = i / max(n - 1, 1)
            scale = 0.9 + 0.3 * math.sin(t * math.pi * 2)
            sx = max(1, int(sprite.width * scale))
            sy = max(1, int(sprite.height * scale))
            scaled = sprite.resize((sx, sy), Image.Resampling.LANCZOS)
            img = Image.new("RGB", (w, h), bg_color)
            img.paste(scaled, ((w - sx) // 2, (h - sy) // 2), scaled)
            frames.append(img)
        return frames

    if mode == "wave":
        sprite = Image.new("RGBA", (tw + 4, th + 4), (0, 0, 0, 0))
        ImageDraw.Draw(sprite).text((-bbox[0], -text_y_off), text,
                                     fill=font_color, font=font)
        amp = 40
        for i in range(n):
            t = i / max(n - 1, 1)
            dy = int(amp * math.sin(t * math.pi * 4))
            img = Image.new("RGB", (w, h), bg_color)
            img.paste(sprite, ((w - sprite.width) // 2,
                               (h - sprite.height) // 2 + dy), sprite)
            frames.append(img)
        return frames

    raise ValueError(f"unknown text mode: {mode!r}")


# ═════════════════════════════════════════════════════════════════════════════
# Simple pattern frames (port de community image_converter.py)
# ═════════════════════════════════════════════════════════════════════════════

SimplePattern = Literal["gradient", "pulse", "checker", "rainbow", "wave"]


def simple_pattern_frames(pattern: SimplePattern, *,
                          target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                          frame_count: int = 60,
                          color1: tuple[int, int, int] = (255, 0, 0),
                          color2: tuple[int, int, int] = (0, 0, 255)) -> list:
    """Simple math-based pattern frames (PIL drawing). Returns list[PIL.Image]."""
    _require_pil()
    from PIL import Image, ImageDraw

    w, h = target_size
    frames = []

    for i in range(frame_count):
        t = i / max(frame_count - 1, 1)
        img = Image.new("RGB", (w, h), (0, 0, 0))

        if pattern == "gradient":
            draw = ImageDraw.Draw(img)
            for y in range(h):
                ratio = ((y / h) + t) % 1.0
                c = (
                    int(color1[0] * (1 - ratio) + color2[0] * ratio),
                    int(color1[1] * (1 - ratio) + color2[1] * ratio),
                    int(color1[2] * (1 - ratio) + color2[2] * ratio),
                )
                draw.line([(0, y), (w, y)], fill=c)

        elif pattern == "pulse":
            b = (math.sin(t * math.pi * 2) + 1) / 2
            img = Image.new("RGB", (w, h),
                            (int(color1[0] * b),
                             int(color1[1] * b),
                             int(color1[2] * b)))

        elif pattern == "checker":
            draw = ImageDraw.Draw(img)
            cell = max(1, w // 8)
            off = int(t * cell)
            for cy in range(-1, h // cell + 2):
                for cx in range(-1, w // cell + 2):
                    c = color1 if (cx + cy) % 2 == 0 else color2
                    draw.rectangle([cx * cell + off, cy * cell + off,
                                    cx * cell + off + cell,
                                    cy * cell + off + cell], fill=c)

        elif pattern == "rainbow":
            import colorsys
            draw = ImageDraw.Draw(img)
            for y in range(h):
                hue = ((y / h) + t) % 1.0
                r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                draw.line([(0, y), (w, y)],
                          fill=(int(r * 255), int(g * 255), int(b * 255)))

        elif pattern == "wave":
            # Downsample para speed: 4× smaller buffer → nearest-resize.
            step = 4
            sw, sh = max(1, w // step), max(1, h // step)
            small = Image.new("RGB", (sw, sh))
            px = small.load()
            for x in range(sw):
                for y in range(sh):
                    val = (math.sin((x / sw + t) * math.pi * 4) +
                           math.sin((y / sh + t) * math.pi * 4)) / 2
                    br = (val + 1) / 2
                    px[x, y] = (
                        int(color1[0] * br + color2[0] * (1 - br)),
                        int(color1[1] * br + color2[1] * (1 - br)),
                        int(color1[2] * br + color2[2] * (1 - br)),
                    )
            img = small.resize((w, h), Image.Resampling.NEAREST)

        else:
            raise ValueError(
                f"unknown simple pattern: {pattern!r} "
                "(choices: gradient, pulse, checker, rainbow, wave)"
            )

        frames.append(img)
    return frames


# ═════════════════════════════════════════════════════════════════════════════
# Numpy-vectorized patterns (port de web-bluetooth-e87 pattern-generators.ts)
# ═════════════════════════════════════════════════════════════════════════════


def plasma_waves(target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                 frame_count: int = 60,
                 *, circular_mask: bool = True) -> list:
    """Sinusoidal plasma. Port de pattern-generators.ts:generatePlasmaWaves.

    Vectorizado con numpy — 360×360×60 frames generan rápido.
    """
    _require_pil()
    _require_numpy()
    import numpy as np
    from PIL import Image

    w, h = target_size
    xs = np.arange(w, dtype=np.float32)
    ys = np.arange(h, dtype=np.float32)
    X, Y = np.meshgrid(xs, ys)
    nx = X / w - 0.5
    ny = Y / h - 0.5
    rad = np.sqrt(nx * nx + ny * ny)

    if circular_mask:
        half = w / 2
        cx = X - half
        cy = Y - h / 2
        dist_from_center = np.sqrt(cx * cx + cy * cy)
        radius = min(w, h) / 2 - 2
        mask = dist_from_center <= radius
    else:
        mask = None

    TAU = 2 * math.pi
    frames = []
    for f in range(frame_count):
        t = (f / frame_count) * TAU
        v1 = np.sin(X * 0.03 + t * 2.5)
        v2 = np.sin(Y * 0.04 - t * 1.8)
        v3 = np.sin((X + Y) * 0.02 + t * 3.0)
        v4 = np.sin(rad * 20 - t * 4)
        v = (v1 + v2 + v3 + v4) / 4

        r = ((np.sin(v * math.pi) * 0.5 + 0.5) * 120).astype(np.uint8)
        g = ((np.sin(v * math.pi + 2) * 0.5 + 0.5) * 255).astype(np.uint8)
        b = ((np.sin(v * math.pi + 4) * 0.5 + 0.5) * 255).astype(np.uint8)
        rgb = np.stack([r, g, b], axis=-1)

        if mask is not None:
            rgb = np.where(mask[..., None], rgb, 0).astype(np.uint8)

        frames.append(Image.fromarray(rgb, mode="RGB"))
    return frames


def concentric_waves(target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                     frame_count: int = 60,
                     *, circular_mask: bool = True) -> list:
    """Multi-ring concentric waves con 3 colores. Port de generateConcentricWaves."""
    _require_pil()
    _require_numpy()
    import numpy as np
    from PIL import Image

    w, h = target_size
    half_w, half_h = w / 2, h / 2
    xs = np.arange(w, dtype=np.float32) - half_w
    ys = np.arange(h, dtype=np.float32) - half_h
    X, Y = np.meshgrid(xs, ys)
    dist = np.sqrt(X * X + Y * Y)
    radius = min(w, h) / 2 - 2
    mask = dist <= radius if circular_mask else None
    edge_fade = np.clip(1 - dist / radius, 0, 1)

    wave_colors = np.array([
        [0, 180, 255],   # cyan
        [80, 255, 200],  # mint
        [200, 100, 255], # violet
    ], dtype=np.float32)

    TAU = 2 * math.pi
    frames = []
    for f in range(frame_count):
        t = (f / frame_count) * TAU
        wave1 = np.sin(dist * 0.06 - t * 3) * 0.5 + 0.5
        wave2 = np.sin(dist * 0.04 - t * 2 + 1.5) * 0.5 + 0.5
        wave3 = np.sin(dist * 0.08 - t * 4 + 3.0) * 0.5 + 0.5

        rgb_f = (
            wave_colors[0][:, None, None] * wave1
            + wave_colors[1][:, None, None] * wave2
            + wave_colors[2][:, None, None] * wave3
        ) / 2  # divide by 2 per community
        rgb_f *= edge_fade
        rgb = np.transpose(rgb_f.clip(0, 255).astype(np.uint8), (1, 2, 0))

        if mask is not None:
            rgb = np.where(mask[..., None], rgb, 0).astype(np.uint8)

        frames.append(Image.fromarray(rgb, mode="RGB"))
    return frames


# ═════════════════════════════════════════════════════════════════════════════
# AVI bundling helpers
# ═════════════════════════════════════════════════════════════════════════════


def frames_to_avi(frames: list, *, fps: int = DEFAULT_FPS,
                  jpeg_quality: int = 80) -> bytes:
    """PIL.Image list → AVI MJPG bytes."""
    from qix_ble.image import encode_video_jpeg

    if not frames:
        raise ValueError("frames vacío")
    first = frames[0]
    w, h = first.size
    jpeg_frames = [encode_video_jpeg(f, quality=jpeg_quality) for f in frames]
    return build_mjpg_avi(jpeg_frames, width=w, height=h, fps=fps)


def prepare_text(text: str, *, mode: TextMode = "scroll",
                 target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                 fps: int = DEFAULT_FPS,
                 duration: float = 5.0,
                 font_size: int = 64,
                 font_color: tuple[int, int, int] = (255, 255, 255),
                 bg_color: tuple[int, int, int] = (0, 0, 0),
                 font_path: Optional[str] = None,
                 bold: bool = False,
                 jpeg_quality: int = 80) -> bytes:
    """Text animation → AVI MJPG bytes."""
    frames = text_frames(text, mode=mode, target_size=target_size, fps=fps,
                         duration=duration, font_size=font_size,
                         font_color=font_color, bg_color=bg_color,
                         font_path=font_path, bold=bold)
    return frames_to_avi(frames, fps=fps, jpeg_quality=jpeg_quality)


def prepare_pattern(pattern: str, *,
                    target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
                    fps: int = DEFAULT_FPS,
                    frame_count: int = 60,
                    color1: tuple[int, int, int] = (255, 0, 0),
                    color2: tuple[int, int, int] = (0, 0, 255),
                    jpeg_quality: int = 80) -> bytes:
    """Pattern animation → AVI MJPG bytes.

    Acepta tanto simple patterns (gradient/pulse/checker/rainbow/wave) como
    los vectorizados con numpy (plasma_waves, concentric_waves).
    """
    if pattern in ("gradient", "pulse", "checker", "rainbow", "wave"):
        frames = simple_pattern_frames(pattern, target_size=target_size,
                                        frame_count=frame_count,
                                        color1=color1, color2=color2)
    elif pattern == "plasma_waves":
        frames = plasma_waves(target_size=target_size, frame_count=frame_count)
    elif pattern == "concentric_waves":
        frames = concentric_waves(target_size=target_size, frame_count=frame_count)
    else:
        raise ValueError(
            f"unknown pattern: {pattern!r}. Choices: gradient, pulse, "
            "checker, rainbow, wave, plasma_waves, concentric_waves"
        )
    return frames_to_avi(frames, fps=fps, jpeg_quality=jpeg_quality)
