# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_comfyui — make and edit pictures on the ComfyUI server you run, each one fetched into G6's media folder and inspected.

An extension in the media family, and the family's ``comfyui`` image backend. ComfyUI is a program you install and
start yourself (``__g6_outside__``); G6 reaches it only through ``g6.core.outside.call_service``, plain HTTP on this
machine at 127.0.0.1:8188 or the local address ``G6_COMFYUI_URL`` names, and never installs, bundles or stands in for
it. A server that is not answering is a ``blocked-escalated`` refusal with the install steps.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_comfyui", "txt2img", {"prompt": "a lighthouse at dusk",
                                                             "model": "your-checkpoint.safetensors"}).unwrap()
    out["artifact"]["path"], out["completion_state"]   # a PNG under $G6_HOME/media, "qualified-draft"

What it does:

* ``txt2img``, ``img2img``, ``inpaint`` and ``upscale`` build graphs from ComfyUI's core nodes; ``submit_workflow``
  sends your own API-format graph. Every graph is checked against the server's ``/object_info`` before it is sent:
  each node class exists and is one of ComfyUI's core nodes or one the person who runs G6 names in
  ``G6_COMFYUI_NODES`` (any other, a custom node or one of ComfyUI's API nodes that spend money, is refused by name),
  each link names a node and an output it has, each required input is given, and the only output node is
  ``PreviewImage``, whose pictures stay in ComfyUI's temporary folder until G6 copies them into its own.
  G6's own graphs also need the checkpoint, upscale model, sampler and scheduler to be ones the server lists;
* it polls the prompt's history, treating an errored prompt as a failure and a fully cached one (ComfyUI answers it
  with no outputs) as the earlier run of exactly the same graph, which it names; ``get_image`` fetches a finished
  prompt's pictures later;
* every picture is written into the media folder and inspected from disk: it decodes, is a PNG, is the size asked for
  and is not blank, and there are as many as were asked for; an upscale must come back larger by one whole factor.
  Any failure refuses the whole result and removes the files (``G6_E_MEDIA_NO_OUTPUT``, ``_BLANK``,
  ``_SIZE_MISMATCH``). A result is ``qualified-draft``: the inspector measures, nothing judges what a picture shows;
* ``list_models``, ``capabilities`` and ``get_status`` pass on what the server says about itself.

Every op but ``ops`` and ``help`` talks to your ComfyUI, so every one is sensitive: it runs from Python, and over MCP
only when the person who runs the server lists it (or ``adapt_comfyui``) in ``G6_MCP_SENSITIVE``.

Not ported from the archive's adapt_comfyui, and why:

* the comfy_script bridge, which ran caller Python inside G6's process: workflow JSON covers the need;
* the p5.js code generator, the prompt library, brand kits, format presets, projects, assets, style references and the
  design pipeline, the result store, the advisory recommender (it called a model router by default) and the scaffold;
* ``download_model``, which streamed any URL into the models folder: the person puts models there themselves;
* ``interrupt``, raw uploads, node schemas and the native backend operations; the archive's settings-file server
  address (the address is the declared one or the operator's ``G6_COMFYUI_URL``, on this machine only);
* LoRA, SDXL and ControlNet presets (a ``submit_workflow`` graph can use those nodes), and the non-core
  ``LoadImageFromBase64`` node the archive's graphs relied on: sources are uploaded and read with ``LoadImage``.
"""
from g6.media._types import image_backends
from g6.media.adapt_comfyui import workflows
from g6.media.adapt_comfyui.block import (
    COMFYUI,
    SENSITIVE_OPS,
    URL_ENV,
    AdaptComfyUIBlock,
    ComfyUIBackend,
    ComfyUIInput,
)

__g6_component__ = True
__g6_block__ = "AdaptComfyUIBlock"
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # each one talks to the ComfyUI server on your machine
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("visual_check_failed",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (COMFYUI,)
__g6_extras__: dict[str, tuple[str, ...]] = {"image": ("PIL",)}
__g6_image_backends__: tuple[str, ...] = ("comfyui",)  # the family's registry imports this package to find it

image_backends.register(ComfyUIBackend(), replace=True)

__all__ = ["COMFYUI", "SENSITIVE_OPS", "URL_ENV", "AdaptComfyUIBlock", "ComfyUIBackend", "ComfyUIInput", "workflows"]
