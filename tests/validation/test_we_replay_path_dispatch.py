# SPDX-License-Identifier: Apache-2.0
"""Keep Path-based synthetic sources compatible with handle-backed reads."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)
ROOT = Path(__file__).absolute().parents[2] / ".build" / "path-dispatch-fake"
COMMIT = "a" * 40
IMAGE = "sha256:" + "2" * 64


@pytest.fixture
def memory(monkeypatch):
    files, reads = {}, []
    original_read, original_is_file = Path.read_bytes, Path.is_file

    def read(path):
        if path.is_relative_to(ROOT):
            reads.append(path)
            if path not in files:
                raise FileNotFoundError(str(path))
            return files[path]
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "is_file", lambda path:
                        path in files if path.is_relative_to(ROOT) else original_is_file(path))
    return files, reads


def add_run(files, *, stamp="001", label=False, missing=None, error=False):
    run, check = f"run/{TASK.id}/{stamp}", f"check/{TASK.id}/{stamp}"
    files[ROOT / run / "final.patch"] = stamp.encode()
    files[ROOT / run / "prompt.txt"] = b"Issue #496: synthetic issue"
    files[ROOT / run / "rounds.json"] = b"[]"
    record = {"status": "finished", "checks": [
        {"patch": "final", "patch_applied": True, "passes": label}]}
    if error:
        record["error"] = "synthetic oracle failure"
    files[ROOT / check / "run.json"] = json.dumps(record).encode()
    rows = [{"event": "started", "kind": "run", "task": TASK.id, "run": run}]
    if missing != "label":
        rows += [{"event": "started", "kind": "check", "task": TASK.id,
                  "run": check, "checked_run": run},
                 {"event": "ended", "run": check, "status": "finished"}]
    if missing == "patch":
        del files[ROOT / run / "final.patch"]
    index = ROOT / "runs.jsonl"
    files[index] = files.get(index, b"") + b"".join(json.dumps(row).encode() + b"\n" for row in rows)


@pytest.mark.parametrize("missing,error,reason", [
    (None, True, "errored archived check"),
    ("label", False, "no archived label"),
    ("patch", False, "no final patch"),
])
def test_path_memory_source_keeps_named_exclusion(memory, missing, error, reason):
    files, reads = memory
    add_run(files, missing=missing, error=error)
    cases, excluded = rc.enumerate_cases(ROOT, [TASK])
    assert [item["reason"] for item in excluded] == [reason]
    assert cases == []
    assert ROOT / "runs.jsonl" in reads


def test_path_memory_source_keeps_eligible_candidate(memory):
    files, _ = memory
    add_run(files)
    cases, excluded = rc.enumerate_cases(ROOT, [TASK])
    assert len(cases) == 1
    assert cases[0].label == "wrong"
    assert excluded == []


@pytest.mark.parametrize("missing", ["label", "patch"])
def test_path_memory_source_live_prediction_stays_incomplete(memory, monkeypatch, capsys, missing):
    files, _ = memory
    add_run(files, stamp="001", label=True)
    add_run(files, stamp="002", label=False)
    add_run(files, stamp="003", missing=missing)
    finished = []

    class Folder:
        def write(self, name, data):
            return ROOT / "output" / name

        def finish(self, body, status):
            result = {**body, "status": status}
            finished.append(result)
            return result

    class Docker:
        def __call__(self, *args, **kwargs):
            payload = b""
            if args[:2] == ("image", "inspect"):
                payload = json.dumps([{"Id": IMAGE, "Config": {
                    "Labels": {"we.g6.commit": COMMIT}}}]).encode()
            elif args[0] == "exec":
                payload = json.dumps({"ok": True, "assessment": {
                    "outcome": "insufficient_evidence", "completion": {}}}).encode()
            return subprocess.CompletedProcess(args, 0, payload, b"")

        def sh(self, name, script, *args, **kwargs):
            state = {"head": TASK.parent, "tree": "1" * 40, "parent_tree": "1" * 40,
                     "dirty": 0, "commits": 1, "oracle": False}
            return subprocess.CompletedProcess([], 0, json.dumps(state).encode(), b"")

    archive = SimpleNamespace(root=ROOT / "output", start=lambda *args, **kwargs: Folder())
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setenv("G6_WE_ARCHIVE", str(archive.root))
    monkeypatch.setattr(rc, "output_archive", lambda *args: archive)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    assert rc.main(["--source", str(ROOT), "--registered-commit", COMMIT,
                    "--mode", "hook"], docker=Docker()) == 0
    capsys.readouterr()
    assert len(finished[-1]["plan"]["excluded"]) == 1
    assert len(finished[-1]["records"]) == 2
    assert all(record["status"] == "finished" for record in finished[-1]["records"])
    assert finished[-1]["image_ids"] == {TASK.id: IMAGE}
    assert finished[-1]["evaluation"]["predictions"]["P3"]["status"] == "incomplete"


def test_path_memory_source_timeout_enters_no_outcome(memory, monkeypatch):
    files, _ = memory
    add_run(files)
    cases, _ = rc.enumerate_cases(ROOT, [TASK])
    assert len(cases) == 1
    finished = []

    class Folder:
        def write(self, name, data):
            return ROOT / "output" / name

        def finish(self, body, status):
            result = {**body, "status": status}
            finished.append(result)
            return result

    class Docker:
        def __call__(self, *args, **kwargs):
            if args[0] == "exec":
                raise subprocess.TimeoutExpired(args, 2400)
            return subprocess.CompletedProcess(args, 0, b"", b"")

        def sh(self, name, script, *args, **kwargs):
            state = {"head": TASK.parent, "tree": "1" * 40, "parent_tree": "1" * 40,
                     "dirty": 0, "commits": 1, "oracle": False}
            return subprocess.CompletedProcess([], 0, json.dumps(state).encode(), b"")

    monkeypatch.setattr(rc, "load_registration", lambda: rc._parse_registration(rc.REGISTRATION.read_bytes()))
    archive = SimpleNamespace(start=lambda *args, **kwargs: Folder())
    result = rc.replay_case(cases[0], TASK, "hook", archive, Docker(), "sha256:" + "2" * 64, "a" * 40)
    group = rc.evaluate([result], cases, modes=["hook"])["by_bug"][TASK.id]["hook"]
    assert result["status"] == "failed"
    assert result["result"] == "no review"
    assert finished == [result]
    assert group["reviewed"] == group["completed_calls"] == 0
    assert group["failed_calls"] == 1
