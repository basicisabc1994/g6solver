# SPDX-License-Identifier: Apache-2.0
"""RC replay acceptance checks: synthetic archives and fake Docker only."""
from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from pathlib import Path

import pytest

from worked_examples import run as we

COMMIT = "6b8c2567b1b4479e6ad44f8e8f4b4dc0cf8b8e07"
TREE = "1" * 40
IMAGE_ID = "sha256:" + "2" * 64


def harness():
    # Import inside each check so the initial missing harness fails at this line.
    return importlib.import_module("worked_examples.replay_rc")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def add_run(root, stamp, patch=b"patch one\n", *, label=False, bug="black-5634", arm="B", rounds=True):
    rel = f"run/{bug}/{stamp}"
    folder = root / rel
    folder.mkdir(parents=True)
    (folder / "final.patch").write_bytes(patch)
    (folder / "prompt.txt").write_text("Runner instructions\n\nIssue #496: keep blank lines\n\nTask body", encoding="utf-8")
    args = {"repo": "/old/repo", "base": "old-base", "task": "Agent's task", "requirements": [
        {"id": "R1", "text": "keep lines", "checks": ["tests/test_lines.py"]}],
        "search": {"propose": True}, "template_instances": []}
    write_json(folder / "rounds.json", [{"round": 1, "input": args}] if rounds else [])
    if rounds:
        (folder / "rounds").mkdir()
        (folder / "rounds/round-1.patch").write_bytes(patch)
        write_json(folder / "rounds/round-1.input.json", {"tool_input": args})
    write_json(folder / "run.json", {"base_commit": "old-base", "status": "finished"})
    rows = [{"event": "started", "kind": "run", "task": bug, "run": rel, "arm": arm},
            {"event": "ended", "run": rel, "status": "finished"}]
    if label is not None:
        check = f"check/{bug}/{stamp}"
        write_json(root / check / "run.json", {"status": "finished", "checks": [
            {"patch": "final", "patch_applied": True, "passes": label}]})
        rows += [{"event": "started", "kind": "check", "task": bug, "run": check, "checked_run": rel},
                 {"event": "ended", "run": check, "status": "finished"}]
    with (root / "runs.jsonl").open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    return folder


def listing(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class FakeDocker:
    def __init__(self, *, commit=COMMIT, fail="", review=None, cleanup_fails=False):
        self.commit, self.fail, self.cleanup_fails = commit, fail, cleanup_fails
        self.review = review or {"ok": True, "assessment": {"outcome": "insufficient_evidence",
                              "reasons": [], "completion": {}, "report": "Review text"}}
        self.calls = []
        self.programs = []

    def __call__(self, *args, input=None, **kwargs):
        self.calls.append(args)
        code, stdout = 0, b""
        if args[:2] == ("image", "inspect"):
            stdout = json.dumps([{"Id": IMAGE_ID, "Config": {"Labels": {"we.g6.commit": self.commit}}}]).encode()
        elif args[0] == "run":
            code = 1 if self.fail == "container" else 0
        elif args[0] == "cp":
            code = 1 if self.fail == "copy" else 0
        elif args[0] == "rm":
            code = int(self.cleanup_fails)
        elif args[0] == "exec" and input:
            self.programs.append(input.decode())
            code = 1 if self.fail == "review" else 0
            stdout = b"garbage" if self.fail == "json" else json.dumps(self.review).encode()
        return subprocess.CompletedProcess(args, code, stdout, b"fake error" if code else b"")

    def sh(self, name, script, *args, **kwargs):
        self.calls.append(("sh", name, script))
        if "git apply" in script:
            code, stdout = int(self.fail == "patch"), b""
        else:
            code = int(self.fail == "git")
            stdout = json.dumps({"head": we.load_tasks()[0].parent, "tree": TREE,
                                 "parent_tree": TREE, "dirty": 0, "commits": 1, "oracle": False}).encode()
        return subprocess.CompletedProcess([], code, stdout, b"fake failure" if code else b"")


def test_enumerates_final_digests_and_retains_round_arguments(tmp_path):
    rc = harness()
    source = tmp_path / "source"
    add_run(source, "001", b"z\n")
    add_run(source, "002", b"a\n", label=True)
    second = add_run(source / "round2", "003", b"z\n")
    second_round = json.loads((second / "rounds.json").read_text(encoding="utf-8"))
    second_round[0]["input"]["claim"] = "A different archived call"
    write_json(second / "rounds.json", second_round)
    write_json(second / "rounds/round-1.input.json", {"tool_input": second_round[0]["input"]})
    cases, excluded = rc.enumerate_cases(source, we.load_tasks())
    assert not excluded
    assert [c.digest for c in cases] == sorted(hashlib.sha256(x).hexdigest() for x in (b"z\n", b"a\n"))
    assert {c.label for c in cases} == {"right", "wrong"}
    assert all(c.bug == "black-5634" and c.round == 1 and c.arm == "B" for c in cases)
    assert sorted(len(c.origins) for c in cases) == [1, 2]
    assert all(c.arguments["requirements"][0]["checks"] == ["tests/test_lines.py"] for c in cases)
    duplicate = next(c for c in cases if len(c.origins) == 2)
    assert len(rc.agent_views(duplicate)) == 2
    assert len({view.origins[0]["run"] for view in rc.agent_views(duplicate)}) == 2
    assert len({json.dumps(view.arguments, sort_keys=True) for view in rc.agent_views(duplicate)}) == 2
    repeated = [{"bug": view.bug, "digest": view.digest, "label": view.label, "mode": "agent",
                 "origin_run": view.origins[0]["run"], "status": "finished", "seconds": 1,
                 "review": {"ok": True, "assessment": {"outcome": "supported_within_scope", "completion": {}}}}
                for view in rc.agent_views(duplicate)]
    group = rc.evaluate(repeated, cases, modes=["agent"], s0="after")["by_bug"]["black-5634"]["agent"]
    assert group["reviewed"] == 1 and group["wrong_supported"] == 1 and group["completed_calls"] == 2
    # Earlier review arguments must never be applied to a different final patch.
    changed = add_run(source, "004", b"unreviewed final\n")
    (changed / "rounds/round-1.patch").write_bytes(b"earlier\n")
    cases, _ = rc.enumerate_cases(source, we.load_tasks())
    assert next(c for c in cases if c.patch == changed / "final.patch").arguments is None


def test_source_read_only_output_is_new_and_disjoint(tmp_path, monkeypatch):
    rc = harness()
    source, output = tmp_path / "source", tmp_path / "output"
    add_run(source, "001")
    before = listing(source)
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    monkeypatch.setattr(rc, "require_registration", lambda: "registration-digest")
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                   docker=FakeDocker()) == 0
    assert listing(source) == before
    assert isinstance(rc.output_archive(source, tmp_path / "another"), we.c.Archive)
    assert list(output.glob("replay-rc/*/*/run.json"))
    for target in (source, source / "inside", tmp_path, output):
        with pytest.raises(ValueError):
            rc.output_archive(source, target)


@pytest.mark.parametrize("rel", ["final-secret.json", "nested/final-cohort.json", "sealed/x.json",
                                  "nested/SeAlEd/x.json", "../escape.json"])
def test_forbidden_paths_refused_before_open(tmp_path, monkeypatch, rel):
    rc = harness()
    original = Path.open

    def guarded_open(path, *args, **kwargs):
        assert not any(p.lower() == "sealed" for p in path.parts)
        assert not path.name.lower().startswith("final-")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(ValueError):
        rc.Source(tmp_path).read_json(rel)
    # The same boundary applies to index-supplied paths.
    (tmp_path / "runs.jsonl").write_text(json.dumps({"event": "started", "kind": "run", "task": "black-5634",
                                                   "run": rel}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        rc.enumerate_cases(tmp_path, we.load_tasks())


@pytest.mark.parametrize("commit", ["", "0" * 40, COMMIT[:8]])
def test_image_commit_mismatch_refuses_before_container(tmp_path, monkeypatch, commit):
    rc = harness()
    fake = FakeDocker(commit=commit)
    with pytest.raises(ValueError, match="commit"):
        rc.inspect_images(fake, {"black-5634": "g6we-task:black-5634-rc"}, COMMIT)
    assert not any(c[0] == "run" for c in fake.calls)
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, b"", b"missing"))
    with pytest.raises(ValueError, match="registration"):
        rc.require_registration()
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        [], 0, rc.REGISTRATION.read_bytes(), b""))
    assert len(rc.require_registration()) == 64


@pytest.mark.parametrize("failure", ["container", "copy", "git", "patch", "review", "json", "refusal", "cleanup"])
def test_failures_are_saved_no_review_and_counted_nowhere(tmp_path, failure):
    rc = harness()
    source = tmp_path / "source"
    add_run(source, "001")
    cases, _ = rc.enumerate_cases(source, we.load_tasks())
    fake = FakeDocker(fail=failure, cleanup_fails=failure == "cleanup")
    if failure == "refusal":
        fake.review = {"ok": False, "error": "refused"}
    archive = rc.output_archive(source, tmp_path / "output")
    record = rc.replay_case(cases[0], we.load_tasks()[0], "hook", archive, fake, IMAGE_ID, COMMIT)
    assert record["status"] == "failed" and record["result"] == "no review"
    assert record["error"]
    saved = json.loads(next(archive.root.glob("replay-rc/*/*/run.json")).read_text(encoding="utf-8"))
    assert saved == record
    summary = rc.evaluate([record], cases, modes=["hook"])
    group = summary["by_bug"]["black-5634"]["hook"]
    assert group["reviewed"] == 0 and group["no_review"] == 1
    assert group["wrong"] == 0 and group["right"] == 0
    assert summary["predictions"]["P3"]["status"] == "incomplete"


def test_unlabelled_final_is_excluded_and_named(tmp_path):
    rc = harness()
    source = tmp_path / "source"
    folder = add_run(source, "001", label=None)
    cases, excluded = rc.enumerate_cases(source, we.load_tasks())
    assert cases == []
    assert excluded[0]["patch"] == folder.relative_to(source).as_posix() + "/final.patch"
    assert excluded[0]["reason"] == "no archived label"
    # A failed application is no label, even if its check says passes=False.
    add_run(source, "002")
    check = source / "check/black-5634/002/run.json"
    body = json.loads(check.read_text(encoding="utf-8"))
    body["checks"][0]["patch_applied"] = False
    write_json(check, body)
    assert rc.enumerate_cases(source, we.load_tasks())[0] == []


def test_task_image_and_hook_contract_never_send_labels(tmp_path):
    rc = harness()
    source = tmp_path / "source"
    add_run(source, "001")
    cases, _ = rc.enumerate_cases(source, we.load_tasks())
    fake = FakeDocker()
    image = rc.inspect_images(fake, {"black-5634": we.image_names(we.load_tasks()[0], COMMIT)[0]}, COMMIT)
    archive = rc.output_archive(source, tmp_path / "output")
    for mode in ("agent", "hook"):
        result = rc.replay_case(cases[0], we.load_tasks()[0], mode, archive, fake, image["black-5634"], COMMIT)
        assert result["status"] == "finished"
    launches = [c for c in fake.calls if c[0] == "run"]
    assert launches and all(IMAGE_ID in c for c in launches)
    assert all("--network" in c and "none" in c for c in launches)
    assert all("G6_LLM_PROVIDER=none" in c for c in launches)
    assert "tests/test_lines.py" in fake.programs[0]
    assert "tests/test_lines.py" not in fake.programs[1]
    assert '"id": "task"' in fake.programs[1] and '"checks": []' in fake.programs[1]
    assert "Issue #496: keep blank lines" in fake.programs[1]
    assert "old-base" not in "".join(fake.programs)
    assert not any("g6we-oracle" in str(c) or "/oracle/fix.patch" in str(c) for c in fake.calls)
    assert not any("passes" in p or '"label"' in p for p in fake.programs)
    assert rc.neighbourhood_arguments(cases[0], we.load_tasks()[0]) is None


def test_plain_predictions_from_saved_records_and_dry_without_docker(tmp_path, monkeypatch, capsys):
    rc = harness()
    source = tmp_path / "source"
    add_run(source, "001", label=True)
    add_run(source, "002", b"other\n", label=False)
    cases, _ = rc.enumerate_cases(source, we.load_tasks())
    fake = FakeDocker()
    output = tmp_path / "output"
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--dry"], docker=fake) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["by_bug"]["black-5634"]["hook"] == 2
    assert plan["by_bug"]["black-5634"]["agent"] == 2
    assert plan["by_bug"]["black-5634"]["neighbourhood"] == 0
    assert plan["images"]["black-5634"].startswith("g6we-task:")
    assert not fake.calls and not output.exists()
    monkeypatch.delenv("G6_WE_ARCHIVE")
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--dry"], docker=fake) == 0
    capsys.readouterr()
    assert not fake.calls
    records = [{"bug": c.bug, "digest": c.digest, "label": c.label, "mode": "hook", "status": "finished",
                "seconds": 1, "review": {"ok": True, "assessment": {"outcome": "defect_detected",
                                        "reasons": [], "completion": {}}}} for c in cases]
    write_json(tmp_path / "saved.json", records)
    summary = rc.evaluate(json.loads((tmp_path / "saved.json").read_text()), cases, modes=["hook"])
    assert set(summary["predictions"]) == {f"P{n}" for n in range(1, 8)}
    assert summary["predictions"]["P3"]["prediction_met"] is False
    assert summary["predictions"]["P3"]["kill"] is False  # one right defect; kill requires more than two
    assert summary["predictions"]["P4"]["status"] == "disabled"
    assert summary["predictions"]["P1"]["status"] == "incomplete"  # no fix control in this archive
    assert summary["by_bug"]["black-5634"]["hook"]["right_defect"] == 1
    # Mode 3's scorer is usable on saved records while its execution hook is absent.
    mode3 = [{**r, "mode": "neighbourhood", "seconds": 301, "review": {"ok": True, "assessment": {
        "outcome": "insufficient_evidence", "reasons": [], "completion": {"neighbourhood": {
            "cases": [{"class": "symptom_persists"}], "question_candidates": [{"id": "Q"}]}}}}} for r in records]
    third = rc.evaluate(mode3, cases, modes=["neighbourhood"])
    assert third["predictions"]["P5"]["prediction_met"] is True
    assert third["predictions"]["P7"]["kill"] is True
    assert third["predictions"]["P4"]["by_bug"]["black-5634"]["reached_40_percent"] is True
    alarm = [{**records[0], "review": {"ok": True, "assessment": {
        "outcome": "test_integrity_compromised", "reasons": [], "completion": {}}}}]
    # Pick the right patch independently of digest order.
    alarm[0].update(bug=next(c for c in cases if c.label == "right").bug,
                    digest=next(c for c in cases if c.label == "right").digest, label="right")
    assert rc.evaluate(alarm, cases, modes=["hook"])["predictions"]["P1"]["kill"] is True
    wrong = next(c for c in cases if c.label == "wrong")
    accepted = [{"bug": wrong.bug, "digest": wrong.digest, "label": "wrong", "mode": "agent",
                 "origin_run": wrong.origins[0]["run"], "status": "finished", "seconds": 1,
                 "review": {"ok": True, "assessment": {"outcome": "supported_within_scope", "completion": {}}}}]
    assert rc.evaluate(accepted, cases, modes=["agent"], s0="after")["predictions"]["P2"]["kill"] is True
    before = rc.evaluate(accepted, cases, modes=["agent"], s0="before")["predictions"]["P2"]
    assert before["kill"] is False and before["prediction_met"] is None
    # Saved mode 3 records exercise every numerical boundary without a container.
    shaped = [{"bug": bug, "digest": str(i), "label": "wrong", "arguments": None}
              for i, bug in enumerate((*rc.NEIGHBOURS, "black-5521", "click-3678"))]
    def result(case, lead=False, seconds=119):
        return {**case, "mode": "neighbourhood", "status": "finished", "seconds": seconds,
                "review": {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {
                    "neighbourhood": {"cases": [{"class": "agreement lost"}] if lead else [],
                                      "question_candidates": ["Q"] if case["bug"] == "click-3678" else []}}}}}
    successful = [result(case, i < 3) for i, case in enumerate(shaped)]
    scored = rc.evaluate(successful, shaped, modes=["neighbourhood"])["predictions"]
    assert scored["P4"]["prediction_met"] is True and scored["P6"]["prediction_met"] is True
    failing = [result(case, i == 0) for i, case in enumerate(shaped)]
    assert rc.evaluate(failing, shaped, modes=["neighbourhood"])["predictions"]["P4"]["kill"] is True
    for seconds, met, kill in ((119, True, False), (120, False, False), (300, False, False), (301, False, True)):
        p7 = rc.evaluate([result(case, seconds=seconds) for case in shaped], shaped,
                         modes=["neighbourhood"])["predictions"]["P7"]
        assert p7["prediction_met"] is met and p7["kill"] is kill
    rights = [{"bug": "black-5521", "digest": str(i), "label": "right", "arguments": None} for i in range(11)]
    for leads, met, kill in ((5, True, False), (6, False, False), (11, False, True)):
        p5 = rc.evaluate([result(case, i < leads) for i, case in enumerate(rights)], rights,
                         modes=["neighbourhood"])["predictions"]["P5"]
        assert p5["prediction_met"] is met and p5["kill"] is kill
