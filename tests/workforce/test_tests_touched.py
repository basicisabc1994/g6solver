# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Import reach, ordered scheduling and honest execution evidence for selected tests."""
from pathlib import Path
from dataclasses import replace
import ast
import json
import os
import subprocess
import sys
from types import ModuleType

import pytest

from g6.workforce import repo_tests as rt


def fixture_change(tmp_path, monkeypatch, files, changed=("src/pkg/core.py",)):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    listing = "\0".join(files).encode() + b"\0"
    monkeypatch.setattr(rt, "_git", lambda repo, *args, **kw: b"" if "--others" in args else listing)
    sources = tuple(rel for rel in changed if not rel.startswith("tests/"))
    return rt.Change(tmp_path, "HEAD", "a" * 40, "a" * 40, changed, {}, {}, sources, ())


def loading_report(inside, complete=True):
    node = "tests/test_name.py::test_ok"
    return {"verdict": "passed", "said": "", "output": "", "rootdir": "", "loaded_complete": complete,
            "loaded_inside": inside, "loaded_outside": {},
            "test_outcomes": {node: {"setup": "passed", "call": "passed", "teardown": "passed"}},
            "failed_ids": [], "setup_errors": {}, "collected_ids": [node], "omitted_ids": [],
            "passed": 1, "failed": 0, "total": 1, "not_passed": 0}


def test_transitive_import_without_stem_is_selected(tmp_path, monkeypatch):
    change = fixture_change(tmp_path, monkeypatch, {
        "src/pkg/__init__.py": "", "src/pkg/core.py": "VALUE = 1\n",
        "src/pkg/api.py": "from .core import VALUE\n",
        "tests/test_api.py": "from pkg.api import VALUE\n",
    })
    selected, more = rt.derive_tests(change)
    assert selected == ["tests/test_api.py"]
    assert selected.reasons["tests/test_api.py"] == [{"rule": "imports", "distance": 2}]
    assert more == 0


def test_thirty_files_are_run_or_named_when_budget_expires(tmp_path, monkeypatch):
    files = {"src/pkg/core.py": "VALUE = 1\n", **{
        f"tests/test_{i:02}.py": "# core\ndef test_ok():\n    assert True\n" for i in range(30)}}
    change = fixture_change(tmp_path, monkeypatch, files)
    selected, more = rt.derive_tests(change)
    assert len(selected) == 30 and more == 0
    clock = [0.0]
    monkeypatch.setattr(rt.time, "monotonic", lambda: clock[0])
    called = []

    def execute(ids, timeout_s):
        called.extend(ids)
        clock[0] += 1
        return {"verdict": "passed"}

    results = dict(rt.run_selection(change, selected, deadline=3.0, execute=execute))
    assert called == selected[:3]
    assert list(results) == selected
    assert all(results[rel]["said"] == "time budget" for rel in selected[3:])
    assert all(selected.reasons[rel] == [{"rule": "names"}] for rel in selected)


def test_safety_ceiling_names_every_unreached_file(tmp_path, monkeypatch):
    change = fixture_change(tmp_path, monkeypatch, {"src/pkg/core.py": "VALUE = 1\n", **{
        f"tests/test_{i}.py": "# core\n" for i in range(5)}})
    selected, more = rt.derive_tests(change, limit=2)
    assert more == 3
    assert selected.unreached == [f"tests/test_{i}.py" for i in range(2, 5)]
    notes = rt.selection_notes(selected)
    assert all(rel in " ".join(notes) for rel in selected.unreached)


def test_order_is_changed_then_distance_then_names_and_is_deterministic(tmp_path, monkeypatch):
    files = {"src/pkg/core.py": "VALUE = 1\n", "src/pkg/api.py": "import pkg.core\n",
             "tests/test_z.py": "import pkg.core\n", "tests/test_a.py": "import pkg.api\n",
             "tests/test_names.py": "# core\n", "tests/test_changed.py": ""}
    changed = ("src/pkg/core.py", "tests/test_changed.py")
    change = fixture_change(tmp_path, monkeypatch, files, changed)
    expected = ["tests/test_changed.py", "tests/test_z.py", "tests/test_a.py", "tests/test_names.py"]
    assert rt.derive_tests(change)[0] == expected
    fixture_change(tmp_path, monkeypatch, dict(reversed(list(files.items()))), changed)
    assert rt.derive_tests(change)[0] == expected


@pytest.mark.parametrize("inside,complete,expected", [({}, True, []), ({"pkg.core": "src/pkg/core.py"}, True,
                         ["src/pkg/core.py"]), ({"pkg.core": "elsewhere/core.py"}, True, []), ({}, False, None)])
def test_a_selected_file_only_tests_the_change_if_it_loaded_it(inside, complete, expected):
    run = loading_report(inside, complete)
    assert rt.changed_loaded(("src/pkg/core.py",), run) == expected


def test_no_load_is_named_and_never_counted(tmp_path, monkeypatch):
    change = fixture_change(tmp_path, monkeypatch, {"src/pkg/core.py": "VALUE = 1\n", "tests/test_name.py": "# core\n"})
    selected, _ = rt.derive_tests(change)
    runs = dict(rt.run_selection(change, selected, deadline=rt.time.monotonic() + 30,
                                execute=lambda *a, **kw: loading_report({})))
    assert rt.tests_change(change, runs["tests/test_name.py"]) is False
    assert rt.selection_audit(change, runs) == ["1 selected files did not load the changed code: tests/test_name.py"]


def test_loading_a_changed_test_itself_does_not_count_as_loading_production_change(tmp_path, monkeypatch):
    change = fixture_change(tmp_path, monkeypatch, {"src/pkg/core.py": "VALUE = 1\n", "tests/test_name.py": "# core\n"})
    change = replace(change, after={"tests/test_name.py": "# core\n"})
    run = loading_report({"test_name": "tests/test_name.py"})
    assert not rt.tests_change(change, run)


def test_every_derive_tests_consumer_names_unreached_files():
    root = Path(__file__).resolve().parents[2]
    consumers = {"src/g6/bundles/agent_change.py", "src/g6/code/swe_diagnostics/block.py",
                 "src/g6/harness/patch_review.py", "validation/v7_swebench/g6check.py"}
    found = set()
    listed = subprocess.run(["git", "ls-files", "-z", "src", "validation"], cwd=root,
                            check=True, capture_output=True).stdout.decode().split("\0")
    for rel in listed:
        path = root / rel
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8")
        if "derive_tests(change" in text and path.name != "repo_tests.py":
            found.add(rel)
            assert "selection_notes(" in text, path
            assert "more test file(s)" not in text, path
    assert found == consumers
    implementation = (root / "src/g6/workforce/repo_tests.py").read_text(encoding="utf-8")
    assert "results[rel] = _not_run_report(OLD_TEST_LIMIT" in implementation
    assert rt.MAX_DERIVED > 25


@pytest.mark.parametrize("api", ["from pkg import core", "import pkg.core as c", "from . import core"])
def test_aliases_and_relative_imports_reach_change(tmp_path, monkeypatch, api):
    change = fixture_change(tmp_path, monkeypatch, {
        "src/pkg/__init__.py": "", "src/pkg/core.py": "VALUE = 1\n", "src/pkg/api.py": api,
        "tests/test_api.py": "import pkg.api\n"})
    selected, _ = rt.derive_tests(change)
    assert selected.reasons["tests/test_api.py"] == [{"rule": "imports", "distance": 2}]


def test_inside_inventory_is_emitted_by_the_real_bootstrap_loader(tmp_path, monkeypatch):
    path = tmp_path / "src" / "pkg" / "core.py"
    path.parent.mkdir(parents=True)
    path.write_text("VALUE = 1\n", encoding="utf-8")
    module = ModuleType("pkg.core")
    module.__file__ = str(path)
    monkeypatch.setitem(sys.modules, "pkg.core", module)
    monkeypatch.chdir(tmp_path)
    tree = ast.parse(rt._BOOT)
    loader = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_Loaded")
    output = tmp_path / "loaded.json"
    scope = {"json": json, "os": os, "sys": sys, "loaded": str(output), "limit": rt.LOADED_LIMIT}
    exec(compile(ast.Module(body=[loader], type_ignores=[]), "<loader>", "exec"), scope)
    scope["_Loaded"]().pytest_sessionfinish(None)
    inventory = rt._loaded_inside(output)
    assert inventory["pkg.core"] == "src/pkg/core.py"
    assert rt.changed_loaded(("src/pkg/core.py",), loading_report(inventory)) == ["src/pkg/core.py"]


def test_custom_namespace_root_and_cycles_are_resolved(tmp_path, monkeypatch):
    change = fixture_change(tmp_path, monkeypatch, {
        "lib/pkg/core.py": "VALUE = 1\n", "lib/pkg/api.py": "import pkg.cycle\n",
        "lib/pkg/cycle.py": "import pkg.api\nimport pkg.core\n", "tests/test_api.py": "import pkg.api\n"},
        changed=("lib/pkg/core.py",))
    selected, _ = rt.derive_tests(change, roots=("lib",))
    assert selected == ["tests/test_api.py"]
    assert selected.reasons["tests/test_api.py"] == [{"rule": "imports", "distance": 3}]


def test_import_of_package_reaches_changed_initializer(tmp_path, monkeypatch):
    change = fixture_change(tmp_path, monkeypatch, {
        "lib/pkg/__init__.py": "VALUE = 1\n", "lib/pkg/api.py": "", "tests/test_api.py": "import pkg.api\n"},
        changed=("lib/pkg/__init__.py",))
    selected, _ = rt.derive_tests(change)
    assert selected.reasons["tests/test_api.py"] == [{"rule": "imports", "distance": 1}]


def test_dynamic_import_is_not_claimed_as_static_reach(tmp_path, monkeypatch):
    change = fixture_change(tmp_path, monkeypatch, {
        "src/pkg/core.py": "VALUE = 1\n", "src/pkg/api.py": "import importlib\nimportlib.import_module('pkg.core')\n",
        "tests/test_api.py": "import pkg.api\n"})
    selected, more = rt.derive_tests(change)
    assert selected == [] and more == 0
    assert "dynamic imports, plugins and importlib" in " ".join(rt.selection_notes(selected))
