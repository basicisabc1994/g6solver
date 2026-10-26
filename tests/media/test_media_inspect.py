# SPDX-License-Identifier: Apache-2.0
"""The media family's inspectors measure the file on disk against what was asked for, and every failure has its own
stable code: no file, a file that does not decode, the wrong format, the wrong size, or a blank one. The blank check is
proved general (D1): it catches a blank image whatever its colour, mode or transparency, and a few stray pixels, and it
passes sparse real content."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageChops, ImageDraw  # noqa: E402

from g6.media import _inspect  # noqa: E402
from g6.media._inspect import inspect_image, inspect_mesh, inspect_svg  # noqa: E402
from g6.media._types import (  # noqa: E402
    BLANK,
    EXTRA_MISSING,
    FORMAT_MISMATCH,
    NO_OUTPUT,
    SIZE_MISMATCH,
    UNDECODABLE,
)


def _drawn(path: Path, size: tuple[int, int] = (64, 48), fmt: str = "PNG") -> Path:
    image = Image.new("RGB", size, (10, 20, 30))
    ImageDraw.Draw(image).ellipse((8, 8, size[0] - 8, size[1] - 8), fill=(240, 200, 40))
    image.save(path, format=fmt)
    return path


def test_a_drawn_png_of_the_size_asked_for_passes_every_check(tmp_path: Path) -> None:
    found = inspect_image(_drawn(tmp_path / "ok.png"), width=64, height=48)
    assert found.passed and found.failure is None and found.mime == "image/png"
    assert [c.name for c in found.checks] == ["exists", "decodes", "format_matches_request", "size_matches_request",
                                              "not_blank"]
    assert found.measured["width"] == 64 and found.measured["height"] == 48 and found.measured["content_fraction"] > .1
    assert "Pillow" in found.inspector


def test_a_missing_or_empty_file_is_no_output(tmp_path: Path) -> None:
    assert inspect_image(tmp_path / "nothing.png", width=8, height=8).failure.code == NO_OUTPUT
    (tmp_path / "empty.png").write_bytes(b"")
    assert inspect_image(tmp_path / "empty.png", width=8, height=8).failure.code == NO_OUTPUT


def test_a_truncated_or_foreign_file_does_not_decode(tmp_path: Path) -> None:
    whole = _drawn(tmp_path / "whole.png").read_bytes()
    (tmp_path / "cut.png").write_bytes(whole[: len(whole) // 2])
    assert inspect_image(tmp_path / "cut.png", width=64, height=48).failure.code == UNDECODABLE
    (tmp_path / "text.png").write_text("not an image at all", encoding="utf-8")
    assert inspect_image(tmp_path / "text.png", width=64, height=48).failure.code == UNDECODABLE


def test_a_jpeg_named_png_is_not_the_png_asked_for(tmp_path: Path) -> None:
    found = inspect_image(_drawn(tmp_path / "photo.png", fmt="JPEG"), width=64, height=48)
    assert found.failure.code == FORMAT_MISMATCH and found.mime == "image/jpeg"
    assert inspect_image(tmp_path / "photo.png", width=64, height=48, formats=("JPEG",)).passed


@pytest.mark.parametrize(("width", "height"), [(64, 64), (65, 48), (64, 47), (48, 64)])
def test_any_other_size_than_the_one_asked_for_is_refused(tmp_path: Path, width: int, height: int) -> None:
    found = inspect_image(_drawn(tmp_path / "sized.png"), width=width, height=height)
    assert found.failure.code == SIZE_MISMATCH and found.failure.name == "size_matches_request"
    assert "64x48" in found.failure.detail


def _uniform_rgb(path: Path) -> Path:
    Image.new("RGB", (40, 30), (0, 0, 0)).save(path)
    return path


def _uniform_grey(path: Path) -> Path:
    Image.new("L", (40, 30), 128).save(path)
    return path


def _uniform_palette(path: Path) -> Path:
    image = Image.new("RGB", (40, 30), (200, 30, 90)).quantize(colors=4)   # an adaptive palette: no dithering
    assert image.mode == "P"
    image.save(path)
    return path


def _transparent_over_noise(path: Path) -> Path:
    # A check that looked only at colour spread would pass this: the hidden colour channels are noise.
    rng = random.Random(7)
    image = Image.new("RGBA", (40, 30))
    image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256), 0) for _ in range(40 * 30)])
    image.save(path)
    return path


def _noise_hidden_but_one_dot(path: Path) -> Path:
    # noise under fully transparent pixels, and one opaque dot in 2,000 pixels (0.05 %): nothing to see
    rng = random.Random(11)
    image = Image.new("RGBA", (50, 40))
    image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256), 0) for _ in range(50 * 40)])
    image.putpixel((10, 10), (255, 0, 0, 255))
    image.save(path)
    return path


def _stray_pixels(path: Path) -> Path:
    # 3 stray pixels in 400 x 300 (0.0025 %): not a picture, at any size
    image = Image.new("RGB", (400, 300), (255, 255, 255))
    for xy in ((5, 5), (200, 150), (390, 290)):
        image.putpixel(xy, (0, 0, 0))
    image.save(path)
    return path


def _faint_noise(path: Path) -> Path:
    # every pixel differs, but by at most 3 levels: it looks like one flat grey
    rng = random.Random(3)
    image = Image.new("L", (40, 30))
    image.putdata([120 + rng.randrange(4) for _ in range(40 * 30)])
    image.save(path)
    return path


@pytest.mark.parametrize("make", [_uniform_rgb, _uniform_grey, _uniform_palette, _transparent_over_noise,
                                  _noise_hidden_but_one_dot, _stray_pixels, _faint_noise])
def test_d1_a_blank_image_is_refused_whatever_its_colour_mode_or_transparency(tmp_path: Path, make) -> None:
    path = make(tmp_path / "blank.png")
    with Image.open(path) as image:
        size = image.size
    found = inspect_image(path, width=size[0], height=size[1])
    assert not found.passed and found.failure.code == BLANK and found.failure.name == "not_blank", found.checks
    assert [c.name for c in found.checks if c.passed] == ["exists", "decodes", "format_matches_request",
                                                          "size_matches_request"]


@pytest.mark.parametrize("width", [1, 3])
def test_sparse_real_content_is_not_blank(tmp_path: Path, width: int) -> None:
    image = Image.new("RGB", (200, 200), (0, 0, 0))
    ImageDraw.Draw(image).line((0, 100, 199, 100), fill=(255, 255, 255), width=width)  # 0.5 % or 1.5 % of pixels
    image.save(tmp_path / "line.png")
    assert inspect_image(tmp_path / "line.png", width=200, height=200).passed


def test_d1_a_large_picture_is_measured_on_a_reduced_copy_and_still_tells_blank_from_one_small_detail(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    measured: list[tuple[int, int]] = []
    difference = ImageChops.difference

    def watched(first, second):  # noqa: ANN001, ANN202 - Pillow's own signature
        measured.append(first.size)
        return difference(first, second)

    monkeypatch.setattr(ImageChops, "difference", watched)
    flat = Image.new("L", (4096, 4096), 200)
    flat.save(tmp_path / "flat.png")
    found = inspect_image(tmp_path / "flat.png", width=4096, height=4096)
    assert found.failure.code == BLANK and found.failure.name == "not_blank"
    flat.paste(30, (2000, 2000, 2160, 2160))  # one dark square, 160 x 160 pixels: 0.15 % of the picture
    flat.save(tmp_path / "detail.png")
    assert inspect_image(tmp_path / "detail.png", width=4096, height=4096).passed
    assert measured and max(max(size) for size in measured) <= 1024, measured  # never a full-size copy


def test_without_pillow_no_image_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _drawn(tmp_path / "ok.png")
    monkeypatch.setattr(_inspect, "pillow", lambda: None)
    found = inspect_image(path, width=64, height=48)
    assert not found.passed and found.failure.code == EXTRA_MISSING and "g6[image]" in found.failure.detail


def _svg(path: Path, body: str, width: str = "120", height: str = "80") -> Path:
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">{body}</svg>',
                    encoding="utf-8")
    return path


def test_an_svg_that_draws_at_the_size_asked_for_passes(tmp_path: Path) -> None:
    found = inspect_svg(_svg(tmp_path / "ok.svg", '<polyline points="0,0 50,40 100,10" stroke="#fff"/>'),
                        width=120, height=80)
    assert found.passed and found.mime == "image/svg+xml" and found.measured["shapes_with_extent"] == 1


@pytest.mark.parametrize(("body", "code"), [
    ("", BLANK),
    ('<polyline points="5,5 5,5"/><rect width="0" height="0"/>', BLANK),
    ('<rect width="120" height="80" fill="#000"/><path d="M 10 10" stroke="#fff"/>', BLANK),  # a background only
])
def test_an_svg_that_draws_nothing_is_blank(tmp_path: Path, body: str, code: str) -> None:
    assert inspect_svg(_svg(tmp_path / "blank.svg", body), width=120, height=80).failure.code == code


def test_an_svg_is_refused_for_the_wrong_size_a_document_type_or_another_root(tmp_path: Path) -> None:
    line = '<line x1="0" y1="0" x2="10" y2="10"/>'
    assert inspect_svg(_svg(tmp_path / "s.svg", line, width="121"), width=120, height=80).failure.code == SIZE_MISMATCH
    (tmp_path / "bomb.svg").write_text('<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg>&a;</svg>',
                                       encoding="utf-8")
    assert inspect_svg(tmp_path / "bomb.svg").failure.code == UNDECODABLE
    (tmp_path / "html.svg").write_text("<html><body/></html>", encoding="utf-8")
    assert inspect_svg(tmp_path / "html.svg").failure.code == FORMAT_MISMATCH
    (tmp_path / "broken.svg").write_text("<svg><line", encoding="utf-8")
    assert inspect_svg(tmp_path / "broken.svg").failure.code == UNDECODABLE


def _grid_obj(path: Path, heights: list[list[float]]) -> Path:
    rows, cols = len(heights), len(heights[0])
    lines = [f"v {c} {heights[r][c]} {r}" for r in range(rows) for c in range(cols)]
    for r in range(rows - 1):
        for c in range(cols - 1):
            i = r * cols + c + 1
            lines += [f"f {i} {i + 1} {i + cols}", f"f {i + 1} {i + cols + 1} {i + cols}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_heightmap_mesh_with_the_counts_asked_for_passes(tmp_path: Path) -> None:
    found = inspect_mesh(_grid_obj(tmp_path / "hill.obj", [[0, 1, 0], [1, 2, 1], [0, 1, 0]]), vertices=9, faces=8,
                         min_axes=3)
    assert found.passed and found.mime == "model/obj" and found.measured["extent"] == [2.0, 2.0, 2.0]


def test_a_mesh_with_other_counts_a_flat_heightmap_or_bad_indices_is_refused(tmp_path: Path) -> None:
    hill = _grid_obj(tmp_path / "hill.obj", [[0, 1], [1, 0]])
    assert inspect_mesh(hill, vertices=9, faces=2).failure.code == SIZE_MISMATCH
    flat = _grid_obj(tmp_path / "flat.obj", [[0.5, 0.5], [0.5, 0.5]])
    assert inspect_mesh(flat, vertices=4, faces=2, min_axes=3).failure.code == BLANK
    assert inspect_mesh(flat, vertices=4, faces=2, min_axes=2).passed           # a plane is a fine mesh
    (tmp_path / "bad.obj").write_text("v 0 0 0\nv 1 0 0\nf 1 2 3\n", encoding="utf-8")
    assert inspect_mesh(tmp_path / "bad.obj").failure.code == UNDECODABLE
    (tmp_path / "nan.obj").write_text("v 0 nan 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    assert inspect_mesh(tmp_path / "nan.obj").failure.code == UNDECODABLE
    (tmp_path / "none.obj").write_text("# only a comment\n", encoding="utf-8")
    assert inspect_mesh(tmp_path / "none.obj").failure.code == NO_OUTPUT
