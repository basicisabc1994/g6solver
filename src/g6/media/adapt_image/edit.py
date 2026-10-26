# SPDX-License-Identifier: Apache-2.0
"""The Pillow core: read a picture strictly, change it, and say exactly what size the result must be.

* :func:`decode_base64` is the size guard on a picture sent inline: a payload whose decoded size would pass
  :data:`MAX_BYTES` is refused before it is decoded, and malformed base64 is refused, not repaired. A ``data:`` URL
  is read for its base64 part.
* :func:`open_strict` opens the bytes with the readers of the formats G6 handles only
  (:data:`~g6.media._inspect.READABLE`), so a picture another program would render (an EPS for Ghostscript, a WMF for
  the system's renderer) is refused unread; it refuses a picture of more than
  :data:`~g6.media._inspect.MAX_INPUT_PIXELS` (4096 x 4096) from its header, before decoding it, verifies the container
  and decodes the pixels in full (for an animation, its first frame), so a truncated or corrupt file is refused, not
  half-read. :func:`info` measures one (format and MIME type from the bytes, size, mode, frames, SHA-256).
* Each op returns the new picture and the size it must have, which the inspector then compares with the file it
  wrote: ``resize`` (Lanczos, one side given keeps the aspect ratio, never to more than
  :data:`~g6.media._inspect.MAX_INPUT_PIXELS`), ``crop`` (a box inside the picture: Pillow would
  pad a box outside it with black, so that is refused), ``rotate`` (a right angle turns exactly; any other angle
  turns on a canvas grown to hold it, whose size is not compared), ``flip``, ``adjust_colour`` (brightness, contrast
  and saturation as factors, 1 for no change), ``composite`` (an overlay placed on the picture) and ``convert``.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
from typing import Any

from g6.media._inspect import INPUT_SIDE, MAX_INPUT_PIXELS, READABLE, READABLE_NAMES
from g6.media._types import INPUT_INVALID, UNDECODABLE, MediaError

#: The largest picture accepted inline, decoded (25 MiB).
MAX_BYTES = 25 * 1024 * 1024
#: The base64 characters a payload of MAX_BYTES takes, with room for padding and line breaks.
MAX_BASE64_CHARS = MAX_BYTES * 4 // 3 + 1024
#: What a caller is told when a picture, given or asked for, is larger than that.
TOO_LARGE = f"more than a caller may give ({INPUT_SIDE}x{INPUT_SIDE}, {MAX_INPUT_PIXELS} pixels in any shape)"


def decode_base64(value: str, field: str) -> bytes:
    """The bytes ``value`` (base64, or a ``data:`` URL holding base64) carries; :class:`MediaError` when it is not
    strict base64 or would be larger than :data:`MAX_BYTES`."""
    raw = value.strip()
    if raw.startswith("data:"):
        header, sep, raw = raw.partition(",")
        if not sep or ";base64" not in header.lower():
            raise MediaError(INPUT_INVALID, f"{field} is a data URL without a base64 payload")
    raw = "".join(raw.split())
    if len(raw) * 3 // 4 > MAX_BYTES + 3:
        raise MediaError(INPUT_INVALID, f"{field} would decode to more than {MAX_BYTES} bytes")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaError(INPUT_INVALID, f"{field} is not valid base64: {exc}") from None
    if not data or len(data) > MAX_BYTES:
        raise MediaError(INPUT_INVALID, f"{field} decodes to {len(data)} bytes; it must be 1 to {MAX_BYTES}")
    return data


def _pillow() -> Any:
    from PIL import Image

    return Image


def open_strict(data: bytes, field: str) -> Any:
    """A Pillow image of ``data``, decoded in full; :class:`MediaError` (``G6_E_MEDIA_UNDECODABLE``) when it is not a
    picture in a format G6 reads, or is corrupt or truncated; (``G6_E_MEDIA_INPUT_INVALID``) when its header says it
    has more pixels than a caller may give, before it is decoded."""
    Image = _pillow()
    try:
        with Image.open(io.BytesIO(data), formats=READABLE) as probe:
            width, height = probe.size
            if width * height > MAX_INPUT_PIXELS:
                raise MediaError(INPUT_INVALID, f"{field} is {width}x{height} pixels, {TOO_LARGE}; make it smaller "
                                                "first")
            probe.verify()
        image = Image.open(io.BytesIO(data), formats=READABLE)
        image.load()
    except MediaError:
        raise
    except Image.UnidentifiedImageError:
        raise MediaError(UNDECODABLE, f"{field} is not a picture in a format G6 reads ({READABLE_NAMES})") from None
    except Exception as exc:  # noqa: BLE001 - whatever Pillow raises, the bytes are not a whole picture
        raise MediaError(UNDECODABLE, f"{field} does not decode as a picture: {type(exc).__name__}: {exc}"[:500]) \
            from None
    return image


def info(data: bytes, field: str) -> dict[str, Any]:
    """What the bytes are, measured: format, MIME type, width, height, mode, frames, size and SHA-256."""
    image = open_strict(data, field)
    Image = _pillow()
    with image:
        return {"format": image.format or "", "mime": Image.MIME.get(image.format or "", ""), "width": image.width,
                "height": image.height, "mode": image.mode, "frames": int(getattr(image, "n_frames", 1)),
                "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def resize(image: Any, width: int | None, height: int | None) -> tuple[Any, tuple[int, int]]:
    Image = _pillow()
    if width is None and height is None:
        raise MediaError(INPUT_INVALID, "resize needs width, height or both")
    w = width if width is not None else max(1, round(image.width * height / image.height))
    h = height if height is not None else max(1, round(image.height * width / image.width))
    if w * h > MAX_INPUT_PIXELS:
        raise MediaError(INPUT_INVALID, f"resize to {w}x{h} pixels is {TOO_LARGE}")
    return image.resize((w, h), Image.Resampling.LANCZOS), (w, h)


def crop(image: Any, box: tuple[int, int, int, int]) -> tuple[Any, tuple[int, int]]:
    left, top, right, bottom = box
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise MediaError(INPUT_INVALID, f"the box {list(box)} is not inside the {image.width}x{image.height} picture "
                                        "(left < right and top < bottom, within its edges)")
    return image.crop(box), (right - left, bottom - top)


def rotate(image: Any, angle: float) -> tuple[Any, tuple[int, int] | None]:
    """Counter-clockwise by ``angle`` degrees."""
    Image = _pillow()
    turns = angle / 90
    if turns == int(turns):
        quarter = int(turns) % 4
        if quarter == 0:
            return image.copy(), image.size
        method = {1: Image.Transpose.ROTATE_90, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_270}[quarter]
        return image.transpose(method), (image.height, image.width) if quarter % 2 else image.size
    source = image if image.mode in ("RGB", "RGBA", "L", "LA") else image.convert("RGBA")
    return source.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True), None


def flip(image: Any, direction: str) -> tuple[Any, tuple[int, int]]:
    Image = _pillow()
    method = Image.Transpose.FLIP_LEFT_RIGHT if direction == "horizontal" else Image.Transpose.FLIP_TOP_BOTTOM
    return image.transpose(method), image.size


def adjust_colour(image: Any, brightness: float, contrast: float, saturation: float) -> tuple[Any, tuple[int, int]]:
    from PIL import ImageEnhance

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    out = image.convert("RGB") if image.mode not in ("RGB", "L") else image
    for enhancer, factor in ((ImageEnhance.Brightness, brightness), (ImageEnhance.Contrast, contrast),
                             (ImageEnhance.Color, saturation)):
        if factor != 1.0:
            out = enhancer(out).enhance(factor)
    if alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return out, image.size


def composite(image: Any, overlay: Any, x: int, y: int) -> tuple[Any, tuple[int, int]]:
    Image = _pillow()
    base = image.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(overlay.convert("RGBA"), (x, y))
    return Image.alpha_composite(base, layer), image.size


def encode(image: Any, fmt: str, quality: int) -> bytes:
    """The picture written as ``fmt``: a JPEG cannot hold transparency, so a transparent picture is laid on white."""
    Image = _pillow()
    out = image
    if fmt == "jpeg":
        if "A" in out.getbands() or out.mode == "P":
            rgba = out.convert("RGBA")
            out = Image.new("RGB", rgba.size, (255, 255, 255))
            out.paste(rgba, mask=rgba.getchannel("A"))
        elif out.mode not in ("RGB", "L", "CMYK"):
            out = out.convert("RGB")
    elif fmt == "webp" and out.mode not in ("RGB", "RGBA"):
        out = out.convert("RGBA" if "A" in out.getbands() or out.mode == "P" else "RGB")
    elif fmt == "png" and out.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"):
        out = out.convert("RGBA")
    buffer = io.BytesIO()
    options = {"quality": quality} if fmt in ("jpeg", "webp") else {}
    out.save(buffer, format={"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[fmt], **options)
    return buffer.getvalue()


__all__ = ["MAX_BASE64_CHARS", "MAX_BYTES", "TOO_LARGE", "adjust_colour", "composite", "crop", "decode_base64",
           "encode", "flip", "info", "open_strict", "resize", "rotate"]
