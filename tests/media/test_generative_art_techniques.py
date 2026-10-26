# SPDX-License-Identifier: Apache-2.0
"""Every procedural technique makes a file the inspector passed, at exactly the size asked for, from its seed alone:
the same seed gives the same bytes and another seed another picture. A few results are checked against known values
(an L-system's rewrite, a Wolfram rule, the Game of Life, the Mandelbrot set, Koch and Perlin properties), so the
numbers are computed, not asserted."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from g6.core.registry import get_registry  # noqa: E402
from g6.media._inspect import inspect_image, inspect_svg  # noqa: E402
from g6.media._types import media_root  # noqa: E402
from g6.media.adapt_generative_art import TECHNIQUES, techniques  # noqa: E402

SMALL = {
    "lsystem": {"iterations": 3},
    "cellular_automaton": {"rule": 30},
    "fractal": {"max_iter": 60},
    "noise": {"scale": 16, "octaves": 3},
    "flow_field": {"particles": 300, "steps": 40, "scale": 24},
    "reaction_diffusion": {"steps": 600},
    "attractor": {"points": 60000},
    "voronoi": {"cells": 12},
    "cppn": {"layers": 3, "neurons": 8},
    "domain_colouring": {"expression": "(z**3 - 1) / (z**2 + 0.5)"},
    "random_walk": {"steps": 3000, "walkers": 3},
    "shape_grammar": {"depth": 5},
    "evolved": {"generations": 4, "population": 6},
    "terrain": {"kind": "diamond_square"},
}


def _invoke(op: str, **params):
    result = get_registry().invoke("adapt_generative_art", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _make(technique: str, **extra):
    return _invoke("generate", technique=technique, params=SMALL[technique], width=64, height=48, seed=7, **extra)


def test_the_test_table_covers_every_technique() -> None:
    assert sorted(SMALL) == sorted(TECHNIQUES)


@pytest.mark.parametrize("technique", sorted(SMALL))
def test_each_technique_makes_an_inspected_png_of_the_size_asked_for(technique: str) -> None:
    out = _make(technique)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    art = out["artifact"]
    assert (art["kind"], art["mime"], art["requested"]["width"], art["requested"]["height"]) == ("image", "image/png",
                                                                                                64, 48)
    assert (art["measured"]["width"], art["measured"]["height"]) == (64, 48)
    assert [c["name"] for c in art["checks"]] == ["exists", "decodes", "format_matches_request",
                                                  "size_matches_request", "not_blank"]
    assert art["provenance"]["seed"] == 7 and art["provenance"]["technique"] == technique
    assert Path(art["path"]).parent == media_root() and inspect_image(art["path"], width=64, height=48).passed


@pytest.mark.parametrize("technique", ["noise", "voronoi", "random_walk", "cppn"])
def test_a_seed_reproduces_its_bytes_and_another_seed_another_picture(technique: str) -> None:
    first = _make(technique)["artifact"]
    again = _invoke("generate", technique=technique, params=SMALL[technique], width=64, height=48, seed=7)["artifact"]
    other = _invoke("generate", technique=technique, params=SMALL[technique], width=64, height=48, seed=8)["artifact"]
    assert first["sha256"] == again["sha256"] and first["path"] == again["path"]
    assert other["sha256"] != first["sha256"] and other["provenance"]["recipe_sha256"] != \
        first["provenance"]["recipe_sha256"]


def test_without_a_seed_one_is_drawn_and_reported() -> None:
    out = _invoke("generate", technique="noise", params=SMALL["noise"], width=32, height=32)
    seed = out["artifact"]["provenance"]["seed"]
    assert isinstance(seed, int) and 0 <= seed < 2**32
    again = _invoke("generate", technique="noise", params=SMALL["noise"], width=32, height=32, seed=seed)
    assert again["artifact"]["sha256"] == out["artifact"]["sha256"]


@pytest.mark.parametrize("technique", ["lsystem", "random_walk", "fractal"])
def test_a_path_technique_can_also_be_an_inspected_svg(technique: str) -> None:
    params = {**SMALL[technique], **({"kind": "koch", "depth": 3} if technique == "fractal" else {})}
    out = _invoke("generate", technique=technique, params=params, width=120, height=90, seed=2, format="svg")
    art = out["artifact"]
    assert out["completion_state"] == "qualified-draft" and (art["kind"], art["mime"]) == ("svg", "image/svg+xml")
    assert inspect_svg(art["path"], width=120, height=90).passed and art["measured"]["shapes_with_extent"] >= 1


def test_composition_tone_maps_and_filters_change_the_picture_and_are_recorded() -> None:
    plain = _make("noise")["artifact"]
    styled = _invoke("generate", technique="noise", params=SMALL["noise"], width=64, height=48, seed=7,
                     tone_map="equalise", palette="magma",
                     layers=[{"technique": "voronoi", "params": {"cells": 10}, "blend": "multiply", "weight": 0.8}],
                     filters=[{"filter": "blur", "radius": 1}, {"filter": "posterise", "levels": 5}])["artifact"]
    assert styled["sha256"] != plain["sha256"]
    recipe = styled["provenance"]["recipe"]
    assert recipe["tone_map"] == "equalise" and recipe["layers"][0]["technique"] == "voronoi"
    assert [f["filter"] for f in recipe["filters"]] == ["blur", "posterise"] and recipe["palette"] == "magma"
    custom = _invoke("generate", technique="noise", params=SMALL["noise"], width=64, height=48, seed=7,
                     colours=["#000000", "#ff8800", "#ffffff"])["artifact"]
    assert custom["provenance"]["recipe"]["colours"] == ["#000000", "#ff8800", "#ffffff"]


def test_known_values_lsystem_wolfram_life_mandelbrot_koch_perlin() -> None:
    assert techniques.expand("F", {"F": "F+F-F-F+F"}, 1) == "F+F-F-F+F"
    assert techniques.expand("A", {"A": "AB", "B": "A"}, 4) == "ABAABABA"           # Fibonacci words
    rows = techniques.wolfram(90, width=9, rows=3, start=None)
    assert rows.astype(int).tolist() == [[0, 0, 0, 0, 1, 0, 0, 0, 0], [0, 0, 0, 1, 0, 1, 0, 0, 0],
                                         [0, 0, 1, 0, 0, 0, 1, 0, 0]]               # rule 90: Sierpinski's triangle
    blinker = np.zeros((5, 5), dtype=bool)
    blinker[2, 1:4] = True
    once = techniques.life_step(blinker)
    assert once[1:4, 2].all() and once.sum() == 3 and (techniques.life_step(once) == blinker).all()
    escape = techniques.mandelbrot_escape(np.array([0j, -1 + 0j, 2 + 0j, 1 + 1j]), max_iter=100)
    assert escape.tolist()[:2] == [100, 100] and escape[2] < 3 and escape[3] < 5    # 0 and -1 stay; 2 escapes
    assert len(techniques.koch(3)) == 3 * 4**3 + 1
    lattice = techniques.perlin(64, 64, cell=16, rng=np.random.default_rng(1))
    assert np.allclose(lattice[::16, ::16], 0.0)                                     # gradient noise is 0 on its lattice


def test_the_techniques_op_lists_every_technique_with_its_parameters() -> None:
    listed = _invoke("techniques")
    assert listed["completion_state"] == "qualified-draft"
    found = {t["technique"]: t for t in listed["techniques"]}
    assert sorted(found) == sorted(TECHNIQUES)
    assert found["lsystem"]["svg"] and not found["noise"]["svg"] and found["terrain"]["mesh"]
    assert "iterations" in found["lsystem"]["params"] and "expression" in found["domain_colouring"]["params"]
    assert "viridis" in listed["palettes"] and "blur" in listed["filters"]
    json.dumps(listed)
