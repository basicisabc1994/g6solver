# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_generative_art — make procedural pictures, vector drawings and heightmap meshes from seeded techniques.

An extension in the media family, and the family's ``procedural`` image backend: it needs no model, no network and
no outside program, only NumPy and Pillow (``pip install 'g6[procedural] @ git+https://github.com/basicisabc1994/g6solver'``).

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_generative_art", "generate",
                                {"technique": "reaction_diffusion", "width": 256, "height": 256, "seed": 7}).unwrap()
    out["artifact"]["path"], out["completion_state"]   # a PNG under $G6_HOME/media, "qualified-draft"

What it does:

* fourteen deterministic techniques, each drawn from its seed alone: L-systems, cellular automata (Wolfram rules and
  the Game of Life), fractals (Mandelbrot, Julia, Sierpinski, Koch), Perlin noise, curl-noise flow fields,
  Gray-Scott reaction-diffusion, strange attractors, Voronoi cells, pattern networks (CPPN), domain colouring of a
  complex expression, random walks, shape grammars, an evolved pattern network (a small genetic algorithm) and
  terrain (diamond-square and Perlin heightmaps);
* tone maps, layers blended onto each other, filters and palettes; PNG for every technique, SVG for the ones that draw
  strokes, and OBJ meshes from heightmaps, handed back with a ``SceneSpec`` holding the mesh;
* every file is written into G6's media folder under a plain name the caller chooses, then inspected from disk by the
  media family's inspector (it decodes, is the format and size asked for, and is not blank); a file that fails is
  removed and the op refused with the check's code (``G6_E_MEDIA_BLANK`` and others). A result is ``qualified-draft``:
  the inspector measures the file, nothing judges what it shows.

Not ported from the archive's adapt_generative_art and adapt_synthetic_world (merged in here), and why:

* the ``custom`` technique, caller JavaScript or GLSL and headless browser rendering of it: caller code never runs;
* ``eval`` of a caller's expression: domain colouring parses it against an allow-list and walks it itself;
* neural style transfer (its network had random weights), manim, the p5.js, Processing and Shadertoy bundles, the
  context-free text grammar, the prompt-to-art tables and their default model fallback, the bundle of the vendored
  algorithmic-art skill (it taught p5.js sketches, which are cut), the MCP servers, the store, presets, palettes and
  genomes kept between calls, the backend source ops and the scaffold;
* GIF, WAV and text output; the archive's output path chosen by the caller;
* from adapt_synthetic_world: computer use (it reported a model's proposal as an action taken), its direct paid model
  calls, the world store and its backends. Its terrain and heightmap-to-mesh are here; its scene tree is ``SceneSpec``.
"""
from g6.media._types import image_backends
from g6.media.adapt_generative_art import spec
from g6.media.adapt_generative_art.block import AdaptGenerativeArtBlock, GenerativeArtInput, ProceduralBackend
from g6.media.adapt_generative_art.expression import ExpressionError
from g6.media.adapt_generative_art.spec import TECHNIQUES

__g6_component__ = True
__g6_block__ = "AdaptGenerativeArtBlock"
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = ()  # nothing reaches the network, spends money or acts outside G6's folder
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("visual_check_failed",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"procedural": ("PIL", "numpy")}
__g6_image_backends__: tuple[str, ...] = ("procedural",)  # the family's registry imports this package to find it

image_backends.register(ProceduralBackend(), replace=True)


def __getattr__(name: str):  # noqa: ANN202 - the NumPy modules load only when asked for, so the package imports without
    if name in ("techniques", "render"):
        import importlib

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(name)


__all__ = ["TECHNIQUES", "AdaptGenerativeArtBlock", "ExpressionError", "GenerativeArtInput", "ProceduralBackend",
           "spec"]
