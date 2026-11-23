# SPDX-License-Identifier: Apache-2.0
"""Repair class checks using synthetic files, saved records and fake processes only."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from worked_examples import replay_rc as rc

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64


@pytest.fixture
def registration(monkeypatch):
    original = rc.REGISTRATION.read_bytes()
    read = Path.read_bytes

    def install(old="", new=""):
        content = original.replace(old.encode(), new.encode()) if old else original
        monkeypatch.setattr(Path, "read_bytes", lambda path: content if path == rc.REGISTRATION else read(path))
        monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k:
                            subprocess.CompletedProcess(a, 0, content, b""))
        return content.replace(b"\r\n", b"\n")

    install()
    return install


def sample(label="wrong", *, mode="hook", outcome="defect_detected", seconds=1, bug="black-5634"):
    case = {"bug": bug, "digest": label, "label": label}
    record = {**case, "mode": mode, "status": "finished", "seconds": seconds,
              "g6_commit": COMMIT, "image_id": IMAGE,
              "review": {"ok": True, "assessment": {"outcome": outcome, "completion": {}}}}
    return case, record


@pytest.mark.parametrize("alias", ["sealed.", "SeAlEd ", "final-cohort.json.", "final-cohort.json "])
def test_forbidden_alias_is_refused_before_io(tmp_path, monkeypatch, alias):
    def no_read(*args, **kwargs):
        pytest.fail("forbidden alias reached an open")

    monkeypatch.setattr(Path, "read_bytes", no_read)
    with pytest.raises(ValueError, match="forbidden"):
        rc.Source(tmp_path).read_bytes(alias + "/checker" if alias.lower().startswith("sealed") else alias)


def test_resolved_forbidden_component_is_refused(tmp_path, monkeypatch):
    alias = tmp_path / "safe" / "checker"
    resolved = tmp_path / "sealed" / "checker"

    @contextmanager
    def opened(path, **kwargs):
        yield rc._Opened(resolved if path == alias else path, None, None)

    monkeypatch.setattr(rc, "_open_handle", opened)
    with pytest.raises(ValueError, match="forbidden"):
        rc.Source(tmp_path).path("safe/checker")


@pytest.mark.skipif(os.name != "nt", reason="Windows path aliases")
@pytest.mark.parametrize("direction", ["inside", "parent"])
@pytest.mark.parametrize("extended", ["source", "output"])
def test_extended_paths_are_disjoint_both_ways(tmp_path, direction, extended):
    source = tmp_path / "source"
    output = source / "child" if direction == "inside" else tmp_path
    source = Path("\\\\?\\" + str(source)) if extended == "source" else source
    output = Path("\\\\?\\" + str(output)) if extended == "output" else output
    with pytest.raises(ValueError, match="disjoint"):
        rc.output_archive(source, output)


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC spelling")
@pytest.mark.parametrize("extended", ["source", "output"])
def test_unc_prefix_cannot_bypass_disjointness(monkeypatch, extended):
    # All filesystem probes are fakes; no share is contacted.
    @contextmanager
    def opened(path, **kwargs):
        yield rc._Opened(rc._absolute_path(path), None, None)

    monkeypatch.setattr(rc, "_open_handle", opened)
    monkeypatch.setattr(Path, "exists", lambda path: False)
    source = Path(r"\\synthetic-host\synthetic-share\archive")
    output = source / "output"
    if extended == "source":
        source = Path("\\\\?\\UNC\\" + str(source)[2:])
    else:
        output = Path("\\\\?\\UNC\\" + str(output)[2:])
    with pytest.raises(ValueError, match="disjoint"):
        rc.output_archive(source, output)


@pytest.mark.skipif(os.name != "nt", reason="Windows short names")
def test_short_name_resolves_to_forbidden_component(tmp_path):
    # Only a synthetic folder is created; no forbidden file is opened.
    folder = tmp_path / "final-synthetic-long-name.json"
    folder.mkdir()
    short = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetShortPathNameW(str(folder), short, len(short))
    if not size or Path(short.value) == folder:
        pytest.skip("volume has no short-name alias")
    with pytest.raises(ValueError, match="forbidden"):
        rc.Source(Path(short.value))


def test_score_forbidden_alias_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("forbidden score was opened"))
    assert rc.main(["--score", str(tmp_path / "final-synthetic.json.")]) == 1
    assert "forbidden" in capsys.readouterr().err


@pytest.mark.parametrize("field,value,reason", [
    ("g6_commit", "c" * 40, "replay record G6 commit differs from plan"),
    ("g6_commit", None, "replay record G6 commit differs from plan"),
    ("image_id", "sha256:" + "d" * 64, "replay record image ID differs from plan"),
    ("image_id", None, "replay record image ID differs from plan"),
])
def test_rescore_refuses_each_mismatched_pin(tmp_path, registration, capsys, field, value, reason):
    case, record = sample()
    record[field] = value
    content = registration()
    batch = {"plan": {"cases": [case], "modes": ["hook"], "registered_commit": COMMIT, "excluded": []},
             "records": [record], "image_ids": {case["bug"]: IMAGE}, "s0": "before",
             "registration_sha256": hashlib.sha256(content).hexdigest()}
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(batch), encoding="utf-8")
    assert rc.main(["--score", str(path)]) == 1
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("digest", [None, "0" * 64])
def test_rescore_refuses_missing_or_different_registration(tmp_path, registration, capsys, digest):
    case, record = sample()
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({
        "plan": {"cases": [case], "modes": ["hook"], "registered_commit": COMMIT},
        "records": [record], "image_ids": {case["bug"]: IMAGE}, "s0": "before",
        "registration_sha256": digest}), encoding="utf-8")
    assert rc.main(["--score", str(path)]) == 1
    assert "saved registration digest differs from committed registration" in capsys.readouterr().err


def test_rescore_carries_exclusions_and_registered_hash(tmp_path, registration, capsys):
    cases, records = zip(sample("wrong"), sample("right", outcome="insufficient_evidence"))
    content = registration()
    digest = hashlib.sha256(content).hexdigest()
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({
        "plan": {"cases": cases, "modes": ["hook"], "registered_commit": COMMIT,
                 "excluded": [{"bug": "black-5634", "patch": "missing", "reason": "no final patch"}]},
        "records": records, "image_ids": {"black-5634": IMAGE}, "s0": "before",
        "registration_sha256": digest}), encoding="utf-8")
    assert rc.main(["--score", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["registration_sha256"] == digest
    assert result["predictions"]["P3"]["status"] == "incomplete"
    assert result["predictions"]["P3"]["missing"] == 1


@pytest.mark.parametrize("old,new,prediction,mode,seconds", [
    ("at most 3 of 130", "at most 0 of 130", "P3", "hook", 1),
    ("under 120 s", "under 1 s", "P7", "neighbourhood", 1),
])
def test_committed_limits_drive_scoring(registration, old, new, prediction, mode, seconds):
    content = registration(old, new)
    cases, records = zip(sample("wrong", mode=mode, seconds=seconds),
                         sample("right", mode=mode, outcome="insufficient_evidence", seconds=seconds))
    result = rc.evaluate(list(records), list(cases), modes=[mode])
    assert result["predictions"][prediction]["prediction_met"] is False
    assert new in result["predictions"][prediction]["prediction"]
    assert result["registration_sha256"] == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("old,new,prediction,mode", [
    ("More than 2 of 113", "More than 0 of 113", "P3", "hook"),
    ("More than 10 of 113", "More than 0 of 113", "P5", "neighbourhood"),
    ("Over 300 s", "Over 0 s", "P7", "neighbourhood"),
])
def test_committed_kill_limits_drive_scoring(registration, old, new, prediction, mode):
    registration(old, new)
    cases, records = zip(sample("wrong", mode=mode), sample("right", mode=mode))
    for record in records:
        record["review"]["assessment"]["completion"] = {
            "neighbourhood": {"cases": [{"class": "symptom_persists"}]}}
    result = rc.evaluate(list(records), list(cases), modes=[mode])["predictions"][prediction]
    assert result["kill"] is True
    assert new in result["kill_criterion"]


@pytest.mark.parametrize("old,new,field,expected", [
    ("at least 40%", "at least 60%", "prediction_met", False),
    ("at least 3 of the 4", "at least 4 of the 4", "prediction_met", False),
    ("fewer than 2 of the 4", "fewer than 4 of the 4", "kill", True),
])
def test_committed_neighbour_limits_drive_scoring(registration, old, new, field, expected):
    registration(old, new)
    cases, records = [], []
    for index, bug in enumerate(rc.NEIGHBOURS):
        for number in range(2):
            case, record = sample(mode="neighbourhood", bug=bug, outcome="insufficient_evidence")
            case["digest"] = record["digest"] = str(number)
            if index < 3 and number == 0:
                record["review"]["assessment"]["completion"] = {
                    "neighbourhood": {"cases": [{"class": "symptom_persists"}]}}
            cases.append(case)
            records.append(record)
    result = rc.evaluate(records, cases, modes=["neighbourhood"])["predictions"]["P4"]
    assert result[field] is expected


def test_committed_right_lead_limit_drives_scoring(registration):
    registration("at most 5 of the 113", "at most 0 of the 113")
    case, record = sample("right", mode="neighbourhood", outcome="insufficient_evidence")
    record["review"]["assessment"]["completion"] = {
        "neighbourhood": {"cases": [{"class": "symptom_persists"}]}}
    result = rc.evaluate([record], [case], modes=["neighbourhood"])["predictions"]["P5"]
    assert result["prediction_met"] is False


@pytest.mark.parametrize("old,new", [
    ("at most 3 of 130", "at most several of 130"),
    ("| P4 |", "| P3 |"),
    ("no integrity alarm", "some integrity alarms"),
    ("Over 300 s", "Often slow"),
])
def test_unparseable_registration_refused(registration, old, new):
    registration(old, new)
    with pytest.raises(ValueError, match="registration"):
        rc.require_registration()


@pytest.mark.parametrize("where", ["record", "result", "nested"])
@pytest.mark.parametrize("error", [None, "", False, "check failed"])
@pytest.mark.parametrize("key", ["error", "errors"])
def test_any_check_error_is_named_exclusion(tmp_path, where, error, key):
    source = tmp_path / "source"
    folder = source / "run/black-5634/001"
    folder.mkdir(parents=True)
    (folder / "final.patch").write_bytes(b"synthetic patch")
    (folder / "rounds.json").write_text("[]", encoding="utf-8")
    (folder / "prompt.txt").write_text("Issue #496: synthetic issue", encoding="utf-8")
    check_path = source / "check/black-5634/001/run.json"
    check_path.parent.mkdir(parents=True)
    result = {"patch": "final", "patch_applied": True, "passes": False}
    check = {"status": "finished", "checks": [result]}
    if where == "nested":
        result["details"] = {key: error}
    else:
        (check if where == "record" else result)[key] = error
    check_path.write_text(json.dumps(check), encoding="utf-8")
    rows = [{"event": "started", "kind": "run", "task": "black-5634", "run": "run/black-5634/001"},
            {"event": "started", "kind": "check", "task": "black-5634", "run": "check/black-5634/001",
             "checked_run": "run/black-5634/001"},
            {"event": "ended", "run": "check/black-5634/001", "status": "finished"}]
    (source / "runs.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    cases, excluded = rc.enumerate_cases(source, rc.we.load_tasks())
    assert cases == []
    assert excluded[0]["reason"] == "errored archived check"


@pytest.mark.parametrize("reason", ["no final patch", "no archived label", "errored archived check"])
def test_exclusions_count_as_missing(registration, reason):
    cases, records = zip(sample("wrong"), sample("right", outcome="insufficient_evidence"))
    excluded = [{"bug": "black-5634", "patch": "run/black-5634/003/final.patch", "reason": reason}]
    result = rc.evaluate(list(records), list(cases), modes=["hook"], excluded=excluded)
    assert result["by_bug"]["black-5634"]["hook"]["missing"] == 1
    assert result["predictions"]["P3"]["status"] == "incomplete"
    assert result["predictions"]["P3"]["missing"] == 1


def test_exclusion_only_bug_is_kept(registration):
    result = rc.evaluate([], [], modes=["hook"], excluded=[
        {"bug": "black-5634", "patch": "missing", "reason": "no final patch"}])
    assert result["by_bug"]["black-5634"]["hook"]["missing"] == 1
    assert result["predictions"]["P3"]["status"] == "incomplete"


def test_exclusion_does_not_hide_observed_kill(registration):
    cases, records = zip(*(sample(str(i), outcome="defect_detected") for i in range(3)))
    cases = [{**case, "label": "right"} for case in cases]
    records = [{**record, "label": "right"} for record in records]
    result = rc.evaluate(records, cases, modes=["hook"], excluded=[
        {"bug": "black-5634", "patch": "missing", "reason": "no archived label"}])
    prediction = result["predictions"]["P3"]
    assert prediction["status"] == "incomplete"
    assert prediction["kill"] is True
