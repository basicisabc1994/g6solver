# SPDX-License-Identifier: Apache-2.0
"""Saved writer arguments and unique cases are required for source-free scoring."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


class Docker:
    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            value = {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, *args, **kwargs):
        value = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")


@pytest.fixture
def batch(tmp_path, monkeypatch, capsys):
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *a: (f"g6we-task:{TASK.id}-synthetic", "unused"))
    source, output = tmp_path / "source", tmp_path / "output"
    archive = rc.c.Archive(source)
    # Two origins of the right patch keep different agent arguments.
    for number, passes in enumerate((True, False, True)):
        run = archive.start("run", TASK.id, arm="B")
        patch = b"right" if passes else b"wrong"
        run.write("final.patch", patch)
        run.write("prompt.txt", "Issue #1: synthetic issue")
        run.write("rounds/round-1.patch", patch)
        run.write_json("rounds.json", [{"round": 1, "input": {
            "task": f"synthetic origin {number}", "requirements": [], "timeout_s": 600,
            "repo": "/archived/repo", "base": "archived-base"}}])
        run.finish({}, "finished")
        archive.start("check", TASK.id, checked_run=run.rel).finish({"checks": [
            {"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT], docker=Docker()) == 0
    live = json.loads(capsys.readouterr().out)
    assert live["predictions"]["P2"]["status"] == live["predictions"]["P3"]["status"] == "met"
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    return SimpleNamespace(source=source, path=path, live=live,
                           saved=json.loads(path.read_text(encoding="utf-8")))


def score(batch, capsys, source=False):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    argv = ["--score", str(batch.path)]
    if source:
        argv += ["--source", str(batch.source)]
    code = rc.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return code, json.loads(captured.out)


@pytest.mark.parametrize("mode,prediction", [("agent", "P2"), ("hook", "P3")])
@pytest.mark.parametrize("field", ["task", "requirements", "timeout_s", "repo", "base", "extra", "missing"])
@pytest.mark.parametrize("source", [False, True])
def test_record_argument_mismatch_uses_older_route(batch, capsys, mode, prediction, field, source):
    record = next(r for r in batch.saved["records"] if r["mode"] == mode)
    if field == "missing":
        record.pop("arguments")
    else:
        record["arguments"][field] = {
            "task": "different task", "requirements": [{"id": "other", "text": "other", "checks": []}],
            "timeout_s": 1, "repo": "/other/repo", "base": "e" * 40, "extra": True}[field]
    code, result = score(batch, capsys, source)
    assert code == (0 if source else 2)
    assert result["evidence_source"] == ("source" if source else "unavailable")
    assert result["predictions"][prediction]["status"] == "incomplete"
    if source:
        assert any(row["reason"] == "saved review no longer matches current evidence"
                   for row in result["index_excluded"])


def test_agent_arguments_belong_to_the_named_origin(batch, capsys):
    agents = [r for r in batch.saved["records"] if r["mode"] == "agent" and r["label"] == "right"]
    assert len(agents) == 2 and agents[0]["arguments"] != agents[1]["arguments"]
    agents[0]["arguments"] = copy.deepcopy(agents[1]["arguments"])
    code, result = score(batch, capsys)
    assert code == 2 and result["evidence_source"] == "unavailable"
    assert result["predictions"]["P2"]["status"] == "incomplete"


@pytest.mark.parametrize("field", ["label", "task_text", "arguments", "origins", "identical"])
@pytest.mark.parametrize("source", [False, True])
def test_duplicate_planned_identity_uses_older_route(batch, capsys, field, source):
    duplicate = copy.deepcopy(batch.saved["plan"]["cases"][0])
    if field == "label":
        duplicate[field] = "wrong" if duplicate[field] == "right" else "right"
    elif field == "task_text":
        duplicate[field] = "different issue"
    elif field == "arguments":
        duplicate[field]["task"] = "different review"
    elif field == "origins":
        duplicate[field][0]["arguments"]["task"] = "different origin"
    batch.saved["plan"]["cases"].insert(0, duplicate)
    code, result = score(batch, capsys, source)
    assert code == (0 if source else 2)
    assert result["evidence_source"] == ("source" if source else "unavailable")
    if source:
        assert result["predictions"] == batch.live["predictions"]
    else:
        assert result["predictions"]["P3"]["status"] == "incomplete"


def test_matching_arguments_and_reordered_origins_need_no_source(batch, capsys):
    batch.source.rename(batch.source.with_name("moved-source"))
    batch.saved["records"].reverse()
    batch.saved["plan"]["cases"].reverse()
    for case in batch.saved["plan"]["cases"]:
        case["origins"].reverse()
    code, result = score(batch, capsys)
    assert code == 0 and result["evidence_source"] == "batch"
    assert result["predictions"] == batch.live["predictions"]
    assert result["by_bug"][TASK.id]["agent"]["completed_calls"] == 3
    assert result["by_bug"][TASK.id]["hook"]["completed_calls"] == 2


@pytest.mark.parametrize("name", ["_batch_start", "_current_evaluation", "_well_formed_case",
                                  "_well_formed_snapshot", "review_arguments", "agent_views", "score_saved",
                                  "evaluate", "validate_record_pins", "load_registration"])
def test_existing_digest_guard_rejects_trust_function_mutations(tmp_path, monkeypatch, name):
    path = Path(rc.__file__)
    spec = importlib.util.spec_from_file_location(
        "saved_trust_rule_guard", path.parents[2] / "tests/validation/test_we_replay_review4_closure.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard.test_evidence_rule_code_requires_a_version_change()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    function.body = [ast.Return(value=ast.Constant(value=None))]
    ast.fix_missing_locations(tree)
    changed = tmp_path / "changed_replay_rc.py"
    changed.write_text(ast.unparse(tree), encoding="utf-8")
    monkeypatch.setattr(rc, "__file__", str(changed))
    with pytest.raises(AssertionError, match="evidence rule changed"):
        guard.test_evidence_rule_code_requires_a_version_change()
