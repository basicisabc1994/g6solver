# SPDX-License-Identifier: Apache-2.0
"""AdaptGenerativeArtBlock, the registry and MCP door (``g6_invoke``) to adapt_generative_art, and the ``procedural``
image backend it registers with the media family.

Ops: ``generate`` (a PNG, or an SVG for a technique that draws strokes), ``mesh`` (a heightmap as an OBJ mesh, with a
``SceneSpec`` holding it) and ``techniques`` (every technique with its parameters), plus ``ops`` and ``help``. Every
file is written under ``state_path("media")`` with the name the caller chose (or one made from the technique, seed and
recipe hash), then inspected from disk; a file that fails is removed and the op refused with the failed check's code.
A result is ``qualified-draft`` at best. Unknown ops, fields and parameters are refused by the typed input
(``G6_E_INPUT_VALIDATION``), as is a request that would take more than ``spec.WORK_LIMIT`` steps.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media._inspect import inspect_image, inspect_mesh, inspect_svg
from g6.media._scene import SceneObject, SceneSpec
from g6.media._types import (
    BACKEND_FAILED,
    DESCRIBED,
    EXTRA_MISSING,
    INPUT_INVALID,
    ImageRequest,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    check_name,
    discard,
    media_artifact,
    media_file,
    refuse,
    write_media,
)
from g6.media.adapt_generative_art import spec

BACKEND = "procedural"
MESH_LIMIT = 513
_OPS = {
    "generate": "generate(technique, params, width, height, seed, palette or colours, tone_map, layers, filters, "
                "format=png|svg, name, overwrite): a PNG (or an SVG for a technique that draws strokes) from a seeded "
                "procedural technique, inspected before it is returned",
    "mesh": "mesh(technique, params, width, height, seed, height_scale, tone_map, filters, name, overwrite): a "
            "heightmap as an OBJ triangle mesh, inspected, with a SceneSpec holding it",
    "techniques": "techniques(): every technique with what it draws and its parameters, and the palettes, tone maps, "
                  "filters and blends",
}


class GenerativeArtInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["generate", "mesh", "techniques", "ops", "help"] = "help"
    technique: spec.TechniqueName | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    width: int = Field(256, ge=8, le=2048)
    height: int = Field(256, ge=8, le=2048)
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    palette: spec.PaletteName = "viridis"
    colours: list[spec.HexColour] | None = Field(None, min_length=2, max_length=16)
    tone_map: spec.ToneMap = "none"
    layers: list[spec.Layer] = Field(default_factory=list, max_length=4)
    filters: list[spec.Filter] = Field(default_factory=list, max_length=8)
    format: Literal["png", "svg"] = "png"
    height_scale: float = Field(0.3, gt=0, le=10, allow_inf_nan=False)
    name: str = Field("", max_length=100)
    overwrite: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if value:
            try:
                check_name(value)
            except MediaError as exc:
                raise ValueError(exc.reason) from None
        return value

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> GenerativeArtInput:
        if self.op not in ("generate", "mesh"):
            return self
        if self.technique is None:
            raise ValueError(f"{self.op} needs a technique: one of {', '.join(spec.TECHNIQUES)}")
        params = spec.technique_params(self.technique, self.params)
        drawn = spec.strokes(self.technique, params)
        work = spec.work(self.technique, params, self.width, self.height)
        for layer in self.layers:
            work += spec.work(layer.technique, spec.technique_params(layer.technique, layer.params), self.width,
                              self.height)
        if work > spec.WORK_LIMIT:
            raise ValueError(f"too much work: about {work:,} steps, more than the limit of {spec.WORK_LIMIT:,}; "
                             "use a smaller picture or fewer steps, points or iterations")
        if self.op == "mesh":
            if drawn or not spec.TECHNIQUES[self.technique][2]:
                raise ValueError(f"a mesh needs a heightmap technique, not {self.technique} drawing strokes")
            if self.format != "png" or self.layers:
                raise ValueError("a mesh is written as OBJ and takes no layers")
            if max(self.width, self.height) > MESH_LIMIT:
                raise ValueError(f"a mesh grid is at most {MESH_LIMIT} by {MESH_LIMIT} vertices")
        elif self.format == "svg":
            if not drawn:
                raise ValueError(f"{self.technique} makes a raster picture; ask for png (an SVG needs strokes: "
                                 "lsystem, random_walk or a koch fractal)")
            if self.layers or self.filters or self.tone_map != "none":
                raise ValueError("layers, filters and tone maps work on pixels: ask for png")
        return self


def _ids(d: GenerativeArtInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _missing_extra() -> list[str]:
    missing = []
    for module in ("numpy", "PIL"):
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    return missing


def _recipe(d: GenerativeArtInput, seed: int) -> dict[str, Any]:
    """Everything that shapes the file: the technique's checked parameters, its size, seed and styling."""
    params = spec.technique_params(d.technique, d.params).model_dump(mode="json")
    recipe: dict[str, Any] = {"backend": BACKEND, "op": d.op, "technique": d.technique, "params": params,
                              "width": d.width, "height": d.height, "seed": seed, "tone_map": d.tone_map,
                              "filters": [f.model_dump(mode="json") for f in d.filters]}
    if d.op == "mesh":
        recipe["height_scale"] = d.height_scale
    else:
        recipe.update(format=d.format, palette=d.palette, colours=d.colours, layers=[
            {**layer.model_dump(mode="json"),
             "params": spec.technique_params(layer.technique, layer.params).model_dump(mode="json")}
            for layer in d.layers])
    return recipe


def _version() -> str:
    import numpy
    import PIL

    return f"g6 procedural (NumPy {numpy.__version__}, Pillow {PIL.__version__})"


def _make(d: GenerativeArtInput) -> MediaResult:
    missing = _missing_extra()
    if missing:
        return refuse(d.op, EXTRA_MISSING, f"{' and '.join(missing)} not installed, so nothing can be drawn: "
                                           "pip install 'g6[procedural] @ git+https://github.com/basicisabc1994/g6solver'", **_ids(d))
    from g6.media.adapt_generative_art import render, techniques

    seed = d.seed if d.seed is not None else secrets.randbelow(2**32)
    recipe = _recipe(d, seed)
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    suffix = ".obj" if d.op == "mesh" else f".{d.format}"
    try:
        path = media_file(d.name or f"{d.technique}-{seed}-{digest[:10]}", suffix)
        params = spec.technique_params(d.technique, d.params)
        drawing = techniques.draw(d.technique, params, d.width, d.height, seed)
        table = render.palette(d.palette, d.colours)
        if d.op == "generate" and d.format == "svg":
            payload = render.svg(drawing, d.width, d.height, table)
        else:
            grid = render.tone_map(render.raster(drawing, d.width, d.height), d.tone_map)
            for offset, layer in enumerate(d.layers, start=1):
                other = techniques.draw(layer.technique, spec.technique_params(layer.technique, layer.params),
                                        d.width, d.height, (seed + offset) % 2**32)
                grid = render.blend(grid, render.raster(other, d.width, d.height), layer.blend, layer.weight)
            grid = render.apply_filters(grid, d.filters)
            payload = (render.obj(grid, d.height_scale, f"g6 adapt_generative_art {d.technique} seed {seed}")
                       if d.op == "mesh" else render.png(grid, table))
        written = write_media(path, payload, overwrite=d.overwrite)
    except MediaError as exc:
        return refuse(d.op, exc.code, exc.reason, **_ids(d))
    except Exception as exc:  # noqa: BLE001 - a technique that fails is a refusal, never a partial picture
        return refuse(d.op, BACKEND_FAILED, f"{d.technique} failed: {type(exc).__name__}: {exc}"[:500], **_ids(d))
    if d.op == "mesh":
        requested = {"vertices": d.width * d.height, "faces": 2 * (d.width - 1) * (d.height - 1), "width": d.width,
                     "height": d.height, "format": "obj"}
        inspection = inspect_mesh(path, vertices=requested["vertices"], faces=requested["faces"], min_axes=3)
    else:
        requested = {"width": d.width, "height": d.height, "format": d.format}
        inspection = (inspect_svg(path, width=d.width, height=d.height) if d.format == "svg"
                      else inspect_image(path, width=d.width, height=d.height, formats=("PNG",)))
    drawn = {"kind": "technique", "technique": d.technique, "seed": seed, **drawing.info}
    if not inspection.passed:
        failure = inspection.failure
        removed = discard(path)
        return refuse(d.op, failure.code, f"{d.technique}: {failure.detail}",
                      evidence=[inspection.evidence(), {**drawn, "file_removed": removed}], **_ids(d))
    artifact = media_artifact(path, inspection, Provenance(backend=BACKEND, backend_version=_version(),
                                                           technique=d.technique, seed=seed, recipe=recipe,
                                                           recipe_sha256=digest), requested)
    extra: dict[str, Any] = {}
    if d.op == "mesh":
        extra["scene"] = SceneSpec(objects=(SceneObject(name="terrain" if d.technique == "terrain" else "heightmap",
                                                        mesh=artifact.name),)).model_dump(mode="json")
    what = "mesh" if d.op == "mesh" else f"{d.format.upper()} picture"
    return answer(d.op, f"a {d.technique} {what}, inspected: it decodes, is the size asked for and is not blank",
                  artifact=artifact, evidence=[drawn], technique=d.technique, seed=seed, written=written, **extra,
                  **_ids(d))


def _techniques(d: GenerativeArtInput) -> MediaResult:
    return answer("techniques", "the techniques and their parameters, from the package's own models", card=DESCRIBED,
                  evidence=[{"kind": "package_table", "techniques": len(spec.TECHNIQUES)}],
                  techniques=spec.describe(), palettes=sorted(spec.PALETTES), tone_maps=["none", "equalise",
                                                                                          "reinhard"],
                  filters=list(spec.FILTERS), blends=["add", "multiply", "screen", "overlay", "max", "min",
                                                      "difference"], work_limit=spec.WORK_LIMIT, **_ids(d))


@block_contract(
    purpose="make procedural pictures and heightmap meshes from seeded techniques, each inspected before it is "
            "returned",
    mitigates_failure_modes=("visual_check_failed",),
    verification_method="g6.media inspectors: the file decodes, is the format and size asked for, and is not blank",
)
@component_maturity(
    "beta",
    reason="deterministic NumPy techniques from a seed; every file is inspected (decodes, size, not blank) and a "
           "failed one refused; qualified-draft at best: nothing judges what a picture shows",
)
class AdaptGenerativeArtBlock(AIBlock[GenerativeArtInput, dict, dict]):
    """Make a procedural picture or mesh and say what was measured about it. Never ``verified``."""

    def __init__(self, name: str = "adapt_generative_art", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: GenerativeArtInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _techniques(data) if data.op == "techniques" else _make(data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


class ProceduralBackend:
    """The media family's ``procedural`` image backend: a named technique (in ``options``) drawn on this machine, with
    no network and no cost. It reads no prompt, so a request with one is refused rather than drawn from guesswork."""

    name, local, network, paid = BACKEND, True, False, False

    def available(self) -> str:
        missing = _missing_extra()
        return f"{' and '.join(missing)} not installed: pip install 'g6[procedural] @ git+https://github.com/basicisabc1994/g6solver'" if missing else ""

    def generate(self, request: ImageRequest) -> MediaResult:
        ids = {"request_id": request.request_id, "run_id": request.run_id}
        if request.prompt:
            return refuse("generate", INPUT_INVALID, "the procedural backend draws a named technique and reads no "
                                                     "prompt: put technique and params in options", **ids)
        clash = sorted(set(request.options) & {"op", "width", "height", "seed", "name", "overwrite", "format",
                                               "request_id", "run_id"})
        if clash:
            return refuse("generate", INPUT_INVALID, f"set {', '.join(clash)} on the request, not in options", **ids)
        try:
            data = GenerativeArtInput.model_validate({**request.options, "op": "generate", "width": request.width,
                                                      "height": request.height, "seed": request.seed,
                                                      "name": request.name, "overwrite": request.overwrite,
                                                      "format": request.format, **ids})
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(x) for x in first.get("loc") or ()) or "options"
            return refuse("generate", INPUT_INVALID, f"{where}: {first.get('msg', '')}"[:500], **ids)
        return _make(data)


__all__ = ["BACKEND", "AdaptGenerativeArtBlock", "GenerativeArtInput", "ProceduralBackend"]
