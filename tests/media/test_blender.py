# SPDX-License-Identifier: Apache-2.0
"""adapt_blender: Blender runs G6's own script and never a caller's (ADAPT_BLENDER-01), as a bounded child process with
a clean environment and a deadline; every render is a PNG inspected from disk (it decodes, is the size asked for and is
not blank) and every export a mesh inspected, whatever Blender's exit code said (D1, ADAPT_BLENDER-02); what was asked
of the scene is read back and compared, and a difference is a refusal that leaves the session as it was (-03); nothing
is verified. A stand-in Blender (a G6-owned script, run through the same child-process path) covers what must hold
without Blender; the tests that need the real one skip without it."""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import struct
from pathlib import Path

import pytest
from media_kit import outside_link, put, stand_in

import g6.media.adapt_blender as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.media._types import (
    BACKEND_FAILED,
    BLANK,
    INPUT_INVALID,
    NO_OUTPUT,
    OUTSIDE_MISSING,
    READBACK_MISMATCH,
    SIZE_MISMATCH,
    media_root,
)
from g6.media.adapt_blender import block, inspect3d, runner

pytest.importorskip("PIL.Image")
SRC = Path(package.__file__).parent
REAL = pytest.mark.skipif(not (shutil.which("blender")), reason="Blender is not installed here")
STAND_IN = (Path(__file__).parent / "blender_stand_in.txt").read_text(encoding="utf-8")


class Blender:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def behave(self, **mode: object) -> None:
        (self.folder / "mode.json").write_text(json.dumps(mode), encoding="utf-8")

    def calls(self) -> list[dict]:
        log = self.folder / "calls.jsonl"
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


@pytest.fixture
def blender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Blender:
    monkeypatch.setenv("G6_BLENDER", str(stand_in(tmp_path, "blender", STAND_IN)))
    found = Blender(tmp_path)
    found.behave()
    return found


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_blender", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_blender", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


def _session() -> str:
    out = _invoke("session_new")
    assert out["completion_state"] == "qualified-draft" and out["session"].startswith("s-")
    return out["session"]


def _with_cube(**colour) -> str:
    session = _session()
    out = _invoke("create_primitive", session=session, object="Box", primitive="cube", location=[1, 2, 3], **colour)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    return session


# -- D1: a render or an export Blender called done but that is blank, the wrong size or missing is refused -------------

def test_d1_a_blank_render_is_refused_and_removed(blender: Blender) -> None:
    session = _with_cube()
    blender.behave(blank=True)
    before = _media_files()
    out = _invoke("render", session=session, width=64, height=48, name="d1-blank")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == BLANK, out.get("reason")
    assert _media_files() == before and check_envelope(out) == []


@pytest.mark.parametrize(("mode", "code"), [("wrong_size", SIZE_MISMATCH), ("no_output", NO_OUTPUT)])
def test_d1_a_render_of_the_wrong_size_or_none_at_all_is_refused(blender: Blender, mode: str, code: str) -> None:
    session = _with_cube()
    blender.behave(**{mode: True})
    assert _invoke("viewport_preview", session=session, width=64, height=48)["code"] == code


def test_d1_an_export_blender_wrote_nothing_for_is_refused(blender: Blender) -> None:
    session = _with_cube()
    blender.behave(no_output=True)
    assert _invoke("export", session=session, format="obj", name="d1-none")["code"] == NO_OUTPUT


def test_a_good_render_is_an_inspected_png_and_a_camera_placed_is_said(blender: Blender) -> None:
    session = _with_cube()
    out = _invoke("render", session=session, width=64, height=48, name="box-render", overwrite=True)
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_INSPECTED"
    assert out["artifact"]["measured"]["width"] == 64 and out["camera_placed"] is True


@pytest.mark.parametrize(("fmt", "mime"), [("obj", "model/obj"), ("stl", "model/stl"), ("glb", "model/gltf-binary"),
                                           ("fbx", "application/octet-stream")])
def test_each_export_is_inspected(blender: Blender, fmt: str, mime: str) -> None:
    out = _invoke("export", session=_with_cube(), format=fmt, name=f"box-{fmt}", overwrite=True)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    assert out["artifact"]["mime"] == mime and out["artifact"]["kind"] == "mesh"


def test_an_exports_card_says_how_far_its_format_was_read_and_an_fbx_card_never_says_it_decodes(
        blender: Blender) -> None:
    fbx = _invoke("export", session=_with_cube(), format="fbx", name="box-card", overwrite=True)
    said = json.dumps(fbx["warning_card"]).lower()
    assert fbx["completion_state"] == "qualified-draft" and "decode" not in said and "header" in said, said
    assert next(e for e in fbx["evidence"] if e["kind"] == "mesh_read")["format"] == "fbx"
    glb = _invoke("export", session=_with_cube(), format="glb", name="box-card", overwrite=True)
    assert "decode" not in json.dumps(glb["warning_card"]).lower()
    assert "binary chunk was not read" in glb["warning_card"]["message"]


# -- ADAPT_BLENDER-03: what was asked is read back; a difference refuses and leaves the session as it was --------------

def test_p2_a_transform_or_colour_that_does_not_read_back_is_refused_and_the_session_kept(blender: Blender) -> None:
    session = _with_cube(colour=[0.8, 0.1, 0.1])
    saved = runner.session_file(session).read_bytes()
    blender.behave(drift=True)
    moved = _invoke("transform_object", session=session, object="Box", location=[4, 4, 4])
    assert moved["code"] == READBACK_MISMATCH and "location" in moved["reason"]
    assert runner.session_file(session).read_bytes() == saved
    painted = _invoke("set_material", session=session, object="Box", colour=[0.1, 0.8, 0.1])
    assert painted["code"] == READBACK_MISMATCH
    blender.behave()
    good = _invoke("set_material", session=session, object="Box", colour=[0.1, 0.8, 0.1])
    assert good["completion_state"] == "qualified-draft"
    assert [c["name"] for c in good["evidence"][0]["checks"]] == ["material_confirmed"]


def test_a_scene_spec_is_built_and_every_object_read_back(blender: Blender) -> None:
    spec = {"objects": [{"name": "Base", "primitive": "plane", "transform": {"scale": [5, 5, 1]}},
                        {"name": "Ball", "primitive": "sphere", "parent": "Base", "colour": [0.2, 0.4, 0.9],
                         "transform": {"location": [0, 0, 1]}}],
            "camera": {"location": [6, -6, 4], "look_at": [0, 0, 0], "lens_mm": 35},
            "lights": [{"name": "Key", "kind": "sun", "location": [3, -3, 8], "energy": 3}]}
    out = _invoke("build_scene", session=_session(), scene=spec)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    found = {o["name"]: o for o in out["scene"]["objects"]}
    assert found["Ball"]["parent"] == "Base" and "scene_matches_spec" in json.dumps(out["evidence"])


def test_list_objects_and_capabilities_report_what_blender_says(blender: Blender) -> None:
    session = _with_cube()
    assert [o["name"] for o in _invoke("list_objects", session=session)["scene"]["objects"]] == ["Box"]
    caps = _invoke("capabilities")
    assert caps["warning_card"]["code"] == "G6_E_MEDIA_REPORTED" and "CYCLES" in caps["engines"]
    assert caps["version"].startswith("Blender 4.2.0")


def test_import_file_reads_a_mesh_from_the_media_folder(blender: Blender) -> None:
    session = _with_cube()
    (media_root() / "terrain.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 1\nf 1 2 3\n", encoding="utf-8")
    out = _invoke("import_file", session=session, source="terrain.obj")
    assert out["completion_state"] == "qualified-draft" and "terrain" in [o["name"] for o in out["scene"]["objects"]]


def _glb(gltf: dict) -> bytes:
    data = json.dumps(gltf).encode()
    data += b" " * (-len(data) % 4)
    return b"glTF" + struct.pack("<II", 2, 20 + len(data)) + struct.pack("<I", len(data)) + b"JSON" + data


_INLINE = "data:application/octet-stream;base64,AAAAAA=="
#: Mesh files that name other files, which Blender's importers would open wherever they are.
NAMING = {
    "up.gltf": json.dumps({"asset": {"version": "2.0"},
                           "buffers": [{"uri": "../../secret.bin", "byteLength": 4}]}).encode(),
    "absolute.gltf": json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": _INLINE, "byteLength": 4}],
                                 "images": [{"uri": "D:/shared/private.png"},
                                            {"uri": "file:///etc/passwd"}]}).encode(),
    "material.obj": b"mtllib ../../outside.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 1\nf 1 2 3\n",
    "outside.glb": _glb({"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4},
                                                                {"uri": "../outside.bin", "byteLength": 4}]}),
}


@pytest.mark.parametrize("name", sorted(NAMING))
def test_p1_a_mesh_that_names_another_file_is_refused_before_blender_starts(blender: Blender, name: str) -> None:
    session = _session()
    put(name, NAMING[name])
    imported = _invoke("import_file", session=session, source=name)
    assert imported["code"] == INPUT_INVALID, imported.get("reason")
    built = _invoke("build_scene", session=session, scene={"objects": [{"name": "Model", "mesh": name}]})
    assert built["code"] == INPUT_INVALID and blender.calls() == []


def test_p1_an_fbx_is_not_imported_because_its_texture_paths_cannot_be_read_before_blender_opens_them(
        blender: Blender) -> None:
    put("model.fbx", b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400) + b"\0" * 200)
    assert "G6_E_INPUT_VALIDATION" in _refused("import_file", session=_session(), source="model.fbx")
    assert blender.calls() == []


def test_a_self_contained_gltf_is_imported(blender: Blender) -> None:
    put("inline.gltf", json.dumps({"asset": {"version": "2.0"},
                                   "buffers": [{"uri": _INLINE, "byteLength": 4}]}).encode())
    out = _invoke("import_file", session=_session(), source="inline.gltf")
    assert out["completion_state"] == "qualified-draft", out.get("reason")


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_link_to_a_mesh_outside_the_media_folder_is_refused_before_blender_runs(blender: Blender, tmp_path: Path,
                                                                                  kind: str) -> None:
    session = _session()
    outside_link(tmp_path, "linked.obj", b"v 0 0 0\nv 1 0 0\nv 0 1 1\nf 1 2 3\n", kind)
    imported = _invoke("import_file", session=session, source="linked.obj")
    assert imported["code"] == INPUT_INVALID and "link" in imported["reason"]
    built = _invoke("build_scene", session=session, scene={"objects": [{"name": "Linked", "mesh": "linked.obj"}]})
    assert built["code"] == INPUT_INVALID and blender.calls() == []


# -- ADAPT_BLENDER-01 and the literal discipline: only G6's script runs; a caller's text never becomes code -----------

HOSTILE = ("x')\nimport os\nos.system('echo PWNED')\n#", "x\"); import os; #", "{__import__('os')}",
           "1); import os; PWNED = (1", "x\r\nimport os\r\n", "../../evil", "Box; rm -rf /", "-P evil.py")


@pytest.mark.parametrize("payload", HOSTILE)
@pytest.mark.parametrize(("op", "field", "extra"), [
    ("create_primitive", "object", {"primitive": "cube"}), ("transform_object", "object", {}),
    ("set_material", "object", {"colour": [1, 0, 0]}), ("add_light", "object", {}),
    ("export", "name", {"format": "obj"}), ("import_file", "source", {}),
    ("render", "engine", {}), ("create_primitive", "primitive", {"object": "Box"})])
def test_p1_a_hostile_string_in_any_field_is_refused_before_blender_runs(blender: Blender, payload: str, op: str,
                                                                         field: str, extra: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused(op, session="s-000000000000", **{field: payload, **extra})
    assert blender.calls() == []


def test_p1_a_hostile_name_inside_a_scene_spec_is_refused(blender: Blender) -> None:
    spec = {"objects": [{"name": "x')\nimport os", "primitive": "cube"}]}
    assert "G6_E_INPUT_VALIDATION" in _refused("build_scene", session="s-000000000000", scene=spec)


@pytest.mark.parametrize("field", [{"script": "import os"}, {"code": "import os"}, {"python": "x.py"},
                                   {"driver_expression": "__import__('os')"}, {"approved": True},
                                   {"completion_state": "verified"}, {"output_path": "D:/x.png"}, {"tenant": "a"}])
def test_p1_run_script_is_gone_and_callers_code_claims_and_paths_are_refused(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("render", session="s-000000000000", **field)
    assert "G6_E_INPUT_VALIDATION" in _refused("run_script", session="s-000000000000")


def test_p1_blender_runs_only_g6s_own_script_with_its_safety_flags_in_a_clean_environment(
        blender: Blender, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "not-for-blender")
    _with_cube()
    (call,) = blender.calls()
    args = call["args"]
    assert args[:1] == ["--background"] and {"--factory-startup", "--disable-autoexec", "--offline-mode"} <= set(args)
    assert args[args.index("--python-exit-code") + 1] == "1"
    script = Path(args[args.index("--python") + 1])
    assert hashlib.sha256(script.read_bytes()).hexdigest() == hashlib.sha256(
        (SRC / "inside_blender.txt").read_bytes()).hexdigest()
    assert call["job"]["params"]["object"] == "Box" and set(call["job"]) == {"op", "params", "blend", "save", "out",
                                                                             "result"}


def test_p1_the_script_g6_runs_reads_its_job_as_data_and_runs_nothing_it_is_given() -> None:
    tree = ast.parse((SRC / "inside_blender.txt").read_text(encoding="utf-8"))
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not calls & {"exec", "eval", "compile", "__import__", "os.system", "subprocess.run", "runpy.run_path"}
    assert "json.load" in calls and not [n for n in ast.walk(tree) if isinstance(n, ast.Import)
                                         and any(a.name in ("os", "subprocess", "socket") for a in n.names)]


# -- G1, G4, G5-G8, D2, D4, D6 ---------------------------------------------------------------------------------------

def test_g1_nothing_is_verified_and_the_package_never_assigns_it(blender: Blender) -> None:
    session = _with_cube(colour=[0.5, 0.5, 0.5])
    out = _invoke("transform_object", session=session, object="Box", scale=[2, 2, 2])
    assert out["completion_state"] == "qualified-draft"
    for path in SRC.rglob("*.py"):
        assert "\"verified\"" not in path.read_text(encoding="utf-8"), path.name


def test_g4_a_missing_or_failing_blender_and_an_unknown_session_are_refusals(blender: Blender, tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    blender.behave(fail=True)
    out = _invoke("create_primitive", session=session, object="Box", primitive="cube")
    assert out["code"] == BACKEND_FAILED and "stand-in failure" in out["reason"]
    assert _invoke("render", session="s-0123456789ab")["code"] == INPUT_INVALID
    monkeypatch.setenv("G6_BLENDER", str(tmp_path / "absent-blender.exe"))
    missing = _invoke("create_primitive", session=session, object="Box", primitive="cube")
    assert missing["code"] == OUTSIDE_MISSING and "G6 never installs it" in missing["reason"]


def test_sessions_live_under_g6s_state_and_are_deleted_whole(blender: Blender) -> None:
    session = _with_cube()
    folder = runner.session_file(session).parent
    assert folder.is_relative_to(runner.sessions_root()) and runner.sessions_root().name == "sessions"
    assert _invoke("session_delete", session=session)["deleted"] is True and not folder.exists()


def test_g5_every_op_that_runs_blender_is_sensitive_and_sessions_are_not() -> None:
    ops = set(_invoke("ops")["ops"])
    assert set(package.__g6_sensitive_ops__) == ops - {"session_new", "session_delete"}
    for op in package.__g6_sensitive_ops__:
        denied = invoke_reply("adapt_blender", op, json.dumps({"session": "s-000000000000"}),
                              allow_sensitive=frozenset())
        assert not denied["ok"] and denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED", op
    assert invoke_reply("adapt_blender", "session_new", "{}")["ok"]


def test_g6_g7_g8_no_key_no_model_no_stand_in_and_blender_only_as_a_program() -> None:
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md", ".txt"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("api_key", "getenv", "litellm", "openrouter", "mock", "fake", "simulat", "experimental",
                     "all rights reserved", "httpx", "polyhaven", "fastmcp"):
            assert word not in text, (path.name, word)
    assert package.__g6_extras__ == {"image": ("PIL",)} and [p.name for p in package.__g6_outside__] == ["Blender"]
    assert package.__g6_outside__[0].env == "G6_BLENDER" and "never bundles" in package.__g6_outside__[0].licence


def test_d2_it_declares_the_failure_mode_it_catches_and_left_planned() -> None:
    assert package.__g6_mitigates__ == ("visual_check_failed",) and package.__g6_introduces__ == ()
    assert "g6.media.adapt_blender" not in FailureModeRegistry.planned()


def test_d4_the_block_is_typed_and_imports_no_other_member() -> None:
    assert get_registry().get_class("adapt_blender") is block.AdaptBlenderBlock
    assert block.BlenderInput.model_config["extra"] == "forbid"
    assert len(_invoke("help")["help"]) == len(_invoke("ops")["ops"]) == 15
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("g6.media."):
                assert node.module.split(".")[2].startswith("_") or node.module.split(".")[2] == "adapt_blender"


def test_d6_an_agent_finds_it_describes_it_and_opens_a_session_over_mcp() -> None:
    assert "adapt_blender" in [r["component"] for r in discovery.find("render a 3D scene with Blender")]
    info = describe_reply("adapt_blender")
    assert info["family"] == "media" and info["skill"] and info["outside"][0]["name"] == "Blender"
    reply = invoke_reply("adapt_blender", "session_new", '{"request_id": "q1", "run_id": "u1"}')
    assert reply["ok"] and reply["result"]["request_id"] == "q1" and check_envelope(reply["result"]) == []


def test_the_mesh_inspectors_read_stl_glb_and_fbx_and_refuse_a_broken_one(tmp_path: Path) -> None:
    broken = tmp_path / "x.glb"
    broken.write_bytes(b"glTF" + b"\x02\x00\x00\x00" + b"\xff\xff\x00\x00")
    assert not inspect3d.inspect_glb(broken).passed
    empty = tmp_path / "x.stl"
    empty.write_bytes(b"\0" * 80 + b"\0\0\0\0")
    assert inspect3d.inspect_stl(empty).failure.code == NO_OUTPUT


# -- with the real Blender ---------------------------------------------------------------------------------------------

@REAL
def test_real_a_cube_is_made_lit_rendered_and_exported_each_checked() -> None:
    session = _session()
    spec = {"objects": [{"name": "Box", "primitive": "cube", "colour": [0.8, 0.2, 0.1],
                         "transform": {"location": [0, 0, 1]}}],
            "camera": {"location": [6, -6, 4], "look_at": [0, 0, 1], "lens_mm": 40},
            "lights": [{"name": "Key", "kind": "sun", "location": [3, -3, 8], "energy": 4}]}
    assert _invoke("build_scene", session=session, scene=spec)["completion_state"] == "qualified-draft"
    shot = _invoke("viewport_preview", session=session, width=160, height=120, name="real-preview", overwrite=True)
    assert shot["completion_state"] == "qualified-draft", shot.get("reason")
    mesh = _invoke("export", session=session, format="obj", name="real-box", overwrite=True)
    assert mesh["completion_state"] == "qualified-draft" and mesh["artifact"]["measured"]["vertices"] >= 8
