# SPDX-License-Identifier: Apache-2.0
"""adapt_comfyui against a ComfyUI stand-in served by this test on 127.0.0.1, never a real server: every picture is
fetched into the media folder and inspected, and a blank, missing, short or wrong-sized one refuses the whole result
(D1, ADAPT_COMFYUI-02); no caller code runs (ADAPT_COMFYUI-01); nothing is ``verified`` (-03); graphs are checked
against the server's nodes before they are sent; a cached prompt is read from the earlier identical run; the server is
reached only through g6.core.outside.call_service (loopback, no redirects, errors are errors); every op that talks to
it is sensitive; and it joins the family's backend registry."""
from __future__ import annotations

import ast
import copy
import email.parser
import http.server
import io
import json
import random
import re
import socket
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

Image = pytest.importorskip("PIL.Image")

import g6.media.adapt_comfyui as package  # noqa: E402
from g6.core import discovery  # noqa: E402
from g6.core.failure_modes import FailureModeRegistry  # noqa: E402
from g6.core.registry import get_registry  # noqa: E402
from g6.invariants import check_envelope  # noqa: E402
from g6.mcp import describe_reply, invoke_reply  # noqa: E402
from g6.media._types import (  # noqa: E402
    BACKEND_FAILED,
    BLANK,
    EXTRA_MISSING,
    INPUT_INVALID,
    NO_OUTPUT,
    OUTSIDE_MISSING,
    PENDING,
    SIZE_MISMATCH,
    ImageRequest,
    declares_backends,
    image_backends,
    media_file,
    media_root,
    write_media,
)
from g6.media.adapt_comfyui import block, client, workflows  # noqa: E402

SRC = Path(package.__file__).parent
CHECKPOINT = "sd15-test.safetensors"


def _combo(*values: str) -> list:
    return [list(values), {}]


OBJECT_INFO = {
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": _combo(CHECKPOINT, "sdxl-test.safetensors")}},
                               "output": ["MODEL", "CLIP", "VAE"], "output_node": False},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]}},
                       "output": ["CONDITIONING"], "output_node": False},
    "EmptyLatentImage": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}],
                                                "batch_size": ["INT", {}]}}, "output": ["LATENT"], "output_node": False},
    "KSampler": {"input": {"required": {"model": ["MODEL"], "seed": ["INT", {}], "steps": ["INT", {}],
                                        "cfg": ["FLOAT", {}], "sampler_name": _combo("euler", "dpmpp_2m"),
                                        "scheduler": _combo("normal", "karras"), "positive": ["CONDITIONING"],
                                        "negative": ["CONDITIONING"], "latent_image": ["LATENT"],
                                        "denoise": ["FLOAT", {}]}}, "output": ["LATENT"], "output_node": False},
    "VAEDecode": {"input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}}, "output": ["IMAGE"],
                  "output_node": False},
    "VAEEncode": {"input": {"required": {"pixels": ["IMAGE"], "vae": ["VAE"]}}, "output": ["LATENT"],
                  "output_node": False},
    "VAEEncodeForInpaint": {"input": {"required": {"pixels": ["IMAGE"], "vae": ["VAE"], "mask": ["MASK"],
                                                   "grow_mask_by": ["INT", {}]}}, "output": ["LATENT"],
                            "output_node": False},
    "LoadImage": {"input": {"required": {"image": _combo("example.png")}}, "output": ["IMAGE", "MASK"],
                  "output_node": False},
    "LoadImageMask": {"input": {"required": {"image": _combo("example.png"),
                                             "channel": _combo("alpha", "red", "green", "blue")}}, "output": ["MASK"],
                      "output_node": False},
    "ImageScale": {"input": {"required": {"image": ["IMAGE"], "upscale_method": _combo("bilinear", "lanczos"),
                                          "width": ["INT", {}], "height": ["INT", {}],
                                          "crop": _combo("disabled", "center")}}, "output": ["IMAGE"],
                   "output_node": False},
    "UpscaleModelLoader": {"input": {"required": {"model_name": ["COMBO", {"options": ["2x-test.pth"]}]}},
                           "output": ["UPSCALE_MODEL"], "output_node": False},
    "ImageUpscaleWithModel": {"input": {"required": {"upscale_model": ["UPSCALE_MODEL"], "image": ["IMAGE"]}},
                              "output": ["IMAGE"], "output_node": False},
    "PreviewImage": {"input": {"required": {"images": ["IMAGE"]}}, "output": [], "output_node": True},
    "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING", {}]}}, "output": [],
                  "output_node": True},
    # Listed as a real server lists what is installed beside its core: a custom node that fetches an address, one of
    # ComfyUI's own API nodes (it spends money with an outside service), and a custom image filter.
    "LoadImageFromUrl": {"input": {"required": {"url": ["STRING", {}]}}, "output": ["IMAGE", "MASK"],
                         "output_node": False},
    "StabilityStableImageUltraNode": {"input": {"required": {"prompt": ["STRING", {}]}}, "output": ["IMAGE"],
                                      "output_node": False, "api_node": True},
    "ExampleFilter": {"input": {"required": {"image": ["IMAGE"]}}, "output": ["IMAGE"], "output_node": False},
}


def _png(width: int, height: int, *, seed: int = 1, blank: bool = False) -> bytes:
    if blank:
        picture = Image.new("RGB", (width, height), (0, 0, 0))
    else:
        picture = Image.frombytes("RGB", (width, height), random.Random(seed).randbytes(width * height * 3))
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return out.getvalue()


class FakeComfyUI:
    """What a ComfyUI server answers, for the routes adapt_comfyui uses. ``modes`` bend it: ``blank`` (every picture
    black, as a NaN decode leaves it), ``blank_second``, ``wrong_size``, ``fewer``, ``missing_view``, ``redirect_view``,
    ``error``, ``empty`` (finished, no outputs, nothing cached), ``refuse`` (400 on /prompt), ``nocache``."""

    def __init__(self) -> None:
        self.modes: set[str] = set()
        self.object_info = copy.deepcopy(OBJECT_INFO)
        self.history: dict[str, dict] = {}
        self.files: dict[tuple[str, str, str], bytes] = {}
        self.uploads: dict[str, bytes] = {}
        self.requests: list[tuple[str, str]] = []
        self.pending_polls = 0

    def _size(self, graph: dict, node_id: str) -> tuple[int, int, int]:
        node = graph[node_id]
        cls, inputs = node["class_type"], node["inputs"]
        if cls == "EmptyLatentImage":
            return inputs["width"], inputs["height"], inputs["batch_size"]
        if cls == "ImageScale":
            return inputs["width"], inputs["height"], 1
        if cls == "LoadImage":
            source = Image.open(io.BytesIO(self.uploads[inputs["image"].rsplit(" [", 1)[0]]))
            return source.width, source.height, 1
        if cls == "ImageUpscaleWithModel":
            w, h, n = self._size(graph, inputs["image"][0])
            return 2 * w, 2 * h, n
        upstream = [v[0] for v in inputs.values() if isinstance(v, list) and len(v) == 2 and v[0] in graph]
        for source in ([inputs["latent_image"][0]] if "latent_image" in inputs else []) + upstream:
            try:
                return self._size(graph, source)
            except KeyError:
                continue
        raise KeyError(node_id)

    def run(self, graph: dict) -> dict:
        prompt_id = str(uuid.uuid4())
        number = len(self.history)
        same = [e for e in self.history.values() if e["prompt"][2] == graph and e["outputs"]]
        entry = {"prompt": [number, prompt_id, graph, {}, []], "outputs": {},
                 "status": {"status_str": "success", "completed": True, "messages": []}}
        if "error" in self.modes:
            entry["status"] = {"status_str": "error", "completed": False, "messages": [
                ["execution_start", {}], ["execution_error", {"node_type": "KSampler",
                                                              "exception_message": "CUDA out of memory"}]]}
        elif same and "nocache" not in self.modes or "empty" in self.modes:
            entry["status"]["messages"] = [["execution_cached", {"nodes": list(graph)}]]
        else:
            for node_id, node in graph.items():
                if node["class_type"] != "PreviewImage":
                    continue
                width, height, count = self._size(graph, node["inputs"]["images"][0])
                if "wrong_size" in self.modes:
                    width += 8
                if "fewer" in self.modes:
                    count -= 1
                pictures = []
                for index in range(count):
                    name = f"ComfyUI_temp_{number:05d}_{index}.png"
                    blank = "blank" in self.modes or ("blank_second" in self.modes and index == 1)
                    self.files[("temp", "", name)] = _png(width, height, seed=number * 10 + index, blank=blank)
                    pictures.append({"filename": name, "subfolder": "", "type": "temp"})
                entry["outputs"][node_id] = {"images": pictures}
        self.history[prompt_id] = entry
        return {"prompt_id": prompt_id, "number": number, "node_errors": {}}


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, *args: object) -> None:
        pass

    def _send(self, status: int, body: bytes, kind: str = "application/json", headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: object, status: int = 200) -> None:
        self._send(status, json.dumps(value).encode())

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        fake, parts = self.server.fake, urlsplit(self.path)
        fake.requests.append(("GET", parts.path))
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        if parts.path == "/":
            self._send(200, b"<html>ComfyUI</html>", "text/html")
        elif parts.path == "/system_stats":
            self._json({"system": {"os": "nt", "comfyui_version": "0.3.test", "python_version": "3.12"},
                        "devices": [{"name": "test device", "type": "cpu", "vram_total": 0}]})
        elif parts.path == "/queue":
            self._json({"queue_running": [], "queue_pending": [["x"]]})
        elif parts.path == "/object_info":
            self._json(fake.object_info)
        elif parts.path == "/history":
            self._json(fake.history)
        elif parts.path.startswith("/history/"):
            prompt_id = parts.path.rsplit("/", 1)[1]
            if fake.pending_polls > 0:
                fake.pending_polls -= 1
                self._json({})
            else:
                self._json({prompt_id: fake.history[prompt_id]} if prompt_id in fake.history else {})
        elif parts.path == "/view":
            key = (query.get("type", ""), query.get("subfolder", ""), query.get("filename", ""))
            first = key[2].endswith("_0.png")
            if "redirect_view" in fake.modes:
                self._send(302, b"", headers={"Location": "http://127.0.0.1:9/elsewhere.png"})
            elif key not in fake.files or ("missing_view" in fake.modes and first):
                self._send(404, b"no such file")
            else:
                self._send(200, fake.files[key], "image/png")
        else:
            self._send(404, b"{}")

    def do_POST(self) -> None:  # noqa: N802
        fake, path = self.server.fake, urlsplit(self.path).path
        fake.requests.append(("POST", path))
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if path == "/prompt":
            if "refuse" in fake.modes:
                self._json({"error": {"message": "Prompt outputs failed validation"}, "node_errors": {"7": {}}}, 400)
            else:
                self._json(fake.run(json.loads(body)["prompt"]))
        elif path == "/upload/image":
            message = email.parser.BytesParser().parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            fields = {part.get_param("name", header="content-disposition"): part for part in message.get_payload()}
            picture = fields["image"]
            name = picture.get_filename()
            fake.uploads[name] = picture.get_payload(decode=True)
            self._json({"name": name, "subfolder": "", "type": fields["type"].get_payload()})
        else:
            self._send(404, b"{}")


class _Server(http.server.ThreadingHTTPServer):
    fake: FakeComfyUI


@pytest.fixture
def comfy(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeComfyUI]:
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.fake = FakeComfyUI()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(block.URL_ENV, f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.delenv("G6_COMFYUI_NODES", raising=False)  # only the operator's own setting, never the shell's
    monkeypatch.setattr(client, "POLL_INTERVAL", 0.01)
    yield httpd.fake
    httpd.shutdown()
    httpd.server_close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_comfyui", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_comfyui", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


def _put(name: str, data: bytes) -> str:
    write_media(media_file(name, ".png"), data, overwrite=True)
    return f"{name}.png"


TXT = {"prompt": "a lighthouse at dusk", "model": CHECKPOINT, "width": 64, "height": 48, "steps": 4}


# -- D1: a blank picture is refused, never returned ------------------------------------------------------------------

def test_d1_a_black_picture_from_a_failed_decode_is_refused_and_removed(comfy: FakeComfyUI) -> None:
    comfy.modes.add("blank")
    before = _media_files()
    out = _invoke("txt2img", **TXT, seed=3, name="d1-black")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == BLANK, out.get("reason")
    assert out["artifact"] is None and check_envelope(out) == [] and _media_files() == before
    assert [c["name"] for c in out["evidence"][0]["checks"] if not c["passed"]] == ["not_blank"]


def test_d1_one_black_picture_in_a_batch_refuses_the_batch_and_removes_its_good_sibling(comfy: FakeComfyUI) -> None:
    comfy.modes.add("blank_second")
    before = _media_files()
    out = _invoke("txt2img", **TXT, batch_size=2, seed=4, name="d1-batch")
    assert out["code"] == BLANK and "picture 2 of 2" in out["reason"] and _media_files() == before


# -- ADAPT_COMFYUI-02: a finished run is checked picture by picture, never reported done without them ----------------

@pytest.mark.parametrize(("mode", "code", "said"), [
    ("missing_view", NO_OUTPUT, "was not saved"),     # the archive skipped a failed fetch without a word
    ("fewer", NO_OUTPUT, "not the 2 asked for"),      # the archive said "complete" whatever arrived
    ("empty", NO_OUTPUT, "finished with no pictures"),
    ("wrong_size", SIZE_MISMATCH, "not the 64x48 asked for"),
])
def test_p1_a_run_whose_pictures_are_missing_short_or_wrong_is_refused(comfy: FakeComfyUI, mode: str, code: str,
                                                                       said: str) -> None:
    comfy.modes.add(mode)
    before = _media_files()
    out = _invoke("txt2img", **TXT, batch_size=2, seed=5)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == code and said in out["reason"], out
    assert _media_files() == before


def test_a_good_batch_is_two_inspected_pngs_in_the_media_folder(comfy: FakeComfyUI) -> None:
    out = _invoke("txt2img", **TXT, batch_size=2, seed=6, name="lighthouse", request_id="q", run_id="u")
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_INSPECTED"
    assert [a["name"] for a in out["artifacts"]] == ["lighthouse-1.png", "lighthouse-2.png"]
    assert out["artifact"]["measured"]["width"] == 64 and out["artifact"]["mime"] == "image/png"
    assert out["seed"] == 6 and out["artifact"]["provenance"]["backend"] == "comfyui"
    assert out["artifact"]["provenance"]["backend_version"] == "ComfyUI 0.3.test"
    assert [e["kind"] for e in out["evidence"]] == ["media_inspection", "media_inspection", "comfyui_run"]
    assert check_envelope(out) == [] and (out["request_id"], out["run_id"]) == ("q", "u")
    graph = out["artifact"]["provenance"]["recipe"]["graph"]
    assert {n["class_type"] for n in graph.values()} >= {"KSampler", "PreviewImage"}
    assert "SaveImage" not in {n["class_type"] for n in graph.values()}  # nothing lands in ComfyUI's output folder


def test_a_cached_prompt_is_served_from_the_earlier_identical_run_and_says_so(comfy: FakeComfyUI) -> None:
    first = _invoke("txt2img", **TXT, seed=7, name="cached-a")
    again = _invoke("txt2img", **TXT, seed=7, name="cached-b")
    assert again["completion_state"] == "qualified-draft" and again["cached_from"] == first["prompt_id"]
    assert again["artifact"]["sha256"] == first["artifact"]["sha256"] and "cached run" in again["warning_card"][
        "message"]


def test_without_a_seed_each_run_draws_and_reports_its_own(comfy: FakeComfyUI) -> None:
    seeds = {_invoke("txt2img", **TXT)["seed"] for _ in range(2)}
    assert len(seeds) == 2


# -- ADAPT_COMFYUI-01 and -03: no caller code runs, nothing is verified ----------------------------------------------

def test_p0_no_script_runs_the_bridge_is_gone_and_its_op_and_fields_are_refused() -> None:
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not {"eval", "exec", "compile", "__import__"} & called, path.name
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
            n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not [m for m in imported if "comfy_script" in m or m in ("subprocess", "runpy")], path.name
    assert not (SRC / "comfy_script_bridge").exists()
    assert "G6_E_INPUT_VALIDATION" in _refused("comfy_script_run_script", script_text="import os")
    assert "G6_E_INPUT_VALIDATION" in _refused("txt2img", prompt="x", script_text="import os")


def test_g1_nothing_is_verified_and_the_package_never_assigns_it(comfy: FakeComfyUI) -> None:
    for path in SRC.rglob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name
    for op in ("capabilities", "get_status", "list_models"):
        out = _invoke(op)
        assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_REPORTED"


@pytest.mark.parametrize("field", [{"approved": True}, {"reviewer": "Dr Who"}, {"completion_state": "verified"},
                                   {"safety_context": {"ok": True}}, {"allow_unvalidated": True},
                                   {"server_url": "http://example.com"}, {"output_path": "C:/x.png"}])
def test_g3_a_callers_claims_and_addresses_are_refused_not_honoured(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("txt2img", **TXT, **field)


# -- graphs are checked against the server before they are sent ------------------------------------------------------

def _graph(**changes) -> dict:
    graph = workflows.txt2img(prompt="x", negative="", model=CHECKPOINT, width=64, height=64, steps=2, cfg=7.0,
                              sampler="euler", scheduler="normal", seed=1, batch=1)
    graph.update(changes)
    return graph


@pytest.mark.parametrize(("change", "said"), [
    ({"9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "x"}}}, "end the workflow in"),
    ({"5": {"class_type": "MadeUpNode", "inputs": {}}}, "no node class MadeUpNode"),
    ({"8": {"class_type": "VAEDecode", "inputs": {"samples": ["77", 0], "vae": ["1", 2]}}}, "not in the workflow"),
    ({"8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0]}}}, "missing its required input 'vae'"),
    ({"8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 4], "vae": ["1", 2]}}}, "asks for output 4"),
    ({"2": {"class_type": "CLIPTextEncode", "inputs": {"text": "../../secrets.txt", "clip": ["1", 1]}}},
     "leaves its folder"),
])
def test_a_callers_workflow_that_this_server_cannot_run_is_refused_before_it_is_sent(
        comfy: FakeComfyUI, change: dict, said: str) -> None:
    out = _invoke("submit_workflow", workflow=_graph(**change))
    assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT_INVALID and said in out["reason"]
    assert ("POST", "/prompt") not in comfy.requests


def _through(cls: str, inputs: dict) -> dict:
    """G6's txt2img graph with one more node, ``cls``, whose picture is the one previewed."""
    return _graph(**{"10": {"class_type": cls, "inputs": inputs},
                     "9": {"class_type": "PreviewImage", "inputs": {"images": ["10", 0]}}})


@pytest.mark.parametrize(("cls", "inputs"), [
    ("LoadImageFromUrl", {"url": "https://example.com/x.png"}),  # a custom node that fetches an address
    ("StabilityStableImageUltraNode", {"prompt": "a kite"}),     # one of ComfyUI's own API nodes: it spends money
    ("ExampleFilter", {"image": ["8", 0]}),                      # any custom node, until the person allows it
])
def test_a_node_class_the_server_lists_but_g6_does_not_send_is_refused_by_name_before_it_is_sent(
        comfy: FakeComfyUI, cls: str, inputs: dict) -> None:
    assert cls in comfy.object_info  # the server lists it: only G6's allow-list stands in the way
    out = _invoke("submit_workflow", workflow=_through(cls, inputs))
    assert out["code"] == INPUT_INVALID and cls in out["reason"] and "G6_COMFYUI_NODES" in out["reason"], out
    assert ("POST", "/prompt") not in comfy.requests


def test_a_node_class_the_person_who_runs_g6_allows_is_sent_and_a_call_cannot_allow_one(
        comfy: FakeComfyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = _through("ExampleFilter", {"image": ["8", 0]})
    assert _invoke("submit_workflow", workflow=workflow)["code"] == INPUT_INVALID
    assert "G6_E_INPUT_VALIDATION" in _refused("submit_workflow", workflow=workflow, allowed_nodes=["ExampleFilter"])
    assert ("POST", "/prompt") not in comfy.requests
    monkeypatch.setenv("G6_COMFYUI_NODES", "LoadImageFromUrl , ExampleFilter")  # where G6 runs: the operator's
    out = _invoke("submit_workflow", workflow=workflow, images=1, width=64, height=64)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    assert "ExampleFilter" in out["evidence"][-1]["node_classes"]


def test_a_checkpoint_or_sampler_the_server_lacks_is_refused_with_what_it_has(comfy: FakeComfyUI) -> None:
    out = _invoke("txt2img", **{**TXT, "model": "absent.safetensors"})
    assert out["code"] == INPUT_INVALID and CHECKPOINT in out["reason"]
    assert _invoke("txt2img", **{**TXT, "sampler": "made_up"})["code"] == INPUT_INVALID
    unnamed = _invoke("txt2img", prompt="x")
    assert unnamed["code"] == INPUT_INVALID and "needs model" in unnamed["reason"] and CHECKPOINT in unnamed["reason"]
    assert ("POST", "/prompt") not in comfy.requests


def test_a_good_caller_workflow_runs_and_its_pictures_are_checked(comfy: FakeComfyUI) -> None:
    out = _invoke("submit_workflow", workflow=_graph(), images=1, width=64, height=64, name="own")
    assert out["completion_state"] == "qualified-draft" and out["artifact"]["name"] == "own.png"
    assert _invoke("submit_workflow", workflow=_graph(), images=1, width=96, height=64, seed=9)["code"] == \
        SIZE_MISMATCH


def test_sizes_must_be_whole_latents() -> None:
    assert "multiple of 8" in _refused("txt2img", **{**TXT, "width": 65})


# -- img2img, inpaint and upscale upload a picture from the media folder ---------------------------------------------

def test_img2img_inpaint_and_upscale_read_a_media_file_and_check_what_comes_back(comfy: FakeComfyUI) -> None:
    source, mask = _put("src-photo", _png(80, 56, seed=2)), _put("src-mask", _png(80, 56, seed=3))
    common = {"prompt": "as a watercolour", "model": CHECKPOINT, "steps": 2}
    edited = _invoke("img2img", source=source, **common)
    assert edited["completion_state"] == "qualified-draft" and edited["artifact"]["measured"]["width"] == 80
    graph = edited["artifact"]["provenance"]["recipe"]["graph"]
    assert graph["4"]["inputs"]["image"].endswith(" [temp]") and len(comfy.uploads) == 1
    painted = _invoke("inpaint", source=source, mask=mask, **common)
    assert painted["completion_state"] == "qualified-draft" and len(comfy.uploads) == 2
    bigger = _invoke("upscale", source=source, model="2x-test.pth")
    assert bigger["completion_state"] == "qualified-draft" and bigger["artifact"]["measured"]["width"] == 160
    assert "larger_by_one_factor" in [c["name"] for c in bigger["artifact"]["checks"]]
    comfy.modes |= {"wrong_size", "nocache"}  # the same upscale again would come from ComfyUI's cache
    assert _invoke("upscale", source=source, model="2x-test.pth")["code"] == SIZE_MISMATCH
    assert _invoke("img2img", source="absent.png", **common)["code"] == INPUT_INVALID
    assert "G6_E_INPUT_VALIDATION" in _refused("img2img", source="../outside.png", **common)


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_link_in_the_media_folder_to_a_picture_kept_elsewhere_is_refused_before_anything_is_sent(
        comfy: FakeComfyUI, tmp_path: Path, kind: str) -> None:
    """A name in the media folder that is a link, or a second name for a file kept elsewhere, never sends that file to
    ComfyUI: not as a source, and not as a mask after the source went up."""
    from media_kit import outside_link

    outside_link(tmp_path, "linked.png", _png(80, 56, seed=4), kind)
    common = {"prompt": "as a watercolour", "model": CHECKPOINT, "steps": 2}
    for op, params in (("img2img", {"source": "linked.png", **common}),
                       ("upscale", {"source": "linked.png", "model": "2x-test.pth"}),
                       ("inpaint", {"source": _put("plain-src", _png(80, 56, seed=5)), "mask": "linked.png", **common})):
        out = _invoke(op, **params)
        assert out["code"] == INPUT_INVALID and "link" in out["reason"], (op, out.get("reason"))
    assert comfy.uploads == {} and ("POST", "/prompt") not in comfy.requests


def test_a_source_larger_than_a_caller_may_give_is_refused_before_it_is_read_or_sent(comfy: FakeComfyUI) -> None:
    flat = io.BytesIO()
    Image.new("1", (4200, 4200)).save(flat, format="PNG")  # more than 4096 x 4096 pixels
    out = _invoke("upscale", source=_put("huge-source", flat.getvalue()), model="2x-test.pth")
    assert out["code"] == INPUT_INVALID and "more than a caller may give" in out["reason"], out["reason"]
    assert comfy.uploads == {} and ("POST", "/prompt") not in comfy.requests


def test_submitted_without_waiting_is_pending_and_get_image_fetches_it_later(comfy: FakeComfyUI) -> None:
    queued = _invoke("txt2img", **TXT, seed=11, wait=False)
    assert queued["completion_state"] == "blocked-escalated" and queued["code"] == PENDING and queued["prompt_id"]
    comfy.pending_polls = 1
    assert _invoke("get_image", prompt_id=queued["prompt_id"])["code"] == PENDING
    later = _invoke("get_image", prompt_id=queued["prompt_id"], images=1, width=64, height=48)
    assert later["completion_state"] == "qualified-draft" and later["artifact"]["measured"]["height"] == 48


# -- G4: errors tighten ------------------------------------------------------------------------------------------------

def test_g4_an_execution_error_is_a_refusal_with_the_servers_reason(comfy: FakeComfyUI) -> None:
    comfy.modes.add("error")
    out = _invoke("txt2img", **TXT, seed=12)
    assert out["code"] == BACKEND_FAILED and "CUDA out of memory" in out["reason"]


def test_g4_a_workflow_the_server_refuses_is_an_error_not_an_empty_answer(comfy: FakeComfyUI) -> None:
    comfy.modes.add("refuse")
    out = _invoke("txt2img", **TXT, seed=13)
    assert out["code"] == BACKEND_FAILED and "400" in out["reason"]


def test_g4_still_running_past_the_deadline_is_pending_with_its_id(comfy: FakeComfyUI) -> None:
    comfy.pending_polls = 10_000
    out = _invoke("txt2img", **TXT, seed=14, timeout=1)
    assert out["code"] == PENDING and out["prompt_id"]


def test_g4_without_pillow_nothing_is_sent(comfy: FakeComfyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(block, "pillow", lambda: None)
    assert _invoke("txt2img", **TXT)["code"] == EXTRA_MISSING and comfy.requests == []


def test_a_server_that_is_not_running_is_the_honest_refusal_with_install_steps(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(block.URL_ENV, f"http://127.0.0.1:{_free_port()}")
    for op, params in (("txt2img", TXT), ("get_status", {}), ("list_models", {}), ("capabilities", {})):
        out = _invoke(op, **params)
        assert out["completion_state"] == "blocked-escalated" and out["code"] == OUTSIDE_MISSING, op
        assert "G6 never installs it" in out["reason"] and "python main.py" in out["reason"]
        # the whole message, never cut at its first colon, and its code only once (in "code", not in the text)
        assert "ComfyUI" in out["reason"] and "is not ready" in out["reason"] and "[G6_E_" not in out["reason"], op
    assert "nothing answered" in image_backends.get("comfyui").available()


# -- G9: only loopback, no redirect followed, an error status is an error ---------------------------------------------

def test_g9_a_redirect_from_the_server_is_refused_never_followed(comfy: FakeComfyUI) -> None:
    comfy.modes.add("redirect_view")
    out = _invoke("txt2img", **TXT, seed=15)
    assert out["code"] == NO_OUTPUT and "302" in out["reason"] and "redirect" in out["reason"]


def test_g9_the_operators_address_cannot_point_off_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(block.URL_ENV, "http://10.0.0.5:8188")
    out = _invoke("get_status")
    assert out["code"] == "G6_E_OUTSIDE_SERVICE_NOT_LOCAL"


# -- G5, G6, G7, G8: sensitive ops, no key or model, no stand-ins, licences ------------------------------------------

def test_g5_every_op_that_talks_to_comfyui_is_sensitive_and_refused_over_mcp_unless_allowed(
        comfy: FakeComfyUI) -> None:
    assert set(package.__g6_sensitive_ops__) == set(_invoke("ops")["ops"])
    for op in package.__g6_sensitive_ops__:
        denied = invoke_reply("adapt_comfyui", op, json.dumps(TXT), allow_sensitive=frozenset())
        assert not denied["ok"] and denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED", op
    assert comfy.requests == []
    allowed = invoke_reply("adapt_comfyui", "txt2img", json.dumps({**TXT, "seed": 16}),
                           allow_sensitive=frozenset({"adapt_comfyui"}))
    assert allowed["ok"] and allowed["completion_state"] == "qualified-draft" and check_envelope(allowed) == []
    for op in ("ops", "help"):
        assert invoke_reply("adapt_comfyui", op, "{}", allow_sensitive=frozenset())["ok"]


def test_g6_g7_g8_no_key_no_model_no_stand_in_and_nothing_new_to_install() -> None:
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("api_key", "getenv", "litellm", "openrouter", "anthropic", "openai", "mock", "fake", "simulat",
                     "experimental", "all rights reserved", "httpx", "requests", "urllib.request", "socket"):
            assert word not in text, (path.name, word)
        if path.suffix == ".py":
            assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name
    assert package.__g6_extras__ == {"image": ("PIL",)}
    assert package.__g6_outside__ == (block.COMFYUI,) and block.COMFYUI.url == "http://127.0.0.1:8188"
    assert block.COMFYUI.env == "G6_COMFYUI_URL" and not block.COMFYUI.paid and not block.COMFYUI.online


# -- D2, D5, D6: failure modes, the family's backend registry, MCP -----------------------------------------------------

def test_d2_it_declares_the_failure_mode_it_catches_and_left_planned() -> None:
    assert package.__g6_mitigates__ == ("visual_check_failed",) and package.__g6_introduces__ == ()
    assert "g6.media.adapt_comfyui" not in FailureModeRegistry.planned()


def test_d5_the_comfyui_backend_joins_the_family_registry_and_answers_through_it(comfy: FakeComfyUI) -> None:
    assert declares_backends("g6.media.adapt_comfyui") == ("comfyui",)
    backend = image_backends.get("comfyui")
    assert (backend.local, backend.network, backend.paid) == (True, False, False) and backend.available() == ""
    made = image_backends.generate("comfyui", ImageRequest(width=64, height=64, seed=17, prompt="a red kite",
                                                           options={"model": CHECKPOINT, "steps": 2}))
    assert made.completion_state == "qualified-draft" and made.operation == "generate"
    assert made.artifact.measured["width"] == 64
    assert image_backends.generate("comfyui", ImageRequest(options={"model": CHECKPOINT})).code == INPUT_INVALID
    assert image_backends.generate("comfyui", ImageRequest(prompt="x", options={"lora": "y"})).code == INPUT_INVALID


_FOUND = """
import json
from g6.media._types import image_backends
print(json.dumps(image_backends.names()))
"""


def test_d5_the_family_registry_finds_the_backends_without_importing_their_packages(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, "-c", _FOUND], capture_output=True, text=True, timeout=120,
                          env={"G6_HOME": str(tmp_path), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr
    assert {"comfyui", "procedural"} <= set(json.loads(done.stdout.strip().splitlines()[-1]))


def test_d6_an_agent_finds_it_describes_it_and_invokes_it_over_mcp(comfy: FakeComfyUI) -> None:
    assert "adapt_comfyui" in [r["component"] for r in discovery.find("generate images with a ComfyUI server")]
    info = describe_reply("adapt_comfyui", allow_sensitive=frozenset({"adapt_comfyui"}))
    assert info["family"] == "media" and info["package"] == "g6.media.adapt_comfyui" and info["skill"]
    assert info["outside"][0]["name"] == "ComfyUI" and info["sensitive_ops_allowed_here"] == info["sensitive_ops"]
    reply = invoke_reply("adapt_comfyui", "get_status", '{"request_id": "q1", "run_id": "u1"}',
                         allow_sensitive=frozenset({"adapt_comfyui.get_status"}))
    assert reply["ok"] and reply["result"]["queue_pending"] == 1 and check_envelope(reply["result"]) == []
    caps = _invoke("capabilities")
    assert caps["workflows"]["txt2img"]["ready"] and caps["checkpoints"] == [CHECKPOINT, "sdxl-test.safetensors"]
    assert caps["upscale_models"] == ["2x-test.pth"]


def test_d4_the_block_is_typed_and_imports_no_other_member() -> None:
    assert get_registry().get_class("adapt_comfyui") is block.AdaptComfyUIBlock
    assert block.ComfyUIInput.model_config["extra"] == "forbid"
    assert len(_invoke("help")["help"]) == len(_invoke("ops")["ops"]) == 9
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("g6.media."):
                assert node.module.split(".")[2] in ("_types", "_inspect", "adapt_comfyui"), node.module


# -- D9: without Pillow, nothing is asked of the server ----------------------------------------------------------------

_WITHOUT = """
import json, sys
sys.modules["PIL"] = None
from g6.core.registry import get_registry
reg = get_registry()
out = {op: reg.invoke("adapt_comfyui", op, params).unwrap() for op, params in (
    ("txt2img", {"prompt": "x", "model": "m"}), ("ops", {}), ("help", {}))}
print(json.dumps({op: [v.get("completion_state"), v.get("code", "")] for op, v in out.items()}))
"""


def test_d9_without_pillow_a_picture_op_says_it_is_not_installed(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          env={"G6_HOME": str(tmp_path), "G6_COMFYUI_URL": f"http://127.0.0.1:{_free_port()}",
                               "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["txt2img"] == ["blocked-escalated", EXTRA_MISSING] and found["ops"][0] is None


# -- the graphs G6 builds --------------------------------------------------------------------------------------------

def test_g6s_own_graphs_pass_the_servers_checks_and_carry_their_seed() -> None:
    common = {"prompt": "x", "negative": "", "model": CHECKPOINT, "width": 64, "height": 64, "steps": 2, "cfg": 7.0,
              "sampler": "euler", "scheduler": "normal", "seed": 42}
    graphs = {"txt2img": workflows.txt2img(**common, batch=1),
              "img2img": workflows.img2img(**common, image="a.png [temp]", denoise=0.5),
              "inpaint": workflows.inpaint(**common, image="a.png [temp]", mask="m.png [temp]", denoise=1.0),
              "upscale": workflows.upscale(image="a.png [temp]", model="2x-test.pth")}
    for op, graph in graphs.items():
        assert workflows.problems(graph, OBJECT_INFO, choices=True) == [], op
        assert {n["class_type"] for n in graph.values()} == set(workflows.NEEDS[op]), op
        assert set(workflows.NEEDS[op]) <= workflows.CORE_NODES, op
    assert not {"SaveImage", "LoadImageFromUrl", "StabilityStableImageUltraNode"} & workflows.CORE_NODES
    assert workflows.seed_of(graphs["txt2img"]) == 42 and workflows.seed_of(graphs["upscale"]) is None
    assert workflows.options(["COMBO", {"options": ["a"]}]) == ["a"] and workflows.options(["INT", {}]) is None
    assert workflows.problems(graphs["txt2img"], {**OBJECT_INFO, "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": _combo("other.safetensors")}}, "output": ["MODEL", "CLIP", "VAE"]}},
        choices=True)
