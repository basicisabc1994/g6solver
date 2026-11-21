# SPDX-License-Identifier: Apache-2.0
"""Every active task resolves checks, mutations and binding from its fixture inventory."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


m = _load("routing_paths_checks", HERE / "test_routing_moment_outcome_checks.py")
run, score = m.run, m.score
TASKS = run.load_moment()["tasks"]


@pytest.mark.parametrize("task", TASKS, ids=[t["id"] for t in TASKS])
def test_every_active_tasks_check_outcome_and_binding_paths_exist(task):
    fixture = run.fixture_dir(task["fixture"])
    paths = run.fixture_paths(task)
    assert fixture == run.active_moment_fixtures()[task["fixture"]]
    assert paths and all((fixture / p).is_file() for p in paths.values()), task["id"]
    folder = run.OUTCOME / task["id"]
    if task["polarity"] == "positive":
        tree = ast.parse((folder / "test_outcome.py").read_text(encoding="utf-8"))
        # A literal fixture path or package import bypasses the inventory and can silently go stale.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in {"attribute", "path_for"}:
                name, role = [ast.literal_eval(arg) for arg in node.args[:2]]
                assert name == task["fixture"] and role in paths, (task["id"], name, role)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path":
                assert not node.args or not isinstance(node.args[0], ast.Constant), task["id"]
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in {
                    "shop", "classify", "ledger", "fees", "stock", "invoices"}, task["id"]
        right, wrong = m.FIXES[task["id"]]
        for edits in [right, *wrong.values()]:
            assert all(p in paths.values() and (fixture / p).is_file() for p in edits), task["id"]
    else:
        protected = run.freeze.read(folder / score.PROTECTED)["protected"]
        assert all(p in paths.values() and (fixture / p).is_file() for p in protected), task["id"]


@pytest.mark.parametrize("task", [t for t in TASKS if t["kind"] == "stuck"], ids=lambda t: t["id"])
def test_unchanged_test_check_loads_without_importing_the_fixture(task, monkeypatch):
    check = run.OUTCOME / task["id"] / "test_outcome.py"
    namespace = {}
    exec(compile(check.read_text(encoding="utf-8"), str(check), "exec"), namespace)
    monkeypatch.chdir(run.fixture_dir(task["fixture"]))
    namespace["test_the_tests_are_as_shipped"]()


@pytest.mark.parametrize("task", TASKS, ids=[t["id"] for t in TASKS])
def test_each_active_fixture_byte_is_part_of_the_admission_binding(task, monkeypatch):
    original = run.freeze.tree_digest
    changed = run.fixture_dir(task["fixture"])
    monkeypatch.setattr(run.freeze, "tree_digest", lambda path: "changed-byte" if path == changed else original(path))
    with pytest.raises(run.freeze.FrozenError, match="fixtures"):
        run.moment_fixtures()


def test_outcome_reader_refuses_a_protected_path_outside_the_fixture_table(monkeypatch):
    task = next(t for t in TASKS if t["id"] == "moment.missing_tool.3")
    roles = dict(run.fixture_paths(task))
    del roles["tests"]
    monkeypatch.setitem(score.fixture_paths.FILES, task["fixture"], roles)
    with pytest.raises(score.TaskError, match="protected"):
        score.outcome(task, "completed", b"", fixture=run.fixture_dir(task["fixture"]),
                      folder=run.OUTCOME / task["id"])
