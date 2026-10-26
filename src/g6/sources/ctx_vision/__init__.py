# SPDX-License-Identifier: Apache-2.0
"""g6.sources.ctx_vision — show a picture to an image-capable model you give it, record its account as a claim, and cross-check its reading of text with Tesseract.

An extension in the sources family. ``describe``, ``answer``, ``extract_text`` and ``detect_objects`` send one picture
to the ``g6.core.llm`` caller you give the block from Python (``CtxVisionBlock(caller=...)``); a caller that cannot
take images is refused, and with none (over MCP) nothing is reported. The picture is read only from G6's media folder
by plain name or from bytes given, capped at 20 MiB and decoded in full with Pillow first. Every answer is a
``qualified-draft``: a model's account of a picture is its claim. ``extract_text`` with ``ocr_check`` adds Tesseract's
reading (an outside program you install, ``__g6_outside__``) compared word by word with the model's; without a model
it gives Tesseract's reading alone. ``detect_objects`` checks each box against the picture's measured size.

    from g6.core.llm import OllamaCaller
    from g6.sources.ctx_vision import CtxVisionBlock, CtxVisionInput
    block = CtxVisionBlock(caller=OllamaCaller(model="llava"))
    out = block.infer(CtxVisionInput(op="extract_text", image="receipt.png", ocr_check=True)).unwrap()
    out["ocr"]["agree"], out["ocr"]["only_in_model"]   # whether the two readings match, and where they differ

``extract_text`` is sensitive (its check runs tesseract on your computer); the other ops reach nothing but the caller
you gave.

Not ported from the archive's ctx_vision, and why: the litellm gateway and its paid default model (OpenRouter's); the
key custody, self-settlement billing and tenant identity; the classify op and its confidence counted from the number
of labels; images by URL (nothing is fetched); the MCP sub-package and its store; the resource reports, the pattern
catalogue and the production gate; and the metadata files, one of which held a personal address.
"""
from g6.sources.ctx_vision import images, ocr
from g6.sources.ctx_vision.block import SENSITIVE_OPS, CtxVisionBlock, CtxVisionInput
from g6.sources.ctx_vision.ocr import TESSERACT

__g6_component__ = True
__g6_block__ = "CtxVisionBlock"
# The block's model keyword: g6_invoke passes the model the person configured, if any. Claude Code and Codex as G6's
# model take no images, so with either of them every op but extract_text with ocr_check says so and asks nothing.
__g6_model__ = "caller"
__g6_family__ = "sources"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # extract_text's cross-check runs tesseract on your computer
__g6_requires__: tuple[str, ...] = ("grounding", "knowledge")  # the knowledge family's shared reply and types
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (text in a
# picture can carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("misread_image",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = (TESSERACT,)
__g6_extras__: dict[str, tuple[str, ...]] = {"image": ("PIL",)}

__all__ = ["SENSITIVE_OPS", "TESSERACT", "CtxVisionBlock", "CtxVisionInput", "images", "ocr"]
