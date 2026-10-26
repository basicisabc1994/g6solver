# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_image — edit, convert and measure pictures, and generate them through the media family's backends, local first, each one inspected.

An extension in the media family. Its Pillow ops read a picture from the media folder (or inline base64, at most 25
MiB), compute the size the result must have, write the result into G6's media folder and inspect it from disk; a
result that does not decode, is the wrong size or format, or is blank is refused and its file removed. A result is
``qualified-draft``: the inspector measures, nothing judges what a picture shows.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_image", "resize", {"source": "photo.png", "width": 320}).unwrap()
    out["artifact"]["path"], out["artifact"]["measured"]   # a PNG under $G6_HOME/media, 320 wide, aspect kept

What it does:

* ``resize`` (Lanczos), ``crop``, ``rotate``, ``flip``, ``adjust_colour``, ``composite`` and ``convert`` (PNG, JPEG,
  WebP), and ``get_image_info``, which decodes a picture in full and measures it;
* ``generate`` over the family's registered local backends: ``comfyui`` (the person's ComfyUI, for a prompt) and
  ``procedural`` (a named technique, no model), or one the person registered from Python; it never falls back to a
  paid one, and never starts a backend another member runs (``blender`` is adapt_blender's: its own ops, or from
  Python ``image_backends.generate("blender", ...)``), so its permission covers no program its text does not name;
* ``generate_paid`` over paid backends the person registers (Stability AI, Replicate, an OpenAI-compatible images
  endpoint; :mod:`g6.media.adapt_image.paid`), each with the key it declares in ``__g6_keys__`` taken from the
  person's key file, ``~/.g6/.env``: every request is a payment that ``g6.action_gating`` decides before anything is
  sent. The gate reads that key in the file as the person's approval to spend it (``allow_with_notice``,
  ``G6_E_KEY_FILE_APPROVED``); without it the reply is the dry run, with the gate's decision and the line to add. The
  caller's ``allow_paid`` is a claim that can only hold a request back. It is sensitive, and so is ``generate`` (it
  may run a workflow on the person's ComfyUI): over MCP each runs only when the person who runs the server lists it
  in ``G6_MCP_SENSITIVE``;
* an optional vision caller (``AdaptImageBlock(vision=...)``, from Python) asked what a result shows, its answer kept
  in the evidence as a claim that never changes the label.

Not ported from the archive's adapt_image and adapt_creative_api (merged in here), and why:

* fetching a picture from a URL (its guard checked only literal addresses), and base64 out: pictures are files;
* the vision ops (description, classification, objects, text), which belong with a shared vision backend; the advanced
  lane, the model planner that recommended an op, the result store, billing and key custody;
* the ComfyUI client with its fixed demo workflow and seed (the family's ``comfyui`` backend replaces it), "upscale" as
  a Lanczos resize labelled ComfyUI, image-to-image by describing a picture and drawing a new one, and variations with
  a fixed seed;
* the silent fallback to a paid online model router; Stability's edit and upscale and Replicate's model listing; the keys
  each client looked up for itself;
* from adapt_creative_api: the MCP tier that called every result verified, its store, the FORMA template library and
  page, native provider calls, the recommender, the scaffold, and the design-tool and stock-library accounts, which
  wait for the owner's decision.
"""
from g6.media.adapt_image import edit, paid
from g6.media.adapt_image.block import SENSITIVE_OPS, AdaptImageBlock, AdaptImageInput
from g6.media.adapt_image.paid import ImagesEndpointBackend, PaidService, ReplicateBackend, StabilityBackend

__g6_component__ = True
__g6_block__ = "AdaptImageBlock"
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # generate may drive your ComfyUI; generate_paid spends money
__g6_requires__: tuple[str, ...] = ("action_gating",)  # it decides every paid request before anything is sent
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("visual_check_failed", "unchosen_paid_service")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()  # ComfyUI is adapt_comfyui's; paid services are the person's own accounts, never assumed
__g6_extras__: dict[str, tuple[str, ...]] = {"image": ("PIL",)}
__g6_keys__ = paid.KEYS  # one per paid backend, from the person's key file; a key there approves spending it

__all__ = ["SENSITIVE_OPS", "AdaptImageBlock", "AdaptImageInput", "ImagesEndpointBackend", "PaidService",
           "ReplicateBackend", "StabilityBackend", "edit", "paid"]
