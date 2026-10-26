# SPDX-License-Identifier: Apache-2.0
"""AdaptComfyUIBlock, the registry and MCP door (``g6_invoke``) to adapt_comfyui, and the ``comfyui`` image backend it
registers with the media family.

Every op but ``ops`` and ``help`` talks to the ComfyUI server the person runs, so every one is in
``__g6_sensitive_ops__``: it runs from Python, and over MCP only when the person who runs the server lists it (or
``adapt_comfyui``) in ``G6_MCP_SENSITIVE``. A missing server is the honest ``G6_E_OUTSIDE_PROGRAM_MISSING`` refusal
with install steps; a picture the server made is fetched into G6's media folder, inspected from disk, and refused
(with the file removed) when it is missing, blank, the wrong size or not a PNG. A result is ``qualified-draft`` at best.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from g6.core import outside
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media._inspect import MAX_INPUT_PIXELS, inspect_image, pillow
from g6.media._types import (
    EXTRA_MISSING,
    INPUT_INVALID,
    NO_OUTPUT,
    PENDING,
    REPORTED,
    SIZE_MISMATCH,
    Check,
    ImageRequest,
    Inspection,
    MediaArtifact,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    check_name,
    discard,
    media_artifact,
    media_file,
    media_source,
    refuse,
    write_media,
)
from g6.media.adapt_comfyui import workflows
from g6.media.adapt_comfyui.client import Comfy, pictures

BACKEND = "comfyui"
URL_ENV = "G6_COMFYUI_URL"
COMFYUI = outside.OutsideProgram(
    name="ComfyUI",
    purpose="every adapt_comfyui op, and the media family's comfyui image backend, run their workflows on it",
    install="Install ComfyUI (github.com/comfyanonymous/ComfyUI), put the checkpoints and upscale models you want in "
            "its models folders, and start it with python main.py (it listens on 127.0.0.1:8188; set "
            f"{URL_ENV} where G6 runs to use another local address)",
    licence="GPL-3.0 (G6 talks to it over HTTP and never bundles it)", url="http://127.0.0.1:8188", env=URL_ENV)
_GRAPHS = ("txt2img", "img2img", "inpaint", "upscale")
_PICTURES = (*_GRAPHS, "submit_workflow", "get_image")
_SAYS = " (sensitive: it {}; from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)"
_RUNS, _ASKS = _SAYS.format("runs a workflow on your ComfyUI"), _SAYS.format("asks your ComfyUI")
_OPS = {
    "txt2img": "txt2img(prompt, model, negative_prompt, width, height, steps, cfg, sampler, scheduler, seed, "
               "batch_size, name, overwrite, wait, timeout): pictures from a prompt with a checkpoint the server has, "
               "each inspected" + _RUNS,
    "img2img": "img2img(source, prompt, model, denoise, ...): a picture in the media folder sampled again from a "
               "prompt" + _RUNS,
    "inpaint": "inpaint(source, mask, prompt, model, ...): a picture repainted where the mask's red channel is "
               "white" + _RUNS,
    "upscale": "upscale(source, model): a picture enlarged by an upscale model the server has; it must come back "
               "larger by one whole factor in both directions" + _RUNS,
    "submit_workflow": "submit_workflow(workflow, images, width, height, wait, timeout): your own API-format graph of "
                       "ComfyUI's core nodes (and any the person who runs G6 allows), checked against the server's "
                       "nodes and ending in PreviewImage; its pictures inspected" + _RUNS,
    "get_image": "get_image(prompt_id, images, width, height): the pictures of a prompt already run, fetched and "
                 "inspected" + _ASKS,
    "list_models": "list_models(): the checkpoints and upscale models the server lists" + _ASKS,
    "capabilities": "capabilities(): the server's version, devices and node count, and which of G6's workflows its "
                    "nodes and models can run" + _ASKS,
    "get_status": "get_status(): the server's version, devices and queue" + _ASKS,
}
SENSITIVE_OPS: tuple[str, ...] = tuple(_OPS)
_NAME = r"^[A-Za-z0-9_.+-]{1,64}$"


def _plain(value: str) -> str:
    if value:
        try:
            check_name(value)
        except MediaError as exc:
            raise ValueError(exc.reason) from None
    return value


class ComfyUIInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["txt2img", "img2img", "inpaint", "upscale", "submit_workflow", "get_image", "list_models",
                "capabilities", "get_status", "ops", "help"] = "help"
    prompt: str = Field("", max_length=4000)
    negative_prompt: str = Field("", max_length=4000)
    model: str = Field("", max_length=300)
    width: int | None = Field(None, ge=16, le=4096)
    height: int | None = Field(None, ge=16, le=4096)
    steps: int = Field(20, ge=1, le=150)
    cfg: float = Field(7.0, ge=0, le=30, allow_inf_nan=False)
    sampler: str = Field("euler", pattern=_NAME)
    scheduler: str = Field("normal", pattern=_NAME)
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    batch_size: int = Field(1, ge=1, le=8)
    denoise: float | None = Field(None, gt=0, le=1, allow_inf_nan=False)
    source: str = Field("", max_length=100)
    mask: str = Field("", max_length=100)
    workflow: dict[str, Any] | None = None
    prompt_id: str = Field("", pattern=r"^[A-Za-z0-9-]{0,64}$")
    images: int | None = Field(None, ge=1, le=64)
    wait: bool = True
    timeout: float = Field(300.0, ge=1, le=3600, allow_inf_nan=False)
    name: str = Field("", max_length=90)
    overwrite: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("name", "source", "mask")
    @classmethod
    def _plain_names(cls, value: str) -> str:
        return _plain(value)

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> ComfyUIInput:
        needs = {"txt2img": ("prompt",), "img2img": ("source", "prompt"), "inpaint": ("source", "mask", "prompt"),
                 "upscale": ("source",), "submit_workflow": ("workflow",), "get_image": ("prompt_id",)}
        for field in needs.get(self.op, ()):
            if not getattr(self, field):
                raise ValueError(f"{self.op} needs {field}")
        if self.op in ("txt2img", "img2img", "inpaint"):
            for side in ("width", "height"):
                value = getattr(self, side)
                if value is not None and value % 8:
                    raise ValueError(f"{side} must be a multiple of 8 (a latent is an eighth of the picture); "
                                     f"{value} is not")
        if self.op == "upscale" and self.batch_size != 1:
            raise ValueError("upscale makes one picture from one source")
        return self


def _ids(d: ComfyUIInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _digest(graph: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _source(name: str, field: str) -> tuple[bytes, Inspection]:
    """A picture the caller named in the media folder, read and inspected (any PNG, JPEG or WebP that decodes, of at
    most 4096 x 4096 pixels: a larger one is refused from its header). A link, or a second name for a file kept
    elsewhere, is refused unread (:func:`media_source`), so nothing outside the folder is ever sent."""
    path = media_source(name, field)
    found = inspect_image(path, formats=("PNG", "JPEG", "WEBP"), max_pixels=MAX_INPUT_PIXELS)
    if not found.passed:
        raise MediaError(INPUT_INVALID, f"{field} {name!r} in the media folder is not a usable picture: "
                                        f"{found.failure.detail}")
    return path.read_bytes(), found


def _upload(comfy: Comfy, data: bytes, found: Inspection) -> str:
    suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[found.measured["format"]]
    return comfy.upload(data, f"g6-{hashlib.sha256(data).hexdigest()[:20]}{suffix}")


def _model(d: ComfyUIInput, info: dict[str, Any], kind: str) -> str:
    have = workflows.listed(info, kind)
    if d.model:
        return d.model  # checked against the server's list with the rest of the graph
    shown = ", ".join(have[:20]) or "none: put one in its models folder"
    raise MediaError(INPUT_INVALID, f"{d.op} needs model, one of the {kind.replace('_', ' ')} this ComfyUI lists: "
                                    f"{shown}")


def _floor8(value: int) -> int:
    return max(16, value - value % 8)


def _graph(d: ComfyUIInput, comfy: Comfy, info: dict[str, Any], seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """The graph to send and what the pictures must measure (``width``, ``height``, ``images``, ``larger_than``)."""
    if d.op == "submit_workflow":
        return d.workflow or {}, {"width": d.width, "height": d.height, "images": d.images}
    if d.op == "upscale":
        data, found = _source(d.source, "source")
        graph = workflows.upscale(image=_upload(comfy, data, found), model=_model(d, info, "upscale_models"))
        return graph, {"images": 1, "larger_than": (found.measured["width"], found.measured["height"])}
    common = {"prompt": d.prompt, "negative": d.negative_prompt, "model": _model(d, info, "checkpoints"),
              "steps": d.steps, "cfg": d.cfg, "sampler": d.sampler, "scheduler": d.scheduler, "seed": seed}
    if d.op == "txt2img":
        width, height = d.width or 512, d.height or 512
        graph = workflows.txt2img(**common, width=width, height=height, batch=d.batch_size)
        return graph, {"width": width, "height": height, "images": d.batch_size}
    data, found = _source(d.source, "source")
    mask = _source(d.mask, "mask") if d.op == "inpaint" else None  # both read and checked before either is sent
    width = d.width or _floor8(found.measured["width"])
    height = d.height or _floor8(found.measured["height"])
    image = _upload(comfy, data, found)
    if mask is None:
        graph = workflows.img2img(**common, image=image, width=width, height=height, denoise=d.denoise or 0.75)
    else:
        mask_data, mask_found = mask
        graph = workflows.inpaint(**common, image=image, mask=_upload(comfy, mask_data, mask_found), width=width,
                                  height=height, denoise=d.denoise or 1.0)
    return graph, {"width": width, "height": height, "images": 1}


def _larger(found: Inspection, than: tuple[int, int]) -> Inspection:
    """``found`` with a check that an upscale came back larger by one whole factor in both directions."""
    w, h = found.measured.get("width", 0), found.measured.get("height", 0)
    ok = w > than[0] and h > than[1] and w * than[1] == h * than[0]
    check = Check(name="larger_by_one_factor", passed=ok, code="" if ok else SIZE_MISMATCH,
                  detail=f"{w}x{h} from a {than[0]}x{than[1]} source"
                         + ("" if ok else ": not larger by the same factor in both directions"))
    return found.model_copy(update={"checks": (*found.checks, check)})


def _collect(d: ComfyUIInput, comfy: Comfy, entry: dict[str, Any], prompt_id: str, want: dict[str, Any],
             version: str) -> MediaResult:
    """Fetch every picture the finished entry lists into the media folder and inspect each; all, or a refusal."""
    ids, listed = _ids(d), pictures(entry)
    cached = str(entry.get("cached_from") or "")
    ran = entry.get("prompt")
    graph = d.workflow if d.op == "submit_workflow" else (
        ran[2] if isinstance(ran, (list, tuple)) and len(ran) > 2 and isinstance(ran[2], dict) else {})
    run = {"kind": "comfyui_run", "prompt_id": prompt_id, "cached_from": cached, "pictures_listed": len(listed),
           "node_classes": sorted({n.get("class_type", "") for n in graph.values() if isinstance(n, dict)})}
    expected = want.get("images")
    if not listed or (expected and len(listed) < expected):
        return refuse(d.op, NO_OUTPUT, f"prompt {prompt_id} listed {len(listed)} pictures"
                                       + (f", not the {expected} asked for" if expected else ""),
                      evidence=[run], prompt_id=prompt_id, **ids)
    base = (d.name[:-4] if d.name.lower().endswith(".png") else d.name) or f"comfyui-{d.op}-{prompt_id[:8]}"
    seed = workflows.seed_of(graph)
    provenance = Provenance(backend=BACKEND, backend_version=f"ComfyUI {version}" if version else "ComfyUI",
                            technique=d.op, seed=seed, recipe={"graph": graph, "prompt_id": prompt_id,
                                                               "cached_from": cached},
                            recipe_sha256=_digest(graph), prompt=d.prompt[:4000])
    requested = {k: v for k, v in (("width", want.get("width")), ("height", want.get("height")),
                                   ("images", expected), ("format", "png")) if v is not None}
    made: list[MediaArtifact] = []
    ours: list[Path] = []
    for index, picture in enumerate(listed, start=1):
        path = media_file(base if len(listed) == 1 else f"{base}-{index}", ".png")
        try:
            data = comfy.view(picture)
            if write_media(path, data, overwrite=d.overwrite) != "unchanged":
                ours.append(path)
        except MediaError as exc:
            for p in ours:
                discard(p)
            code = exc.code if exc.code.startswith("G6_E_MEDIA_") else NO_OUTPUT
            return refuse(d.op, code, f"picture {index} of {len(listed)} ({picture.get('filename')}) was not "
                                      f"saved: {exc.reason}", evidence=[run], prompt_id=prompt_id, **ids)
        found = inspect_image(path, width=want.get("width"), height=want.get("height"), formats=("PNG",))
        if found.passed and want.get("larger_than"):
            found = _larger(found, want["larger_than"])
        if not found.passed:
            removed = [p.name for p in {*ours, path} if discard(p)]
            return refuse(d.op, found.failure.code, f"picture {index} of {len(listed)}: {found.failure.detail}",
                          evidence=[found.evidence(), {**run, "files_removed": sorted(removed)}], prompt_id=prompt_id,
                          **ids)
        made.append(media_artifact(path, found, provenance, requested))
    others = [{"kind": "media_inspection", "inspector": a.inspector, "passed": True,
               "checks": [c.model_dump() for c in a.checks], "measured": dict(a.measured), "mime": a.mime,
               "sha256": a.sha256} for a in made[1:]]
    said = f"{len(made)} picture{'s' if len(made) > 1 else ''} from ComfyUI, each inspected: it decodes, is a PNG" + (
        " of the size asked for" if want.get("width") else "") + " and is not blank"
    return answer(d.op, said + (f" (served from the cached run {cached})" if cached else ""), artifact=made[0],
                  evidence=[*others, run], artifacts=[a.model_dump(mode="json") for a in made], prompt_id=prompt_id,
                  seed=seed, cached_from=cached, **ids)


def _pictures(d: ComfyUIInput, comfy: Comfy) -> MediaResult:
    ids = _ids(d)
    if pillow() is None:
        return refuse(d.op, EXTRA_MISSING, "Pillow is not installed, so no picture could be inspected and none is "
                                           "asked for: pip install 'g6[image] @ git+https://github.com/basicisabc1994/g6solver'", **ids)
    prompt_id = d.prompt_id
    try:
        version = comfy.version()
        if d.op == "get_image":
            entry = comfy.finished(prompt_id, comfy.entry(prompt_id))
            if entry is None:
                return refuse(d.op, PENDING, f"prompt {prompt_id} is not finished (or the server does not know it)",
                              prompt_id=prompt_id, **ids)
            want = {"width": d.width, "height": d.height, "images": d.images}
        else:
            info = comfy.object_info()
            seed = d.seed if d.seed is not None else secrets.randbelow(2**32)
            graph, want = _graph(d, comfy, info, seed)
            found = workflows.problems(graph, info, choices=d.op != "submit_workflow",
                                       permitted=workflows.allowed(comfy.environ))
            if found:
                return refuse(d.op, INPUT_INVALID, "; ".join(found[:6]), **ids)
            prompt_id = comfy.submit(graph)
            if not d.wait:
                return refuse(d.op, PENDING, f"submitted as prompt {prompt_id}; nothing is inspected until you ask "
                                             "get_image with it", prompt_id=prompt_id, seed=workflows.seed_of(graph),
                              **ids)
            entry = comfy.wait(prompt_id, d.timeout)
            if d.op != "submit_workflow":
                entry = {**entry, "prompt": [0, prompt_id, graph]} if "prompt" not in entry else entry
        return _collect(d, comfy, entry, prompt_id, want, version)
    except MediaError as exc:
        return refuse(d.op, exc.code, exc.reason, prompt_id=prompt_id, **ids)


def _reported(d: ComfyUIInput, comfy: Comfy) -> MediaResult:
    ids = _ids(d)
    try:
        stats = comfy.get_json("/system_stats")
        system = stats.get("system") if isinstance(stats, dict) and isinstance(stats.get("system"), dict) else {}
        devices = [str(x.get("name", "")) for x in (stats.get("devices") or []) if isinstance(x, dict)] if isinstance(
            stats, dict) else []
        version = str(system.get("comfyui_version") or "")
        seen = {"kind": "service_report", "service": COMFYUI.name, "version": version,
                "address": outside.service_url(COMFYUI)}
        if d.op == "get_status":
            queue = comfy.get_json("/queue")
            running = len(queue.get("queue_running") or []) if isinstance(queue, dict) else 0
            pending = len(queue.get("queue_pending") or []) if isinstance(queue, dict) else 0
            return answer(d.op, f"ComfyUI {version or '(version not reported)'} answered: {running} running, "
                                f"{pending} waiting", card=REPORTED, evidence=[seen], version=version,
                          devices=devices, queue_running=running, queue_pending=pending, **ids)
        info = comfy.object_info()
        models = {kind: workflows.listed(info, kind) for kind in workflows.MODEL_LISTS}
        if d.op == "list_models":
            return answer(d.op, f"{len(models['checkpoints'])} checkpoints and {len(models['upscale_models'])} "
                                "upscale models listed by ComfyUI", card=REPORTED, evidence=[seen], **models, **ids)
        ready = {}
        for op, needed in workflows.NEEDS.items():
            missing = [c for c in needed if c not in info]
            kind = "upscale_models" if op == "upscale" else "checkpoints"
            ready[op] = {"missing_nodes": missing, "models": len(models[kind]),
                         "ready": not missing and bool(models[kind])}
        return answer(d.op, f"ComfyUI {version or '(version not reported)'} has {len(info)} node classes; G6's "
                            f"workflows it can run: {', '.join(op for op, r in ready.items() if r['ready']) or 'none'}",
                      card=REPORTED, evidence=[seen], version=version, devices=devices, node_classes=len(info),
                      workflows=ready, submit_workflow={
                          "output_node": workflows.OUTPUT_NODE, "max_nodes": workflows.MAX_NODES,
                          "node_classes": sorted(workflows.allowed(comfy.environ) & set(info))}, **models, **ids)
    except MediaError as exc:
        return refuse(d.op, exc.code, exc.reason, **ids)


@block_contract(
    purpose="generate and edit pictures on the ComfyUI server the person runs, each fetched into G6's media folder "
            "and inspected before it is returned",
    mitigates_failure_modes=("visual_check_failed",),
    verification_method="g6.media image inspector on every picture fetched: decodes, a PNG of the size asked for, not "
                        "blank, as many as asked for",
)
@component_maturity(
    "beta",
    reason="a ComfyUI HTTP client through g6.core.outside.call_service; graphs checked against the server's nodes "
           "before they are sent; every picture inspected and a failed one refused; qualified-draft at best: nothing "
           "judges what a picture shows",
)
class AdaptComfyUIBlock(AIBlock[ComfyUIInput, dict, dict]):
    """Run a picture workflow on ComfyUI and say what was measured about each picture. Never ``verified``."""

    def __init__(self, name: str = "adapt_comfyui", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: ComfyUIInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        comfy = Comfy(COMFYUI)
        result = _pictures(data, comfy) if data.op in _PICTURES else _reported(data, comfy)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


class ComfyUIBackend:
    """The media family's ``comfyui`` image backend: a prompt drawn by a checkpoint on the person's ComfyUI (named in
    ``options["model"]``, with ``negative_prompt``, ``steps``, ``cfg``, ``sampler`` and ``scheduler`` there too)."""

    name, local, network, paid = BACKEND, True, False, False

    def available(self) -> str:
        found = outside.detect(COMFYUI)
        return "" if found.found else f"{found.detail}; G6 never installs ComfyUI: {COMFYUI.install}"

    def generate(self, request: ImageRequest) -> MediaResult:
        ids = {"request_id": request.request_id, "run_id": request.run_id}
        allowed = {"model", "negative_prompt", "steps", "cfg", "sampler", "scheduler"}
        extra = sorted(set(request.options) - allowed)
        if extra:
            return refuse("generate", INPUT_INVALID, f"the comfyui backend takes only {', '.join(sorted(allowed))} in "
                                                     f"options, not {', '.join(extra)}", **ids)
        try:
            data = ComfyUIInput.model_validate({**request.options, "op": "txt2img", "prompt": request.prompt,
                                                "width": request.width, "height": request.height,
                                                "seed": request.seed, "name": request.name,
                                                "overwrite": request.overwrite, **ids})
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(x) for x in first.get("loc") or ()) or "options"
            return refuse("generate", INPUT_INVALID, f"{where}: {first.get('msg', '')}"[:500], **ids)
        result = _pictures(data, Comfy(COMFYUI))
        return result.model_copy(update={"operation": "generate"})


__all__ = ["BACKEND", "COMFYUI", "SENSITIVE_OPS", "URL_ENV", "AdaptComfyUIBlock", "ComfyUIBackend", "ComfyUIInput"]
