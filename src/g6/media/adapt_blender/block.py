# SPDX-License-Identifier: Apache-2.0
"""AdaptBlenderBlock, the registry and MCP door (``g6_invoke``) to adapt_blender, and the ``blender`` image backend.

``session_new`` and ``session_delete`` open and remove a working scene in G6's state; every other op runs Blender
(:mod:`g6.media.adapt_blender.runner`, G6's own script, a bounded child process) and is sensitive. An op that changes
the scene reads it back and compares it with what was asked (a transform, a colour, a camera, a light, a whole
``SceneSpec``); a difference is ``G6_E_MEDIA_READBACK_MISMATCH`` and the session keeps its earlier scene. A render is a
PNG inspected from disk (it decodes, is the size asked for and is not blank); an export is a mesh read from disk as far
as its format's inspector reads it (all of an OBJ or STL, a GLB's JSON but not its binary chunk, an FBX's header
alone), which its card (``G6_E_MEDIA_MESH_READ``) says. Results are ``qualified-draft`` at best.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from g6.core import outside
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media._inspect import inspect_image, inspect_mesh, pillow
from g6.media._scene import Colour, ObjectName, Primitive, SceneSpec, Vec3
from g6.media._types import (
    EXTRA_MISSING,
    INPUT_INVALID,
    INSPECTED,
    MESH_READ,
    READ_BACK,
    READBACK_MISMATCH,
    REPORTED,
    SESSION,
    Check,
    ImageRequest,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    check_name,
    discard,
    media_artifact,
    media_file,
    media_root,
    media_source,
    place,
    refuse,
)
from g6.media.adapt_blender import inspect3d, runner

_SAYS = " (sensitive: it runs Blender on your machine; from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)"
_OPS = {
    "session_new": "session_new(): a new, empty working scene; its id is the session for every other op",
    "session_delete": "session_delete(session): the working scene and its folder removed",
    "create_primitive": "create_primitive(session, object, primitive, location, rotation_euler, scale, colour): a cube, "
                        "sphere, plane, cylinder, cone or torus, read back",
    "transform_object": "transform_object(session, object, location, rotation_euler, scale): read back",
    "delete_object": "delete_object(session, object): read back as gone",
    "set_material": "set_material(session, object, colour): a base colour, read back",
    "set_camera": "set_camera(session, location, look_at, lens_mm): read back",
    "add_light": "add_light(session, object, kind, location, energy, colour): a point, sun, spot or area light, read "
                 "back",
    "build_scene": "build_scene(session, scene): a whole SceneSpec (objects in a tree, camera, lights) built in a fresh "
                   "scene and every object read back",
    "render": "render(session, width, height, engine=eevee|cycles|workbench, samples, name): a PNG, inspected",
    "viewport_preview": "viewport_preview(session, width, height, name): a quick solid-shaded PNG, inspected",
    "export": "export(session, format=obj|stl|glb|fbx, name): the scene's meshes as a file, inspected",
    "import_file": "import_file(session, source): an OBJ, STL or glTF file from the media folder that names no other "
                   "file, added to the scene",
    "list_objects": "list_objects(session): what Blender says the scene holds",
    "capabilities": "capabilities(): Blender's version, render engines and exporters",
}
SESSION_OPS = ("session_new", "session_delete")
SENSITIVE_OPS: tuple[str, ...] = tuple(op for op in _OPS if op not in SESSION_OPS)
_SUFFIX = {"obj": ".obj", "stl": ".stl", "glb": ".glb", "fbx": ".fbx"}
#: What import_file reads. Not FBX: its texture paths are absolute and Blender opens them wherever they are, and G6
#: does not read FBX far enough to see them first.
_MESH_IN = (".obj", ".stl", ".glb", ".gltf")
TOLERANCE = 1e-4


def _plain(value: str) -> str:
    if value:
        try:
            check_name(value)
        except MediaError as exc:
            raise ValueError(exc.reason) from None
    return value


class BlenderInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["session_new", "session_delete", "create_primitive", "transform_object", "delete_object",
                "set_material", "set_camera", "add_light", "build_scene", "render", "viewport_preview", "export",
                "import_file", "list_objects", "capabilities", "ops", "help"] = "help"
    session: str = Field("", pattern=r"^(s-[0-9a-f]{12})?$")
    object: ObjectName | None = None
    primitive: Primitive = "cube"
    location: Vec3 | None = None
    rotation_euler: Vec3 | None = None
    scale: tuple[float, float, float] | None = None
    colour: Colour | None = None
    look_at: Vec3 = (0.0, 0.0, 0.0)
    lens_mm: float = Field(50.0, ge=1, le=500, allow_inf_nan=False)
    kind: Literal["point", "sun", "spot", "area"] = "point"
    energy: float = Field(1000.0, gt=0, le=1e7, allow_inf_nan=False)
    scene: SceneSpec | None = None
    width: int = Field(512, ge=16, le=4096)
    height: int = Field(512, ge=16, le=4096)
    engine: Literal["eevee", "cycles", "workbench"] = "eevee"
    samples: int = Field(32, ge=1, le=4096)
    format: Literal["obj", "stl", "glb", "fbx"] = "obj"
    source: str = Field("", max_length=100)
    name: str = Field("", max_length=90)
    overwrite: bool = False
    timeout: float = Field(runner.DEFAULT_SECONDS, ge=1, le=600, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("source", "name")
    @classmethod
    def _plain_names(cls, value: str) -> str:
        return _plain(value)

    @field_validator("scale")
    @classmethod
    def _positive(cls, value: tuple[float, float, float] | None) -> tuple[float, float, float] | None:
        if value is not None and not all(0 < v <= 1e6 for v in value):
            raise ValueError("a scale is three numbers above 0, at most 1e6")
        return value

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> BlenderInput:
        if self.op in ("ops", "help", "session_new", "capabilities"):
            return self
        if not self.session:
            raise ValueError(f"{self.op} needs session: an id from session_new")
        if self.op in ("create_primitive", "transform_object", "delete_object", "set_material", "add_light") and \
                not self.object:
            raise ValueError(f"{self.op} needs object: the name of an object in the scene")
        if self.op == "set_material" and self.colour is None:
            raise ValueError("set_material needs colour: three numbers from 0 to 1")
        if self.op == "set_camera" and self.location is None:
            raise ValueError("set_camera needs location")
        if self.op == "transform_object" and not (self.location or self.rotation_euler or self.scale):
            raise ValueError("transform_object needs location, rotation_euler or scale")
        if self.op == "build_scene" and self.scene is None:
            raise ValueError("build_scene needs scene: a SceneSpec")
        if self.op == "import_file" and self.source.lower().endswith(".fbx"):
            raise ValueError("an FBX file is not imported: its texture paths are absolute and Blender opens them "
                             "wherever they are; export it as GLB and import that")
        if self.op == "import_file" and not self.source.lower().endswith(_MESH_IN):
            raise ValueError(f"import_file needs source: a {', '.join(_MESH_IN)} file in the media folder")
        return self

    def transform(self) -> dict[str, Any]:
        return {k: list(v) for k, v in (("location", self.location), ("rotation_euler", self.rotation_euler),
                                        ("scale", self.scale)) if v is not None}


def _ids(d: BlenderInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _close(a: Any, b: Any, tolerance: float = TOLERANCE) -> bool:
    return a is not None and b is not None and len(a) == len(b) and all(abs(x - y) <= tolerance for x, y in zip(a, b))


def _objects(scene: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["name"]: o for o in scene.get("objects") or [] if isinstance(o, dict) and "name" in o}


def _object_checks(name: str, found: dict[str, dict[str, Any]], transform: dict[str, Any], colour: Any,
                   parent: Any = ..., label: str = "transform_confirmed") -> list[Check]:
    got = found.get(name)
    if got is None:
        return [Check(name=label, passed=False, code=READBACK_MISMATCH, detail=f"{name} is not in the scene read back")]
    out = []
    off = [f"{k} {got.get(k)} (asked {v})" for k, v in transform.items() if not _close(got.get(k), v)]
    if parent is not ... and got.get("parent") != parent:
        off.append(f"parent {got.get('parent')} (asked {parent})")
    out.append(Check(name=label, passed=not off, code=READBACK_MISMATCH if off else "",
                     detail=f"{name}: " + ("; ".join(off) if off else "read back as asked")))
    if colour is not None:
        ok = _close(got.get("colour"), list(colour), 1e-3)
        out.append(Check(name="material_confirmed", passed=ok, code="" if ok else READBACK_MISMATCH,
                         detail=f"{name}: colour {got.get('colour')}" + ("" if ok else f" (asked {list(colour)})")))
    return out


def _checks(d: BlenderInput, scene: dict[str, Any], result: dict[str, Any]) -> list[Check]:
    found = _objects(scene)
    if d.op == "create_primitive":
        return _object_checks(d.object, found, d.transform(), d.colour)
    if d.op == "transform_object":
        return _object_checks(d.object, found, d.transform(), None)
    if d.op == "set_material":
        return [c for c in _object_checks(d.object, found, {}, d.colour) if c.name == "material_confirmed"]
    if d.op == "delete_object":
        gone = d.object not in found
        return [Check(name="object_removed", passed=gone, code="" if gone else READBACK_MISMATCH,
                      detail=f"{d.object} {'is gone' if gone else 'is still in the scene'}")]
    if d.op == "set_camera":
        cam = scene.get("camera") or {}
        ok = _close(cam.get("location"), list(d.location)) and abs((cam.get("lens") or 0) - d.lens_mm) <= 1e-3
        return [Check(name="camera_confirmed", passed=ok, code="" if ok else READBACK_MISMATCH,
                      detail=f"camera at {cam.get('location')}, lens {cam.get('lens')} mm")]
    if d.op == "add_light":
        got = found.get(d.object) or {}
        light = got.get("light") or {}
        ok = (_close(got.get("location"), list(d.location or (4.0, -4.0, 6.0))) and str(light.get("kind", "")).lower()
              == d.kind and abs(float(light.get("energy") or 0) - d.energy) <= 1e-3 * d.energy)
        return [Check(name="light_confirmed", passed=ok, code="" if ok else READBACK_MISMATCH,
                      detail=f"{d.object}: {light} at {got.get('location')}")]
    if d.op == "build_scene":
        off: list[str] = []
        for o in d.scene.objects:
            t = o.transform.model_dump()
            for c in _object_checks(o.name, found, {k: list(v) for k, v in t.items()}, o.colour, o.parent):
                if not c.passed:
                    off.append(c.detail)
        for light in d.scene.lights:
            if light.name not in found:
                off.append(f"light {light.name} is not in the scene read back")
        if d.scene.camera is not None and not _close((scene.get("camera") or {}).get("location"),
                                                     list(d.scene.camera.location)):
            off.append(f"camera at {(scene.get('camera') or {}).get('location')}")
        return [Check(name="scene_matches_spec", passed=not off, code=READBACK_MISMATCH if off else "",
                      detail="; ".join(off)[:500] if off else f"{len(d.scene.objects)} objects, "
                                                             f"{len(d.scene.lights)} lights read back as asked")]
    if d.op == "import_file":
        added = [n for n in result.get("imported") or [] if isinstance(n, str)]
        ok = bool(added) and all(n in found for n in added)
        return [Check(name="imported", passed=ok, code="" if ok else READBACK_MISMATCH,
                      detail=f"{len(added)} objects added from {d.source}: {added[:20]}" if ok else
                      f"nothing from {d.source} is in the scene read back")]
    return []


def _params(d: BlenderInput) -> dict[str, Any]:
    if d.op == "create_primitive":
        return {"object": d.object, "primitive": d.primitive, "transform": d.transform(),
                "colour": list(d.colour) if d.colour else None}
    if d.op in ("transform_object", "delete_object", "set_material"):
        return {"object": d.object, "transform": d.transform(), "colour": list(d.colour) if d.colour else None}
    if d.op == "set_camera":
        return {"location": list(d.location), "look_at": list(d.look_at), "lens_mm": d.lens_mm}
    if d.op == "add_light":
        return {"object": d.object, "kind": d.kind, "location": list(d.location or (4.0, -4.0, 6.0)),
                "energy": d.energy, "colour": list(d.colour or (1.0, 1.0, 1.0))}
    if d.op == "build_scene":
        spec = d.scene.model_dump(mode="json")
        for o in spec["objects"]:
            if o.get("mesh"):
                path = media_source(o["mesh"], "mesh")
                inspect3d.self_contained(path)
                o["mesh_path"] = str(path)
        return {"scene": spec}
    if d.op == "import_file":
        path = media_source(d.source)
        inspect3d.self_contained(path)
        return {"path": str(path)}
    if d.op in ("render", "viewport_preview"):
        return {"width": d.width, "height": d.height, "samples": d.samples,
                "engine": "workbench" if d.op == "viewport_preview" else d.engine}
    if d.op == "export":
        return {"format": d.format}
    return {}


def _scene_op(d: BlenderInput) -> MediaResult:
    runner.need(d.session)
    result, _, record = runner.run(d.op, _params(d), session=d.session, save=d.op != "list_objects", seconds=d.timeout)
    scene = result.get("scene") or {}
    if d.op == "list_objects":
        return answer(d.op, f"Blender says the scene holds {len(scene.get('objects') or [])} objects", card=REPORTED,
                      evidence=[record], scene=scene, **_ids(d))
    checks = _checks(d, scene, result)
    readback = {"kind": "readback", "checks": [c.model_dump() for c in checks], "compared_with": "the request"}
    failed = next((c for c in checks if not c.passed), None)
    if failed is not None:
        runner.drop(d.session)
        return refuse(d.op, failed.code, failed.detail, evidence=[readback, record], scene=scene, **_ids(d))
    runner.commit(d.session)
    return answer(d.op, f"{d.op} done in Blender and read back: " + "; ".join(c.detail for c in checks)[:300],
                  card=READ_BACK, evidence=[readback, record], scene=scene, session=d.session, **_ids(d))


def _keep(d: BlenderInput, data: bytes, suffix: str, inspect: Any, requested: dict[str, Any], said: str,
          evidence: list[dict[str, Any]], card: str = INSPECTED, **fields: Any) -> MediaResult:
    temp = media_root() / f".partial-{secrets.token_hex(8)}{suffix}"
    try:
        final = media_file(d.name or f"blender-{d.op}-{secrets.token_hex(6)}", suffix)
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(data)
        found = inspect(temp)
        if not found.passed:
            discard(temp)
            return refuse(d.op, found.failure.code, found.failure.detail, evidence=[found.evidence(), *evidence],
                          **fields, **_ids(d))
        written = place(temp, final, overwrite=d.overwrite)
    except MediaError as exc:
        discard(temp)
        return refuse(d.op, exc.code, exc.reason, evidence=evidence, **fields, **_ids(d))
    recipe = {"op": d.op, "params": _params(d), "session": d.session}
    provenance = Provenance(backend="blender", backend_version=str(evidence[-1].get("version") or "")[:200],
                            technique=d.op, recipe=recipe,
                            recipe_sha256=hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest())
    artifact = media_artifact(final, found, provenance, requested)
    return answer(d.op, said, artifact=artifact, card=card, evidence=evidence, written=written, **fields, **_ids(d))


def _output(d: BlenderInput) -> MediaResult:
    runner.need(d.session)
    picture = d.op in ("render", "viewport_preview")
    if picture and pillow() is None:
        return refuse(d.op, EXTRA_MISSING, "Pillow is not installed, so no render could be inspected and none is "
                                           "made: pip install 'g6[image]'", **_ids(d))
    suffix = ".png" if picture else _SUFFIX[d.format]
    result, data, record = runner.run(d.op, _params(d), session=d.session, save=False, out_suffix=suffix,
                                      seconds=d.timeout)
    fields = {k: bool(result.get(k)) for k in ("camera_placed", "light_placed")} if picture else {}
    fields["scene"] = result.get("scene") or {}
    if picture:
        return _keep(d, data, suffix, lambda p: inspect_image(p, width=d.width, height=d.height, formats=("PNG",)),
                     {"width": d.width, "height": d.height, "format": "png"},
                     f"a {d.width}x{d.height} PNG from Blender, inspected from disk", [record], **fields)
    inspect = {"obj": lambda p: inspect_mesh(p, min_axes=2), "stl": inspect3d.inspect_stl,
               "glb": inspect3d.inspect_glb, "fbx": inspect3d.inspect_fbx}[d.format]
    read = inspect3d.READ[d.format]
    return _keep(d, data, suffix, inspect, {"format": d.format}, f"a {d.format} export from Blender, read from disk: "
                 f"{read}", [{"kind": "mesh_read", "format": d.format, "read": read}, record], card=MESH_READ,
                 **fields)


def _capabilities(d: BlenderInput) -> MediaResult:
    found = outside.detect(runner.BLENDER)
    result, _, record = runner.run("capabilities", {}, session=None, save=False, seconds=d.timeout)
    return answer(d.op, f"{found.version or 'Blender'} answered", card=REPORTED, evidence=[record],
                  version=found.version, where=found.where, engines=result.get("engines") or [],
                  exporters=result.get("exporters") or [], **_ids(d))


def handle(d: BlenderInput) -> MediaResult:
    try:
        if d.op == "session_new":
            session = runner.new_session()
            return answer(d.op, f"session {session} opened: an empty scene", card=SESSION,
                          evidence=[{"kind": "session", "session": session}], session=session, **_ids(d))
        if d.op == "session_delete":
            deleted = runner.delete_session(d.session)
            return answer(d.op, f"session {d.session} {'removed' if deleted else 'was not there'}", card=SESSION,
                          evidence=[{"kind": "session", "session": d.session, "deleted": deleted}], deleted=deleted,
                          **_ids(d))
        if d.op == "capabilities":
            return _capabilities(d)
        if d.op in ("render", "viewport_preview", "export"):
            return _output(d)
        return _scene_op(d)
    except MediaError as exc:
        if d.session and d.op not in SESSION_OPS:
            try:
                runner.drop(d.session)
            except MediaError:
                pass
        return refuse(d.op, exc.code, exc.reason, **_ids(d))


@block_contract(
    purpose="build, light, render and export 3D scenes with the Blender the person installed, running only G6's own "
            "script, each change read back and each render and export inspected before it is returned",
    mitigates_failure_modes=("visual_check_failed",),
    verification_method="the scene read back from Blender and compared with the request; g6.media image inspector on "
                        "every render (decodes, the size asked for, not blank); a mesh inspector on every export",
)
@component_maturity(
    "beta",
    reason="Blender over its command line through g6.core.component_process with G6's own script and a clean "
           "environment; every change read back, every render and export inspected; qualified-draft at best",
)
class AdaptBlenderBlock(AIBlock[BlenderInput, dict, dict]):
    """Do one Blender op and say what was read back or measured. Never ``verified``."""

    def __init__(self, name: str = "adapt_blender", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: BlenderInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help",
                              "help": [v + ("" if k in SESSION_OPS else _SAYS) for k, v in _OPS.items()]})
        return Result.ok({"component": self.name, "op": data.op, **handle(data).model_dump(mode="json")})


class BlenderBackend:
    """The media family's ``blender`` image backend: a ``SceneSpec`` (``options["scene"]``) built in a throwaway
    session and rendered at the size asked for (``options["engine"]``, ``options["samples"]`` optional)."""

    name, local, network, paid = "blender", True, False, False

    def available(self) -> str:
        found = outside.detect(runner.BLENDER)
        return "" if found.found else f"{found.detail}; G6 never installs Blender: {runner.BLENDER.install}"

    def generate(self, request: ImageRequest) -> MediaResult:
        ids = {"request_id": request.request_id, "run_id": request.run_id}
        extra = sorted(set(request.options) - {"scene", "engine", "samples"})
        if extra or "scene" not in request.options:
            return refuse("generate", INPUT_INVALID, "the blender backend takes options scene (a SceneSpec), engine "
                                                     "and samples" + (f", not {', '.join(extra)}" if extra else ""),
                          **ids)
        session = runner.new_session()
        try:
            common = {"session": session, "timeout": runner.DEFAULT_SECONDS, **ids}
            built = handle(BlenderInput(op="build_scene", scene=request.options["scene"], **common))
            if built.completion_state == "blocked-escalated":
                return built.model_copy(update={"operation": "generate"})
            shot = handle(BlenderInput(op="render", width=request.width, height=request.height, name=request.name,
                                       overwrite=request.overwrite, engine=request.options.get("engine", "eevee"),
                                       samples=request.options.get("samples", 32), **common))
            return shot.model_copy(update={"operation": "generate"})
        except ValidationError as exc:
            return refuse("generate", INPUT_INVALID, str(exc.errors()[0].get("msg", ""))[:500], **ids)
        finally:
            runner.delete_session(session)


__all__ = ["SENSITIVE_OPS", "SESSION_OPS", "AdaptBlenderBlock", "BlenderBackend", "BlenderInput"]
