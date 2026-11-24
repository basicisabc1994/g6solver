"""Real-disk review reproductions; all archives and Docker replies are synthetic."""
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
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
    path = TREE / ".bt_rev_wrfu3" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def no_real_host_program(monkeypatch):
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")


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


def seed(root):
    archive = rc.c.Archive(root)
    runs = []
    for passes in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        run.write("final.patch", b"right" if passes else b"wrong")
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.write_json("rounds.json", [])
        run.finish({}, "finished")
        archive.start("check", TASK.id, checked_run=run.rel).finish({"checks": [
            {"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
        runs.append(run)
    archive.start("check", TASK.id, checked_run=runs[0].rel).finish({"checks": [
        {"patch": "base", "patch_applied": True, "passes": False}]}, "finished")


def test_pre_repair_batch_cannot_rescore_missing_final_as_met(tmp_path, monkeypatch, capsys):
    source, output = tmp_path / "source", tmp_path / "old-output"
    seed(source)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *a: (f"g6we-task:{TASK.id}-synthetic", "unused"))
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    args = ["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"]
    assert rc.main([*args, "--dry"]) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["evaluation"]["predictions"]["P3"]["status"] == "incomplete"
    assert any(row.get("final_results") == 0 for row in current["index_excluded"])

    previous = ModuleType("worked_examples._review_previous")
    previous.__file__, previous.__package__ = rc.__file__, "worked_examples"
    monkeypatch.setitem(sys.modules, previous.__name__, previous)
    code = subprocess.run(["git", "show", "45d5744b:validation/worked_examples/replay_rc.py"],
                          cwd=TREE, check=True, capture_output=True).stdout
    exec(compile(code, rc.__file__, "exec"), previous.__dict__)
    assert previous.main(args, docker=Docker()) == 0
    assert json.loads(capsys.readouterr().out)["predictions"]["P3"]["status"] == "met"
    batch = next(output.glob("replay-rc-batch/development/*/run.json"))
    saved = json.loads(batch.read_text(encoding="utf-8"))
    assert saved["status"] == "finished"
    assert saved["plan"]["index_excluded"][0]["affects_denominator"] is False

    code = rc.main(["--score", str(batch)])
    captured = capsys.readouterr()
    if code == 1:
        assert captured.err and not captured.out
    else:
        scored = json.loads(captured.out)["predictions"]["P3"]
        assert scored["status"] == "incomplete" and scored["prediction_met"] is None, (
            f"pre-repair batch rescored with exit={code}, P3={scored['status']}, "
            f"prediction_met={scored['prediction_met']}; current enumeration detects its missing final")
