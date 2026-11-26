"""Fake-only regressions for the RC replay review; no Docker or real archive I/O."""
import ctypes
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from worked_examples import replay_rc as rc

COMMIT = "6b8c2567b1b4479e6ad44f8e8f4b4dc0cf8b8e07"
IMAGE = "sha256:" + "2" * 64
ROOT = Path(__file__).absolute().parents[2] / ".build" / "wereplay1-fake"


@pytest.fixture
def memory(monkeypatch):
    files = {}
    reads = []
    original_read = Path.read_bytes
    original_is_file = Path.is_file

    def read(path):
        if path.is_relative_to(ROOT):
            reads.append(path)
            if path not in files:
                raise FileNotFoundError(str(path))
            return files[path]
        return original_read(path)

    def is_file(path):
        return path in files if path.is_relative_to(ROOT) else original_is_file(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "is_file", is_file)
    return files, reads


def put_json(files, path, data):
    files[path] = json.dumps(data).encode()


def add_run(files, source, stamp, *, label=False, error=None):
    bug = "black-5634"
    run = f"run/{bug}/{stamp}"
    check = f"check/{bug}/{stamp}"
    folder = source / run
    files[folder / "final.patch"] = stamp.encode()
    files[folder / "prompt.txt"] = b"Issue #496: keep blank lines\nTask body"
    put_json(files, folder / "rounds.json", [])
    rows = [
        {"event": "started", "kind": "run", "task": bug, "run": run, "arm": "B"},
        {"event": "ended", "run": run, "status": "finished"},
    ]
    if label is not None:
        record = {"status": "finished", "checks": [
            {"patch": "final", "patch_applied": True, "passes": label}]}
        if error:
            record["error"] = error
        put_json(files, source / check / "run.json", record)
        rows += [
            {"event": "started", "kind": "check", "task": bug,
             "run": check, "checked_run": run},
            {"event": "ended", "run": check, "status": "finished"},
        ]
    index = source / "runs.jsonl"
    files[index] = files.get(index, b"") + b"".join(
        json.dumps(row).encode() + b"\n" for row in rows)


@pytest.mark.skipif(os.name != "nt", reason='Windows GetFullPathNameW and extended-drive alias semantics')
def test_dotted_sealed_component_reaches_archive_reads(memory):
    files, reads = memory
    source = ROOT / "source"
    add_run(files, source, "001")
    original = "check/black-5634/001"
    alias = "sealed./checker"
    index = files[source / "runs.jsonl"].decode().replace(original, alias)
    files[source / "runs.jsonl"] = index.encode()
    forbidden = source / alias / "run.json"
    files[forbidden] = files.pop(source / original / "run.json")
    canonical = ctypes.create_unicode_buffer(32768)
    ctypes.windll.kernel32.GetFullPathNameW(
        str(forbidden), len(canonical), canonical, None)
    assert "sealed" in Path(canonical.value).parts
    rc.enumerate_cases(source, rc.we.load_tasks())
    assert forbidden not in reads, f"archive read through sealed alias: {forbidden}"


@pytest.mark.skipif(os.name != "nt", reason='Windows GetFullPathNameW and extended-drive alias semantics')
def test_dotted_final_cohort_score_is_opened(monkeypatch):
    score = ROOT / "final-cohort.json."
    opened = []
    original_open = Path.open

    def fake_open(path, *args, **kwargs):
        if path == score:
            opened.append(path)
            return io.StringIO(json.dumps({
                "records": [], "plan": {"cases": [], "modes": ["hook"]}, "s0": "before"}))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    assert rc.main(["--score", str(score)]) == 0
    assert not opened, f"opened forbidden final-cohort alias: {opened}"


@pytest.mark.parametrize("extended_source", [True, False])
@pytest.mark.skipif(os.name != "nt", reason='Windows GetFullPathNameW and extended-drive alias semantics')
def test_extended_prefix_cannot_bypass_output_containment(extended_source):
    plain_source = ROOT / "archive"
    plain_output = plain_source / "output"
    source = Path("\\\\?\\" + str(plain_source)) if extended_source else plain_source
    output = plain_output if extended_source else Path("\\\\?\\" + str(plain_output))
    with pytest.raises(ValueError, match="disjoint"):
        rc.output_archive(source, output)


def test_committed_prediction_edit_cannot_use_old_scoring(monkeypatch):
    original = rc.REGISTRATION.read_bytes()
    old = rc.PREDICTIONS[2][0]
    new = old.replace("at most 3", "at most 0")
    committed = original.replace(old.encode(), new.encode())
    assert committed != original
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes",
                        lambda path: committed if path == rc.REGISTRATION else read_bytes(path))
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, committed, b""))
    rc.require_registration()
    cases = [
        {"bug": "black-5634", "digest": "wrong", "label": "wrong"},
        {"bug": "black-5634", "digest": "right", "label": "right"},
    ]
    records = [
        {**case, "mode": "hook", "status": "finished", "seconds": 1,
         "review": {"ok": True, "assessment": {
             "outcome": "defect_detected" if case["label"] == "wrong" else "insufficient_evidence",
             "completion": {}}}}
        for case in cases
    ]
    result = rc.evaluate(records, cases, modes=["hook"])["predictions"]["P3"]
    assert result["prediction"] == new and result["prediction_met"] is False, result


def test_check_with_error_cannot_supply_a_label(memory):
    files, _ = memory
    source = ROOT / "source"
    add_run(files, source, "001", error="oracle check failed")
    cases, excluded = rc.enumerate_cases(source, rc.we.load_tasks())
    assert not cases and excluded, f"errored check supplied counted label: {cases}"


class MemoryArchive:
    def __init__(self, root):
        self.root = root
        self.finished = []

    def start(self, kind, task, **meta):
        archive = self

        class Folder:
            def write(self, name, data):
                return archive.root / kind / task / name

            def finish(self, body, status):
                result = {**body, "status": status}
                archive.finished.append(result)
                return result

        return Folder()


class FakeDocker:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, input=None, **kwargs):
        self.calls.append(args)
        payload = b""
        if args[:2] == ("image", "inspect"):
            payload = json.dumps([{"Id": IMAGE, "Config": {
                "Labels": {"we.g6.commit": COMMIT}}}]).encode()
        elif args[0] == "exec":
            payload = json.dumps({"ok": True, "assessment": {
                "outcome": "insufficient_evidence", "completion": {}}}).encode()
        return subprocess.CompletedProcess(args, 0, payload, b"")

    def sh(self, name, script, *args, **kwargs):
        self.calls.append(("sh", name, script))
        payload = b""
        if "git apply" not in script:
            payload = json.dumps({
                "head": rc.we.load_tasks()[0].parent,
                "tree": "1" * 40, "parent_tree": "1" * 40,
                "dirty": 0, "commits": 1, "oracle": False}).encode()
        return subprocess.CompletedProcess([], 0, payload, b"")


@pytest.mark.parametrize("missing", ["label", "patch"])
def test_excluded_candidate_keeps_live_prediction_incomplete(memory, monkeypatch, missing):
    files, _ = memory
    source = ROOT / "source"
    add_run(files, source, "001", label=True)
    add_run(files, source, "002", label=False)
    add_run(files, source, "003", label=None)
    if missing == "patch":
        del files[source / "run/black-5634/003/final.patch"]
    archive = MemoryArchive(ROOT / "output")
    fake = FakeDocker()
    monkeypatch.setenv("G6_WE_ARCHIVE", str(archive.root))
    monkeypatch.setattr(rc, "output_archive", lambda *args: archive)
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                   docker=fake) == 0
    batch = archive.finished[-1]
    assert batch["status"] == "finished"
    assert len(batch["plan"]["excluded"]) == 1
    assert batch["image_ids"]["black-5634"] == IMAGE
    assert len(batch["records"]) == 2
    assert all(r["g6_commit"] == COMMIT and r["image_id"] == IMAGE for r in batch["records"])
    prediction = batch["evaluation"]["predictions"]["P3"]
    assert prediction["status"] == "incomplete", prediction


def test_timeout_control_enters_no_outcome(memory):
    files, _ = memory
    source = ROOT / "source"
    add_run(files, source, "001")
    cases, _ = rc.enumerate_cases(source, rc.we.load_tasks())

    class TimeoutDocker(FakeDocker):
        def __call__(self, *args, **kwargs):
            if args[0] == "exec":
                raise subprocess.TimeoutExpired(args, 2400)
            return super().__call__(*args, **kwargs)

    result = rc.replay_case(cases[0], rc.we.load_tasks()[0], "hook",
                            MemoryArchive(ROOT / "output"), TimeoutDocker(), IMAGE, COMMIT)
    group = rc.evaluate([result], cases, modes=["hook"])["by_bug"]["black-5634"]["hook"]
    assert result["status"] == "failed"
    assert result["result"] == "no review"
    assert group["reviewed"] == group["completed_calls"] == 0
    assert group["failed_calls"] == 1


def test_multiple_call_control_preserves_each_adverse_outcome():
    cases = []
    records = []
    for label in ("right", "wrong"):
        case = {"bug": "black-5634", "digest": label, "label": label, "arguments": {},
                "origins": [{"run": label + str(i), "arguments": {}} for i in range(2)]}
        cases.append(case)
        outcomes = (["test_integrity_compromised", "insufficient_evidence"] if label == "right"
                    else ["supported_within_scope", "defect_detected"])
        for origin, outcome in zip(case["origins"], outcomes):
            records.append({**case, "mode": "agent", "origin_run": origin["run"],
                            "status": "finished", "seconds": 1, "review": {
                                "ok": True, "assessment": {"outcome": outcome, "completion": {}}}})
    result = rc.evaluate(records, cases, modes=["agent"], s0="after")
    group = result["by_bug"]["black-5634"]["agent"]
    assert group["reviewed"] == 2
    assert group["completed_calls"] == 4
    assert group["right_integrity"] == group["wrong_supported"] == group["wrong_defect"] == 1
    assert result["predictions"]["P1"]["kill"] is True
    assert result["predictions"]["P2"]["kill"] is True


def test_rescore_refuses_records_from_other_commit_and_image(monkeypatch, capsys):
    cases = [{"bug": "black-5634", "digest": label, "label": label} for label in ("right", "wrong")]
    records = [
        {**case, "mode": "hook", "status": "finished", "seconds": 1,
         "g6_commit": "f" * 40, "image_id": "sha256:" + "3" * 64,
         "review": {"ok": True, "assessment": {
             "outcome": "insufficient_evidence", "completion": {}}}}
        for case in cases
    ]
    saved = {
        "plan": {"cases": cases, "modes": ["hook"], "registered_commit": COMMIT},
        "image_ids": {"black-5634": IMAGE}, "records": records, "s0": "before",
    }
    path = ROOT / "batch.json"
    original_open = Path.open

    def fake_open(candidate, *args, **kwargs):
        return io.StringIO(json.dumps(saved)) if candidate == path else original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    code = rc.main(["--score", str(path)])
    output = capsys.readouterr().out
    assert code == 1, f"accepted mismatched pins; P3={json.loads(output)['predictions']['P3']}"
