"""Synthetic fixtures, with an optional memory fallback; no containers or models."""
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


class FakeDocker:
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
def world(tmp_path, monkeypatch, capsys):
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *a: (f"g6we-task:{TASK.id}-synthetic", "unused"))
    source = tmp_path / "source"
    archive = rc.c.Archive(source)
    for passes in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        patch = b"right" if passes else b"wrong"
        run.write("final.patch", patch)
        run.write("prompt.txt", "Issue #1: synthetic issue")
        run.write("rounds/round-1.patch", patch)
        run.write_json("rounds.json", [{"round": 1, "input": {"task": "synthetic", "requirements": []}}])
        run.finish({}, "finished")
        archive.start("check", TASK.id, checked_run=run.rel).finish({"checks": [
            {"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")

    def replay(name="out"):
        output = tmp_path / name
        monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
        code = rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                       docker=FakeDocker())
        captured = capsys.readouterr()
        path = next(output.glob("replay-rc-batch/development/*/run.json"))
        return code, path, captured

    code, path, captured = replay()
    assert code == 0, captured.err
    live = json.loads(captured.out)
    assert live["predictions"]["P3"]["status"] == "met"
    saved = json.loads(path.read_text(encoding="utf-8"))
    return SimpleNamespace(source=source, path=path, saved=saved, replay=replay, live=live)


def save(world):
    world.path.write_text(json.dumps(world.saved), encoding="utf-8")


def score(world, capsys, source=False):
    args = ["--score", str(world.path)]
    if source:
        args += ["--source", str(world.source)]
    code = rc.main(args)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return code, json.loads(captured.out)


def test_current_record_arguments_must_match_the_plan(world, capsys):
    # A mismatched saved response, without recomputing the saved evaluation.
    world.saved["records"][0]["arguments"]["task"] = "a different task was reviewed"
    save(world)
    code, result = score(world, capsys)
    world.saved["evidence_rule_version"] = 0
    save(world)
    _, refreshed = score(world, capsys, source=True)
    assert refreshed["predictions"]["P3"]["status"] == "incomplete"
    assert (code, result["evidence_source"], result["predictions"]["P3"]["status"]) == (
        2, "unavailable", "incomplete"), (code, result["evidence_source"], result["predictions"]["P3"]["status"])


def test_duplicate_conflicting_cases_are_not_a_writer_batch(world, capsys):
    duplicate = copy.deepcopy(world.saved["plan"]["cases"][0])
    duplicate["label"] = "wrong" if duplicate["label"] == "right" else "right"
    world.saved["plan"]["cases"].insert(0, duplicate)
    save(world)
    code, result = score(world, capsys)
    assert (code, result["evidence_source"]) == (2, "unavailable"), (
        code, result["evidence_source"], result["predictions"]["P3"]["status"])


def test_rule_digest_covers_batch_start_validation(world, tmp_path, monkeypatch, capsys):
    # Mutate only a fixture copy, then run the actual branch guard on that copy.
    index = world.path.parents[3] / "runs.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    start = next(row for row in rows if row.get("event") == "started" and row.get("kind") == "replay-rc-batch")
    index.write_text("".join(json.dumps(row) + "\n" for row in rows if row != start), encoding="utf-8")
    code, result = score(world, capsys)
    assert code == 2 and result["evidence_source"] == "unavailable"
    original = Path(rc.__file__)
    tree = ast.parse(original.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_batch_start")
    function.body = [ast.Return(value=ast.parse(repr(start), mode="eval").body)]
    ast.fix_missing_locations(tree)
    changed = tmp_path / "changed_replay_rc.py"
    changed.write_text(ast.unparse(tree), encoding="utf-8")
    guard_path = original.parents[2] / "tests/validation/test_we_replay_review4_closure.py"
    spec = importlib.util.spec_from_file_location("review4_guard", guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard.test_evidence_rule_code_requires_a_version_change()
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(changed), "exec"), namespace)
    monkeypatch.setattr(rc, "_batch_start", namespace["_batch_start"])
    code, result = score(world, capsys)
    assert code == 0 and result["evidence_source"] == "batch"
    monkeypatch.setattr(rc, "__file__", str(changed))
    with pytest.raises(AssertionError, match="evidence rule changed"):
        guard.test_evidence_rule_code_requires_a_version_change()


@pytest.mark.parametrize("version", [2, float("nan"), "half \ud83d"])
def test_unavailable_versions_emit_standard_json(world, capsys, version):
    world.saved["evidence_rule_version"] = version
    save(world)
    assert rc.main(["--score", str(world.path)]) == 2
    captured = capsys.readouterr()
    def forbidden(value):
        pytest.fail(f"nonstandard JSON constant: {value}")
    result = json.loads(captured.out, parse_constant=forbidden)
    assert result["evidence_source"] == "unavailable"


def test_changed_explicit_source_is_refused(world, capsys):
    world.saved["evidence_rule_version"] = 0
    save(world)
    index = world.source / "runs.jsonl"
    index.write_bytes(index.read_bytes() + b"\n")
    code, result = score(world, capsys, source=True)
    assert code == 2 and result["evidence_source"] == "unavailable"


def test_reordering_does_not_change_the_score(world, capsys):
    world.saved["plan"]["cases"].reverse()
    world.saved["records"].reverse()
    save(world)
    code, result = score(world, capsys)
    assert code == 0 and result["predictions"] == world.live["predictions"]


def test_duplicate_records_and_mixed_start_versions_are_refused(world, capsys):
    original = copy.deepcopy(world.saved)
    world.saved["records"].append(copy.deepcopy(world.saved["records"][0]))
    save(world)
    code, result = score(world, capsys)
    assert code == 2 and result["evidence_source"] == "unavailable"
    world.saved = original
    save(world)
    index = world.path.parents[3] / "runs.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row.get("kind") == "replay-rc-batch":
            row["evidence_rule_version"] = 2
    index.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    code, result = score(world, capsys)
    assert code == 2 and result["evidence_source"] == "unavailable"
