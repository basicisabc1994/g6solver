"""Real-disk writer/replay counterexamples; no model or Docker process runs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

TREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TREE / "validation"))
from worked_examples import replay_rc as rc

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


@pytest.fixture
def tmp_path():
    path = TREE / ".bt_rev_wrfu2" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def no_real_host_program(monkeypatch):
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")


class Docker:
    def __call__(self, *args, **kwargs):
        data = None
        if args[:2] == ("image", "inspect"):
            data = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            data = {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")

    def sh(self, *args, **kwargs):
        data = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")


def writer_source(root, monkeypatch, label):
    archive = rc.c.Archive(root)
    archive.start("validate", TASK.id).finish(
        {"fail_to_pass": ["test_fix"], "pass_to_pass": []}, "finished")

    def oracle(ctx, task, *, patch, **kwargs):
        return {"patch_applied": True, "results": {
            "test_fix": "passed" if patch.read_bytes() == b"right" else "failed"}}

    monkeypatch.setattr(rc.we, "_oracle_run", oracle)
    for patch in (b"right", b"wrong"):
        run = archive.start("run", TASK.id, arm="B")
        run.write("final.patch", patch)
        run.write(f"rounds/{label}.patch", b"earlier patch")
        run.write_json("rounds.json", [])
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.finish({}, "finished")
        result = rc.we.check(SimpleNamespace(archive=archive), TASK, run.rel)
        assert [row["patch"] for row in result["checks"]] == [label, "final"]
        assert result["checks"][0]["passes"] is False


@pytest.mark.parametrize("label", ["round-0", "round-01", "ROUND-1", "round-1 "])
@pytest.mark.parametrize("entry", ["live", "score"])
def test_writer_nonfinal_label_does_not_invalidate_final_verdict(tmp_path, monkeypatch, capsys, label, entry):
    if label == "ROUND-1" and os.name != "nt":
        pytest.skip("case-insensitive Windows glob")
    source, output = tmp_path / "source", tmp_path / "output"
    writer_source(source, monkeypatch, label)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *args: (f"g6we-task:{TASK.id}-synthetic", "unused"))
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                   docker=Docker()) == 0
    live = json.loads(capsys.readouterr().out)
    batch = next(output.glob("replay-rc-batch/development/*/run.json"))
    saved = json.loads(batch.read_text(encoding="utf-8"))
    assert saved["status"] == "finished" and saved["evaluation"] == live
    result = live
    if entry == "score":
        assert rc.main(["--score", str(batch)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == live
    group = result["by_bug"][TASK.id]["hook"]
    assert group["reviewed"] == group["expected"] == 2
    assert group["completed_calls"] == group["expected_calls"] == 2
    assert group["excluded"] == group["missing"] == 0
    prediction = result["predictions"]["P3"]
    assert prediction["status"] == "met" and prediction["prediction_met"] is True, (
        f"writer label {label!r} made fully reviewed P3 {prediction['status']}; "
        f"prediction_met={prediction['prediction_met']}"
    )


@pytest.mark.parametrize("hidden_label", ["base", "round-2"])
@pytest.mark.parametrize("entry", ["live", "score"])
def test_missing_final_in_one_check_cannot_hide_behind_another_check(tmp_path, monkeypatch, capsys,
                                                                 hidden_label, entry):
    source, output = tmp_path / "source", tmp_path / "output"
    writer_source(source, monkeypatch, "round-1")
    archive = rc.c.Archive(source)
    run = next(row["run"] for row in archive.rows() if row.get("kind") == "run")
    # A second independent check contradicts the first check's right label.
    monkeypatch.setattr(rc.we, "_oracle_run", lambda *a, **k: {
        "patch_applied": True, "results": {"test_fix": "failed"}})
    second = rc.we.check(SimpleNamespace(archive=archive), TASK, run)
    assert second["checks"][-1]["patch"] == "final"
    assert second["checks"][-1]["passes"] is False
    check = rc.we.latest(archive, "check", TASK.id)
    assert check["record"] == second
    _, exclusions = rc.enumerate_cases(source, [TASK])
    assert any(row["reason"] == "conflicting archived labels" for row in exclusions)
    # Corrupt only the final result's label, leaving a finished check with no
    # final result. The other check must not certify the missing final label.
    second["checks"][-1]["patch"] = hidden_label
    (check["path"] / "run.json").write_text(json.dumps(second), encoding="utf-8")
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *args: (f"g6we-task:{TASK.id}-synthetic", "unused"))
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                   docker=Docker()) == 0
    result = json.loads(capsys.readouterr().out)
    batch = next(output.glob("replay-rc-batch/development/*/run.json"))
    saved = json.loads(batch.read_text(encoding="utf-8"))
    assert saved["status"] == "finished" and saved["evaluation"] == result
    if entry == "score":
        assert rc.main(["--score", str(batch)]) == 0
        result = json.loads(capsys.readouterr().out)
    prediction = result["predictions"]["P3"]
    assert prediction["status"] == "incomplete" and prediction["prediction_met"] is None, (
        f"finished check without any final label scored P3={prediction['status']}, "
        f"prediction_met={prediction['prediction_met']}"
    )
