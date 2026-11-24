"""Synthetic replay evidence; disk by default, optional in-memory fallback."""
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


class DockerStandIn:
    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            from g6.harness import patch_review as pr

            tree = ast.parse(kwargs["input"].decode())
            assignment = next(n for n in tree.body if isinstance(n, ast.Assign))
            arguments = json.loads(ast.literal_eval(assignment.value.args[0]))
            try:
                pr._inputs(arguments["task"], "", [], None, arguments["timeout_s"],
                           arguments["requirements"], search=arguments.get("search"))
            except pr._Refused:
                # These invalid arguments are refused by the actual public API
                # before repository access, execution, or confinement is needed.
                value = pr.review_patch(**arguments)
                assert not rc.readable_review(value)
            else:
                value = {"ok": True, "assessment": {
                    "outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, *args, **kwargs):
        value = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")


@pytest.fixture
def batch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_LLM_PROVIDER", "none")
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
        run.write_json("rounds.json", [{"round": 1, "input": {
            "task": "synthetic", "requirements": [], "timeout_s": 1,
            "search": {"templates": False}}}])
        run.finish({}, "finished")
        archive.start("check", TASK.id, checked_run=run.rel).finish({"checks": [
            {"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
    output = tmp_path / "output"
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT,
                    "--mode", "agent", "--s0", "after"], docker=DockerStandIn()) == 0
    live = json.loads(capsys.readouterr().out)
    assert live["predictions"]["P2"]["status"] == "met"
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    return SimpleNamespace(source=source, path=path, registration=registration,
                           saved=json.loads(path.read_text(encoding="utf-8")))


def score(batch, capsys, source=False):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    argv = ["--score", str(batch.path)]
    if source:
        argv += ["--source", str(batch.source)]
    status = rc.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return status, json.loads(captured.out)


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
@pytest.mark.parametrize("field", ["timeout", "nested-search"])
def test_json_argument_types_must_match(batch, tmp_path, capsys, field, source):
    record = next(r for r in batch.saved["records"] if r["label"] == "wrong")
    if field == "timeout":
        record["arguments"]["timeout_s"] = True  # True == 1 in Python
    else:
        record["arguments"]["search"]["templates"] = 0  # 0 == False
    planned = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == record["digest"])
    case = rc.Case(**{**planned, "patch": batch.source / planned["patch"], "source": batch.source})
    view = rc.agent_views(case)[0]
    assert record["arguments"] == rc.review_arguments(view, TASK, "agent")
    assert json.dumps(record["arguments"], sort_keys=True) != json.dumps(
        rc.review_arguments(view, TASK, "agent"), sort_keys=True)
    # Replaying precisely those recorded arguments actually produces no review.
    view.arguments = {k: copy.deepcopy(v) for k, v in record["arguments"].items()
                      if k not in ("repo", "base")}
    fresh = rc.replay_case(view, TASK, "agent", rc.c.Archive(tmp_path / "fresh"),
                           DockerStandIn(), IMAGE, COMMIT)
    assert fresh["status"] == "failed" and fresh["result"] == "no review"
    replaced = [fresh if r is record else r for r in batch.saved["records"]]
    fresh_result = rc.evaluate(replaced, batch.saved["plan"]["cases"], modes=["agent"],
                               s0="after", registration=batch.registration)
    assert fresh_result["predictions"]["P2"]["status"] == "incomplete"
    if source:
        batch.saved["evidence_rule_version"] = 0
    status, result = score(batch, capsys, source)
    observed = status, result["evidence_source"], result["predictions"]["P2"]["status"]
    assert result["predictions"]["P2"]["status"] == "incomplete", observed


def test_rule_digest_covers_external_task_loader(batch, tmp_path, monkeypatch, capsys):
    original = Path(rc.we.__file__)
    tree = ast.parse(original.read_text(encoding="utf-8"))
    loader = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "load_tasks")
    # A future edit to the loader changes which parent reaches review_arguments.
    # Modify only a fixture copy, never the checkout or the actual task dataset.
    loader.body.insert(-1, ast.parse("for t in data['tasks']:\n    t['parent'] = '" + TASK.parent + "'").body[0])
    ast.fix_missing_locations(tree)
    changed = tmp_path / "changed_run.py"
    changed.write_text(ast.unparse(tree), encoding="utf-8")
    task_file = tmp_path / "tasks.json"
    task_file.write_text(json.dumps({"tasks": [{
        "id": TASK.id, "project": "synthetic", "full_name": "synthetic/black",
        "licence": "MIT", "pr": 1, "issue": 1, "merge_commit": "d" * 40,
        "parent": "e" * 40, "merged_at": "synthetic", "test_modules": [], "install": []}]}), encoding="utf-8")
    # Both functions use this same synthetic task file and the same repo value.
    monkeypatch.setattr(rc.we, "WORK", "/synthetic")
    namespace = {"Path": Path, "Task": rc.we.Task, "TASKS": task_file, "json": json}
    changed_namespace = dict(namespace)
    exec(compile(ast.Module(body=[loader], type_ignores=[]), str(changed), "exec"), changed_namespace)
    original_loader = next(n for n in ast.parse(original.read_text(encoding="utf-8")).body
                           if isinstance(n, ast.FunctionDef) and n.name == "load_tasks")
    exec(compile(ast.Module(body=[original_loader], type_ignores=[]), str(original), "exec"), namespace)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: namespace["load_tasks"](task_file))
    before, before_result = score(batch, capsys)
    assert (before, before_result["evidence_source"]) == (2, "unavailable")
    monkeypatch.setattr(rc.we, "load_tasks", lambda: changed_namespace["load_tasks"](task_file))
    after, after_result = score(batch, capsys)
    assert (after, after_result["evidence_source"]) == (0, "batch")
    guard_path = Path(rc.__file__).parents[2] / "tests/validation/test_we_replay_review4_closure.py"
    spec = importlib.util.spec_from_file_location("review_digest_guard", guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    with pytest.raises(AssertionError, match="evidence rule changed"):
        guard.test_evidence_rule_code_requires_a_version_change()


def test_one_origin_run_cannot_supply_two_different_final_patches(batch, capsys):
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    wrong = next(r for r in batch.saved["records"] if r["label"] == "wrong")
    assert right["digest"] != wrong["digest"] and right["arguments"] == wrong["arguments"]
    planned = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    # Each archived run has only one final.patch. The two views must not bind
    # different digests to that same run, even when its arguments happen to match.
    planned["origins"][0]["run"] = wrong["origin_run"]
    right["origin_run"] = wrong["origin_run"]
    current_status, current = score(batch, capsys)
    batch.saved["evidence_rule_version"] = 0
    _, refreshed = score(batch, capsys, source=True)
    assert refreshed["evidence_source"] == "source"
    assert refreshed["predictions"]["P2"]["status"] == "incomplete"
    observed = current_status, current["evidence_source"], current["predictions"]["P2"]["status"]
    assert (current_status, current["evidence_source"]) == (2, "unavailable"), observed


@pytest.mark.parametrize("kind", ["pin", "registration"])
def test_control_refusals_still_precede_other_reads(batch, monkeypatch, capsys, kind):
    def forbidden(*args, **kwargs):
        pytest.fail("read or enumeration preceded mandatory refusal")

    monkeypatch.setattr(rc, "_batch_start", forbidden)
    monkeypatch.setattr(rc.we, "load_tasks", forbidden)
    monkeypatch.setattr(rc, "enumerate_cases", forbidden)
    if kind == "pin":
        batch.saved["records"][0]["image_id"] = "sha256:" + "f" * 64
        monkeypatch.setattr(rc, "load_registration", forbidden)
    else:
        batch.saved["registration_sha256"] = "f" * 64
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    assert rc.main(["--score", str(batch.path), "--source", str(batch.source)]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert ("image ID differs" if kind == "pin" else "registration digest differs") in captured.err
    with pytest.raises(ValueError):
        rc.score_saved(batch.saved, batch.registration, source=batch.source, start=batch.path)


@pytest.mark.parametrize("edit", ["ambiguous-origin", "absent-origin", "extra-argument"])
def test_control_origin_and_argument_mismatches_are_incomplete(batch, capsys, edit):
    if edit == "ambiguous-origin":
        case = batch.saved["plan"]["cases"][0]
        case["origins"].append(copy.deepcopy(case["origins"][0]))
        batch.saved["evaluation"] = rc.evaluate(
            batch.saved["records"], batch.saved["plan"]["cases"], modes=["agent"],
            s0="after", registration=batch.registration)
    elif edit == "absent-origin":
        batch.saved["records"][0].pop("origin_run")
    else:
        batch.saved["records"][0]["arguments"]["unexpected"] = "different invocation"
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"], result["predictions"]["P2"]["status"]) == (
        2, "unavailable", "incomplete")
