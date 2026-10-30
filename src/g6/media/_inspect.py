# SPDX-License-Identifier: Apache-2.0
"""The media family's inspectors: read a file a backend wrote and measure it against what was asked for.

``_``-prefixed, so it never registers as a component. Every media op runs one of these on the file it wrote, from disk,
before it returns anything; a file that fails is a refusal (:func:`g6.media._types.refuse`) with the failed check's
code, never a result.

* :func:`inspect_image` (Pillow, the ``image`` extra): the file exists and is not empty (``G6_E_MEDIA_NO_OUTPUT``); it
  is one of the formats asked for, opened only by those formats' readers, so a file another program would render (an
  EPS, a WMF) is never decoded (``_FORMAT_MISMATCH`` when it is another format of :data:`READABLE`, ``_UNDECODABLE``
  otherwise); it decodes in full (``_UNDECODABLE``); its width and height are exactly the ones asked for
  (``_SIZE_MISMATCH``); and it is not blank (``_BLANK``): at least
  :data:`MIN_CONTENT_FRACTION` of its pixels differ from its most common colour by more than :data:`COLOUR_TOLERANCE`
  in some channel, alpha included, so a uniform image, a fully transparent one or one with a few stray pixels fails at
  any size. A picture longer than :data:`BLANK_SIDE` on a side is measured on a copy reduced by a whole factor (each
  pixel the mean of a factor-by-factor block), so the check never holds a full-size copy; a caller's own picture is
  refused unread above :data:`MAX_INPUT_PIXELS`.
* :func:`inspect_svg` (standard library): it exists, parses as XML with no document type or entities, its root is an
  ``svg`` element, its ``width`` and ``height`` are the ones asked for, and it draws at least one shape with extent
  (a rectangle covering the whole picture is its background, not a shape).
* :func:`inspect_mesh` (standard library): an OBJ file exists and parses (finite vertices, faces whose indices are in
  range), has the vertex and face counts asked for, and is not degenerate: its bounding box has extent along at least
  ``min_axes`` axes.

What none of them can judge: whether the file shows what the caller wanted. They measure; they do not look.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from g6.media._types import (
    BLANK,
    EXTRA_MISSING,
    FORMAT_MISMATCH,
    NO_OUTPUT,
    SIZE_MISMATCH,
    UNDECODABLE,
    Check,
    Inspection,
)

#: A pixel counts as content when some channel differs from the most common colour by more than this (0 to 255).
COLOUR_TOLERANCE = 8
#: The share of pixels that must be content for an image not to be blank (0.1 %).
MIN_CONTENT_FRACTION = 0.001
#: The largest image the inspector decodes (pixels), and the largest file it reads (bytes).
MAX_PIXELS = 64_000_000
MAX_BYTES = 256 * 1024 * 1024
#: The largest picture a caller may give a media op, or ask an edit to make: 4096 x 4096 pixels (in any shape), the
#: most the family's generators make. Above it, a picture is refused from its header, before it is decoded.
INPUT_SIDE = 4096
MAX_INPUT_PIXELS = INPUT_SIDE * INPUT_SIDE
#: The blank check measures a copy no longer than this on a side, so its memory is bounded whatever the picture.
BLANK_SIDE = 1024
#: An extent smaller than this along an axis counts as none.
MIN_EXTENT = 1e-9
#: The only formats the family ever decodes. Pillow reads others (EPS, WMF and EMF among them) by handing the bytes to
#: a program outside it, Ghostscript or the operating system's renderer, so those are never opened, whoever sent them.
READABLE = ("PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF")
READABLE_NAMES = f"{', '.join(READABLE[:-1])} or {READABLE[-1]}"

_PILLOW = "Pillow is not installed, so the image cannot be inspected: pip install 'g6[image] @ git+https://github.com/basicisabc1994/g6solver'"


def pillow() -> Any:
    """The Pillow ``Image`` module, or ``None`` when the ``image`` extra is not installed."""
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image


def _exists(path: Path) -> Check:
    if not path.is_file():
        return Check(name="exists", passed=False, detail=f"no file at {path}", code=NO_OUTPUT)
    size = path.stat().st_size
    if size == 0:
        return Check(name="exists", passed=False, detail="the file is empty", code=NO_OUTPUT)
    if size > MAX_BYTES:
        return Check(name="exists", passed=False, detail=f"{size} bytes is more than the inspector reads ({MAX_BYTES})",
                     code=UNDECODABLE)
    return Check(name="exists", passed=True, detail=f"{size} bytes")


def _size(found: tuple[int, int], width: int | None, height: int | None) -> Check | None:
    if width is None and height is None:
        return None
    want = (width if width is not None else found[0], height if height is not None else found[1])
    if found != want:
        return Check(name="size_matches_request", passed=False, code=SIZE_MISMATCH,
                     detail=f"{found[0]}x{found[1]} pixels, not the {want[0]}x{want[1]} asked for")
    return Check(name="size_matches_request", passed=True, detail=f"{found[0]}x{found[1]} pixels, as asked")


def _not_asked_for(path: Path, Image: Any, allowed: tuple[str, ...]) -> tuple[Check, str]:
    """Why a file none of the readers asked for opened is refused: ``_FORMAT_MISMATCH`` naming its format when it is
    another one G6 reads (from its header; its pixels are not decoded), otherwise ``_UNDECODABLE``."""
    try:
        with Image.open(path, formats=READABLE) as other:
            fmt = other.format or ""
    except Exception:  # noqa: BLE001 - whatever Pillow raised, it is not a picture G6 reads
        return Check(name="decodes", passed=False, code=UNDECODABLE,
                     detail=f"not a picture in a format G6 reads ({READABLE_NAMES})"), ""
    return Check(name="format_matches_request", passed=False, code=FORMAT_MISMATCH,
                 detail=f"the bytes are {fmt}, not {' or '.join(allowed)}"), Image.MIME.get(fmt, "")


def _most_common(histogram: list[int]) -> int:
    return max(range(256), key=histogram.__getitem__)


def _blank(image: Any, Image: Any) -> tuple[Check, dict[str, Any]]:
    """Whether at least MIN_CONTENT_FRACTION of the pixels differ from the background, alpha included. The background
    is the most common value of each channel; when one colour fills nearly the whole image, that is the colour. A
    picture longer than BLANK_SIDE on a side is measured on a copy reduced by the smallest whole factor that brings it
    within BLANK_SIDE, each pixel the mean of a factor-by-factor block (a colour weighed by its alpha), so no copy the
    check makes is larger than that."""
    from PIL import ImageChops

    bands = "RGBA" if ("A" in image.getbands() or "transparency" in image.info) else "RGB"
    pixels = image if image.mode == bands else image.convert(bands)
    factor = -(-max(pixels.size) // BLANK_SIDE)
    if factor > 1:
        pixels = pixels.reduce(factor)
    if bands == "RGBA":  # what a transparent pixel hides is not content: weigh each colour by its own alpha
        pixels = Image.composite(pixels, Image.new("RGBA", pixels.size, (0, 0, 0, 0)), pixels.getchannel("A"))
    total = pixels.width * pixels.height
    background = tuple(_most_common(band.histogram()) for band in pixels.split())
    differ = ImageChops.difference(pixels, Image.new(bands, pixels.size, background))
    mask = None
    for band in differ.split():
        over = band.point(lambda v: 255 if v > COLOUR_TOLERANCE else 0)
        mask = over if mask is None else ImageChops.lighter(mask, over)
    content = mask.histogram()[255] if mask is not None else 0
    fraction = content / total
    measured: dict[str, Any] = {"background": list(background), "content_fraction": round(fraction, 6)}
    seen = ""
    if factor > 1:
        measured["content_measured_on"] = [pixels.width, pixels.height]
        seen = f" (measured on a copy reduced {factor} times, {pixels.width}x{pixels.height})"
    if bands == "RGBA" and pixels.getextrema()[3][1] == 0:
        return Check(name="not_blank", passed=False, code=BLANK, detail="every pixel is fully transparent"), measured
    if fraction < MIN_CONTENT_FRACTION:
        detail = (f"only {content} of {total} pixels ({fraction:.4%}) differ from the background {background} by more "
                  f"than {COLOUR_TOLERANCE} in any channel; the floor is {MIN_CONTENT_FRACTION:.1%}{seen}")
        return Check(name="not_blank", passed=False, code=BLANK, detail=detail), measured
    return Check(name="not_blank", passed=True,
                 detail=f"{fraction:.2%} of the pixels differ from the background {background}{seen}"), measured


def inspect_image(path: Path | str, *, width: int | None = None, height: int | None = None,
                  formats: Iterable[str] = ("PNG",), max_pixels: int = MAX_PIXELS) -> Inspection:
    """Measure the image at ``path`` against the ``width``, ``height`` and ``formats`` (Pillow format names) asked
    for; one of more than ``max_pixels`` (:data:`MAX_INPUT_PIXELS` for a caller's own picture) is refused from its
    header. Without Pillow, the inspection fails with ``G6_E_MEDIA_EXTRA_MISSING``: no image passes uninspected."""
    path, allowed = Path(path), tuple(f.upper() for f in formats)
    readers = [f for f in allowed if f in READABLE]  # never a reader that hands the bytes to another program
    Image = pillow()
    if Image is None:
        return Inspection(inspector="g6.media image inspector", kind="image",
                          checks=(Check(name="inspector_ready", passed=False, detail=_PILLOW, code=EXTRA_MISSING),))
    import PIL

    inspector = f"g6.media image inspector (Pillow {PIL.__version__})"
    checks = [_exists(path)]
    measured: dict[str, Any] = {}
    if not checks[0].passed:
        return Inspection(inspector=inspector, kind="image", checks=tuple(checks))
    try:
        with Image.open(path, formats=readers) as image:
            fmt, size = image.format or "", image.size
            if size[0] * size[1] > max_pixels:
                said = "a caller may give" if max_pixels == MAX_INPUT_PIXELS else "the inspector decodes"
                raise ValueError(f"{size[0]}x{size[1]} pixels is more than {said} ({max_pixels})")
            image.verify()
        image = Image.open(path, formats=readers)
        image.load()
    except Image.UnidentifiedImageError:
        failed, mime = _not_asked_for(path, Image, allowed)
        return Inspection(inspector=inspector, kind="image", checks=(*checks, failed), mime=mime)
    except Exception as exc:  # noqa: BLE001 - whatever Pillow raises, the file did not decode
        checks.append(Check(name="decodes", passed=False, code=UNDECODABLE,
                            detail=f"{type(exc).__name__}: {exc}"[:500]))
        return Inspection(inspector=inspector, kind="image", checks=tuple(checks))
    with image:
        measured = {"width": size[0], "height": size[1], "mode": image.mode, "format": fmt,
                    "frames": int(getattr(image, "n_frames", 1))}
        checks.append(Check(name="decodes", passed=True, detail=f"a {fmt} image, mode {image.mode}"))
        mime = Image.MIME.get(fmt, "")
        if fmt.upper() not in allowed:
            checks.append(Check(name="format_matches_request", passed=False, code=FORMAT_MISMATCH,
                                detail=f"the bytes are {fmt or 'an unknown format'}, not {' or '.join(allowed)}"))
            return Inspection(inspector=inspector, kind="image", checks=tuple(checks), measured=measured, mime=mime)
        checks.append(Check(name="format_matches_request", passed=True, detail=f"{fmt}, as asked"))
        sized = _size(size, width, height)
        if sized is not None:
            checks.append(sized)
        try:
            blank, seen = _blank(image, Image)
        except Exception as exc:  # noqa: BLE001 - a file that cannot be measured is never passed
            blank, seen = Check(name="not_blank", passed=False, code=UNDECODABLE,
                                detail=f"could not be measured: {type(exc).__name__}: {exc}"[:500]), {}
        checks.append(blank)
        measured.update(seen)
    return Inspection(inspector=inspector, kind="image", checks=tuple(checks), measured=measured, mime=mime)


_SVG = "{http://www.w3.org/2000/svg}"
_SHAPES = ("path", "polyline", "polygon", "line", "rect", "circle", "ellipse")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _length(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+))(?:px)?\s*", value)
    return float(match.group(1)) if match else None


def _extent(element: ET.Element) -> float:
    """The larger side of a shape's bounding box, from its own attributes."""
    tag = element.tag.removeprefix(_SVG)
    get = element.attrib.get
    if tag in ("rect", "ellipse", "circle"):
        sides = [_length(get("width")), _length(get("height"))] if tag == "rect" else (
            [_length(get("r"))] if tag == "circle" else [_length(get("rx")), _length(get("ry"))])
        return max((abs(s) for s in sides if s is not None), default=0.0)
    if tag == "line":
        ends = [_length(get(k)) or 0.0 for k in ("x1", "y1", "x2", "y2")]
        return max(abs(ends[2] - ends[0]), abs(ends[3] - ends[1]))
    numbers = [float(n) for n in _NUMBER.findall(get("d" if tag == "path" else "points", ""))]
    xs, ys = numbers[0::2], numbers[1::2]
    if not xs or not ys:
        return 0.0
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _background(element: ET.Element, size: tuple[float | None, float | None]) -> bool:
    """A rectangle at the origin covering the whole picture: a background, not something drawn."""
    if element.tag.removeprefix(_SVG) != "rect":
        return False
    get = element.attrib.get
    w, h = _length(get("width")), _length(get("height"))
    return ((_length(get("x")) or 0.0) <= 0 and (_length(get("y")) or 0.0) <= 0 and w is not None and h is not None
            and size[0] is not None and size[1] is not None and w >= size[0] and h >= size[1])


def inspect_svg(path: Path | str, *, width: int | None = None, height: int | None = None) -> Inspection:
    """Measure the SVG at ``path`` against the ``width`` and ``height`` asked for (standard library only)."""
    path, inspector = Path(path), "g6.media SVG inspector (xml.etree)"
    checks = [_exists(path)]
    if not checks[0].passed:
        return Inspection(inspector=inspector, kind="svg", checks=tuple(checks))
    text = path.read_text(encoding="utf-8", errors="replace")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        checks.append(Check(name="parses", passed=False, code=UNDECODABLE,
                            detail="the file declares a document type or entities, which the inspector refuses"))
        return Inspection(inspector=inspector, kind="svg", checks=tuple(checks))
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        checks.append(Check(name="parses", passed=False, code=UNDECODABLE, detail=f"not XML: {exc}"[:500]))
        return Inspection(inspector=inspector, kind="svg", checks=tuple(checks))
    checks.append(Check(name="parses", passed=True, detail="well-formed XML"))
    if root.tag not in (f"{_SVG}svg", "svg"):
        checks.append(Check(name="format_matches_request", passed=False, code=FORMAT_MISMATCH,
                            detail=f"the root element is {root.tag!r}, not svg"))
        return Inspection(inspector=inspector, kind="svg", checks=tuple(checks))
    checks.append(Check(name="format_matches_request", passed=True, detail="an svg root element"))
    found = (_length(root.attrib.get("width")), _length(root.attrib.get("height")))
    measured: dict[str, Any] = {"width": found[0], "height": found[1]}
    if width is not None or height is not None:
        want = (float(width) if width is not None else found[0], float(height) if height is not None else found[1])
        ok = found == want
        checks.append(Check(name="size_matches_request", passed=ok, code="" if ok else SIZE_MISMATCH,
                            detail=f"width {found[0]} and height {found[1]}" + ("" if ok else f", not {want}")))
    shapes = [e for e in root.iter() if e.tag.removeprefix(_SVG) in _SHAPES and not _background(e, found)]
    drawn = sum(1 for e in shapes if _extent(e) > MIN_EXTENT)
    measured.update({"shapes": len(shapes), "shapes_with_extent": drawn})
    checks.append(Check(name="not_blank", passed=drawn > 0, code="" if drawn else BLANK,
                        detail=f"{drawn} of {len(shapes)} shapes have extent"))
    return Inspection(inspector=inspector, kind="svg", checks=tuple(checks), measured=measured, mime="image/svg+xml")


def _parse_obj(text: str) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]:
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    for number, line in enumerate(text.splitlines(), 1):
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == "v":
            values = tuple(float(p) for p in parts[1:4])
            if len(values) != 3 or not all(math.isfinite(v) for v in values):
                raise ValueError(f"line {number}: a vertex needs three finite numbers")
            vertices.append(values)  # type: ignore[arg-type]
        elif parts[0] == "f":
            index = tuple(int(p.split("/", 1)[0]) for p in parts[1:])
            if len(index) < 3 or any(i < 1 or i > len(vertices) for i in index):
                raise ValueError(f"line {number}: a face needs three or more indices of vertices already listed")
            faces.append(index)
    return vertices, faces


def inspect_mesh(path: Path | str, *, vertices: int | None = None, faces: int | None = None,
                 min_axes: int = 2) -> Inspection:
    """Measure the OBJ mesh at ``path`` against the ``vertices`` and ``faces`` counts asked for; it must have extent
    along at least ``min_axes`` axes (3 for a heightmap, which is flat otherwise)."""
    path, inspector = Path(path), "g6.media mesh inspector (OBJ)"
    checks = [_exists(path)]
    if not checks[0].passed:
        return Inspection(inspector=inspector, kind="mesh", checks=tuple(checks))
    try:
        found_v, found_f = _parse_obj(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        checks.append(Check(name="parses", passed=False, code=UNDECODABLE, detail=f"not an OBJ mesh: {exc}"[:500]))
        return Inspection(inspector=inspector, kind="mesh", checks=tuple(checks))
    ok = bool(found_v) and bool(found_f)
    checks.append(Check(name="parses", passed=ok, code="" if ok else NO_OUTPUT,
                        detail=f"{len(found_v)} vertices and {len(found_f)} faces"))
    measured: dict[str, Any] = {"vertices": len(found_v), "faces": len(found_f)}
    if not ok:
        return Inspection(inspector=inspector, kind="mesh", checks=tuple(checks), measured=measured)
    for name, want, got in (("vertices_match_request", vertices, len(found_v)), ("faces_match_request", faces,
                                                                                 len(found_f))):
        if want is not None:
            checks.append(Check(name=name, passed=got == want, code="" if got == want else SIZE_MISMATCH,
                                detail=f"{got}" + ("" if got == want else f", not the {want} asked for")))
    extent = [max(v[a] for v in found_v) - min(v[a] for v in found_v) for a in range(3)]
    axes = sum(1 for e in extent if e > MIN_EXTENT)
    measured["extent"] = [round(e, 6) for e in extent]
    checks.append(Check(name="not_degenerate", passed=axes >= min_axes, code="" if axes >= min_axes else BLANK,
                        detail=f"extent along {axes} axes (needs {min_axes}): {measured['extent']}"))
    return Inspection(inspector=inspector, kind="mesh", checks=tuple(checks), measured=measured, mime="model/obj")


__all__ = ["BLANK_SIDE", "COLOUR_TOLERANCE", "INPUT_SIDE", "MAX_BYTES", "MAX_INPUT_PIXELS", "MAX_PIXELS",
           "MIN_CONTENT_FRACTION", "MIN_EXTENT", "READABLE", "READABLE_NAMES", "inspect_image", "inspect_mesh",
           "inspect_svg", "pillow"]
