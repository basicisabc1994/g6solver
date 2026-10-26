# SPDX-License-Identifier: Apache-2.0
"""The picture ctx_vision shows a model: read from a file G6 owns or from bytes given, and decoded before it is sent.

A picture comes as ``image``, the plain name of a file in G6's media folder (``$G6_HOME/media``, where the media family
writes its pictures), or as ``image_base64``. A name with a folder in it, a link (symbolic, a reparse point or a second
hard link) or a file outside the folder is refused; so is anything over :data:`MAX_BYTES`. Never a URL: nothing is
fetched. :func:`load` then opens the picture with Pillow (the ``image`` extra) asked for PNG, JPEG, GIF and WebP only,
so no other format's decoder is reached (Pillow hands an EPS picture to Ghostscript, a program on this machine), and
decodes it in full; one that is not of those formats, does not decode, or has more than :data:`MAX_PIXELS` pixels is
refused before any model sees it.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import stat
import warnings
from dataclasses import dataclass
from pathlib import Path

from g6.core.llm import MAX_IMAGE_BYTES
from g6.core.llm import Image as ModelImage
from g6.core.storage import state_path

MAX_BYTES = MAX_IMAGE_BYTES
MAX_PIXELS = 40_000_000
FORMATS = {"PNG": "png", "JPEG": "jpeg", "GIF": "gif", "WEBP": "webp"}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class ImageRefused(Exception):
    """A picture ctx_vision will not send, with its code and the reason."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason = code, reason


@dataclass(frozen=True)
class Picture:
    """A decoded picture: its bytes, format, size and hash, and the image the model is sent."""

    data: bytes
    format: str
    width: int
    height: int
    sha256: str
    named: str

    def for_model(self) -> ModelImage:
        return ModelImage.from_bytes(self.data)

    def describe(self) -> dict[str, object]:
        return {"format": self.format, "width": self.width, "height": self.height, "bytes": len(self.data),
                "sha256": self.sha256, "from": self.named}


def media_folder() -> Path:
    """G6's media folder, where pictures are read from by name."""
    return state_path("media")


def pillow() -> object | None:
    try:
        import PIL.Image
    except ImportError:
        return None
    return PIL.Image


def _from_folder(name: str) -> bytes:
    if not _NAME.fullmatch(name) or ".." in name:
        raise ImageRefused("G6_E_VISION_INPUT", f"{name!r} is not a plain file name (letters, digits, '.', '_' and "
                                                "'-'; no folder)")
    root = media_folder()
    path = root / name
    try:
        found = path.lstat()
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ImageRefused("G6_E_VISION_INPUT", f"{name!r} is not a file in G6's media folder ({root})") from None
    linked = (stat.S_ISLNK(found.st_mode) or getattr(found, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
              or found.st_nlink > 1)
    if linked or not stat.S_ISREG(found.st_mode) or real.parent != root.resolve():
        raise ImageRefused("G6_E_VISION_INPUT", f"{name!r} is a link or not a plain file kept in G6's media folder: "
                                                "copy the picture in")
    if found.st_size > MAX_BYTES:
        raise ImageRefused("G6_E_VISION_TOO_LARGE", f"{name!r} is over {MAX_BYTES} bytes")
    return path.read_bytes()


def _from_base64(text: str) -> bytes:
    if len(text) > (MAX_BYTES * 4) // 3 + 8:
        raise ImageRefused("G6_E_VISION_TOO_LARGE", f"image_base64 decodes to more than {MAX_BYTES} bytes")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ImageRefused("G6_E_VISION_INPUT", "image_base64 is not valid base64") from None


def load(name: str = "", inline: str = "") -> Picture:
    """The picture named or given, decoded in full; :class:`ImageRefused` for anything that may not be sent."""
    if bool(name) == bool(inline):
        raise ImageRefused("G6_E_VISION_INPUT", "give the picture as image (a file in G6's media folder) or "
                                                "image_base64, one of them")
    module = pillow()
    if module is None:
        raise ImageRefused("G6_E_VISION_EXTRA_MISSING", "Pillow is not installed, so no picture can be decoded and "
                                                        "checked before it is sent: pip install 'g6[image] @ git+https://github.com/basicisabc1994/g6solver'")
    data = _from_folder(name) if name else _from_base64(inline)
    if not data:
        raise ImageRefused("G6_E_VISION_INPUT", "the picture is empty")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", module.DecompressionBombWarning)   # a refusal here, not a warning
            with module.open(io.BytesIO(data), formats=list(FORMATS)) as probe:
                width, height, kind = probe.width, probe.height, str(probe.format or "")
                if kind not in FORMATS:
                    raise ImageRefused("G6_E_VISION_FORMAT", f"the picture is {kind or 'of no known format'}; a model "
                                                             "is sent PNG, JPEG, GIF or WebP")
                if width * height > MAX_PIXELS:
                    raise ImageRefused("G6_E_VISION_TOO_LARGE", f"the picture is {width}x{height}, more than "
                                                                f"{MAX_PIXELS} pixels")
                probe.load()
    except ImageRefused:
        raise
    except module.UnidentifiedImageError:
        raise ImageRefused("G6_E_VISION_FORMAT", "Pillow, asked for PNG, JPEG, GIF and WebP only, does not recognise "
                                                 "the picture as any of them; no other format is opened") from None
    except Exception as exc:  # noqa: BLE001 - any failure to decode is a picture that is not sent
        raise ImageRefused("G6_E_VISION_UNDECODABLE", f"the picture does not decode: {type(exc).__name__}: "
                                                      f"{str(exc)[:200]}") from None
    return Picture(data, FORMATS[kind], width, height, hashlib.sha256(data).hexdigest(),
                   f"media/{name}" if name else "image_base64")


__all__ = ["FORMATS", "MAX_BYTES", "MAX_PIXELS", "ImageRefused", "Picture", "load", "media_folder", "pillow"]
