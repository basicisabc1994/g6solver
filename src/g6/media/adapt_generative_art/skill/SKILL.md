---
name: g6-adapt-generative-art
description: Use when you need an actual picture file and not a description of one (procedural art, a texture, a pattern, a fractal, a vector drawing or a terrain mesh for a 3D scene), made on this machine with no model, no network and no cost, from a seed you can reproduce, and checked before you use it.
---

# G6 adapt_generative_art

`g6.media.adapt_generative_art` draws pictures, vector drawings and heightmap meshes from fourteen seeded, deterministic
techniques in NumPy and Pillow. It writes each file into G6's media folder (`$G6_HOME/media`) and inspects it from disk
before it answers: the file decodes, is the format and size you asked for, and is not blank. It is an extension in the
media family and the family's `procedural` image backend. Its best state is `qualified-draft`.

## When to use it

- You (a coding agent) need a real PNG or SVG to put in a page, a test or a README: placeholder art, a texture, a
  pattern, a fractal, a diagram-like drawing.
- You need a terrain or another heightmap as an OBJ mesh, with a `SceneSpec` holding it, for a 3D scene.
- You want the same picture again later: give the seed, or read the one reported, and the same request gives the same
  bytes.
- You want to know, not guess, that the file you got is a picture of the size you asked for and not an empty canvas.

## When not to use it

- A picture of something described in words (a fox, a logo, a photograph): this backend reads no prompt and refuses
  one. Use an image generator backend such as ComfyUI when the family has one.
- Editing or converting an existing image: not here.
- Judging whether a picture is good or shows what you meant: nothing here judges content; look at it yourself.
- Animation, sound or video: not here (GIF, WAV and text output were not ported).

## Ops

- `generate`: a `technique` with its `params`, drawn at `width` by `height` (8 to 2048) from `seed`, coloured by a
  `palette` (or your own `colours`, two to sixteen `#rrggbb` stops), with an optional `tone_map` (`equalise`,
  `reinhard`), `layers` (other techniques blended on: `add`, `multiply`, `screen`, `overlay`, `max`, `min`,
  `difference`) and `filters` (`blur`, `sharpen`, `edges`, `threshold`, `invert`, `contrast`, `posterise`, `dilate`,
  `erode`). `format` is `png`, or `svg` for a technique that draws strokes. `name` is the file name; G6 chooses the
  folder.
- `mesh`: a heightmap technique as an OBJ triangle mesh, `width` by `height` vertices (8 to 513), heights from 0 to
  `height_scale`, two units across the longer side, y up; the reply's `scene` is a `SceneSpec` holding it.
- `techniques`: every technique with what it draws and its parameters (defaults and bounds), the palettes, tone maps,
  filters and blends. It needs no extra.

The techniques: `lsystem`, `cellular_automaton` (Wolfram rules, Game of Life), `fractal` (Mandelbrot, Julia,
Sierpinski, Koch), `noise` (Perlin), `flow_field`, `reaction_diffusion` (Gray-Scott), `attractor` (Clifford, de Jong,
Lorenz, Rössler), `voronoi`, `cppn`, `domain_colouring`, `random_walk`, `shape_grammar`, `evolved` (a small genetic
algorithm over pattern networks) and `terrain` (diamond-square, Perlin). SVG is offered for `lsystem`, `random_walk`
and the Koch `fractal`.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_generative_art", "generate", '{"technique": "voronoi", "seed": 3}')`, then open the
file at `result.artifact.path`. No op is sensitive: nothing reaches the network, spends money or writes outside G6's
media folder. From Python: `get_registry().invoke("adapt_generative_art", op, params)`, or through the family's
backend registry, `image_backends.generate("procedural", ImageRequest(options={"technique": ...}))`.

Composed:

- The `mesh` op's `scene` is a `SceneSpec`; add objects to it (`scene.add`, `reparent`, `merge`) and hand it to a
  renderer such as the family's Blender backend when it is in the library.
- The artifact's `provenance` (backend, versions, seed, the full recipe and its SHA-256) is what to store beside the
  file to make it again.

## Failure modes it catches

- `visual_check_failed` (an image, video or 3D output that is blank, corrupt or wrong, reported as done): every file is
  inspected from disk before it is returned. A picture with fewer than 0.1 % of its pixels differing from the
  background (a reaction that died out, a rule that leaves one live cell, a constant function, an attractor collapsed
  to a point) is refused with `G6_E_MEDIA_BLANK`; a wrong size with `G6_E_MEDIA_SIZE_MISMATCH`; a file that does not
  decode with `G6_E_MEDIA_UNDECODABLE`; a flat mesh with `G6_E_MEDIA_BLANK`. The failed file is removed.

## Reading the result

- A result is `qualified-draft` with `warning_card.code` `G6_E_MEDIA_INSPECTED`; `artifact` holds the path, MIME type
  read from the bytes, size, SHA-256, what was requested and measured, each check, and the provenance.
- A refusal is `blocked-escalated` with its code: the inspection codes above, `G6_E_MEDIA_EXISTS` (a different file has
  that name: choose another or pass `overwrite`), `G6_E_MEDIA_BACKEND_FAILED` (a technique raised) or
  `G6_E_MEDIA_EXTRA_MISSING`. A request beyond the work limit, an unknown field or parameter, a name with a folder in it
  and an expression outside the allow-list are refused before anything runs (`G6_E_INPUT_VALIDATION`).

## Blind spots

- The inspector measures; it does not look. A picture can pass and still be ugly, or not what you meant.
- Blank means nearly one colour. A picture of noise, or a nearly empty one with 0.1 % of its pixels drawn, passes.
- The same seed gives the same bytes with the same NumPy and Pillow (named in `backend_version`); other versions may
  differ slightly.
- Files are kept in the media folder until you delete them; nothing cleans up.
- Domain colouring's expression may use only `z`, numbers, `i`, `j`, `pi`, `e`, `+ - * / **` and the functions
  `sin cos tan sinh cosh tanh exp log sqrt abs conj real imag`; anything else is refused.

## What it needs

- `pip install "g6[procedural]"` for NumPy and Pillow (both permissively licensed). Without it, `generate` and `mesh`
  refuse with `G6_E_MEDIA_EXTRA_MISSING` and the install line; `techniques` still answers.
- Nothing else: no model, no network, no outside program.

## Examples

A Voronoi picture from seed 3; the reply's `artifact.path` is the PNG and `artifact.checks` what was measured.

```g6-example op=generate
{"technique": "voronoi", "params": {"cells": 24, "colouring": "edges"}, "width": 96, "height": 64, "seed": 3, "palette": "plasma"}
```

A plant drawn by an L-system, as an SVG.

```g6-example op=generate
{"technique": "lsystem", "params": {"axiom": "X", "rules": {"X": "F+[[X]-X]-F[-FX]+X", "F": "FF"}, "iterations": 4, "angle": 25}, "width": 120, "height": 160, "seed": 1, "format": "svg"}
```

A diamond-square terrain as an OBJ mesh, with a scene holding it.

```g6-example op=mesh
{"technique": "terrain", "params": {"roughness": 0.5}, "width": 24, "height": 16, "seed": 11, "height_scale": 0.4}
```

What the techniques take.

```g6-example op=techniques
{}
```

From Python, through the family's backend registry, then a scene built on a mesh; a reaction that dies out is refused,
not returned.

```python
import g6.media.adapt_generative_art  # noqa: F401 - importing it registers the procedural backend
from g6.core.registry import get_registry
from g6.media._scene import SceneObject, SceneSpec
from g6.media._types import ImageRequest, image_backends

made = image_backends.generate("procedural", ImageRequest(width=64, height=48, seed=5, options={
    "technique": "noise", "params": {"scale": 16}, "palette": "earth"}))
assert made.completion_state == "qualified-draft" and made.artifact.measured["width"] == 64
assert image_backends.generate("procedural", ImageRequest(prompt="a fox")).completion_state == "blocked-escalated"

reg = get_registry()
mesh = reg.invoke("adapt_generative_art", "mesh", {"technique": "terrain", "width": 16, "height": 16,
                                                   "seed": 2}).unwrap()
scene = SceneSpec.model_validate(mesh["scene"]).add(SceneObject(name="tower", primitive="cube", parent="terrain"))
assert scene.descendants("terrain") == ("tower",)

dead = reg.invoke("adapt_generative_art", "generate", {"technique": "cellular_automaton", "params": {"rule": 0},
                                                       "width": 96, "height": 64, "seed": 1}).unwrap()
assert dead["completion_state"] == "blocked-escalated" and dead["code"] == "G6_E_MEDIA_BLANK"
```

## The four open problems

- **Learning signals**: each refusal carries the failed check and the technique's own numbers (live cells, the
  reaction's peak, a genetic algorithm's best score by generation). Not yet: nothing records refusals per technique or
  learns better parameters from them.
- **Evaluators the model cannot influence**: the inspector reads the file from disk with Pillow (or the standard
  library for SVG and OBJ); no model is consulted and the caller cannot pass or skip a check.
- **Transfer by failure mode**: the same inspector and codes guard every media backend, so "blank", "wrong size" and
  "does not decode" mean the same from any of them.
- **Enough held-out cases**: Not yet: there is no fixed set of seeded requests with stored hashes per technique; the
  tests check sizes, determinism, known values and blank refusals, not stored pictures.
