# SPDX-License-Identifier: Apache-2.0
"""g6.code.component_creator: the library's scaffolder. A spec read off a module as text or drafted by an injected
model caller, a scaffold in the library's shape whose ops refuse until written, and a static conformance check that
reuses g6.core.failure_modes and g6.core.skills. No model, no network: the caller is FakeLLM."""
from __future__ import annotations

import ast
import builtins
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.code.component_creator as package
import g6.sources.ctx_library_mapper as mapper
from g6.code import _roots
from g6.code.component_creator import ComponentCreatorBlock, ComponentCreatorInput, ComponentSpec
from g6.core.failure_modes import FailureModeRegistry
from g6.core.llm import FakeLLM, LLMError
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply

LIB = '''"""A tiny library."""
__all__ = ["get_total", "delete_item", "Store"]


def get_total(a: int, b: int = 0) -> int:
    return a + b


def delete_item(name: str) -> bool:
    return True


def _private() -> None:
    pass


class Store:
    def put(self, key): ...
'''
SPEC = {"name": "unit_tracker", "family": "code", "summary": "Track units of work.",
        "ops": [{"name": "count_units", "does": "count the units", "kind": "read", "params": ["owner"]},
                {"name": "remove_units", "does": "remove units", "kind": "delete", "sensitive": True}],
        "fields": [{"name": "owner", "type": "str", "required": True}],
        "mitigates": ["incomplete_component"], "introduces": []}


def roots(*folders: Path) -> _roots.Roots:
    return _roots.Roots(tuple(f.resolve() for f in folders), "test")


def run(op: str, *, caller=None, root: Path | None = None, **params) -> dict:
    block = ComponentCreatorBlock(caller=caller, roots=roots(root) if root else _roots.Roots((), "none"))
    return block.infer(ComponentCreatorInput(op=op, **params)).unwrap()


def copy_member(root: Path) -> Path:
    """ctx_library_mapper's package and its tests, copied under ``root`` in the library's layout."""
    src = Path(mapper.__file__).parent
    folder = root / "oss" / "src" / "g6" / "sources" / "ctx_library_mapper"
    shutil.copytree(src, folder, ignore=shutil.ignore_patterns("__pycache__"))
    tests = root / "oss" / "tests" / "sources"
    tests.mkdir(parents=True)
    test_file = Path(__file__).resolve().parents[1] / "sources" / "test_ctx_library_mapper.py"
    shutil.copy(test_file, tests / test_file.name)
    return folder


def link(at: Path, target: Path) -> None:
    """A folder link from ``at`` to ``target``: a symbolic link, or on Windows without that right a junction."""
    try:
        at.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        pass
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(at))
    except (ImportError, OSError):
        pytest.skip("this machine lets a test make neither a symbolic link nor a junction")


def problems(out: dict) -> list[str]:
    return [f"{p['check']}: {p['message']}" for p in out.get("problems", [])]


# --- D1: a component missing its tests or standard parts is caught ----------------------------------------------------

def test_d1_a_real_package_with_one_standard_part_missing_is_caught(tmp_path: Path) -> None:
    folder = copy_member(tmp_path)
    clean = run("check_conformance", root=tmp_path, path=str(folder))
    assert clean["static_clean"] is True and clean["completion_state"] == "qualified-draft", problems(clean)

    block_py = folder / "block.py"
    original = block_py.read_text(encoding="utf-8")
    block_py.write_text(original.replace('model_config = ConfigDict(extra="forbid")', "model_config = ConfigDict()"),
                        encoding="utf-8")
    loose = run("check_conformance", root=tmp_path, path=str(folder))
    assert loose["completion_state"] == "blocked-escalated"
    assert any("does not refuse extra fields" in p for p in problems(loose))

    block_py.write_text(original.replace('"sources", "ops", "help"]', '"sources", "rank", "ops", "help"]'),
                        encoding="utf-8")
    extra_op = problems(run("check_conformance", root=tmp_path, path=str(folder)))
    assert any("Ops list" in p for p in extra_op) and any("'rank' has no g6-example" in p for p in extra_op)
    block_py.write_text(original, encoding="utf-8")

    (tmp_path / "oss" / "tests" / "sources" / "test_ctx_library_mapper.py").unlink()
    untested = problems(run("check_conformance", root=tmp_path, path=str(folder)))
    assert untested == ["tests: no test file for the component was found beside it"]

    init = folder / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8").replace('("reinvented_method",)', "()"), encoding="utf-8")
    undeclared = problems(run("check_conformance", root=tmp_path, path=str(folder)))
    assert any("mitigates no failure mode" in p for p in undeclared)


def test_a_fresh_scaffold_is_reported_incomplete_until_it_is_written(tmp_path: Path) -> None:
    dest = tmp_path / "scaffold"
    out = run("render", root=tmp_path, spec=SPEC, dest=str(dest), dry_run=False)
    assert out["completion_state"] == "qualified-draft" and out["code"] == "G6_E_SCAFFOLD_WRITTEN"
    found = problems(run("check_conformance", root=tmp_path, path=str(dest / "unit_tracker")))
    assert any("still say TODO" in p for p in found)
    assert any("still the scaffold's placeholder" in p for p in found)
    assert not any(p.startswith(("block:", "declarations:", "failure_modes:", "headers:")) for p in found), found


# --- the archive's findings, each with a test where its path survives -------------------------------------------------

def _load_block(path: Path):
    """The rendered block module, imported from its file (it is the test's own scaffold, not untrusted code)."""
    name = f"scaffold_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # pydantic resolves the model's postponed annotations through it
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def test_component_creator_01_generated_handlers_refuse_and_the_generated_test_cannot_pass(tmp_path: Path) -> None:
    files = run("render", spec=SPEC)["files"]
    (tmp_path / "block.py").write_text(files["unit_tracker/block.py"], encoding="utf-8")
    block_module = _load_block(tmp_path / "block.py")
    block = block_module.UnitTrackerBlock()
    for op in ("count_units", "remove_units"):
        out = block.infer(block_module.UnitTrackerInput(op=op, owner="me")).unwrap()
        assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_UNIT_TRACKER_NOT_IMPLEMENTED"
    tree = ast.parse(files["tests/test_unit_tracker.py"])
    first = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    assert first.name == "test_d1_it_catches_incomplete_component"
    assert ast.unparse(first.body[0]).startswith("pytest.fail(")


def test_component_creator_02_the_scaffold_is_the_librarys_shape_not_the_archives(tmp_path: Path) -> None:
    files = run("render", spec=SPEC)["files"]
    assert sorted(files) == ["tests/test_unit_tracker.py", "unit_tracker/__init__.py", "unit_tracker/block.py",
                             "unit_tracker/skill/SKILL.md"]
    text = "\n".join(files.values())
    assert "components/mvp" not in text and "mvp." not in text and "premium" not in text
    init = files["unit_tracker/__init__.py"]
    for declaration in ("__g6_component__ = True", '__g6_block__ = "UnitTrackerBlock"', '__g6_family__ = "code"',
                        "__g6_sensitive_ops__: tuple[str, ...] = ('remove_units',)",
                        "__g6_mitigates__: tuple[str, ...] = ('incomplete_component',)"):
        assert declaration in init
    assert 'model_config = ConfigDict(extra="forbid")' in files["unit_tracker/block.py"]
    for path in ("unit_tracker/__init__.py", "unit_tracker/block.py", "tests/test_unit_tracker.py"):
        compile(files[path], path, "exec")  # compiled, never run


def test_auto_component_integrator_01_no_reviewer_string_or_approval_is_read() -> None:
    for field in ("reviewer_signature", "review_artifact_id", "reviewer_id", "approved"):
        with pytest.raises(ValidationError):
            ComponentCreatorInput(op="check_conformance", component="ctx_library_mapper", **{field: "x"})


def test_auto_component_integrator_02_a_module_is_read_as_text_and_never_run(tmp_path: Path) -> None:
    source = "import builtins\nbuiltins.G6_SCAFFOLD_RAN = True\n\ndef get_x(a: int) -> int:\n    return a\n"
    out = run("spec_from_module", source=source, module="sneaky")
    assert [op["name"] for op in out["spec"]["ops"]] == ["get_x"]
    assert not hasattr(builtins, "G6_SCAFFOLD_RAN")
    assert out["evidence"][0]["imported"] is False


def test_auto_component_integrator_03_the_scaffold_wraps_the_functions_it_mapped(tmp_path: Path) -> None:
    (tmp_path / "tinylib_cc.py").write_text(LIB, encoding="utf-8")
    read = run("spec_from_module", root=tmp_path, path=str(tmp_path / "tinylib_cc.py"))
    spec = read["spec"]
    assert [(op["name"], op["kind"], op["sensitive"], op["calls"]) for op in spec["ops"]] == [
        ("get_total", "read", False, "tinylib_cc.get_total"), ("delete_item", "delete", True, "tinylib_cc.delete_item")]
    assert any("Store: a class" in line for line in read["left_out"])
    assert read["problems"] and read["completion_state"] == "qualified-draft"
    spec.update(family="code", mitigates=["incomplete_component"])
    files = run("render", spec=spec)["files"]
    (tmp_path / "block.py").write_text(files["tinylib_cc/block.py"], encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        block_module = _load_block(tmp_path / "block.py")
        out = block_module.TinylibCcBlock().infer(block_module.TinylibCcInput(op="get_total", a=2, b=3)).unwrap()
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("tinylib_cc", None)
    assert out["value"] == 5 and out["completion_state"] == "qualified-draft"
    assert "nothing has checked" in out["warning_card"]["message"]


def test_auto_component_integrator_04_render_writes_only_new_files_and_never_over_one(tmp_path: Path) -> None:
    dest = tmp_path / "taken"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine", encoding="utf-8")
    out = run("render", root=tmp_path, spec=SPEC, dest=str(dest), dry_run=False)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_CODE_PATH_INVALID"
    assert (dest / "keep.txt").read_text(encoding="utf-8") == "mine" and sorted(p.name for p in dest.iterdir()) == [
        "keep.txt"]
    assert "rollback" not in " ".join(package.block._OPS)


def test_auto_component_integrator_05_writes_go_only_where_the_person_declared(tmp_path: Path) -> None:
    allowed, elsewhere = tmp_path / "allowed", tmp_path / "elsewhere"
    allowed.mkdir()
    elsewhere.mkdir()
    no_root = run("render", spec=SPEC, dest=str(allowed / "x"), dry_run=False)
    assert no_root["code"] == "G6_E_CODE_NO_ROOT"
    outside = run("render", root=allowed, spec=SPEC, dest=str(elsewhere / "x"), dry_run=False)
    climbing = run("render", root=allowed, spec=SPEC, dest=str(allowed / ".." / "elsewhere" / "y"), dry_run=False)
    assert outside["code"] == climbing["code"] == "G6_E_CODE_OUTSIDE_ROOT"
    assert list(elsewhere.iterdir()) == []


def test_a_link_out_of_the_root_counts_as_outside(tmp_path: Path) -> None:
    allowed, secret = tmp_path / "allowed", tmp_path / "secret"
    allowed.mkdir()
    secret.mkdir()
    (secret / "lib.py").write_text("def get_secret():\n    return 1\n", encoding="utf-8")
    link(allowed / "link", secret)
    out = run("spec_from_module", root=allowed, path=str(allowed / "link" / "lib.py"))
    assert out["code"] == "G6_E_CODE_OUTSIDE_ROOT"


def test_auto_engineer_01_a_drafted_spec_is_a_draft() -> None:
    caller = FakeLLM(default=json.dumps(SPEC))
    out = run("spec_from_text", caller=caller, text="a component that tracks units of work")
    assert out["completion_state"] == "qualified-draft" and out["code"] == "G6_E_SCAFFOLD_SPEC_DRAFTED"
    assert out["spec"]["name"] == "unit_tracker" and out["problems"] == []
    assert out["evidence"][0]["kind"] == "claim" and len(caller.calls) == 1


def test_auto_engineer_02_a_failed_model_is_a_refusal_never_a_template_called_passed() -> None:
    def broken(system: str, user: str) -> str:
        raise LLMError("the model is down")

    failed = run("spec_from_text", caller=FakeLLM(broken), text="anything")
    prose = run("spec_from_text", caller=FakeLLM(default="I would make a component called thing."), text="anything")
    wrong = run("spec_from_text", caller=FakeLLM(default=json.dumps({**SPEC, "confidence": 1.0})), text="anything")
    assert failed["code"] == "G6_E_SCAFFOLD_MODEL_FAILED" and prose["code"] == wrong["code"] == \
        "G6_E_SCAFFOLD_SPEC_INVALID"
    for out in (failed, prose, wrong):
        assert out["completion_state"] == "blocked-escalated" and "spec" not in out and "confidence" not in out


def test_auto_engineer_03_drafting_a_spec_writes_nothing(tmp_path: Path) -> None:
    from g6.core.storage import state_path

    home = state_path()
    before = sorted(p for p in home.rglob("*")) if home.exists() else []
    run("spec_from_text", caller=FakeLLM(default=json.dumps(SPEC)), text="units")
    after = sorted(p for p in home.rglob("*")) if home.exists() else []
    assert before == after


def test_auto_engineer_04_with_no_caller_no_model_is_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    out = run("spec_from_text", text="units")
    assert out["code"] == "G6_E_SCAFFOLD_NO_MODEL" and out["completion_state"] == "blocked-escalated"


def test_a_drafted_spec_that_names_a_library_function_is_refused() -> None:
    drafted = {**SPEC, "ops": [{**SPEC["ops"][0], "calls": "os.system"}]}
    out = run("spec_from_text", caller=FakeLLM(default=json.dumps(drafted)), text="units")
    assert out["code"] == "G6_E_SCAFFOLD_SPEC_INVALID" and "never does" in out["warning_card"]["message"]


# --- the gates --------------------------------------------------------------------------------------------------------

def test_g1_no_op_marks_its_own_output_verified(tmp_path: Path) -> None:
    folder = copy_member(tmp_path)
    replies = [run("check_conformance", root=tmp_path, path=str(folder)), run("render", spec=SPEC),
               run("spec_from_module", source=LIB, module="lib"),
               run("spec_from_text", caller=FakeLLM(default=json.dumps(SPEC)), text="units"),
               run("check_conformance", component="ctx_library_mapper")]
    assert {r["completion_state"] for r in replies} <= {"qualified-draft", "blocked-escalated"}
    assert all(r["warning_card"]["verified"] is False for r in replies)


def test_g3_a_spec_that_claims_a_label_or_a_review_is_refused() -> None:
    for extra in ({"verified": True}, {"reviewed_by": "a person"}, {"tier": "premium"}):
        with pytest.raises(ValidationError):
            ComponentSpec.model_validate({**SPEC, **extra})


def test_g4_a_declaration_that_does_not_parse_refuses_rather_than_reading_as_no_root(monkeypatch) -> None:
    monkeypatch.setenv("G6_CODE_CONFIG", "{not json")
    out = ComponentCreatorBlock().infer(ComponentCreatorInput(op="spec_from_module", path="C:/x/y.py")).unwrap()
    assert out["code"] == "G6_E_CODE_CONFIG_INVALID" and out["completion_state"] == "blocked-escalated"
    monkeypatch.setenv("G6_CODE_CONFIG", json.dumps({"roots": ["relative/folder"]}))
    out = ComponentCreatorBlock().infer(ComponentCreatorInput(op="spec_from_module", path="C:/x/y.py")).unwrap()
    assert out["code"] == "G6_E_CODE_CONFIG_INVALID"


def test_g4_a_package_whose_init_does_not_parse_is_never_clean(tmp_path: Path) -> None:
    folder = copy_member(tmp_path)
    (folder / "__init__.py").write_text("# SPDX-License-Identifier: Apache-2.0\ndef broken(:\n", encoding="utf-8")
    out = run("check_conformance", root=tmp_path, path=str(folder))
    assert out["completion_state"] == "blocked-escalated" and any("does not parse" in p for p in problems(out))


def test_g5_render_writes_files_so_it_is_refused_on_an_untrusted_surface() -> None:
    assert package.__g6_sensitive_ops__ == ("render",)
    said = str(get_registry().invoke("component_creator", "render", {"spec": SPEC}, block_sensitive=True).failure())
    assert said.startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")


def test_render_refuses_a_spec_with_no_failure_mode_or_an_unknown_one() -> None:
    for change in ({"mitigates": []}, {"mitigates": ["made_up_mode"]}, {"family": ""}):
        out = run("render", spec={**SPEC, **change})
        assert out["code"] == "G6_E_SCAFFOLD_SPEC_INVALID" and out["completion_state"] == "blocked-escalated"


def test_spec_from_module_respects_all_and_maps_by_prefix() -> None:
    out = run("spec_from_module", source=LIB, module="lib")
    assert [op["name"] for op in out["spec"]["ops"]] == ["get_total", "delete_item"]
    assert [f["name"] for f in out["spec"]["fields"]] == ["a", "b", "name"]
    assert out["interfaces"][0]["all"] == ["get_total", "delete_item", "Store"]


# --- D2, D6 -----------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("incomplete_component",)
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.code.component_creator" not in FailureModeRegistry.planned()


def test_d6_check_conformance_round_trips_over_mcp_and_describe_serves_the_skill() -> None:
    reply = invoke_reply("component_creator", "check_conformance", json.dumps({"component": "ctx_library_mapper"}))
    assert reply["ok"] and reply["result"]["static_clean"] is True, reply["result"].get("problems")
    assert describe_reply("component_creator")["skill"].startswith("---\nname: g6-component-creator")


def test_the_scaffolders_own_package_passes_its_own_check() -> None:
    out = run("check_conformance", component="component_creator")
    assert out["static_clean"] is True, problems(out)
