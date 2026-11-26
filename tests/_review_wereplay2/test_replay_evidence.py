"""Trace synthetic indexes through a real plan and fake-container replay."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from worked_examples import replay_rc as rc

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
TASK = SimpleNamespace(id="black-5634", repo=str(Path(__file__).resolve().parent / "synthetic-black"), parent="c" * 40)


def archive(root, *, foreign_task=False, check_prefix="check"):
    rows = []
    for stamp, label in (("001", True), ("002", False)):
        run = f"run/{TASK.id}/{stamp}"
        check_bug = "click-3769" if foreign_task else TASK.id
        check = f"{check_prefix}/{check_bug}/{stamp}"
        folder = root / run
        folder.mkdir(parents=True)
        (folder / "final.patch").write_bytes(stamp.encode())
        (folder / "rounds.json").write_text("[]", encoding="utf-8")
        (folder / "prompt.txt").write_text("Issue #496: synthetic issue", encoding="utf-8")
        record = root / check / "run.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"status": "finished", "checks": [
            {"patch": "final", "patch_applied": True, "passes": label}]}), encoding="utf-8")
        rows += [
            {"event": "started", "kind": "run", "task": TASK.id, "run": run},
            {"event": "ended", "run": run, "status": "finished"},
            {"event": "started", "kind": "check", "task": "click-3769" if foreign_task else TASK.id,
             "run": check, "checked_run": run},
            {"event": "ended", "run": check, "status": "finished"},
        ]
    (root / "runs.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")


class Docker:
    def __init__(self):
        self.launches = []

    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "run":
            self.launches.append(args)
        elif args[0] == "exec":
            value = {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, name, script, *args, **kwargs):
        value = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess([], 0, json.dumps(value).encode(), b"")


def test_foreign_task_check_cannot_supply_counted_labels(tmp_path, monkeypatch, capsys):
    source, output = tmp_path / "source", tmp_path / "output"
    archive(source, foreign_task=True)
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *args: (f"g6we-task:{TASK.id}-fake", "unused"))
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    docker = Docker()
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                   docker=docker) == 0
    result = json.loads(capsys.readouterr().out)
    batches = list(output.glob("replay-rc-batch/development/*/run.json"))
    assert len(batches) == 1
    saved = json.loads(batches[0].read_text(encoding="utf-8"))
    assert saved["evaluation"] == result
    assert len(saved["records"]) == len(docker.launches)
    assert result["by_bug"][TASK.id]["hook"]["reviewed"] == 0, result["predictions"]["P3"]


def test_cohort_check_is_not_opened_from_root_index(tmp_path, monkeypatch):
    source = tmp_path / "source"
    archive(source, check_prefix="cohort/unseen/check")
    read = Path.read_bytes
    opened = []

    def record_read(path):
        if "cohort" in path.parts:
            opened.append(path)
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    try:
        rc.enumerate_cases(source, [TASK])
    except ValueError:
        pass
    assert not opened, f"out-of-scope cohort checks were read: {opened}"
