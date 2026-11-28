"""Ordinary per-round oracle results must not make final-patch replay incomplete."""
from __future__ import annotations

import json
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
    # The managed Windows sandbox denies pytest's recursive basetemp cleanup.
    path = TREE / ".bt_rev_wrfu1" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


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


@pytest.mark.parametrize("entry", ["live", "score"])
def test_normal_round_labels_do_not_invalidate_final_patch_denominator(tmp_path, monkeypatch, capsys, entry):
    source, output = tmp_path / "source", tmp_path / "output"
    archive = rc.c.Archive(source)
    for passes in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        patch = b"right patch" if passes else b"wrong patch"
        run.write("final.patch", patch)
        run.write("rounds/round-1.patch", patch)
        run.write_json("rounds/round-1.input.json", {"task": "synthetic issue"})
        run.write_json("rounds.json", [{"round": 1, "input": {"task": "synthetic issue"}}])
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.finish({}, "finished")
        check = archive.start("check", TASK.id, checked_run=run.rel)
        # run.check writes an entry for every round patch, then final.patch.
        check.finish({"checks": [
            {"patch": "round-1", "patch_applied": True, "passes": passes},
            {"patch": "final", "patch_applied": True, "passes": passes},
        ]}, "finished")
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
        f"all final patches reviewed, but P3={prediction['status']}, "
        f"prediction_met={prediction['prediction_met']}; index_excluded={result['index_excluded']}"
    )
