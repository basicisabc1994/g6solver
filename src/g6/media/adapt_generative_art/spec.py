# SPDX-License-Identifier: Apache-2.0
"""The parameters of every procedural technique, the palettes, tone maps, filters and blends, and the work limit.

Pure pydantic, with no NumPy, so the parameters can be listed and checked without the ``procedural`` extra. Each
technique's model refuses unknown fields; each number is bounded; and :func:`work` estimates how many elementary steps a
request would take, so a request beyond :data:`WORK_LIMIT` is refused before it starts rather than left to run.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Real = Annotated[float, Field(allow_inf_nan=False)]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LSystem(_Params):
    """String rewriting drawn by a turtle: F and G draw a step, f moves without drawing, + and - turn, [ and ] save and
    restore the turtle. Other letters only take part in rewriting."""

    axiom: str = Field("F", min_length=1, max_length=64, pattern=r"^[A-Za-z+\-\[\]]+$")
    rules: dict[str, str] = Field(default_factory=lambda: {"F": "F+F-F-F+F"}, max_length=12)
    iterations: int = Field(4, ge=0, le=12)
    angle: Real = Field(90.0, ge=-360, le=360)
    line_width: int = Field(1, ge=1, le=8)

    @field_validator("rules")
    @classmethod
    def _letters_to_strings(cls, rules: dict[str, str]) -> dict[str, str]:
        for key, value in rules.items():
            if len(key) != 1 or not key.isalpha() or not key.isascii():
                raise ValueError(f"a rule rewrites one letter, not {key!r}")
            if len(value) > 64 or not all(ch.isascii() and (ch.isalpha() or ch in "+-[]") for ch in value):
                raise ValueError(f"rule {key!r}: up to 64 letters and + - [ ]")
        return rules


class CellularAutomaton(_Params):
    """An elementary (Wolfram) rule, one row per step down the picture, or Conway's Game of Life from random cells."""

    kind: Literal["wolfram", "life"] = "wolfram"
    rule: int = Field(30, ge=0, le=255)
    start: Literal["centre", "random"] = "centre"
    steps: int = Field(60, ge=1, le=2000)
    density: Real = Field(0.3, ge=0.01, le=0.99)


class Fractal(_Params):
    """Escape-time Mandelbrot and Julia sets, the Sierpinski triangle by the chaos game, and the Koch snowflake."""

    kind: Literal["mandelbrot", "julia", "sierpinski", "koch"] = "mandelbrot"
    max_iter: int = Field(200, ge=10, le=5000)
    centre: tuple[Real, Real] | None = None
    zoom: Real = Field(1.0, ge=0.01, le=1e9)
    c: tuple[Real, Real] = (-0.7, 0.27015)
    points: int = Field(200_000, ge=1000, le=5_000_000)
    depth: int = Field(4, ge=0, le=7)
    line_width: int = Field(1, ge=1, le=8)


class Noise(_Params):
    """Gradient (Perlin) noise summed over octaves: ``scale`` is the size of the largest feature in pixels."""

    scale: Real = Field(64.0, ge=2, le=4096)
    octaves: int = Field(4, ge=1, le=10)
    persistence: Real = Field(0.5, ge=0.05, le=0.95)


class FlowField(_Params):
    """Particles carried along the curl of a noise field, their paths accumulated as density."""

    particles: int = Field(2000, ge=10, le=50_000)
    steps: int = Field(100, ge=1, le=1000)
    scale: Real = Field(96.0, ge=4, le=4096)
    step_size: Real = Field(1.0, ge=0.05, le=8)


class ReactionDiffusion(_Params):
    """The Gray-Scott model on a wrapped grid: ``feed`` and ``kill`` choose spots, stripes or coral."""

    feed: Real = Field(0.055, ge=0, le=0.12)
    kill: Real = Field(0.062, ge=0, le=0.12)
    steps: int = Field(3000, ge=1, le=50_000)
    diffusion_u: Real = Field(0.16, gt=0, le=0.25)
    diffusion_v: Real = Field(0.08, gt=0, le=0.25)


class Attractor(_Params):
    """A strange attractor's orbits as density: Clifford and de Jong maps (``a`` to ``d`` change their shape), and the
    Lorenz and Rössler flows. Many short orbits start near the origin, spread by the seed."""

    kind: Literal["clifford", "dejong", "lorenz", "rossler"] = "clifford"
    points: int = Field(500_000, ge=1000, le=10_000_000)
    a: Real | None = Field(None, ge=-5, le=5)
    b: Real | None = Field(None, ge=-5, le=5)
    c: Real | None = Field(None, ge=-5, le=5)
    d: Real | None = Field(None, ge=-5, le=5)


class Voronoi(_Params):
    """Cells around random sites, optionally relaxed towards even sizes (Lloyd's method), drawn as edges, cells or
    distance to the nearest site."""

    cells: int = Field(64, ge=2, le=1024)
    colouring: Literal["edges", "cells", "distance"] = "edges"
    relax: int = Field(0, ge=0, le=20)


class CPPN(_Params):
    """A compositional pattern-producing network: random weights and activations map each pixel's position to a
    value."""

    layers: int = Field(4, ge=1, le=8)
    neurons: int = Field(12, ge=2, le=32)
    zoom: Real = Field(1.0, ge=0.05, le=50)


class DomainColouring(_Params):
    """A complex function of ``z`` over a region of the plane, coloured by its phase or by its modulus. The expression
    may use z, numbers, i or j, pi, e, + - * / **, and sin cos tan sinh cosh tanh exp log sqrt abs conj real imag."""

    expression: str = Field("(z**3 - 1) / (z**2 + 1)", min_length=1, max_length=200)
    centre: tuple[Real, Real] = (0.0, 0.0)
    extent: Real = Field(2.0, ge=1e-6, le=1e6)
    colour_by: Literal["phase", "modulus"] = "phase"

    @field_validator("expression")
    @classmethod
    def _allowed(cls, text: str) -> str:
        from g6.media.adapt_generative_art.expression import parse

        parse(text)
        return text


class RandomWalk(_Params):
    """Walkers taking random steps: on the four lattice directions, or Brownian (normally distributed) steps."""

    kind: Literal["brownian", "lattice"] = "brownian"
    steps: int = Field(20_000, ge=10, le=2_000_000)
    walkers: int = Field(4, ge=1, le=64)
    line_width: int = Field(1, ge=1, le=8)


class ShapeGrammar(_Params):
    """Recursive subdivision by a rule set: Mondrian rectangles, rooms and corridors, circuit blocks or circles."""

    rule_set: Literal["mondrian", "architectural", "circuit", "organic"] = "mondrian"
    depth: int = Field(6, ge=1, le=12)
    min_cell: Real = Field(0.04, ge=0.005, le=0.5)


class Evolved(_Params):
    """A small genetic algorithm evolving the weights of a pattern network, scored on a 48 x 48 preview by
    ``fitness``: mirror ``symmetry``, ``complexity`` (contrast and edges) or ``smoothness`` (contrast without noise)."""

    fitness: Literal["symmetry", "complexity", "smoothness"] = "symmetry"
    generations: int = Field(30, ge=1, le=300)
    population: int = Field(16, ge=4, le=64)
    mutation: Real = Field(0.3, gt=0, le=2)


class Terrain(_Params):
    """A heightmap: diamond-square midpoint displacement (``roughness``: lower is smoother) or Perlin noise over
    octaves (``scale`` is the largest feature in pixels)."""

    kind: Literal["diamond_square", "perlin"] = "diamond_square"
    roughness: Real = Field(0.55, ge=0.05, le=1.0)
    scale: Real = Field(64.0, ge=2, le=4096)
    octaves: int = Field(5, ge=1, le=10)


#: Each technique: its parameters, whether it draws strokes (so it can be an SVG) and whether it makes a heightmap.
TECHNIQUES: dict[str, tuple[type[_Params], bool, bool]] = {
    "lsystem": (LSystem, True, False),
    "cellular_automaton": (CellularAutomaton, False, True),
    "fractal": (Fractal, True, True),
    "noise": (Noise, False, True),
    "flow_field": (FlowField, False, True),
    "reaction_diffusion": (ReactionDiffusion, False, True),
    "attractor": (Attractor, False, True),
    "voronoi": (Voronoi, False, True),
    "cppn": (CPPN, False, True),
    "domain_colouring": (DomainColouring, False, True),
    "random_walk": (RandomWalk, True, False),
    "shape_grammar": (ShapeGrammar, False, True),
    "evolved": (Evolved, False, True),
    "terrain": (Terrain, False, True),
}
TechniqueName = Literal["lsystem", "cellular_automaton", "fractal", "noise", "flow_field", "reaction_diffusion",
                        "attractor", "voronoi", "cppn", "domain_colouring", "random_walk", "shape_grammar", "evolved",
                        "terrain"]

#: Elementary steps a request may take (cells times steps, points, pixels times neurons, ...).
WORK_LIMIT = 400_000_000


def strokes(technique: str, params: _Params) -> bool:
    """Whether a technique draws strokes, which an SVG can carry (Koch is the fractal that does)."""
    drawn = TECHNIQUES[technique][1]
    return drawn and (technique != "fractal" or getattr(params, "kind", "") == "koch")


def work(technique: str, params: Any, width: int, height: int) -> int:
    """An estimate of the elementary steps ``technique`` takes at ``width`` by ``height``."""
    cells = width * height
    p = params
    if technique == "cellular_automaton":
        return cells * (p.steps if p.kind == "life" else 1)
    if technique == "fractal":
        return {"mandelbrot": cells * p.max_iter, "julia": cells * p.max_iter, "sierpinski": p.points,
                "koch": 3 * 4 ** p.depth}[p.kind]
    if technique in ("noise", "terrain"):
        return cells * (p.octaves if technique == "noise" or p.kind == "perlin" else 2)
    if technique == "flow_field":
        return p.particles * p.steps + cells
    if technique == "reaction_diffusion":
        return cells * p.steps
    if technique == "attractor":
        return p.points
    if technique == "voronoi":
        return cells * p.cells * (p.relax + 1)
    if technique == "cppn":
        return cells * p.neurons * p.layers
    if technique == "domain_colouring":
        return cells * len(p.expression)
    if technique == "random_walk":
        return p.steps * p.walkers
    if technique == "evolved":
        return p.generations * p.population * 48 * 48 * 16 + cells * 16
    return cells  # lsystem (bounded as it expands) and shape_grammar


#: Palettes as colour stops. Viridis, magma, inferno and plasma are approximated by their published ten-step samples
#: (the colour maps were released to the public domain, CC0, by their authors); the rest are G6's own.
PALETTES: dict[str, tuple[str, ...]] = {
    "viridis": ("#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b",
                "#fde725"),
    "magma": ("#000004", "#180f3d", "#440f76", "#721f81", "#9e2f7f", "#cd4071", "#f1605d", "#fd9668", "#feca8d",
              "#fcfdbf"),
    "inferno": ("#000004", "#1b0c41", "#4a0c6b", "#781c6d", "#a52c60", "#cf4446", "#ed6925", "#fb9b06", "#f7d13d",
                "#fcffa4"),
    "plasma": ("#0d0887", "#46039f", "#7201a8", "#9c179e", "#bd3786", "#d8576b", "#ed7953", "#fb9f3a", "#fdca26",
               "#f0f921"),
    "greys": ("#000000", "#ffffff"),
    "fire": ("#000000", "#330000", "#990000", "#ff3300", "#ff9900", "#ffff00", "#ffffff"),
    "ocean": ("#000033", "#000066", "#0033cc", "#0099ff", "#66ccff", "#ccffff"),
    "earth": ("#0b2e59", "#1f6f8b", "#e8d8a8", "#7aa95c", "#3f6b2a", "#7b5b3a", "#f4f4f4"),
    "sunset": ("#0d0221", "#261447", "#6b1d5e", "#b9314f", "#d96459", "#f2a553", "#f7e3af"),
    "hue": ("#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff", "#ff0000"),
}
PaletteName = Literal["viridis", "magma", "inferno", "plasma", "greys", "fire", "ocean", "earth", "sunset", "hue"]
HexColour = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
ToneMap = Literal["none", "equalise", "reinhard"]
Blend = Literal["add", "multiply", "screen", "overlay", "max", "min", "difference"]


class _Filter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Blur(_Filter):
    filter: Literal["blur"]
    radius: int = Field(2, ge=1, le=32)


class Sharpen(_Filter):
    filter: Literal["sharpen"]
    amount: Real = Field(1.0, ge=0, le=5)


class Edges(_Filter):
    filter: Literal["edges"]


class Threshold(_Filter):
    filter: Literal["threshold"]
    level: Real = Field(0.5, ge=0, le=1)


class Invert(_Filter):
    filter: Literal["invert"]


class Contrast(_Filter):
    filter: Literal["contrast"]
    factor: Real = Field(1.5, ge=0.1, le=10)


class Posterise(_Filter):
    filter: Literal["posterise"]
    levels: int = Field(4, ge=2, le=32)


class Dilate(_Filter):
    filter: Literal["dilate"]
    radius: int = Field(1, ge=1, le=8)


class Erode(_Filter):
    filter: Literal["erode"]
    radius: int = Field(1, ge=1, le=8)


Filter = Annotated[Blur | Sharpen | Edges | Threshold | Invert | Contrast | Posterise | Dilate | Erode,
                   Field(discriminator="filter")]
FILTERS = ("blur", "sharpen", "edges", "threshold", "invert", "contrast", "posterise", "dilate", "erode")


class Layer(BaseModel):
    """Another technique drawn at the same size (seeded from the request's seed) and blended onto the picture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    technique: TechniqueName
    params: dict[str, Any] = Field(default_factory=dict)
    blend: Blend = "screen"
    weight: Real = Field(1.0, ge=0, le=1)


def technique_params(technique: str, params: dict[str, Any]) -> _Params:
    """``params`` checked against ``technique``'s own model (unknown fields refused)."""
    return TECHNIQUES[technique][0].model_validate(params)


def describe() -> list[dict[str, Any]]:
    """Each technique with what it draws and its parameters (default and bounds), read from the models."""
    found = []
    for name, (model, drawn, height) in TECHNIQUES.items():
        schema = model.model_json_schema()
        props = {key: {k: v for k, v in value.items() if k != "title"} for key, value in schema["properties"].items()}
        found.append({"technique": name, "about": (model.__doc__ or "").split("\n\n")[0].replace("\n    ", " "),
                      "svg": drawn, "mesh": height, "params": props})
    return found


__all__ = ["FILTERS", "PALETTES", "TECHNIQUES", "WORK_LIMIT", "Blend", "Filter", "HexColour", "Layer", "PaletteName",
           "TechniqueName", "ToneMap", "describe", "strokes", "technique_params", "work"]
