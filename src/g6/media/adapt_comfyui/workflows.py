# SPDX-License-Identifier: Apache-2.0
"""The ComfyUI graphs G6 builds, and the checks every graph passes against the server's own node list before it is sent.

A graph is ComfyUI's API format: ``{node_id: {"class_type": ..., "inputs": {...}}}``, where an input is a literal or a
link ``[node_id, output_index]``. G6's graphs use only ComfyUI's core nodes, and end in ``PreviewImage``, which keeps
its pictures in ComfyUI's temporary folder: G6 fetches them into its own media folder, and nothing is added to
ComfyUI's output folder. A source picture reaches a graph as a ``LoadImage`` of a file G6 uploaded into that temporary
folder (``"name.png [temp]"``).

:func:`problems` checks a graph against ``/object_info`` (every node class exists on the server, every link names a
node and an output it has, every required input is given, the only output node is ``PreviewImage``) and against an
allow-list, not a deny-list: every node class is one of ComfyUI's core nodes in :data:`CORE_NODES`, or one the person
who runs G6 names in ``G6_COMFYUI_NODES`` where G6 runs (:func:`allowed`; never a call's input). Any other class the
server lists, a custom node that may run code, fetch addresses or write files, or one of ComfyUI's API nodes that
spend money with an outside service, is refused by name. With ``choices``, which G6 applies to its own graphs, a
literal choice input (a checkpoint, an upscale model, a sampler or a scheduler) must also be one the server lists. A
caller's graph is checked without ``choices`` (a custom node may accept values its list does not show; ComfyUI checks
them when it runs the graph) and must carry no path that leaves a folder.
"""
from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping
from typing import Any

#: The one output node G6 accepts: it keeps its pictures in ComfyUI's temporary folder, which G6 copies from.
OUTPUT_NODE = "PreviewImage"
#: The most nodes a graph may have.
MAX_NODES = 150
#: The core node classes each of G6's own graphs needs.
NEEDS: dict[str, tuple[str, ...]] = {
    "txt2img": ("CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", OUTPUT_NODE),
    "img2img": ("CheckpointLoaderSimple", "CLIPTextEncode", "LoadImage", "ImageScale", "VAEEncode", "KSampler",
                "VAEDecode", OUTPUT_NODE),
    "inpaint": ("CheckpointLoaderSimple", "CLIPTextEncode", "LoadImage", "LoadImageMask", "ImageScale",
                "VAEEncodeForInpaint", "KSampler", "VAEDecode", OUTPUT_NODE),
    "upscale": ("LoadImage", "UpscaleModelLoader", "ImageUpscaleWithModel", OUTPUT_NODE),
}
#: ComfyUI's own core node classes a graph may use without the person naming them: those G6's graphs use, and the
#: common core samplers, loaders, conditioning, latent, image and mask nodes. None reaches the network, runs code a
#: caller wrote or writes outside ComfyUI's temporary folder; the only output node among them is PreviewImage.
CORE_NODES: frozenset[str] = frozenset({
    *(cls for needed in NEEDS.values() for cls in needed),
    # samplers
    "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced", "KSamplerSelect", "BasicScheduler",
    "KarrasScheduler", "BasicGuider", "CFGGuider", "RandomNoise",
    # loaders (each reads a model the person put in ComfyUI's models folders, by a name the server lists)
    "VAELoader", "LoraLoader", "LoraLoaderModelOnly", "CLIPLoader", "DualCLIPLoader", "UNETLoader",
    "ControlNetLoader", "CLIPVisionLoader", "unCLIPCheckpointLoader",
    # text encoding and conditioning
    "CLIPSetLastLayer", "ConditioningCombine", "ConditioningAverage", "ConditioningConcat", "ConditioningSetArea",
    "ConditioningSetAreaPercentage", "ConditioningSetMask", "ConditioningZeroOut", "ConditioningSetTimestepRange",
    "ControlNetApply", "ControlNetApplyAdvanced", "CLIPVisionEncode", "unCLIPConditioning", "InpaintModelConditioning",
    # latents
    "LatentUpscale", "LatentUpscaleBy", "LatentFromBatch", "RepeatLatentBatch", "LatentComposite", "LatentBlend",
    "LatentCrop", "LatentFlip", "LatentRotate", "SetLatentNoiseMask", "VAEDecodeTiled", "VAEEncodeTiled",
    # images and masks
    "ImageScaleBy", "ImageInvert", "ImageBatch", "ImagePadForOutpaint", "EmptyImage", "ImageCrop", "ImageBlend",
    "ImageBlur", "ImageSharpen", "ImageCompositeMasked", "SolidMask", "InvertMask", "MaskToImage", "ImageToMask",
    "GrowMask", "FeatherMask",
})
#: Where the person who runs G6 names more node classes a graph may use, comma-separated (operator configuration).
NODES_ENV = "G6_COMFYUI_NODES"
#: Where each kind of model is listed on the server: (node class, input).
MODEL_LISTS = {"checkpoints": ("CheckpointLoaderSimple", "ckpt_name"),
               "upscale_models": ("UpscaleModelLoader", "model_name")}
_ANNOTATED = re.compile(r".* \[(?:input|temp|output)\]$")
_LEAVES_FOLDER = re.compile(r"(?:\.\.[\\/])|(?:^[\\/])|(?:^[A-Za-z]:[\\/])")


def _sampling(seed: int, steps: int, cfg: float, sampler: str, scheduler: str, denoise: float, model: list,
              positive: list, negative: list, latent: list) -> dict[str, Any]:
    return {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler,
                                                 "scheduler": scheduler, "denoise": denoise, "model": model,
                                                 "positive": positive, "negative": negative,
                                                 "latent_image": latent}}


def _texts(prompt: str, negative: str) -> dict[str, Any]:
    return {"2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}}}


def _decode_and_preview(samples: str) -> dict[str, Any]:
    return {"8": {"class_type": "VAEDecode", "inputs": {"samples": [samples, 0], "vae": ["1", 2]}},
            "9": {"class_type": OUTPUT_NODE, "inputs": {"images": ["8", 0]}}}


def txt2img(*, prompt: str, negative: str, model: str, width: int, height: int, steps: int, cfg: float,
            sampler: str, scheduler: str, seed: int, batch: int) -> dict[str, Any]:
    """Checkpoint, two text encodings, an empty latent, a sampler, a decode and a preview."""
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}}, **_texts(prompt, negative),
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch}},
            "7": _sampling(seed, steps, cfg, sampler, scheduler, 1.0, ["1", 0], ["2", 0], ["3", 0], ["4", 0]),
            **_decode_and_preview("7")}


def img2img(*, image: str, prompt: str, negative: str, model: str, width: int, height: int, steps: int, cfg: float,
            sampler: str, scheduler: str, seed: int, denoise: float) -> dict[str, Any]:
    """The uploaded picture scaled to ``width`` by ``height``, encoded, and sampled again at ``denoise``."""
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}}, **_texts(prompt, negative),
            "4": {"class_type": "LoadImage", "inputs": {"image": image}},
            "5": {"class_type": "ImageScale", "inputs": {"image": ["4", 0], "width": width, "height": height,
                                                         "upscale_method": "bilinear", "crop": "disabled"}},
            "6": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["1", 2]}},
            "7": _sampling(seed, steps, cfg, sampler, scheduler, denoise, ["1", 0], ["2", 0], ["3", 0], ["6", 0]),
            **_decode_and_preview("7")}


def inpaint(*, image: str, mask: str, prompt: str, negative: str, model: str, width: int, height: int, steps: int,
            cfg: float, sampler: str, scheduler: str, seed: int, denoise: float) -> dict[str, Any]:
    """The uploaded picture repainted where the uploaded mask's red channel is white."""
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}}, **_texts(prompt, negative),
            "4": {"class_type": "LoadImage", "inputs": {"image": image}},
            "5": {"class_type": "ImageScale", "inputs": {"image": ["4", 0], "width": width, "height": height,
                                                         "upscale_method": "bilinear", "crop": "disabled"}},
            "10": {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}},
            "6": {"class_type": "VAEEncodeForInpaint", "inputs": {"pixels": ["5", 0], "vae": ["1", 2],
                                                                  "mask": ["10", 0], "grow_mask_by": 6}},
            "7": _sampling(seed, steps, cfg, sampler, scheduler, denoise, ["1", 0], ["2", 0], ["3", 0], ["6", 0]),
            **_decode_and_preview("7")}


def upscale(*, image: str, model: str) -> dict[str, Any]:
    """The uploaded picture enlarged by an upscale model the server has."""
    return {"1": {"class_type": "LoadImage", "inputs": {"image": image}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
            "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
            "9": {"class_type": OUTPUT_NODE, "inputs": {"images": ["3", 0]}}}


def options(spec: Any) -> list[str] | None:
    """The values a choice input allows, from its ``/object_info`` spec (``[[a, b], {...}]`` or
    ``["COMBO", {"options": [a, b]}]``); ``None`` when the input is not a choice."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    if isinstance(spec[0], list):
        return [str(v) for v in spec[0]]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict) and isinstance(spec[1].get("options"), list):
        return [str(v) for v in spec[1]["options"]]
    return None


def listed(object_info: dict[str, Any], kind: str) -> list[str]:
    """The models of ``kind`` (``checkpoints`` or ``upscale_models``) the server lists, ``[]`` when it has none."""
    node, field = MODEL_LISTS[kind]
    spec = ((object_info.get(node) or {}).get("input") or {}).get("required", {}).get(field)
    return options(spec) or []


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _is_link(value: Any) -> bool:
    return (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
            and isinstance(value[1], int) and not isinstance(value[1], bool))


def allowed(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """The node classes a graph may use: :data:`CORE_NODES` and those the person who runs G6 names in
    :data:`NODES_ENV`, read from the environment G6 runs in (``environ`` replaces it), never from a call."""
    environ = os.environ if environ is None else environ
    named = frozenset(cls.strip() for cls in environ.get(NODES_ENV, "").split(",") if cls.strip())
    return CORE_NODES | named


def problems(graph: Any, object_info: dict[str, Any], *, choices: bool = False,
             permitted: Collection[str] = CORE_NODES) -> list[str]:
    """What is wrong with ``graph`` for this server, in words; ``[]`` when nothing is. Every node class must be in
    ``permitted`` (:func:`allowed` gives ComfyUI's core nodes and the person's own additions)."""
    if not isinstance(graph, dict) or not graph:
        return ["a workflow is a non-empty JSON object of nodes"]
    if len(graph) > MAX_NODES:
        return [f"a workflow has at most {MAX_NODES} nodes; this one has {len(graph)}"]
    found: list[str] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(
                node.get("inputs", {}), dict):
            found.append(f"node {node_id!r} is not {{'class_type': str, 'inputs': {{...}}}}")
    if found:
        return found
    missing = sorted({n["class_type"] for n in graph.values() if n["class_type"] not in object_info})
    if missing:
        return [f"this ComfyUI has no node class {', '.join(missing)}: install the custom nodes or change the "
                "workflow"]
    outputs = 0
    for node_id, node in graph.items():
        cls, inputs = node["class_type"], node.get("inputs", {})
        info = object_info[cls] or {}
        if info.get("output_node"):
            if cls != OUTPUT_NODE:
                found.append(f"node {node_id!r} is a {cls}, an output node that writes outside G6's media folder; end "
                             f"the workflow in {OUTPUT_NODE}, whose pictures G6 copies into its media folder")
            outputs += 1
        elif cls not in permitted:
            found.append(f"node {node_id!r} is a {cls}, which is not one of ComfyUI's core nodes G6 sends (a custom node "
                         "may run code, fetch addresses or write files; an API node spends money with an outside "
                         f"service); the person who runs G6 can allow it by name in {NODES_ENV} where G6 runs")
        required = (info.get("input") or {}).get("required") or {}
        for field in required:
            if field not in inputs:
                found.append(f"node {node_id!r} ({cls}) is missing its required input {field!r}")
        for field, value in inputs.items():
            if _is_link(value):
                source = graph.get(value[0])
                if source is None:
                    found.append(f"node {node_id!r} input {field!r} links to node {value[0]!r}, which is not in the "
                                 "workflow")
                    continue
                made = (object_info.get(source["class_type"]) or {}).get("output")
                if isinstance(made, list) and not 0 <= value[1] < len(made):
                    found.append(f"node {node_id!r} input {field!r} asks for output {value[1]} of node "
                                 f"{value[0]!r} ({source['class_type']}), which has {len(made)}")
            elif choices and field in required:
                allowed = options(required[field])
                if allowed is not None and isinstance(value, str) and not _ANNOTATED.match(value) \
                        and value not in allowed:
                    shown = ", ".join(allowed[:20]) or "none"
                    found.append(f"{value!r} is not a {field} this ComfyUI has ({cls}); it lists: {shown}")
            if not choices:
                bad = [s for s in _strings(value) if _LEAVES_FOLDER.search(s)]
                if bad:
                    found.append(f"node {node_id!r} input {field!r} carries a path that leaves its folder "
                                 f"({bad[0][:80]!r}), which G6 does not send")
    if outputs == 0:
        found.append(f"the workflow has no output node; end it in {OUTPUT_NODE}")
    return found


def seed_of(graph: dict[str, Any]) -> int | None:
    """The seed of the first node with a whole-number ``seed`` input, if any."""
    for node in graph.values():
        seed = (node.get("inputs") or {}).get("seed") if isinstance(node, dict) else None
        if isinstance(seed, int) and not isinstance(seed, bool):
            return seed
    return None


__all__ = ["CORE_NODES", "MAX_NODES", "MODEL_LISTS", "NEEDS", "NODES_ENV", "OUTPUT_NODE", "allowed", "img2img",
           "inpaint", "listed", "options", "problems", "seed_of", "txt2img", "upscale"]
