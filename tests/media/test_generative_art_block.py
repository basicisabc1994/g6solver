# SPDX-License-Identifier: Apache-2.0
"""adapt_generative_art's block: every file it makes is inspected before it is returned, a degenerate one is refused
(D1, ADAPT_GENERATIVE_ART-01 and -05), nothing it says is ``verified``, a caller's claims and code are refused
(ADAPT_GENERATIVE_ART-03), files go only into the media folder under a plain name (ADAPT_GENERATIVE_ART-04), errors
tighten, it reaches no network and no model, it is reachable over MCP, it joins the family's backend registry, and
without its extra every op that draws says it is not installed."""
from __future__ import annotations

import ast
import importlib.metadata
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

import g6.media.adapt_generative_art as package  # noqa: E402
from g6.core import discovery  # noqa: E402
from g6.core.failure_modes import FailureModeRegistry  # noqa: E402
from g6.core.registry import get_registry  # noqa: E402
from g6.invariants import check_envelope  # noqa: E402
from g6.mcp import describe_reply, invoke_reply  # noqa: E402
from g6.media import _inspect  # noqa: E402
from g6.media._types import (  # noqa: E402
    BACKEND_FAILED,
    BLANK,
    EXISTS,
    EXTRA_MISSING,
    INPUT_INVALID,
    ImageRequest,
    image_backends,
    media_root,
)
from g6.media.adapt_generative_art import AdaptGenerativeArtBlock, block, techniques  # noqa: E402

SRC = Path(package.__file__).parent


def _invoke(op: str, **params):
    result = get_registry().invoke("adapt_generative_art", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_generative_art", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


# -- D1: a degenerate picture is refused, never returned -------------------------------------------------------------

DEGENERATE = [
    ("reaction_diffusion", {"feed": 0.0, "kill": 0.1, "steps": 400}),     # the pattern dies out: one flat colour
    ("cellular_automaton", {"rule": 0}),                                   # one live cell, then nothing
    ("domain_colouring", {"expression": "exp(0 * z)"}),                    # a constant function
    ("attractor", {"kind": "clifford", "a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0, "points": 5000}),  # a fixed point
]


@pytest.mark.parametrize(("technique", "params"), DEGENERATE)
def test_d1_a_degenerate_picture_is_refused_and_its_file_removed(technique: str, params: dict) -> None:
    before = _media_files()
    out = _invoke("generate", technique=technique, params=params, width=96, height=64, seed=5, name=f"d1-{technique}")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == BLANK, out.get("reason")
    assert out["artifact"] is None and check_envelope(out) == []
    assert f"d1-{technique}.png" not in _media_files() - before
    inspection = out["evidence"][0]
    assert inspection["kind"] == "media_inspection" and not inspection["passed"]
    assert [c["name"] for c in inspection["checks"] if not c["passed"]] == ["not_blank"]


def test_d1_a_flat_heightmap_is_refused_as_a_mesh() -> None:
    out = _invoke("mesh", technique="domain_colouring", params={"expression": "0 * z + 1", "colour_by": "modulus"},
                  width=16, height=16, seed=1)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == BLANK and out["artifact"] is None


# -- ADAPT_GENERATIVE_ART-01: nothing is verified; a result is qualified-draft at best ---------------------------------

def test_g1_no_result_is_ever_verified_and_the_package_never_assigns_it() -> None:
    good = _invoke("generate", technique="noise", params={"scale": 16}, width=48, height=48, seed=3)
    assert good["completion_state"] == "qualified-draft" and good["warning_card"]["code"] == "G6_E_MEDIA_INSPECTED"
    assert good["evidence"][0]["inspector"].startswith("g6.media image inspector")
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""["']verified["']""", text), path.name


def test_g1_over_mcp_a_wrong_picture_is_blocked_and_a_good_one_a_draft() -> None:
    good = invoke_reply("adapt_generative_art", "generate", json.dumps(
        {"technique": "voronoi", "params": {"cells": 9}, "width": 40, "height": 40, "seed": 1}))
    assert good["ok"] and good["completion_state"] == "qualified-draft" and check_envelope(good) == []
    bad = invoke_reply("adapt_generative_art", "generate", json.dumps(
        {"technique": "cellular_automaton", "params": {"rule": 0}, "width": 96, "height": 64, "seed": 1}))
    assert bad["completion_state"] == "blocked-escalated" and bad["warning_card"]["code"] == BLANK


# -- ADAPT_GENERATIVE_ART-03 and -05: no caller code, no custom technique, no failure drawn as a picture ----------------

@pytest.mark.parametrize("field", [{"custom_code": "function setup(){}"}, {"backend": "p5js"},
                                   {"headless_enabled": True}, {"output_path": "C:/x.png"},
                                   {"completion_state": "verified"}, {"approved": True}, {"reviewer": "Dr Who"}])
def test_g3_a_callers_code_claims_and_approvals_are_refused_not_honoured(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("generate", technique="noise", width=32, height=32, **field)


def test_the_custom_technique_and_unknown_parameters_are_refused() -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("generate", technique="custom", width=32, height=32)
    assert "G6_E_INPUT_VALIDATION" in _refused("generate", technique="noise", params={"made_up": 1})
    assert "G6_E_INPUT_VALIDATION" in _refused("generate", technique="domain_colouring",
                                               params={"expression": "().__class__"})


def test_a_bad_expression_is_refused_never_rendered_as_z() -> None:
    error = _refused("generate", technique="domain_colouring", params={"expression": "z.real"}, width=32, height=32)
    assert "not allowed" in error and "G6_E_INPUT_VALIDATION" in error


# -- ADAPT_GENERATIVE_ART-04: the caller names the file, never the folder ----------------------------------------------

@pytest.mark.parametrize("name", ["../escape", "..\\escape", "C:/Windows/x", "/etc/x", "a/b", "con"])
def test_a_name_with_a_folder_or_drive_is_refused(name: str) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("generate", technique="noise", name=name, width=32, height=32)


def test_files_land_in_the_media_folder_and_a_different_file_is_never_replaced_unasked() -> None:
    first = _invoke("generate", technique="noise", params={"scale": 8}, width=32, height=32, seed=1, name="keep-me")
    assert first["artifact"]["path"] == str(media_root() / "keep-me.png") and first["written"] == "written"
    again = _invoke("generate", technique="noise", params={"scale": 8}, width=32, height=32, seed=1, name="keep-me")
    assert again["written"] == "unchanged"
    other = _invoke("generate", technique="noise", params={"scale": 8}, width=32, height=32, seed=2, name="keep-me")
    assert other["completion_state"] == "blocked-escalated" and other["code"] == EXISTS
    assert Path(first["artifact"]["path"]).read_bytes() and \
        _invoke("generate", technique="noise", params={"scale": 8}, width=32, height=32, seed=2, name="keep-me",
                overwrite=True)["written"] == "replaced"


# -- G4: errors tighten ------------------------------------------------------------------------------------------------

def test_g4_a_technique_that_raises_is_refused_and_leaves_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise FloatingPointError("overflow in the middle")

    monkeypatch.setattr(techniques, "noise", boom)
    before = _media_files()
    out = _invoke("generate", technique="noise", width=32, height=32, seed=1, name="g4-boom")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == BACKEND_FAILED
    assert "overflow in the middle" in out["reason"] and _media_files() == before


def test_g4_without_the_inspector_nothing_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_inspect, "pillow", lambda: None)
    out = _invoke("generate", technique="noise", width=32, height=32, seed=1, name="g4-uninspected")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == EXTRA_MISSING
    assert "g4-uninspected.png" not in _media_files()


def test_too_much_work_is_refused_before_it_starts() -> None:
    error = _refused("generate", technique="reaction_diffusion", params={"steps": 20000}, width=2048, height=2048)
    assert "too much work" in error


# -- G5, G6, G7, G8: no network, no program, no model, no stand-ins, permissive licences ------------------------------

def test_g5_it_has_no_sensitive_ops_because_it_opens_no_socket_and_starts_no_program(
        monkeypatch: pytest.MonkeyPatch) -> None:
    assert package.__g6_sensitive_ops__ == () and package.__g6_outside__ == ()

    def refused(*args, **kwargs):
        raise AssertionError("adapt_generative_art reached outside the machine or started a program")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(subprocess, "Popen", refused)
    for technique in ("lsystem", "terrain", "evolved"):
        out = _invoke("generate", technique=technique, width=32, height=32, seed=4,
                      params={"generations": 2, "population": 4} if technique == "evolved" else {})
        assert out["completion_state"] == "qualified-draft", out.get("reason")


def test_g6_g7_the_package_reads_no_key_calls_no_model_and_returns_no_stand_in() -> None:
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("api_key", "getenv", "environ", "litellm", "openrouter", "anthropic", "openai", "mock", "fake",
                     "simulat", "experimental", "all rights reserved", "puppeteer", "node "):
            assert word not in text, (path.name, word)
        if path.suffix == ".py":
            assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_g8_the_extras_dependencies_are_permissively_licensed() -> None:
    assert package.__g6_extras__ == {"procedural": ("PIL", "numpy")}
    for dist in ("pillow", "numpy"):
        meta = importlib.metadata.metadata(dist)
        said = " ".join(filter(None, [meta.get("License-Expression"), meta.get("License"),
                                      *meta.get_all("Classifier", [])])).upper()
        assert said and not re.search(r"\b(A?GPL|SSPL|NON-?COMMERCIAL)\b", said), (dist, said)


# -- D2, D5, D6: declared failure modes, the family's backend registry, reachable over MCP -----------------------------

def test_d2_it_declares_the_failure_mode_it_catches_and_moved_out_of_planned() -> None:
    assert package.__g6_mitigates__ == ("visual_check_failed",) and package.__g6_introduces__ == ()
    assert FailureModeRegistry.is_registered("visual_check_failed")
    assert "g6.media.adapt_generative_art" not in FailureModeRegistry.planned()


def test_d5_the_procedural_backend_is_registered_and_answers_through_the_family_registry() -> None:
    backend = image_backends.get("procedural")
    assert backend is not None and (backend.local, backend.network, backend.paid) == (True, False, False)
    assert backend.available() == ""
    out = image_backends.generate("procedural", ImageRequest(width=40, height=30, seed=9, request_id="r",
                                                             options={"technique": "voronoi",
                                                                      "params": {"cells": 8}}))
    assert out.completion_state == "qualified-draft" and out.artifact.measured["width"] == 40
    told = image_backends.generate("procedural", ImageRequest(width=40, height=30, prompt="a red fox at dawn"))
    assert told.completion_state == "blocked-escalated" and told.code == INPUT_INVALID and "prompt" in told.reason
    clash = image_backends.generate("procedural", ImageRequest(options={"technique": "noise", "width": 9}))
    assert clash.code == INPUT_INVALID


def test_d6_an_agent_finds_it_by_its_failure_mode_describes_it_and_invokes_it_over_mcp() -> None:
    title = FailureModeRegistry.title("visual_check_failed")
    assert "adapt_generative_art" in [r["component"] for r in discovery.find(title, limit=5)]
    assert "adapt_generative_art" in [r["component"] for r in discovery.find("make procedural generative art")]
    info = describe_reply("adapt_generative_art")
    assert info["family"] == "media" and info["package"] == "g6.media.adapt_generative_art"
    assert sorted(info["ops"]) == ["generate", "mesh", "techniques"] and info["skill"]
    assert info["sensitive_ops"] == [] and info["extras"][0]["extra"] == "procedural"
    for op, params in (("techniques", {}), ("generate", {"technique": "fractal", "width": 40, "height": 40,
                                                         "seed": 2}),
                       ("mesh", {"technique": "terrain", "width": 12, "height": 10, "seed": 2})):
        reply = invoke_reply("adapt_generative_art", op, json.dumps({**params, "request_id": "q1", "run_id": "u1"}))
        assert reply["ok"] and reply["completion_state"] == "qualified-draft", reply
        assert check_envelope(reply) == [] and check_envelope(reply["result"]) == []
        assert (reply["result"]["request_id"], reply["result"]["run_id"]) == ("q1", "u1")


def test_d4_the_block_is_typed_and_lists_its_ops() -> None:
    assert get_registry().get_class("adapt_generative_art") is AdaptGenerativeArtBlock
    assert block.GenerativeArtInput.model_config["extra"] == "forbid"
    assert _invoke("ops")["ops"] == ["generate", "mesh", "techniques"]
    assert len(_invoke("help")["help"]) == 3
    tree = ast.parse((SRC / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("g6.media."):
            assert node.module.split(".")[2] in ("_types", "_inspect", "_scene", "adapt_generative_art"), node.module


# -- D9: without the extra, an honest "not installed" ------------------------------------------------------------------

_WITHOUT = """
import json, sys
sys.modules["numpy"] = None
from g6.core.registry import get_registry
reg = get_registry()
out = {op: reg.invoke("adapt_generative_art", op, params).unwrap() for op, params in (
    ("generate", {"technique": "noise", "width": 32, "height": 32}),
    ("mesh", {"technique": "terrain", "width": 16, "height": 16}),
    ("techniques", {}), ("ops", {}), ("help", {}))}
print(json.dumps({op: [v.get("completion_state"), v.get("code", "")] for op, v in out.items()}))
"""


def test_d9_without_numpy_every_drawing_op_says_it_is_not_installed(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          env={"G6_HOME": str(tmp_path), "PATH": "", "SYSTEMROOT": __import__("os").environ.get(
                              "SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["generate"] == ["blocked-escalated", EXTRA_MISSING] == found["mesh"]
    assert found["techniques"] == ["qualified-draft", ""] and found["ops"][0] is None
