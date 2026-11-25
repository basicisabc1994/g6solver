# SPDX-License-Identifier: Apache-2.0
"""Repair 2: JSON types, batch-wide origins and imported rule dependencies."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from worked_examples import replay_rc as rc

_shared = importlib.util.spec_from_file_location(
    "repair2_shared_archive", Path(__file__).with_name("test_we_replay_saved_trust.py"))
trust = importlib.util.module_from_spec(_shared)
_shared.loader.exec_module(trust)
batch = trust.batch
score = trust.score


@pytest.mark.parametrize("left", [True, 1, 1.0, "1", None, [], {}])
@pytest.mark.parametrize("right", [True, 1, 1.0, "1", None, [], {}])
def test_json_types_are_distinct_at_every_depth(left, right):
    expected = type(left) is type(right)
    for wrap in (lambda v: v, lambda v: [v], lambda v: {"nested": [v]}):
        assert rc._json_equal(wrap(left), wrap(right)) is expected


@pytest.mark.parametrize("mode,prediction", [("agent", "P2"), ("hook", "P3")])
@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
def test_float_timeout_is_not_the_saved_integer(batch, capsys, mode, prediction, source):
    record = next(r for r in batch.saved["records"] if r["mode"] == mode)
    record["arguments"]["timeout_s"] = float(record["arguments"]["timeout_s"])
    if source:
        batch.saved["evidence_rule_version"] = 0
    status, result = score(batch, capsys, source)
    assert status == (0 if source else 2)
    assert result["evidence_source"] == ("source" if source else "unavailable")
    assert result["predictions"][prediction]["status"] == "incomplete"


class RefusingArgumentDocker(trust.Docker):
    """Keep container operations synthetic; use the public review's input refusal."""

    def __call__(self, *args, **kwargs):
        if args[0] != "exec":
            return super().__call__(*args, **kwargs)
        from g6.harness import patch_review as pr

        assignment = ast.parse(kwargs["input"].decode("utf-8")).body[2]
        arguments = json.loads(ast.literal_eval(assignment.value.args[0]))
        # Stop before the public call if these arguments could reach execution.
        with pytest.raises(pr._Refused):
            pr._inputs(arguments["task"], "", [], None, arguments["timeout_s"],
                       arguments["requirements"], search=arguments.get("search"))
        reply = pr.review_patch(**arguments)
        return subprocess.CompletedProcess(args, 0, json.dumps(reply).encode("utf-8"), b"")


@pytest.mark.parametrize("field", ["timeout", "nested-search"])
def test_replay_type_changed_arguments_keep_the_views_origin(batch, tmp_path, field):
    planned = copy.deepcopy(next(c for c in batch.saved["plan"]["cases"] if c["label"] == "wrong"))
    planned["origins"][0]["arguments"]["search"] = {"templates": False}
    case = rc.Case(**{**planned, "patch": batch.source / planned["patch"], "source": batch.source})
    view = rc.agent_views(case)[0]
    origin_run = view.origins[0]["run"]
    view.arguments = copy.deepcopy(view.arguments)
    if field == "timeout":
        view.arguments["timeout_s"] = 600.0
    else:
        view.arguments["search"]["templates"] = 0
    assert view.arguments == view.origins[0]["arguments"]
    fresh = rc.replay_case(view, trust.TASK, "agent", rc.c.Archive(tmp_path / "fresh"),
                           RefusingArgumentDocker(), trust.IMAGE, trust.COMMIT)
    assert fresh["origin_run"] == origin_run
    assert (fresh["status"], fresh["result"]) == ("failed", "no review")
    assert fresh["review"]["ok"] is False
    records = [fresh if r["mode"] == "agent" and r["digest"] == view.digest else r
               for r in batch.saved["records"] if r["mode"] == "agent"]
    result = rc.evaluate(records, batch.saved["plan"]["cases"], modes=["agent"], s0="after")
    assert result["predictions"]["P2"]["status"] == "incomplete"


@pytest.mark.parametrize("origin_count", [0, 2], ids=["absent", "ambiguous"])
def test_replay_without_one_origin_proceeds_but_is_not_trusted(batch, tmp_path, capsys, origin_count):
    planned = copy.deepcopy(next(c for c in batch.saved["plan"]["cases"] if c["label"] == "right"))
    case = rc.Case(**{**planned, "patch": batch.source / planned["patch"], "source": batch.source})
    view = rc.agent_views(case)[0]
    view.origins = copy.deepcopy(case.origins[:origin_count])
    fresh = rc.replay_case(view, trust.TASK, "agent", rc.c.Archive(tmp_path / "fresh"),
                           trust.Docker(), trust.IMAGE, trust.COMMIT)
    assert fresh["origin_run"] is None
    assert (fresh["status"], fresh["result"]) == ("finished", "review")
    batch.saved["records"][0] = fresh
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["predictions"]["P2"]["status"] == "incomplete"


def test_saved_origin_cannot_bind_different_digests(batch, capsys):
    right = next(r for r in batch.saved["records"] if r["mode"] == "agent" and r["label"] == "right")
    wrong = next(r for r in batch.saved["records"] if r["mode"] == "agent" and r["label"] == "wrong")
    cases = {c["digest"]: c for c in batch.saved["plan"]["cases"]}
    origin = next(o for o in cases[right["digest"]]["origins"] if o["run"] == right["origin_run"])
    other = next(o for o in cases[wrong["digest"]]["origins"] if o["run"] == wrong["origin_run"])
    origin["run"] = right["origin_run"] = wrong["origin_run"]
    origin["arguments"] = copy.deepcopy(other["arguments"])
    right["arguments"] = copy.deepcopy(wrong["arguments"])
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["predictions"]["P2"]["status"] == "incomplete"


def test_source_refresh_rejects_batch_wide_origin_conflict(batch, monkeypatch, capsys):
    real_enumerate = rc.enumerate_cases

    def conflicting(*args, **kwargs):
        cases, excluded = real_enumerate(*args, **kwargs)
        assert len(cases) == 2 and cases[0].digest != cases[1].digest
        cases[1].origins[0]["run"] = cases[0].origins[0]["run"]
        return cases, excluded

    batch.saved["evidence_rule_version"] = 0
    monkeypatch.setattr(rc, "enumerate_cases", conflicting)
    status, result = score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["predictions"]["P2"]["status"] == "incomplete"


def test_same_digest_can_keep_multiple_origins(batch, capsys):
    case = next(c for c in batch.saved["plan"]["cases"] if c["label"] == "right")
    assert len(case["origins"]) == 2
    original = copy.deepcopy(batch.saved)
    batch.saved["records"].reverse()
    case["origins"].reverse()
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"]) == (0, "batch")
    assert result["predictions"] == original["evaluation"]["predictions"]


@pytest.mark.parametrize("module_name,name", [("we", "load_tasks"), ("we", "Task"),
                                              ("we", "WORK"), ("we", "TASKS")])
def test_digest_follows_imported_dependencies(tmp_path, monkeypatch, module_name, name):
    guard_path = Path(rc.__file__).parents[2] / "tests/validation/test_we_replay_review4_closure.py"
    spec = importlib.util.spec_from_file_location("repair2_rule_guard", guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    module = getattr(rc, module_name)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            node.body.insert(0, ast.Expr(value=ast.Constant(value="changed dependency")))
            break
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            node.value = ast.Constant(value="changed dependency")
            break
    else:
        pytest.fail(f"missing dependency {name}")
    ast.fix_missing_locations(tree)
    changed = tmp_path / "changed_dependency.py"
    changed.write_text(ast.unparse(tree), encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(changed))
    with pytest.raises(AssertionError, match="evidence rule changed"):
        guard.test_evidence_rule_code_requires_a_version_change()
