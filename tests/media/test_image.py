# SPDX-License-Identifier: Apache-2.0
"""adapt_image: every picture it writes is inspected against the size it must have and a blank, wrong or undecodable
one is refused (D1, ADAPT_IMAGE-04); nothing is ``verified`` (ADAPT_IMAGE-01, ADAPT_CREATIVE_API-01); no paid service
is used without the person (ADAPT_IMAGE-02, ADAPT_CREATIVE_API-02); ops do what their names say and report seeds and
formats from the bytes (ADAPT_IMAGE-03); no URL is fetched for a caller (ADAPT_IMAGE-05); a service's success is not a
check (ADAPT_CREATIVE_API-03); and adapt_creative_api's Stability and Replicate calls live on as paid backends (the
merge). Paid services are reached only through transport doubles here: no key, no network."""
from __future__ import annotations

import base64
import importlib.metadata
import io
import json
import re
import socket
import struct
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")

import g6.media.adapt_image as package  # noqa: E402
from g6.core import discovery, http  # noqa: E402
from g6.core.failure_modes import FailureModeRegistry  # noqa: E402
from g6.core.llm import FakeLLM, NoModel  # noqa: E402
from g6.core.registry import get_registry  # noqa: E402
from g6.invariants import check_envelope  # noqa: E402
from g6.mcp import describe_reply, invoke_reply  # noqa: E402
from g6.media._types import (  # noqa: E402
    BACKEND_FAILED,
    BACKEND_UNAVAILABLE,
    BLANK,
    EXTRA_MISSING,
    FORMAT_MISMATCH,
    INPUT_INVALID,
    PAID_NOT_ALLOWED,
    SIZE_MISMATCH,
    UNDECODABLE,
    ImageRequest,
    image_backends,
    media_file,
    media_root,
    write_media,
)
from g6.media.adapt_image import AdaptImageBlock, AdaptImageInput, block, edit  # noqa: E402
from g6.media.adapt_image.paid import (  # noqa: E402
    ImagesEndpointBackend,
    PaidService,
    ReplicateBackend,
    StabilityBackend,
)

SRC = Path(package.__file__).parent
KEY = "test-key-not-real"
PUBLIC = "93.184.216.34"


def _picture(width: int, height: int, *, seed: int = 1, colour: tuple | None = None, fmt: str = "PNG",
             mode: str = "RGB") -> bytes:
    if colour is not None:
        made = Image.new(mode, (width, height), colour)
    else:
        made = Image.frombytes("RGB", (width, height), __import__("random").Random(seed).randbytes(width * height * 3))
        made = made.convert(mode)
    out = io.BytesIO()
    made.save(out, format=fmt)
    return out.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _put(name: str, data: bytes) -> str:
    write_media(media_file(name, ".png"), data, overwrite=True)
    return f"{name}.png"


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_image", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_image", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


class Wire:
    """A transport double for g6.core.http: answers each request with ``answer(request)`` and records it."""

    def __init__(self, answer: Callable[[http.Request], http.Response]) -> None:
        self.answer, self.sent = answer, []

    def __call__(self, request: http.Request) -> http.Response:
        self.sent.append(request)
        return self.answer(request)


def _reply(status: int, value: object = None, *, body: bytes | None = None, headers: dict | None = None):
    data = body if body is not None else json.dumps(value).encode()
    return lambda request: http.Response(status=status, headers=headers or {}, body=data, url=request.url)


def _client(wire: Wire, resolver: Callable[[str, int], list[str]] | None = None) -> http.Client:
    return http.Client(transport=wire, resolver=resolver or (lambda host, port: [PUBLIC]), min_interval=0.0)


def _stability(picture: bytes, reason: str = "SUCCESS", **extra) -> dict:
    return {"artifacts": [{"base64": _b64(picture), "finishReason": reason, "seed": 99}], **extra}


@pytest.fixture
def paid() -> Iterator[Callable[..., Wire]]:
    """Register a paid backend over a transport double; every one registered is removed afterwards."""
    names: list[str] = []

    def register(kind: str, answer: Callable, *, name: str = "", model: str = "", base_url: str = "",
                 resolver: Callable | None = None) -> Wire:
        wire = Wire(answer)
        cls = {"stability": StabilityBackend, "replicate": ReplicateBackend, "images_api": ImagesEndpointBackend}[kind]
        service = PaidService(key=KEY, model=model or {"stability": "engine-test", "replicate": "owner/model",
                                                       "images_api": "image-model"}[kind],
                              base_url=base_url or ("https://images.example.com/v1" if kind == "images_api" else ""))
        extra = {"sleep": lambda s: None} if kind == "replicate" else {}
        backend = cls(service, name=name or kind, client=_client(wire, resolver), **extra)
        image_backends.register(backend, replace=True)
        names.append(backend.name)
        return wire

    yield register
    for name in names:
        image_backends.unregister(name)


@pytest.fixture
def gate_allows(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The action gate replaced by one that allows, which the real gate gives no payment today: the tests of the paid
    backends' own checks (the reply inspected, every hop checked) stand behind it. It lists the actions it was asked
    about."""
    asked: list[dict] = []

    def allow(action: dict, key_approval: str | None = None) -> dict:
        asked.append(action)
        return {"kind": "action_gate", "gate": "a test's stand-in", "action_type": action["action_type"],
                "decision": "allow", "completion_state": "qualified-draft", "reason_codes": [],
                "required_user_message": "", "required_confirmation_phrase": None, "audit_id": ""}

    monkeypatch.setattr(block, "_ask_gate", allow)
    return asked


GOOD = _picture(64, 48, seed=3)
PAID = {"backend": "stability", "prompt": "a kite over the sea", "width": 64, "height": 48, "allow_paid": True}


# -- D1: a blank result is refused, never returned -------------------------------------------------------------------

def test_d1_brightness_zero_makes_a_black_picture_which_is_refused_and_removed() -> None:
    before = _media_files()
    out = _invoke("adjust_colour", image_base64=_b64(GOOD), brightness=0.0, name="d1-dark")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == BLANK and out["artifact"] is None
    assert _media_files() == before and check_envelope(out) == []


def test_d1_a_crop_of_an_empty_margin_is_refused() -> None:
    framed = Image.new("RGB", (80, 80), (250, 250, 250))
    framed.paste(Image.open(io.BytesIO(GOOD)), (8, 16))
    out_bytes = io.BytesIO()
    framed.save(out_bytes, format="PNG")
    source = _put("d1-framed", out_bytes.getvalue())
    assert _invoke("crop", source=source, box=[0, 0, 80, 12])["code"] == BLANK      # the margin above the picture
    assert _invoke("crop", source=source, box=[8, 16, 72, 64])["completion_state"] == "qualified-draft"


def test_d1_a_paid_services_success_is_not_a_check_a_grey_picture_is_refused(paid, gate_allows) -> None:
    wire = paid("stability", _reply(200, _stability(_picture(64, 48, colour=(128, 128, 128)), usage={"credits": 1})))
    out = _invoke("generate_paid", **PAID)
    assert out["code"] == BLANK and len(wire.sent) == 1


# -- ADAPT_IMAGE-01 and ADAPT_CREATIVE_API-01: nothing is verified ---------------------------------------------------

def test_g1_no_result_is_verified_and_the_package_never_assigns_it(paid, gate_allows) -> None:
    for path in SRC.rglob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name
    edited = _invoke("flip", image_base64=_b64(GOOD))
    assert edited["completion_state"] == "qualified-draft" and edited["warning_card"]["code"] == "G6_E_MEDIA_INSPECTED"
    paid("stability", _reply(200, _stability(GOOD, usage={"credits": 1})))  # usage metadata made the archive say verified
    made = _invoke("generate_paid", **PAID)
    assert made["completion_state"] == "qualified-draft" and made["warning_card"]["code"] == "G6_E_MEDIA_INSPECTED"
    assert _invoke("generate_paid", **{**PAID, "backend": "absent"})["completion_state"] == "blocked-escalated"


# -- ADAPT_IMAGE-02 and ADAPT_CREATIVE_API-02: no money is spent without the person ----------------------------------

def test_p1_a_prompt_with_no_local_backend_ready_is_refused_never_sent_to_a_paid_one(
        paid, monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("G6_COMFYUI_URL", f"http://127.0.0.1:{port}")  # no ComfyUI answering
    wire = paid("stability", _reply(200, _stability(GOOD)))
    out = _invoke("generate", prompt="a kite", width=64, height=48)
    assert out["code"] == BACKEND_UNAVAILABLE and "comfyui" in out["reason"] and wire.sent == []
    named = _invoke("generate", prompt="a kite", backend="stability", width=64, height=48)
    assert named["code"] == PAID_NOT_ALLOWED and wire.sent == []


def test_a_prompt_goes_to_the_local_comfyui_backend_first() -> None:
    from g6.media.adapt_comfyui.block import ComfyUIBackend

    class Stand:  # takes the comfyui name for this test only, and draws through the procedural backend
        name, local, network, paid = "comfyui", True, False, False
        seen: list[ImageRequest] = []

        def available(self) -> str:
            return ""

        def generate(self, request: ImageRequest):  # noqa: ANN201
            self.seen.append(request)
            return image_backends.generate("procedural", request.model_copy(update={
                "prompt": "", "options": {"technique": "noise"}}))

    stand = Stand()
    image_backends.register(stand, replace=True)
    try:
        out = _invoke("generate", prompt="a paper boat", width=32, height=32, seed=4)
        assert out["completion_state"] == "qualified-draft" and stand.seen[0].prompt == "a paper boat"
    finally:
        image_backends.register(ComfyUIBackend(), replace=True)


class _Started:
    """Takes a family backend's name for one test and records each time it is started, drawing through procedural."""

    local, network, paid = True, False, False

    def __init__(self, name: str) -> None:
        self.name, self.started = name, []

    def available(self) -> str:
        return ""

    def generate(self, request: ImageRequest):  # noqa: ANN201
        self.started.append(request)
        return image_backends.generate("procedural", request.model_copy(update={
            "prompt": "", "options": {"technique": "noise"}}))


def test_p1_adapt_image_01_an_agent_allowed_only_generate_cannot_start_blender() -> None:
    # Fails without the owner check in block._generate ("if name not in _NAMED and _owner(name)"): Blender is started.
    from g6.media.adapt_blender import BlenderBackend
    from g6.media.adapt_comfyui.block import ComfyUIBackend

    image_backends.names()  # the family's members are found first, so a stand-in is not replaced by discovery
    blender, comfyui = _Started("blender"), _Started("comfyui")
    image_backends.register(blender, replace=True)
    image_backends.register(comfyui, replace=True)
    only_generate = frozenset({"adapt_image.generate"})
    try:
        asked = invoke_reply("adapt_image", "generate", json.dumps({
            "backend": "blender", "options": {"scene": {"objects": []}}, "width": 32, "height": 32}),
            allow_sensitive=only_generate)
        assert blender.started == [] and asked["completion_state"] == "blocked-escalated", asked
        assert asked["result"]["code"] == BACKEND_UNAVAILABLE and "adapt_blender" in asked["result"]["reason"]
        assert check_envelope(asked) == []
        direct = invoke_reply("adapt_blender", "build_scene", json.dumps({"session": "s-000000000000", "scene": {}}),
                              allow_sensitive=only_generate)
        assert direct["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
        # ComfyUI, which the op's own text names, behaves as before: a prompt goes to it.
        drawn = invoke_reply("adapt_image", "generate", json.dumps({"prompt": "a paper boat", "width": 32,
                                                                    "height": 32, "seed": 4}),
                             allow_sensitive=only_generate)
        assert drawn["ok"] and drawn["completion_state"] == "qualified-draft", drawn
        assert [r.prompt for r in comfyui.started] == ["a paper boat"]
    finally:
        image_backends.register(BlenderBackend(), replace=True)
        image_backends.register(ComfyUIBackend(), replace=True)


def test_p1_adapt_image_01_the_op_names_every_backend_it_may_start_and_python_still_reaches_blender() -> None:
    # Fails if generate's own text stops naming what it may start, or the blender backend leaves the family registry.
    said = _invoke("help")["help"][list(_invoke("ops")["ops"]).index("generate")]
    assert "ComfyUI" in said and "procedural" in said and "adapt_blender" in said
    assert image_backends.get("blender") is not None  # from Python: image_backends.generate("blender", ...)
    skill = (SRC / "skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "never starts Blender" in skill
    blender_skill = (SRC.parent / "adapt_blender" / "skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "its `generate` uses the `blender` backend" not in blender_skill
    assert "`adapt_image`'s `generate` never starts Blender" in blender_skill


def test_p1_generate_paid_without_allow_paid_sends_nothing_and_says_what_it_would_send(paid) -> None:
    wire = paid("stability", _reply(200, _stability(GOOD)))
    out = _invoke("generate_paid", **{**PAID, "allow_paid": False})
    assert out["code"] == PAID_NOT_ALLOWED and wire.sent == [] and out["evidence"][0]["kind"] == "dry_run"
    assert out["evidence"][0]["would_send"]["prompt"] == PAID["prompt"]


def test_g3_allow_paid_and_the_operators_listing_open_nothing_the_action_gate_decides(paid) -> None:
    wire = paid("stability", _reply(200, _stability(GOOD)))
    denied = invoke_reply("adapt_image", "generate_paid", json.dumps(PAID), allow_sensitive=frozenset())
    assert not denied["ok"] and denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and wire.sent == []
    listed = invoke_reply("adapt_image", "generate_paid", json.dumps(PAID),
                          allow_sensitive=frozenset({"adapt_image.generate_paid"}))
    assert not listed["ok"] and listed["completion_state"] == "blocked-escalated" and wire.sent == []
    assert listed["result"]["code"] == PAID_NOT_ALLOWED and check_envelope(listed) == []


def test_p1_a_paid_request_the_gate_does_not_allow_sends_nothing_and_returns_the_gates_decision(paid) -> None:
    wire = paid("stability", _reply(200, _stability(GOOD)))
    out = _invoke("generate_paid", **PAID)  # allow_paid, a backend the person registered, and no person asked
    assert out["code"] == PAID_NOT_ALLOWED and wire.sent == [], out.get("reason")
    kinds = [e["kind"] for e in out["evidence"]]
    assert kinds[0] == "dry_run" and "consent" not in kinds and out["evidence"][0]["would_send"]["width"] == 64
    gate = next(e for e in out["evidence"] if e["kind"] == "action_gate")
    assert (gate["gate"], gate["action_type"]) == ("g6.action_gating", "payment") and gate["decision"] != "allow"
    assert gate["reason_codes"] and gate["audit_id"] and gate["required_user_message"]
    assert gate["decision"] in out["reason"] and gate["required_user_message"] in out["reason"]
    claim = next(e for e in out["evidence"] if "allow_paid" in e)
    assert claim["kind"] == "claim" and claim["allow_paid"] is True and claim["counts_as_check"] is False


def test_a_paid_request_is_sent_only_on_the_gates_allow_and_allow_paid_stays_a_claim(paid, gate_allows) -> None:
    wire = paid("stability", _reply(200, _stability(GOOD)))
    held = _invoke("generate_paid", **{**PAID, "allow_paid": False})  # the gate allows; the caller did not agree
    assert held["code"] == PAID_NOT_ALLOWED and wire.sent == []
    out = _invoke("generate_paid", **PAID)
    assert out["completion_state"] == "qualified-draft" and len(wire.sent) == 1, out.get("reason")
    assert (gate_allows[-1]["action_type"], gate_allows[-1]["target_backend"]) == ("payment", "stability")
    assert [e["kind"] for e in out["evidence"]][-2:] == ["action_gate", "claim"]
    assert all(e["kind"] != "consent" for e in [*held["evidence"], *out["evidence"]])


@pytest.mark.parametrize("field", [{"approved": True}, {"reviewer": "Dr Who"}, {"completion_state": "verified"},
                                   {"allow_paid_api": True}, {"billing_ok": True}, {"api_key": "sk-x"}])
def test_g3_claims_consents_and_keys_in_the_input_are_refused(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("generate_paid", **PAID, **field)


def test_with_no_paid_backend_registered_generate_paid_is_refused() -> None:
    out = _invoke("generate_paid", **{**PAID, "backend": "stability_nowhere"})
    assert out["code"] == BACKEND_UNAVAILABLE and "own key" in out["reason"]


# -- keys: only from the person's key file, and a key there is their approval (owner ruling, 2026-10-21) --------------

FILED = "sk-from-the-key-file"


def test_each_paid_backend_declares_its_key_and_g6_describe_lists_them() -> None:
    declared = {k.name: k for k in package.__g6_keys__}
    assert set(declared) == {"STABILITY_API_KEY", "REPLICATE_API_TOKEN", "G6_IMAGES_API_KEY"}
    assert all(k.paid for k in declared.values())
    assert (StabilityBackend.key.name, ReplicateBackend.key.name, ImagesEndpointBackend.key.name) == (
        "STABILITY_API_KEY", "REPLICATE_API_TOKEN", "G6_IMAGES_API_KEY")
    assert {k["name"] for k in discovery.describe("adapt_image")["keys"]} == set(declared)


def test_a_paid_backend_is_built_from_the_key_file_and_a_missing_key_says_where_to_put_it(key_file) -> None:
    from g6.core import keys

    key_file(f"STABILITY_API_KEY={FILED}\n")
    built = StabilityBackend.from_key_file(model="engine-test")
    assert built.service.key.get_secret_value() == FILED and built.name == "stability"
    key_file("")
    with pytest.raises(keys.KeyMissing) as missing:
        ReplicateBackend.from_key_file(model="owner/model")
    assert str(key_file.path) in missing.value.message and "REPLICATE_API_TOKEN=" in missing.value.message


def test_with_the_key_in_the_file_the_real_gate_approves_and_the_picture_is_sent(paid, key_file) -> None:
    key_file(f"STABILITY_API_KEY={FILED}\n")
    wire = paid("stability", _reply(200, _stability(GOOD)))
    out = _invoke("generate_paid", **PAID)
    assert out["completion_state"] == "qualified-draft" and len(wire.sent) == 1, out.get("reason")
    gate = next(e for e in out["evidence"] if e["kind"] == "action_gate")
    assert gate["gate"] == "g6.action_gating" and gate["decision"] == "allow_with_notice"
    assert "G6_E_KEY_FILE_APPROVED" in gate["reason_codes"] and "STABILITY_API_KEY" in gate["required_user_message"]
    assert FILED not in json.dumps(out)


def test_without_the_key_in_the_file_nothing_is_sent_and_the_reply_says_which_line_to_add(paid, key_file) -> None:
    key_file("")
    wire = paid("stability", _reply(200, _stability(GOOD)))
    out = _invoke("generate_paid", **PAID)
    assert out["code"] == PAID_NOT_ALLOWED and wire.sent == []
    gate = next(e for e in out["evidence"] if e["kind"] == "action_gate")
    assert "G6_E_KEY_MISSING" in gate["reason_codes"]
    assert str(key_file.path) in out["reason"] and "STABILITY_API_KEY=" in out["reason"]


def test_a_key_in_the_environment_is_no_approval(paid, key_file, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file("")
    monkeypatch.setenv("STABILITY_API_KEY", FILED)
    wire = paid("stability", _reply(200, _stability(GOOD)))
    assert _invoke("generate_paid", **PAID)["code"] == PAID_NOT_ALLOWED and wire.sent == []


def test_with_the_key_in_the_file_allow_paid_still_holds_a_request_back(paid, key_file) -> None:
    key_file(f"STABILITY_API_KEY={FILED}\n")
    wire = paid("stability", _reply(200, _stability(GOOD)))
    out = _invoke("generate_paid", **{**PAID, "allow_paid": False})
    assert out["code"] == PAID_NOT_ALLOWED and wire.sent == [] and "allow_paid" in out["reason"]


def test_a_notice_for_any_other_reason_sends_nothing(paid, monkeypatch: pytest.MonkeyPatch) -> None:
    def notice(action: dict, key_approval: str | None = None) -> dict:
        return {"kind": "action_gate", "gate": "a test's stand-in", "action_type": action["action_type"],
                "decision": "allow_with_notice", "completion_state": "qualified-draft",
                "reason_codes": ["G6_E_WEAK_SIGNAL_ACKNOWLEDGED"], "required_user_message": "a weak signal",
                "required_confirmation_phrase": None, "audit_id": ""}

    monkeypatch.setattr(block, "_ask_gate", notice)
    wire = paid("stability", _reply(200, _stability(GOOD)))
    assert _invoke("generate_paid", **PAID)["code"] == PAID_NOT_ALLOWED and wire.sent == []


# -- ADAPT_IMAGE-03: ops do what their names say ----------------------------------------------------------------------

def test_p1_no_op_pretends_resize_says_lanczos_and_formats_come_from_the_bytes(paid, gate_allows) -> None:
    ops = _invoke("ops")["ops"]
    assert not {"upscale", "img2img", "inpaint", "generate_variations", "describe", "encode_base64"} & set(ops)
    out = _invoke("resize", image_base64=_b64(GOOD), width=32)
    assert out["artifact"]["measured"]["width"] == 32 and out["artifact"]["measured"]["height"] == 24
    assert out["artifact"]["provenance"]["backend"] == "pillow"
    assert out["artifact"]["provenance"]["recipe"]["params"]["resample"] == "lanczos"
    paid("stability", _reply(200, _stability(_picture(64, 48, fmt="JPEG"))))
    assert _invoke("generate_paid", **PAID)["code"] == FORMAT_MISMATCH  # the archive reported the format asked for


def test_p1_without_a_seed_each_picture_has_its_own_reported_seed() -> None:
    first, second = (_invoke("generate", options={"technique": "noise"}, width=32, height=32) for _ in range(2))
    assert first["completion_state"] == second["completion_state"] == "qualified-draft"
    assert first["seed"] != second["seed"]


# -- ADAPT_IMAGE-04: the declared checks exist and compare with the request -----------------------------------------

def test_p1_a_service_that_ignores_the_size_asked_for_is_refused(paid, gate_allows) -> None:
    paid("stability", _reply(200, _stability(_picture(96, 48))))
    out = _invoke("generate_paid", **PAID)
    assert out["code"] == SIZE_MISMATCH and "not the 64x48" in out["reason"]


# -- ADAPT_IMAGE-05: no URL is fetched for a caller -----------------------------------------------------------------

@pytest.mark.parametrize("field", [{"image_url": "http://localhost:8080/x.png"}, {"url": "https://example.com/x"}])
def test_p1_a_url_is_refused_not_fetched(field: dict) -> None:
    error = _refused("resize", image_base64=_b64(GOOD), width=8, **field)
    assert "G6_E_INPUT_VALIDATION" in error and next(iter(field)) in error and "not permitted" in error


@pytest.mark.parametrize("source", ["../x.png", "C:/x.png", "/etc/passwd", "a/b.png"])
def test_a_source_outside_the_media_folder_is_refused(source: str) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("resize", source=source, width=8)


def test_a_source_that_is_not_there_is_refused() -> None:
    assert _invoke("resize", source="never-written.png", width=8)["code"] == INPUT_INVALID


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_link_in_the_media_folder_to_a_picture_kept_elsewhere_is_refused_unread(tmp_path: Path, kind: str) -> None:
    """A name in the media folder that is a link, or a second name for a file kept elsewhere, never reads that file:
    as a source or as an overlay."""
    from media_kit import outside_link

    outside_link(tmp_path, "linked.png", _picture(8, 8), kind)
    plain = _put("plain-base", _picture(8, 8, seed=2))
    for op, params in (("get_image_info", {"source": "linked.png"}), ("resize", {"source": "linked.png", "width": 4}),
                       ("composite", {"source": plain, "overlay": "linked.png", "x": 0, "y": 0})):
        out = _invoke(op, **params)
        assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT_INVALID, (op, out)
        assert "link" in out["reason"], (op, out["reason"])


# -- ADAPT_CREATIVE_API-03: a finish reason other than success is a refusal ------------------------------------------

def test_p2_a_content_filtered_picture_is_refused(paid, gate_allows) -> None:
    paid("stability", _reply(200, _stability(GOOD, reason="CONTENT_FILTERED")))
    out = _invoke("generate_paid", **PAID)
    assert out["code"] == BACKEND_FAILED and "CONTENT_FILTERED" in out["reason"]


# -- the merge: adapt_creative_api's Stability and Replicate calls, and an images endpoint, as paid backends ---------

def test_merge_stability_generates_an_inspected_picture_and_its_key_goes_only_to_it(paid, gate_allows) -> None:
    wire = paid("stability", _reply(200, _stability(GOOD)))
    out = _invoke("generate_paid", **{**PAID, "seed": 5, "options": {"steps": 20, "negative_prompt": "blur"}})
    assert out["completion_state"] == "qualified-draft" and out["artifact"]["measured"]["width"] == 64
    sent = wire.sent[0]
    assert sent.url == "https://api.stability.ai/v1/generation/engine-test/text-to-image"
    assert sent.headers["Authorization"] == f"Bearer {KEY}" and json.loads(sent.body)["seed"] == 5
    assert json.loads(sent.body)["text_prompts"][1] == {"text": "blur", "weight": -1.0}
    assert out["artifact"]["provenance"]["seed"] == 99 and KEY not in json.dumps(out)
    assert [e["kind"] for e in out["evidence"]] == ["media_inspection", "paid_request", "action_gate", "claim"]


def test_merge_replicate_runs_a_prediction_polls_it_and_fetches_its_picture_without_the_key(paid, gate_allows) -> None:
    def answer(request: http.Request) -> http.Response:
        if request.url.endswith("/predictions") and request.method == "POST":
            value: object = {"id": "abc123", "status": "processing"}
        elif request.url.endswith("/v1/predictions/abc123"):
            value = {"id": "abc123", "status": "succeeded", "output": ["https://replicate.delivery/x/out.png"]}
        else:
            return http.Response(status=200, headers={"content-type": "image/png"}, body=GOOD, url=request.url)
        return http.Response(status=200, headers={}, body=json.dumps(value).encode(), url=request.url)

    wire = paid("replicate", answer)
    out = _invoke("generate_paid", **{**PAID, "backend": "replicate", "options": {"output_format": "png"}})
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    assert [r.url for r in wire.sent] == ["https://api.replicate.com/v1/models/owner/model/predictions",
                                          "https://api.replicate.com/v1/predictions/abc123",
                                          "https://replicate.delivery/x/out.png"]
    assert "Authorization" in wire.sent[1].headers and "Authorization" not in wire.sent[2].headers
    assert json.loads(wire.sent[0].body)["input"]["output_format"] == "png"


def test_merge_replicate_a_failed_prediction_is_refused(paid, gate_allows) -> None:
    paid("replicate", _reply(200, {"id": "x1", "status": "failed", "error": "NSFW content detected"}))
    out = _invoke("generate_paid", **{**PAID, "backend": "replicate"})
    assert out["code"] == BACKEND_FAILED and "NSFW" in out["reason"]
    with pytest.raises(ValueError):
        ReplicateBackend(PaidService(key=KEY, model="not a model"))


@pytest.mark.parametrize("item", [{"b64_json": _b64(GOOD)}, {"url": "https://files.example.com/pic.png"}])
def test_merge_an_images_endpoint_answers_inline_or_by_address(paid, gate_allows, item: dict) -> None:
    def answer(request: http.Request) -> http.Response:
        if request.url.endswith("/images/generations"):
            return http.Response(status=200, headers={}, body=json.dumps({"data": [item]}).encode(), url=request.url)
        return http.Response(status=200, headers={}, body=GOOD, url=request.url)

    wire = paid("images_api", answer)
    out = _invoke("generate_paid", **{**PAID, "backend": "images_api"})
    assert out["completion_state"] == "qualified-draft" and json.loads(wire.sent[0].body)["size"] == "64x48"
    assert wire.sent[0].url == "https://images.example.com/v1/images/generations"


def test_merge_the_paid_backends_join_the_family_registry_flagged_paid(paid) -> None:
    paid("stability", _reply(200, _stability(GOOD)))
    found = {b["name"]: b for b in _invoke("backends")["backends"]}
    assert (found["stability"]["paid"], found["stability"]["network"], found["stability"]["local"]) == (
        True, True, False)
    made = image_backends.generate("stability", ImageRequest(width=64, height=48, prompt="a kite"))
    assert made.completion_state == "qualified-draft" and made.artifact.provenance.backend == "stability"
    assert image_backends.generate("stability", ImageRequest(width=64, height=48)).code == INPUT_INVALID
    assert image_backends.generate("stability", ImageRequest(prompt="x", options={"image": "y"})).code == INPUT_INVALID
    with pytest.raises(ValueError):
        PaidService(key=KEY, model="m", base_url="http://plain.example.com")
    assert "test-key" not in repr(PaidService(key=KEY, model="m"))


# -- G9: every hop is checked, a 403 is an error ---------------------------------------------------------------------

def test_g9_a_redirect_to_this_machine_is_refused_and_never_followed(paid, gate_allows) -> None:
    wire = paid("stability", _reply(302, headers={"location": "http://127.0.0.1:8188/steal"}, body=b""))
    out = _invoke("generate_paid", **PAID)
    assert out["code"] == BACKEND_FAILED and "G6_E_HTTP_REFUSED" in out["reason"] and len(wire.sent) == 1


def test_g9_a_403_is_an_error_not_an_empty_answer(paid, gate_allows) -> None:
    paid("stability", _reply(403, {"message": "forbidden"}))
    out = _invoke("generate_paid", **PAID)
    assert out["code"] == BACKEND_FAILED and "403" in out["reason"] and out["evidence"][0]["status"] == 403


def test_g9_a_picture_address_that_resolves_inside_the_network_is_refused(paid, gate_allows) -> None:
    def answer(request: http.Request) -> http.Response:
        body = {"data": [{"url": "https://inside.example.com/pic.png"}]}
        return http.Response(status=200, headers={}, body=json.dumps(body).encode(), url=request.url)

    wire = paid("images_api", answer, resolver=lambda host, port: ["10.0.0.7"] if host.startswith("inside") else [
        PUBLIC])
    out = _invoke("generate_paid", **{**PAID, "backend": "images_api"})
    assert out["code"] == BACKEND_FAILED and "non-public" in out["reason"] and len(wire.sent) == 1


# -- G4: errors tighten ----------------------------------------------------------------------------------------------

def test_g4_a_transport_failure_and_a_non_json_reply_are_refusals(paid, gate_allows) -> None:
    def broken(request: http.Request) -> http.Response:
        raise OSError("connection reset")

    paid("stability", broken)
    assert _invoke("generate_paid", **PAID)["code"] == BACKEND_FAILED
    paid("stability", _reply(200, body=b"<html>oops</html>"))
    assert "not JSON" in _invoke("generate_paid", **PAID)["reason"]


def test_g4_without_pillow_no_picture_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(block, "pillow", lambda: None)
    assert _invoke("resize", image_base64=_b64(GOOD), width=8)["code"] == EXTRA_MISSING


# -- the base64 guard and the strict measurement ---------------------------------------------------------------------

def test_the_base64_guard_refuses_before_decoding_and_reads_a_data_url() -> None:
    assert edit.decode_base64("data:image/png;base64," + _b64(GOOD), "image_base64") == GOOD
    with pytest.raises(Exception, match="more than"):
        edit.decode_base64("A" * (edit.MAX_BYTES * 4 // 3 + 16), "image_base64")
    for bad in ("not base64 !!", "data:image/png,rawbytes", ""):
        assert _invoke("get_image_info", image_base64=bad or "=")["code"] == INPUT_INVALID


def test_get_image_info_measures_the_bytes_strictly() -> None:
    out = _invoke("get_image_info", image_base64=_b64(GOOD))
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_MEASURED"
    assert (out["info"]["format"], out["info"]["width"], out["info"]["mime"]) == ("PNG", 64, "image/png")
    assert _invoke("get_image_info", image_base64=_b64(GOOD[: len(GOOD) // 2]))["code"] == UNDECODABLE
    huge = io.BytesIO()
    Image.new("1", (9000, 8000)).save(huge, format="PNG")
    assert "more than" in _invoke("get_image_info", image_base64=_b64(huge.getvalue()))["reason"]


# -- a small request cannot make G6 hold a huge picture ----------------------------------------------------------------

#: The largest picture a caller may give, or ask for: 4096 x 4096 pixels, the most the family's generators make.
MOST = 4096 * 4096


@pytest.fixture
def full_size(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Pillow's full decode and its resize, for anything larger than a caller may give, replaced by a recorder that
    refuses: whatever reaches one is listed, and no memory is spent on it."""
    from PIL import ImageFile

    reached: list[str] = []
    load, resize = ImageFile.ImageFile.load, Image.Image.resize

    def guarded_load(self, *args: object, **kwargs: object) -> object:
        if self.size[0] * self.size[1] > MOST:
            reached.append(f"decode {self.size[0]}x{self.size[1]}")
            raise OSError("a full decode was reached")
        return load(self, *args, **kwargs)

    def guarded_resize(self, size: tuple[int, int], *args: object, **kwargs: object) -> object:
        if size[0] * size[1] > MOST:
            reached.append(f"resize to {size[0]}x{size[1]}")
            raise OSError("a full-size resize was reached")
        return resize(self, size, *args, **kwargs)

    monkeypatch.setattr(ImageFile.ImageFile, "load", guarded_load)
    monkeypatch.setattr(Image.Image, "resize", guarded_resize)
    return reached


def test_a_picture_larger_than_a_caller_may_give_is_refused_before_it_is_decoded(full_size: list[str]) -> None:
    flat = io.BytesIO()
    Image.new("1", (8000, 8000)).save(flat, format="PNG")  # one colour, 8000 x 8000: a few kilobytes as a PNG
    for op in ("convert", "get_image_info"):
        out = _invoke(op, image_base64=_b64(flat.getvalue()))
        assert out["code"] == INPUT_INVALID and full_size == [], (op, full_size, out["reason"])
        assert "more than a caller may give (4096x4096" in out["reason"], out["reason"]


def test_a_size_larger_than_a_caller_may_ask_for_is_refused_before_it_is_made(full_size: list[str]) -> None:
    error = _refused("resize", image_base64=_b64(GOOD), width=8192, height=8192)
    assert "G6_E_INPUT_VALIDATION" in error and "4096x4096" in error, (full_size, error)
    out = _invoke("resize", image_base64=_b64(GOOD), width=8192)  # 64 x 48 wide by 8192: the height follows, 6144
    assert out["code"] == INPUT_INVALID and "8192x6144" in out["reason"] and full_size == [], out["reason"]


# -- only the formats G6 handles are decoded: nothing is handed to a program outside Pillow ---------------------------

#: Headers only, nothing drawn: enough for Pillow to pick its EPS reader (which runs Ghostscript on the host) and its
#: WMF reader (which, on Windows, hands the file to the system's metafile renderer).
EPS = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 64 48\n%%EndComments\n"
WMF = b"\xd7\xcd\xc6\x9a\x00\x00" + struct.pack("<hhhhH", 0, 0, 64, 48, 72) + bytes(6) + b"\x01\x00\x09\x00" + bytes(18)


@pytest.fixture
def renderers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Pillow's hand-offs to programs outside it, each replaced by a recorder that refuses: whatever reaches one is
    listed, and nothing outside runs even then."""
    from PIL import EpsImagePlugin

    reached: list[str] = []

    def recorder(name: str) -> Callable[..., None]:
        def record(*args: object, **kwargs: object) -> None:
            reached.append(name)
            raise OSError(f"{name} was reached")
        return record

    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", recorder("Ghostscript"))
    if hasattr(Image.core, "drawwmf"):  # Windows: the system's metafile renderer
        monkeypatch.setattr(Image.core, "drawwmf", recorder("drawwmf"))
    return reached


@pytest.mark.parametrize("data", [EPS, WMF], ids=["eps", "wmf"])
def test_p0_a_picture_another_program_would_render_is_refused_and_never_rendered(renderers: list[str], paid,
                                                                                  data: bytes) -> None:
    assert Image.open(io.BytesIO(data)).format in ("EPS", "WMF")  # the bytes alone pick Pillow's reader
    sent = _b64(data)
    for op, params in (("get_image_info", {"image_base64": sent}), ("convert", {"image_base64": sent}),
                       ("composite", {"image_base64": _b64(GOOD), "overlay_base64": sent})):
        out = _invoke(op, **params)
        assert out["code"] == UNDECODABLE and renderers == [], (op, renderers, out["reason"])
        assert "PNG, JPEG, WEBP, GIF, BMP or TIFF" in out["reason"], (op, out["reason"])
    paid("stability", _reply(200, _stability(data)))  # a paid service that answers with such a file
    before = _media_files()
    made = image_backends.generate("stability", ImageRequest(width=64, height=48, prompt="a kite"))
    assert made.code == UNDECODABLE and renderers == [] and _media_files() == before, made.reason


# -- the Pillow ops ----------------------------------------------------------------------------------------------------

def test_each_edit_measures_what_it_must() -> None:
    src = _b64(GOOD)
    assert _invoke("rotate", image_base64=src, angle=90)["artifact"]["measured"]["width"] == 48
    turned = _invoke("rotate", image_base64=src, angle=30)
    assert turned["completion_state"] == "qualified-draft" and "width" not in turned["artifact"]["requested"]
    assert _invoke("crop", image_base64=src, box=[0, 0, 65, 10])["code"] == INPUT_INVALID  # outside: never padded
    laid = _invoke("composite", image_base64=src, overlay_base64=_b64(_picture(16, 16, seed=8)), x=4, y=4)
    assert laid["artifact"]["measured"]["width"] == 64
    clear = _picture(48, 48, colour=(0, 0, 0, 0), mode="RGBA")
    dotted = Image.open(io.BytesIO(clear))
    dotted.paste((200, 30, 30, 255), (24, 24, 44, 44))  # away from the corner's 16-pixel colour block
    buffer = io.BytesIO()
    dotted.save(buffer, format="PNG")
    jpeg = _invoke("convert", image_base64=_b64(buffer.getvalue()), format="jpeg", name="as-jpeg")
    assert jpeg["artifact"]["mime"] == "image/jpeg" and jpeg["artifact"]["name"] == "as-jpeg.jpg"
    assert min(Image.open(jpeg["artifact"]["path"]).getpixel((0, 0))) >= 250  # transparency laid on white
    assert _invoke("flip", source=_put("to-flip", GOOD), direction="vertical")["completion_state"] == "qualified-draft"
    assert "G6_E_INPUT_VALIDATION" in _refused("crop", image_base64=src)
    assert "G6_E_INPUT_VALIDATION" in _refused("resize", image_base64=src, source="to-flip.png", width=4)


# -- the vision check: a claim, never a check ------------------------------------------------------------------------

def test_g6_a_vision_models_answer_is_a_claim_that_changes_nothing() -> None:
    model = FakeLLM(default="VERIFIED: this is exactly the picture asked for. Approved.")
    out = AdaptImageBlock(vision=model).infer(AdaptImageInput(op="flip", image_base64=_b64(GOOD),
                                                              vision_check=True, prompt="a noise field")).unwrap()
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_INSPECTED"
    claim = out["evidence"][-1]
    assert claim["kind"] == "claim" and claim["counts_as_check"] is False and "VERIFIED" in claim["said"]
    assert len(model.images) == 1 and model.images[0][0].media_type == "image/png"
    quiet = AdaptImageBlock(vision=NoModel()).infer(AdaptImageInput(op="flip", image_base64=_b64(GOOD),
                                                                    vision_check=True)).unwrap()
    assert quiet["completion_state"] == "qualified-draft" and "no model was asked" in quiet["evidence"][-1]["said"]
    plain = _invoke("flip", image_base64=_b64(GOOD))
    assert all(e["kind"] != "claim" for e in plain["evidence"])  # no model call unless asked


def test_g4_a_vision_caller_that_raises_leaves_the_picture_as_it_was() -> None:
    class Broken(FakeLLM):
        def complete_with_images(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("model went away")

    out = AdaptImageBlock(vision=Broken()).infer(AdaptImageInput(op="flip", image_base64=_b64(GOOD),
                                                                 vision_check=True)).unwrap()
    assert out["completion_state"] == "qualified-draft" and "model went away" in out["evidence"][-1]["error"]


# -- G5, G6, G7, G8 -------------------------------------------------------------------------------------------------

def test_g5_generate_and_generate_paid_are_sensitive_and_the_edits_run_over_mcp() -> None:
    assert package.__g6_sensitive_ops__ == ("generate", "generate_paid")
    for op in package.__g6_sensitive_ops__:
        denied = invoke_reply("adapt_image", op, json.dumps(PAID), allow_sensitive=frozenset())
        assert denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED", op
    edited = invoke_reply("adapt_image", "resize", json.dumps({"image_base64": _b64(GOOD), "width": 16}),
                          allow_sensitive=frozenset())
    assert edited["ok"] and edited["completion_state"] == "qualified-draft" and check_envelope(edited) == []


def test_g6_g7_g8_no_key_is_read_no_model_is_called_unasked_and_nothing_must_not_ship() -> None:
    declared = [k.name.lower() for k in package.__g6_keys__]  # a key is named only as its g6.core.keys declaration
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower().replace("openai-compatible", "")
        for name in declared:
            text = text.replace(name, "")
        for word in ("api_key", "getenv", "environ", "litellm", "openrouter", "anthropic", "openai", "mock", "fake",
                     "simulat", "experimental", "all rights reserved", "httpx", "urllib.request", "figma.com",
                     "canva.com", "envato.com"):
            assert word not in text, (path.name, word)
        if path.suffix == ".py":
            assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name
    assert package.__g6_extras__ == {"image": ("PIL",)} and package.__g6_outside__ == ()
    meta = importlib.metadata.metadata("pillow")
    said = " ".join(filter(None, [meta.get("License-Expression"), meta.get("License"),
                                  *meta.get_all("Classifier", [])])).upper()
    assert said and not re.search(r"\b(A?GPL|SSPL|NON-?COMMERCIAL)\b", said)


# -- D2, D4, D5, D6, D9 -----------------------------------------------------------------------------------------------

def test_d2_it_declares_what_it_guards_against_and_left_planned() -> None:
    assert package.__g6_mitigates__ == ("visual_check_failed", "unchosen_paid_service")
    assert package.__g6_introduces__ == () and "g6.media.adapt_image" not in FailureModeRegistry.planned()


def test_d4_the_block_is_typed_and_lists_its_ops() -> None:
    assert get_registry().get_class("adapt_image") is AdaptImageBlock
    assert AdaptImageInput.model_config["extra"] == "forbid"
    assert len(_invoke("ops")["ops"]) == len(_invoke("help")["help"]) == 11


def test_d5_backends_lists_the_familys_backends_without_asking_and_one_from_outside_is_used() -> None:
    listed = {b["name"]: b for b in _invoke("backends")["backends"]}
    assert {"comfyui", "procedural"} <= set(listed) and listed["comfyui"]["available"] is None

    class Stripes:
        name, local, network, paid = "stripes_test", True, False, False

        def available(self) -> str:
            return ""

        def generate(self, request: ImageRequest):  # noqa: ANN201
            return image_backends.generate("procedural", request.model_copy(update={"options": {
                "technique": "voronoi"}}))

    image_backends.register(Stripes(), replace=True)
    try:
        out = _invoke("generate", backend="stripes_test", width=40, height=40, seed=2)
        assert out["completion_state"] == "qualified-draft" and out["artifact"]["measured"]["width"] == 40
    finally:
        image_backends.unregister("stripes_test")


def test_d6_an_agent_finds_it_and_invokes_its_edits_over_mcp() -> None:
    assert "adapt_image" in [r["component"] for r in discovery.find("resize and convert an image")]
    title = FailureModeRegistry.title("unchosen_paid_service")
    assert "adapt_image" in [r["component"] for r in discovery.find(title)]
    info = describe_reply("adapt_image")
    assert info["family"] == "media" and info["skill"] and info["sensitive_ops"] == ["generate", "generate_paid"]
    for op, params in (("get_image_info", {"image_base64": _b64(GOOD)}), ("backends", {}),
                       ("convert", {"image_base64": _b64(GOOD), "format": "webp"})):
        reply = invoke_reply("adapt_image", op, json.dumps({**params, "request_id": "q1", "run_id": "u1"}))
        assert reply["ok"] and reply["completion_state"] == "qualified-draft" and check_envelope(reply) == [], op
        assert (reply["result"]["request_id"], reply["result"]["run_id"]) == ("q1", "u1")


def test_d6_every_op_that_runs_over_mcp_as_it_is_has_a_skill_example_that_round_trips() -> None:
    from g6.core import skills

    examples = skills.skill_examples(skills.skill_text("adapt_image"))
    open_ops = set(_invoke("ops")["ops"]) - set(package.__g6_sensitive_ops__)
    assert open_ops <= {op for op, _ in examples}, sorted(open_ops - {op for op, _ in examples})
    for op, params in examples:
        if op in package.__g6_sensitive_ops__:
            continue
        reply = invoke_reply("adapt_image", op, json.dumps(params))
        assert reply["ok"] and reply["completion_state"] == "qualified-draft", (op, reply.get("warning_card"))


_WITHOUT = """
import json, sys
sys.modules["PIL"] = None
from g6.core.registry import get_registry
reg = get_registry()
out = {op: reg.invoke("adapt_image", op, params).unwrap() for op, params in (
    ("resize", {"image_base64": "AAAA", "width": 4}), ("get_image_info", {"image_base64": "AAAA"}),
    ("backends", {}), ("ops", {}), ("help", {}))}
print(json.dumps({op: [v.get("completion_state"), v.get("code", "")] for op, v in out.items()}))
"""


def test_d9_without_pillow_every_picture_op_says_it_is_not_installed(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          env={"G6_HOME": str(tmp_path), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["resize"] == ["blocked-escalated", EXTRA_MISSING] == found["get_image_info"]
    assert found["backends"] == ["qualified-draft", ""] and found["ops"][0] is None
