# SPDX-License-Identifier: Apache-2.0
"""Regression cases for the code-family review; exercise real implementations."""
import ast
import shutil
from pathlib import Path

import pytest

from g6.code import _roots
from g6.code.component_creator import ComponentCreatorBlock, ComponentCreatorInput, ComponentSpec
from g6.code.component_creator.render import render


# Keep helpers local: pytest importlib mode does not expose sibling test modules.
def _creator_run(op, *, root=None, **params):
    roots = _roots.Roots((root.resolve(),), "test") if root else _roots.Roots((), "none")
    return ComponentCreatorBlock(roots=roots).infer(ComponentCreatorInput(op=op, **params)).unwrap()


def _swe_run(op, root=None, **params):
    from g6.code.swe_diagnostics import SweDiagnosticsBlock, SweDiagnosticsInput
    roots = _roots.Roots((root.resolve(),), "test") if root else _roots.Roots((), "none")
    return SweDiagnosticsBlock(roots=roots).infer(SweDiagnosticsInput(op=op, **params)).unwrap()


def _copy_member(root):
    import g6.sources.ctx_library_mapper as mapper
    folder = root / "oss" / "src" / "g6" / "sources" / "ctx_library_mapper"
    shutil.copytree(Path(mapper.__file__).parent, folder, ignore=shutil.ignore_patterns("__pycache__"))
    tests = root / "oss" / "tests" / "sources"
    tests.mkdir(parents=True)
    test_file = Path(__file__).resolve().parents[1] / "sources" / "test_ctx_library_mapper.py"
    shutil.copy(test_file, tests / test_file.name)
    return folder


def scaffold_spec(**changes):
    return {"name": "review_unit", "family": "code", "summary": "Track units.",
            "ops": [{"name": "count", "does": "count units", "kind": "read"}],
            "mitigates": ["incomplete_component"], **changes}


@pytest.mark.parametrize("changes", [
    {"summary": 'Track units."""; __import__("os"); """'},
    {"summary": "Track \\ units"},
    {"summary": "Track\x00units"},
    {"fields": [{"name": "x", "does": "\n    import os"}]},
    {"fields": [{"name": "x", "does": "units\x7f"}]},
    {"ops": [{"name": "count", "does": "count\nimport os"}]},
])
def test_descriptions_refused(changes):
    out = ComponentCreatorBlock().infer(ComponentCreatorInput(op="render", spec=scaffold_spec(**changes))).unwrap()
    assert out["code"] == "G6_E_SCAFFOLD_SPEC_INVALID"


def test_legal_descriptions_stay_data():
    spec = ComponentSpec.model_validate(scaffold_spec(
        summary='Track "units"; __import__("os"); # data',
        fields=[{"name": "x", "does": '"""; __import__("os"); \\ data'}]))
    for path, source in render(spec).items():
        if path.endswith(".py"):
            tree = ast.parse(source)
            assert all(isinstance(n, (ast.Expr, ast.Import, ast.ImportFrom, ast.Assign,
                                     ast.AnnAssign, ast.FunctionDef, ast.ClassDef)) for n in tree.body)
            assert all(isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                       for n in tree.body if isinstance(n, ast.Expr))


@pytest.mark.parametrize("surface", ["existing", "destination", "repository", "clone"])
@pytest.mark.parametrize("raw", [r"\\203.0.113.5\s\x", "//203.0.113.5/s/x",
                                 r"\\?\C:\outside", r"\\.\C:\outside"])
def test_paths_reject_before_resolution(tmp_path, monkeypatch, surface, raw):
    import os
    from pathlib import Path
    from g6.code import _roots
    from g6.code._types import CodeRefused
    from g6.sources.ctx_git import config
    calls = []
    # Intercept the filesystem boundary so RED never contacts SMB.
    def resolve(path, *args, **kwargs):
        calls.append(str(path))
        return path
    def realpath(path, *args, **kwargs):
        calls.append(str(path))
        return str(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", resolve)
        patch.setattr(os.path, "realpath", realpath)
        with pytest.raises((CodeRefused, config.GitRefused)):
            if surface in ("existing", "destination"):
                getattr(_roots, surface)(raw, _roots.Roots((tmp_path,), "test"))
            else:
                fn = config.repository if surface == "repository" else config.destination
                fn(raw, config.GitConfig((tmp_path,), (), "test"))
    assert calls == []
    # Removing storage.confined_path's lexical checks permits resolution before refusal.


def design(op, **params):
    from g6.code.design_governance import DesignGovernanceBlock, DesignGovernanceInput
    return DesignGovernanceBlock().infer(DesignGovernanceInput(op=op, **params)).unwrap()


def test_layers_use_longest_prefix():
    out = design("layer_violations", package="shop",
                 files={"shop/api.py": "", "shop/store/db.py": "import shop.api"},
                 contract={"layers": ["shop", "shop.store"]})
    assert not out.get("clean", False)
    assert out["violations"][0]["from"] == "shop.store.db"
    # Removing graph.layer_violations's longest-prefix rank hides this breach.


def test_shadowed_layer_refused():
    out = design("layer_violations", package="shop",
                 files={"shop/store/a.py": "import shop.store.b", "shop/store/b.py": ""},
                 contract={"layers": ["shop", "shop.store"]})
    assert out["code"] == "G6_E_DESIGN_CONTRACT_UNMATCHED"
    # Removing graph.layer_violations's assigned-rank check makes an empty layer clean.


def test_type_checking_else_cycle():
    out = design("cycles", package="shop", include_type_checking=False,
                 files={"shop/a.py": "import shop.b",
                        "shop/b.py": "if TYPE_CHECKING:\n    import typing\nelse:\n    import shop.a"})
    assert out["cycles"]
    # Removing graph._imports's true-branch-only guard drops the closing runtime import.


def test_local_escape_rejected_before_resolution(tmp_path, monkeypatch):
    from pathlib import Path
    from g6.core.storage import confined_path
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", lambda path, **kw: calls.append(path) or path)
        with pytest.raises(ValueError):
            confined_path(tmp_path / ".." / "outside", (tmp_path,))
    assert calls == []
    # Removing confined_path's normalised containment check resolves an outside path.


def healing(patch, **params):
    from g6.code.adapt_healing import AdaptHealingBlock, AdaptHealingInput
    from g6.core.llm import FakeLLM
    defaults = dict(code="def f(x):\n    return 1 / 0\n", error="ZeroDivisionError: division by zero",
                    tests_shown=("assert f(1) == 1",), tests_held_back=("assert f(0) == 0",))
    return AdaptHealingBlock(caller=FakeLLM(default=patch)).infer(
        AdaptHealingInput(op="heal", **(defaults | params))).unwrap()


@pytest.mark.parametrize("returned", ["Pretend()", "[Pretend()]"])
def test_deceptive_repr_not_a_value(returned):
    expected = "0" if returned == "Pretend()" else "[0]"
    out = healing("class Pretend:\n    def __repr__(self): return '0'\n"
                  f"def f(x):\n    return 1 if x else {returned}\n",
                  tests_held_back=(f"assert f(0) == {expected}",))
    assert out["code"] == "G6_E_HEAL_HELD_BACK_FAILED"
    assert "Pretend" in str(out["held_back"])
    # Removing cases._HELD's recursive exact-type check accepts deceptive repr values.


@pytest.mark.parametrize("patch", ["def f(x): return x", "def f(x): return 1"])
def test_healing_process_blind_spot(patch):
    out = healing(patch)
    note = "the checks ran in the patched code's own process; a patch that alters the runner can forge a pass"
    assert note in out["warning_card"]["message"]
    assert note in str(out["evidence"])
    assert out["completion_state"] in ("qualified-draft", "blocked-escalated")
    # Removing AdaptHealingBlock.infer's process-trust disclosure hides the runner blind spot.


def test_triage_uses_observed_failure():
    out = healing("def f(x): return x", code="def f(x):\n    raise FileNotFoundError('missing input')",
                  error="ZeroDivisionError: division by zero")
    assert out["code"] == "G6_E_HEAL_NEEDS_REVIEW"
    assert out["triage"]["error_type"] == "FileNotFoundError"
    assert out["triage"]["source"] == "original_run"
    # Removing heal.heal's original run lets the caller's trivial error select auto-repair.


def test_unreproduced_error_needs_person():
    out = healing("def f(x): return x", code="def f(x): return x")
    assert out["code"] == "G6_E_HEAL_NEEDS_REVIEW"
    assert "not reproduced" in out["warning_card"]["message"]
    # Removing heal.heal's no-observed-exception refusal repairs an unreproduced claim.


@pytest.mark.parametrize("tests", [
    ("assert f('" + "a" * 20000 + "') == 0",),
    ("assert " + "1+" * 5000 + "f(0) == 0",),
    tuple("assert f('" + "a" * 3000 + str(i) + "') == 0" for i in range(50)),
])
def test_healing_assertion_bounds(tests):
    import json
    from g6.mcp import invoke_reply
    reply = invoke_reply("adapt_healing", "check_tests", json.dumps({"tests_held_back": tests}))
    out = reply.get("result") or reply
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"].startswith("G6_E_")
    # Removing cases.check_bounds and AdaptHealingBlock.infer's resource refusal exposes parsing.


@pytest.mark.parametrize("tests", [
    "async def test_bad():\n    assert False\n",
    "def test_bad():\n    yield 1\n    assert False\n",
])
def test_deferred_tests_not_passed(tests):
    from g6.code.meta_programming import MetaProgrammingBlock, MetaProgrammingInput
    out = MetaProgrammingBlock().infer(MetaProgrammingInput(op="run_tests", source="x = 1", tests=tests)).unwrap()
    assert out["code"] == "G6_E_SYNTAX_TEST_FORM_UNSUPPORTED"
    assert out["completion_state"] == "blocked-escalated"
    assert all(r["outcome"] != "passed" for r in out["results"])
    # Removing runner._RUNNER's inspect checks records an unexecuted test as passed.


@pytest.mark.parametrize(("source", "line"), [
    ("total = 0\ndef add():\n    global total\n    total += 1\n    return total\n", 4),
    ("def outer():\n    total = 0\n    def add():\n        nonlocal total\n        total += 1\n"
     "        return total\n    return add\n", 5),
])
def test_extract_external_binding_refused(source, line):
    from g6.code.meta_programming import MetaProgrammingBlock, MetaProgrammingInput
    out = MetaProgrammingBlock().infer(MetaProgrammingInput(
        op="extract_function", source=source, start_line=line, end_line=line, name="increment")).unwrap()
    assert out["code"] == "G6_E_SYNTAX_EXTRACT_REFUSED"
    # Removing extract._boundary's external-declaration write check changes the binding.


def test_conformance_broken_helper_not_clean(tmp_path):
    folder = _copy_member(tmp_path)
    (folder / "helper.py").write_text("# SPDX-License-Identifier: Apache-2.0\nbroken = (\n", encoding="utf-8")
    out = _creator_run("check_conformance", root=tmp_path, path=str(folder))
    assert out["completion_state"] == "blocked-escalated"
    assert out["static_clean"] is False
    assert any(p["file"] == "helper.py" and "parse" in p["message"] for p in out["problems"])
    # Removing conformance.check_package's all-file parse check hides the broken helper.


def test_conformance_skipped_files_incomplete(tmp_path):
    folder = _copy_member(tmp_path)
    (folder / "huge.py").write_text("#" + "x" * 1_000_001, encoding="utf-8")
    out = _creator_run("check_conformance", root=tmp_path, path=str(folder))
    assert out["completion_state"] == "blocked-escalated"
    assert out["complete"] is False and out["static_clean"] is False
    # Removing conformance._incomplete's skipped-file check reports incomplete reads as clean.


def test_conformance_installed_execution_evidence():
    out = _creator_run("check_conformance", component="ctx_library_mapper")
    assert "conforms" not in out
    assert out["evidence"][0]["ran_code"] is True
    assert "g6.sources.ctx_library_mapper" in out["evidence"][0]["imported"]
    assert out["evidence"][0]["invoked"] == ["ctx_library_mapper.ops"]
    # Removing conformance.check_component's execution record conceals import and ops execution.


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize("changed", [True, False])
@pytest.mark.parametrize("kind", ["symlink", "oversized"])
def test_patch_reads_confined_and_bounded(tmp_path, monkeypatch, kind, changed):
    import builtins
    import io
    import os
    from pathlib import Path
    from g6.core import component_process
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "test_external.py"
    outside = tmp_path / "outside.py"
    outside.write_text("import subject\n", encoding="utf-8")
    if kind == "symlink":
        try:
            target.symlink_to(outside)
        except OSError:
            pytest.skip("the OS cannot create a file symlink")
    else:
        target.write_bytes(b"#" * 1_000_001)
    (repo / "subject.py").write_text("x = 1\n", encoding="utf-8")

    # Process double only: exercise real repo_tests change/derive logic without git writes.
    def git_process(argv, *, stdout, stderr, **kwargs):
        if "config" in argv:
            data = b"g6.k1guard\0"
        elif "--show-toplevel" in argv:
            data = str(repo).encode()
        elif "rev-parse" in argv:
            data = b"a" * 40
        elif "diff" in argv:
            data = b"test_external.py\0" if changed else b"subject.py\0"
        elif "ls-files" in argv and "--stage" not in argv:
            data = b"test_external.py\0" if "--others" not in argv else b""
        else:
            data = b""
        stdout.write(data)
        return 0

    opened = []
    real_open, real_io_open = builtins.open, io.open
    def watched(opener):
        def call(file, *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and Path(file) in (target, outside):
                opened.append(str(file))
            return opener(file, *args, **kwargs)
        return call
    monkeypatch.setattr(component_process, "run_process", git_process)
    monkeypatch.setattr(builtins, "open", watched(real_open))
    monkeypatch.setattr(io, "open", watched(real_io_open))
    out = _swe_run("patch_check", repo, repo=str(repo))
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] in ("G6_E_CODE_OUTSIDE_ROOT", "G6_E_CODE_PATH_INVALID")
    assert opened == []
    # Removing patch_check's read_file injection opens changed/derived tests outside the bounds.


def test_pattern_scan_linear_budget():
    import time
    from g6.code.swe_diagnostics.read import code_summarize
    source = "var a=1\n" * 125000
    start = time.perf_counter()
    out = code_summarize(source, "large.js")
    elapsed = time.perf_counter() - start
    assert [s["line"] for s in out["symbols"]] == list(range(1, 201))
    assert elapsed < 2.0, f"one-megabyte scan took {elapsed:.3f}s (budget 2s)"
    # Removing read._matches's moving newline cursor restores repeated prefix scans.


def test_walk_entry_cap_reported(tmp_path):
    from g6.code import _roots
    for i in range(8):
        (tmp_path / f"{i}.txt").touch()
    found = _roots.walk(tmp_path, _roots.Roots((tmp_path,), "test"), max_files=100,
                        max_file_bytes=1000, suffixes=(".py",), max_entries=3)
    assert found.truncated and found.describe()["truncated_reason"] == "entry cap"
    assert found.describe()["entries"] == 3
    # Removing _roots._files's entry budget lets nonmatching files evade the walk cap.


def test_walk_deadline_reported(tmp_path):
    from g6.code import _roots
    found = _roots.walk(tmp_path, _roots.Roots((tmp_path,), "test"), max_files=100,
                        max_file_bytes=1000, timeout_s=0)
    assert found.truncated and found.describe()["truncated_reason"] == "deadline"
    # Removing _roots._files's deadline guard lets traversal run without a time bound.


@pytest.mark.parametrize(("component", "op", "params"), [
    ("component_creator", "spec_from_module",
     {"module": "sample", "source": "__all__ = {[]: 1}\ndef f(): pass"}),
    ("meta_programming", "analyze", {"source": "__all__ = {[]: 1}"}),
    ("meta_programming", "rename", {"source": "x = " + "1+" * 5000 + "1\n", "old": "x", "new": "y"}),
    # Deep enough that Python's own parser refuses it everywhere: Linux parses 5,000 terms, Windows does not
    ("meta_programming", "parse", {"source": "x = " + "1+" * 50_000 + "1\n"}),
    ("design_governance", "cycles",
     {"package": "shop", "files": {"shop/a.py": "import shop.b\nx = " + "1+" * 5000 + "1",
                                   "shop/b.py": "import shop.a"}}),
    ("design_governance", "pass_through",
     {"files": {"shop/a.py": "def f(x):\n    return g(x)\n    'trailing'\n"}}),
])
def test_mcp_malformed_ast_refused(component, op, params):
    import json
    from g6.mcp import invoke_reply
    reply = invoke_reply(component, op, json.dumps(params))
    out = reply.get("result") or reply
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"].startswith("G6_E_")
    # Removing literal-error/resource catches or graph.pass_through's return check leaks exceptions.


@pytest.mark.parametrize("assignment", [
    "__g6_mitigates__ = {[]: 1}",
    "__g6_block__ = {[]: 1}",
])
def test_mcp_conformance_bad_literal_refused(tmp_path, monkeypatch, assignment):
    import json
    from g6.mcp import invoke_reply
    folder = _copy_member(tmp_path)
    init = folder / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8") + "\n" + assignment + "\n", encoding="utf-8")
    monkeypatch.setenv("G6_CODE_CONFIG", json.dumps({"roots": [str(tmp_path)]}))
    reply = invoke_reply("component_creator", "check_conformance", json.dumps({"path": str(folder)}))
    assert reply["result"]["completion_state"] == "blocked-escalated"
    assert reply["result"]["code"].startswith("G6_E_")
    # Removing conformance._literal/failure_modes.declarations_in's TypeError catches escapes the door.


def test_failure_declaration_bad_literal():
    from g6.core.failure_modes import declarations_in
    assert declarations_in("__g6_mitigates__ = {[]: 1}") == {}
    # Removing failure_modes.declarations_in's literal exception set raises TypeError.


def test_oversized_marker_is_unreadable(tmp_path):
    (tmp_path / "package.json").write_bytes(b" " * 1_000_001)
    out = _swe_run("detect_build_env", tmp_path, path=str(tmp_path))
    assert "package.json" in out["unreadable"]
    assert "1000000" in out["unreadable_reasons"]["package.json"]
    # Removing read._inside's reason recording silently treats an oversized marker as absent.


def test_drafted_description_refused():
    import json
    from g6.core.llm import FakeLLM
    draft = scaffold_spec(summary='Track units."""; __import__("os"); """')
    out = ComponentCreatorBlock(caller=FakeLLM(default=json.dumps(draft))).infer(
        ComponentCreatorInput(op="spec_from_text", text="track units")).unwrap()
    assert out["code"] == "G6_E_SCAFFOLD_SPEC_INVALID"
    # Removing ComponentSpec._summary's triple-quote guard accepts executable model text.


def test_literal_type_identity_not_metaclass_equality():
    patch = ("class PretendType(type):\n    def __eq__(self, other): return True\n"
             "class Pretend(metaclass=PretendType):\n    def __repr__(self): return '0'\n"
             "def f(x): return 1 if x else Pretend()\n")
    out = healing(patch)
    assert out["code"] == "G6_E_HEAL_HELD_BACK_FAILED"
    # Replacing cases._HELD's type identity checks with equality admits a deceptive metaclass.
