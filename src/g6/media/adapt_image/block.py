# SPDX-License-Identifier: Apache-2.0
"""AdaptImageBlock, the registry and MCP door (``g6_invoke``) to adapt_image.

Ops: the Pillow ops (``resize``, ``crop``, ``rotate``, ``flip``, ``adjust_colour``, ``composite``, ``convert``), each
writing a new file into the media folder and inspecting it against the size it must have; ``get_image_info``, a strict
measurement of a picture; ``generate``, a picture from one of the family's local image backends (ComfyUI for a prompt,
``procedural`` for a named technique) or one the person registered from Python, never a backend another member runs
(``blender`` is adapt_blender's, reached through its own ops); ``generate_paid``, a picture from a paid backend the person registered, sent
only when ``g6.action_gating`` lets that payment through (and with ``allow_paid``, the caller's word, which can only
hold a request back); and ``backends``, the backends registered, without asking any of them. ``generate`` and
``generate_paid`` are sensitive: the first may run a workflow on the person's ComfyUI, the second spends money.

The action gate decides every paid request before anything is sent, asked with the backend's key name: the gate reads
the person's key file itself, and a key there is their approval to spend it (``allow_with_notice``,
``G6_E_KEY_FILE_APPROVED``). On any other decision the reply is the dry run (nothing sent) with the gate's decision,
reason codes and what a person must do, such as the line to add to the key file.

A picture comes in as ``source`` (a file name in the media folder) or ``image_base64`` (at most 25 MiB decoded); never
as a URL. Results are ``qualified-draft`` at best. A vision caller given to the block (``AdaptImageBlock(vision=...)``,
from Python) can be asked what a result shows (``vision_check``); its answer is recorded as a claim in the evidence and
never changes the label or a check.
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.llm import Image as ModelImage
from g6.core.llm import supports_images
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media._inspect import MAX_INPUT_PIXELS, pillow
from g6.media._types import (
    BACKEND_UNAVAILABLE,
    DESCRIBED,
    EXTRA_MISSING,
    INPUT_INVALID,
    MEASURED,
    PAID_NOT_ALLOWED,
    ImageRequest,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    check_name,
    declares_backends,
    image_backends,
    media_source,
    refuse,
)
from g6.media.adapt_image import edit
from g6.media.adapt_image.keep import FORMATS, digest, keep

_EDITS = ("resize", "crop", "rotate", "flip", "adjust_colour", "composite", "convert")
_SENSITIVE = " (sensitive: it {}; from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)"
_OPS = {
    "resize": "resize(source or image_base64, width, height, format, name): Lanczos resampling to the size given; one "
              "side alone keeps the aspect ratio",
    "crop": "crop(source or image_base64, box=[left, top, right, bottom], format, name): the box, which must lie "
            "inside the picture",
    "rotate": "rotate(source or image_base64, angle, format, name): counter-clockwise; a right angle turns exactly, "
              "any other grows the canvas to hold the picture",
    "flip": "flip(source or image_base64, direction=horizontal|vertical, format, name)",
    "adjust_colour": "adjust_colour(source or image_base64, brightness, contrast, saturation, format, name): factors, "
                     "1 for no change",
    "composite": "composite(source or image_base64, overlay or overlay_base64, x, y, format, name): the overlay "
                 "placed on the picture at x, y",
    "convert": "convert(source or image_base64, format=png|jpeg|webp, quality, name): the same picture in another "
               "format (a JPEG has no transparency: it is laid on white)",
    "get_image_info": "get_image_info(source or image_base64): the picture decoded in full and measured: format and "
                      "MIME type from its bytes, size, mode, frames, bytes, SHA-256",
    "generate": "generate(prompt or options, backend, width, height, seed, name): a picture from a local backend, "
                "ComfyUI for a prompt and procedural for a technique (or one the person registered from Python), "
                "inspected; never a backend another component runs, such as adapt_blender's blender"
                + _SENSITIVE.format("may run a workflow on your ComfyUI"),
    "generate_paid": "generate_paid(backend, prompt, width, height, seed, options, allow_paid, name): a picture from "
                     "a paid backend the person registered with their key, sent only on the action gate's allow for "
                     "the payment (it holds each for a person), inspected"
                     + _SENSITIVE.format("spends money and reaches the network"),
    "backends": "backends(): the image backends registered (local, network, paid), without asking any of them",
}
SENSITIVE_OPS: tuple[str, ...] = ("generate", "generate_paid")
#: The family backends ``generate``'s own sensitive-op text names. Another member's backend needs that member's own
#: ops, so an agent allowed only ``adapt_image.generate`` never starts its program (ADAPT_IMAGE-01: Blender).
_NAMED = ("comfyui", "procedural")
_VISION_ASK = ("You are shown one picture. Say in two or three plain sentences what it shows. If a request is given, "
               "say whether the picture matches it and what differs. Do not rate or approve it.")


def _plain(value: str) -> str:
    if value:
        try:
            check_name(value)
        except MediaError as exc:
            raise ValueError(exc.reason) from None
    return value


class AdaptImageInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["resize", "crop", "rotate", "flip", "adjust_colour", "composite", "convert", "get_image_info",
                "generate", "generate_paid", "backends", "ops", "help"] = "help"
    source: str = Field("", max_length=100)
    image_base64: str = Field("", max_length=edit.MAX_BASE64_CHARS)
    overlay: str = Field("", max_length=100)
    overlay_base64: str = Field("", max_length=edit.MAX_BASE64_CHARS)
    width: int | None = Field(None, ge=1, le=8192)
    height: int | None = Field(None, ge=1, le=8192)
    box: tuple[int, int, int, int] | None = None
    angle: float = Field(0.0, ge=-360, le=360, allow_inf_nan=False)
    direction: Literal["horizontal", "vertical"] = "horizontal"
    brightness: float = Field(1.0, ge=0, le=4, allow_inf_nan=False)
    contrast: float = Field(1.0, ge=0, le=4, allow_inf_nan=False)
    saturation: float = Field(1.0, ge=0, le=4, allow_inf_nan=False)
    x: int = Field(0, ge=-8192, le=8192)
    y: int = Field(0, ge=-8192, le=8192)
    format: Literal["png", "jpeg", "webp"] = "png"
    quality: int = Field(90, ge=1, le=100)
    backend: str = Field("", pattern=r"^([a-z][a-z0-9_]{0,31})?$")
    prompt: str = Field("", max_length=4000)
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    options: dict[str, Any] = Field(default_factory=dict)
    allow_paid: bool = False
    vision_check: bool = False
    name: str = Field("", max_length=100)
    overwrite: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("name", "source", "overlay")
    @classmethod
    def _plain_names(cls, value: str) -> str:
        return _plain(value)

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> AdaptImageInput:
        if self.op in (*_EDITS, "get_image_info") and bool(self.source) == bool(self.image_base64):
            raise ValueError(f"{self.op} needs a picture: source (a file in the media folder) or image_base64, one "
                             "of them")
        if self.op == "composite" and bool(self.overlay) == bool(self.overlay_base64):
            raise ValueError("composite needs overlay or overlay_base64, one of them")
        if self.op == "crop" and self.box is None:
            raise ValueError("crop needs box: [left, top, right, bottom]")
        if self.op == "resize" and self.width is None and self.height is None:
            raise ValueError("resize needs width, height or both")
        if self.width and self.height and self.width * self.height > MAX_INPUT_PIXELS:
            raise ValueError(f"width x height {self.width}x{self.height} is {edit.TOO_LARGE}")
        if self.op == "generate_paid" and not self.backend:
            raise ValueError("generate_paid needs backend: the name of a paid backend the person registered")
        if self.op in ("generate", "generate_paid") and self.format != "png":
            raise ValueError("a generated picture is a png; convert it afterwards")
        return self


def _ids(d: AdaptImageInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _bytes(name: str, inline: str, field: str) -> bytes:
    if inline:
        return edit.decode_base64(inline, f"{field}_base64")
    path = media_source(name, field)  # a plain file kept in the media folder, never a link to one kept elsewhere
    if path.stat().st_size > edit.MAX_BYTES:
        raise MediaError(INPUT_INVALID, f"{field} {name!r} is larger than {edit.MAX_BYTES} bytes")
    return path.read_bytes()


def _version() -> str:
    import PIL

    return f"Pillow {PIL.__version__}"


def _edit(d: AdaptImageInput) -> MediaResult:
    ids = _ids(d)
    if pillow() is None:
        return refuse(d.op, EXTRA_MISSING, "Pillow is not installed, so no picture can be read or made: "
                                           "pip install 'g6[image] @ git+https://github.com/basicisabc1994/g6solver'", **ids)
    try:
        data = _bytes(d.source, d.image_base64, "source")
        if d.op == "get_image_info":
            found = edit.info(data, "source")
            return answer(d.op, f"a {found['format']} picture, {found['width']}x{found['height']}, decoded in full",
                          card=MEASURED, evidence=[{"kind": "media_measurement", "measured": found}], info=found,
                          **ids)
        image = edit.open_strict(data, "source")
        params: dict[str, Any] = {}
        if d.op == "resize":
            made, size = edit.resize(image, d.width, d.height)
            params = {"width": d.width, "height": d.height, "resample": "lanczos"}
        elif d.op == "crop":
            made, size = edit.crop(image, d.box)
            params = {"box": list(d.box)}
        elif d.op == "rotate":
            made, size = edit.rotate(image, d.angle)
            params = {"angle": d.angle}
        elif d.op == "flip":
            made, size = edit.flip(image, d.direction)
            params = {"direction": d.direction}
        elif d.op == "adjust_colour":
            made, size = edit.adjust_colour(image, d.brightness, d.contrast, d.saturation)
            params = {"brightness": d.brightness, "contrast": d.contrast, "saturation": d.saturation}
        elif d.op == "composite":
            laid = _bytes(d.overlay, d.overlay_base64, "overlay")
            made, size = edit.composite(image, edit.open_strict(laid, "overlay"), d.x, d.y)
            params = {"x": d.x, "y": d.y, "overlay_sha256": hashlib.sha256(laid).hexdigest()}
        else:
            made, size = image, image.size
        payload = edit.encode(made, d.format, d.quality)
    except MediaError as exc:
        return refuse(d.op, exc.code, exc.reason, **ids)
    recipe = {"op": d.op, "params": params, "format": d.format, "quality": d.quality,
              "source_sha256": hashlib.sha256(data).hexdigest()}
    provenance = Provenance(backend="pillow", backend_version=_version(), technique=d.op, recipe=recipe,
                            recipe_sha256=digest(recipe))
    width, height = size if size is not None else (None, None)
    said = (f"{d.op} done and inspected: it decodes, is a {FORMATS[d.format][1]}"
            + (f" of {width}x{height}" if size is not None else " (its size, set by the rotation, was not compared)")
            + " and is not blank")
    return keep(d.op, payload, name=d.name or f"{d.op}-{digest(recipe)[:12]}", fmt=d.format, width=width,
                height=height, overwrite=d.overwrite, provenance=provenance, ids=ids, message=said,
                evidence=[{"kind": "edit", "op": d.op, "params": params, "source": {
                    "width": image.width, "height": image.height, "format": image.format or ""}}])


def _request(d: AdaptImageInput) -> ImageRequest:
    return ImageRequest(width=d.width or 512, height=d.height or 512, seed=d.seed, name=d.name, prompt=d.prompt,
                        options=d.options, overwrite=d.overwrite, request_id=d.request_id, run_id=d.run_id)


def _generate(d: AdaptImageInput) -> MediaResult:
    ids = _ids(d)
    name = d.backend or ("comfyui" if d.prompt else "procedural")
    backend = image_backends.get(name)
    if backend is None:
        return refuse(d.op, BACKEND_UNAVAILABLE, f"no image backend named {name!r} is registered (registered: "
                                                 f"{', '.join(image_backends.names()) or 'none'})", **ids)
    if backend.paid or backend.network:
        return refuse(d.op, PAID_NOT_ALLOWED, f"{name} is a paid or online backend, which generate never uses; "
                                              "generate_paid asks the action gate, which holds a payment for a "
                                              "person", **ids)
    owner = _owner(name)
    if name not in _NAMED and owner:
        return refuse(d.op, BACKEND_UNAVAILABLE, f"the {name} backend is {owner}'s and starts a program generate's "
                                                 f"permission does not cover, so generate never uses it: use "
                                                 f"{owner}'s own ops (over MCP the person who runs the server lists "
                                                 f"them, or {owner}, in G6_MCP_SENSITIVE), or from Python "
                                                 f"image_backends.generate({name!r}, request)", **ids)
    try:
        request = _request(d)
    except ValueError as exc:
        return refuse(d.op, INPUT_INVALID, str(exc)[:500], **ids)
    return image_backends.generate(name, request).model_copy(update={"operation": d.op})


def _owner(name: str) -> str:
    """The media family member that declares the image backend ``name`` (``__g6_image_backends__``), or ``""`` for a
    backend no member declares (one the person registered from Python)."""
    from g6.core.registry import get_registry

    return next((ref.name for ref in get_registry().refs.values()
                 if ref.family == "media" and name in declares_backends(ref.module)), "")


def _generate_paid(d: AdaptImageInput) -> MediaResult:
    ids = _ids(d)
    backend = image_backends.get(d.backend)
    if backend is None:
        paid = [b["name"] for b in image_backends.describe(probe=False) if b["paid"]]
        return refuse(d.op, BACKEND_UNAVAILABLE, f"no paid backend named {d.backend!r} is registered (paid backends "
                                                 f"registered: {', '.join(paid) or 'none'}); the person who runs G6 "
                                                 "registers one with their own key", **ids)
    if not backend.paid:
        return refuse(d.op, INPUT_INVALID, f"{d.backend} is not a paid backend; use generate", **ids)
    try:
        request = _request(d)
    except ValueError as exc:
        return refuse(d.op, INPUT_INVALID, str(exc)[:500], **ids)
    would = {"prompt": request.prompt, "width": request.width, "height": request.height, "seed": request.seed,
             "options": request.options}
    dry = {"kind": "dry_run", "backend": d.backend, "would_send": would}
    key = getattr(backend, "key", None)
    gate = _ask_gate(_payment(d.backend, backend, would), key_approval=getattr(key, "name", None))
    said = {"kind": "claim", "about": "the person agreed to pay", "allow_paid": d.allow_paid, "by": "the caller",
            "counts_as_check": False, "note": "a caller's word: it can hold a request back, never send one"}
    if not _lets_through(gate) or not d.allow_paid:
        codes = ", ".join(gate["reason_codes"]) or "no reason code"
        why = (f"the action gate (g6.action_gating) decided {gate['decision']} ({codes}): "
               f"{gate['required_user_message']}" if not _lets_through(gate) else "allow_paid was not given")
        return refuse(d.op, PAID_NOT_ALLOWED, f"nothing was sent to {d.backend}, which spends the person's money: "
                                              f"{why}", evidence=[dry, gate, said], **ids)
    result = image_backends.generate(d.backend, request).model_copy(update={"operation": d.op})
    return result.model_copy(update={"evidence": [*result.evidence, gate, said]})


def _payment(name: str, backend: Any, would: dict[str, Any]) -> dict[str, Any]:
    """The action the gate decides: a payment to the outside service behind ``backend`` for one picture, declared
    external and costly to reverse (the money is spent once the request is sent), with the plan G6 itself follows to
    check what comes back (``keep``: the picture is inspected before it is returned, and removed when it fails)."""
    service = str(getattr(backend, "service_name", "") or name)
    size = f"{would['width']}x{would['height']}"
    plan = {"expected_result": f"one {size} PNG picture that G6's image inspector decodes and finds the size asked "
                               "for and not blank",
            "observation_window": "until the service replies, within the backend's time limit",
            "leading_indicators": ["the reply's status", "the reply's size, under the backend's cap"],
            "lagging_indicators": ["the image inspector's checks on the picture"],
            "monitor": "g6.media's image inspector (adapt_image.keep)",
            "verification_time": "when the reply arrives, before the picture is returned",
            "stop_threshold": "any failed check: the picture is not returned and its file is removed",
            "rollback_procedure": "the money cannot be returned; a picture that fails inspection is removed",
            "model_update_rule": "a failed request is reported with its checks and never retried unasked"}
    return {"action_type": "payment", "target_backend": name, "target_resource": f"{service} (paid image generation)",
            "description": f"send one picture request to {service}, a paid image service, spending the person's money",
            "action_params": {"backend": name, **would}, "requested_by": "adapt_image.generate_paid",
            "actor_type": "agent", "external_side_effect": True, "irreversible_or_costly_to_reverse": True,
            "blast_radius": "external_party", "safety_domains": ["financial"], "verification_contract": plan}


def _lets_through(gate: dict[str, Any]) -> bool:
    """Whether the gate's decision sends the request: its ``allow``, or its ``allow_with_notice`` because the person's
    key file holds the backend's key (``G6_E_KEY_FILE_APPROVED``). A notice for any other reason sends nothing."""
    from g6.action_gating.decision_engine import E_KEY_FILE_APPROVED

    return gate["decision"] == "allow" or (gate["decision"] == "allow_with_notice"
                                           and E_KEY_FILE_APPROVED in gate["reason_codes"])


def _ask_gate(action: dict[str, Any], key_approval: str | None = None) -> dict[str, Any]:
    """``g6.action_gating``'s decision on ``action``, as evidence (its audit record is written under ``$G6_HOME``).
    ``key_approval`` names the backend's key: the gate itself reads whether the person's key file holds it. A gate that
    cannot decide holds the request: its decision is ``block``."""
    from g6.action_gating import evaluate_action

    found: dict[str, Any] = {"kind": "action_gate", "gate": "g6.action_gating", "action_type": action["action_type"]}
    try:
        evaluation = evaluate_action(action, key_approval=key_approval)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot decide never lets money be spent
        return {**found, "decision": "block", "completion_state": "blocked-escalated", "reason_codes": [],
                "required_user_message": f"the action gate could not decide: {type(exc).__name__}: {exc}"[:300],
                "required_confirmation_phrase": None, "audit_id": ""}
    decided = evaluation.gate_decision
    return {**found, "decision": decided.decision, "completion_state": evaluation.completion_state,
            "reason_codes": list(decided.reason_codes), "required_user_message": decided.required_user_message,
            "required_confirmation_phrase": decided.required_confirmation_phrase, "audit_id": decided.audit_id}


def _backends(d: AdaptImageInput) -> MediaResult:
    listed = image_backends.describe(probe=False)
    found = {"kind": "package_table", "backends": len(listed),
             "discovery_errors": dict(image_backends.discovery_errors)}
    return answer(d.op, f"{len(listed)} image backends registered; none was asked whether it is ready",
                  card=DESCRIBED, evidence=[found], backends=listed, **_ids(d))


def _vision(caller: Any, result: MediaResult, prompt: str) -> MediaResult:
    """``result`` with a vision model's account of its picture added to the evidence as a claim (never a check)."""
    if result.artifact is None:
        return result
    who = type(caller).__name__ if caller is not None else ""
    claim: dict[str, Any] = {"kind": "claim", "about": "what the picture shows", "by": who, "counts_as_check": False}
    if caller is None:
        claim["said"] = "no vision caller was given to the block (there is none over MCP), so no model was asked"
    elif not supports_images(caller):
        claim["said"] = f"{who} sends no images, so no model was asked"
    else:
        try:
            with open(result.artifact.path, "rb") as f:
                shown = ModelImage.from_bytes(f.read())
            ask = f"The picture was asked for as: {prompt}" if prompt else "No request was given."
            claim["said"] = str(caller.complete_with_images(_VISION_ASK, ask, [shown], max_tokens=300))[:2000]
        except Exception as exc:  # noqa: BLE001 - a claim that could not be had changes nothing about the picture
            claim["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return result.model_copy(update={"evidence": [*result.evidence, claim]})


@block_contract(
    purpose="edit, convert and measure pictures with Pillow, and generate them through the media family's backends "
            "(local first; a paid one only on the action gate's allow), each inspected before it is returned",
    mitigates_failure_modes=("visual_check_failed", "unchosen_paid_service"),
    verification_method="g6.media image inspector on every file written: decodes, the format and size it must be, "
                        "not blank",
)
@component_maturity(
    "beta",
    reason="Pillow edits with the result's size computed independently and inspected; generation through registered "
           "backends; a paid service only from a sensitive op, on g6.action_gating's allow; qualified-draft at best",
)
class AdaptImageBlock(AIBlock[AdaptImageInput, dict, dict]):
    """Edit, measure or generate a picture and say what was measured. Never ``verified``."""

    def __init__(self, name: str = "adapt_image", state: dict | None = None, *, vision: Any = None) -> None:
        super().__init__(name=name, state=state or {})
        self.vision = vision

    def infer(self, data: AdaptImageInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if data.op == "backends":
            result = _backends(data)
        elif data.op == "generate":
            result = _generate(data)
        elif data.op == "generate_paid":
            result = _generate_paid(data)
        else:
            result = _edit(data)
        if data.vision_check and data.op != "get_image_info":
            result = _vision(self.vision, result, data.prompt)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "AdaptImageBlock", "AdaptImageInput"]
