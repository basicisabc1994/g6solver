# SPDX-License-Identifier: Apache-2.0
"""Source omissions stay named through plans, execution and saved-batch scoring."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)
COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64


def seed(root, *, first_rounds=(), first_round_patch=None):
    archive = rc.c.Archive(root)
    runs = []
    for label in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        run.write("final.patch", b"right" if label else b"wrong")
        run.write("prompt.txt", "Issue #496: synthetic issue")
        if label and first_round_patch is not None:
            run.write("rounds/round-1.patch", first_round_patch)
        run.write_json("rounds.json", list(first_rounds) if label else [])
        run.finish({}, "finished")
        check = archive.start("check", TASK.id, checked_run=run.rel)
        check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": label}]}, "finished")
        runs.append(run)
    return archive, runs


class Docker:
    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            value = {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, name, script, *args, **kwargs):
        value = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess([], 0, json.dumps(value).encode(), b"")


@pytest.mark.parametrize("part", ["", "round2", "round3"])
@pytest.mark.parametrize("tail", [b'{"event":', b'{"text":"\xe2\x82'])
def test_short_tail_names_exact_bytes_and_index(tmp_path, part, tail):
    archive = rc.c.Archive(tmp_path / part)
    archive.append({"event": "started", "kind": "run", "task": TASK.id, "run": f"run/{TASK.id}/one"})
    prefix = archive.index.read_bytes()
    archive.index.write_bytes(prefix + tail)
    omissions = []
    rc.enumerate_cases(tmp_path, [TASK], index_excluded=omissions)
    lost = next(row for row in omissions if row.get("index") == f'{part + "/" if part else ""}runs.jsonl')
    assert lost["offset"] == len(prefix)
    assert lost["length"] == len(tail)
    assert lost["reason"] and lost["affects_denominator"] is True


@pytest.mark.parametrize("dry", [True, False])
def test_short_index_is_incomplete_in_plan_batch_and_rescore(tmp_path, monkeypatch, capsys, dry):
    source, output = tmp_path / "source", tmp_path / "output"
    archive, _ = seed(source)
    with archive.index.open("ab") as stream:
        stream.write(b'{"event":"started","kind":"run"')
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *args: (f"g6we-task:{TASK.id}-synthetic", "unused"))
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook",
                    *(["--dry"] if dry else [])], docker=Docker()) == 0
    result = json.loads(capsys.readouterr().out)
    if dry:
        assert result["evaluation"]["predictions"]["P3"]["status"] == "incomplete"
        assert result["evaluation"]["index_excluded"] == result["index_excluded"]
        assert not output.exists()
    else:
        batch = next(output.glob("replay-rc-batch/development/*/run.json"))
        saved = json.loads(batch.read_text(encoding="utf-8"))
        assert saved["evaluation"] == result
        assert result["predictions"]["P3"]["status"] == "incomplete"
        assert result["predictions"]["P3"]["prediction_met"] is None
        assert rc.main(["--score", str(batch)]) == 0
        assert json.loads(capsys.readouterr().out) == {**result,
        "evidence_rule_version": rc.EVIDENCE_RULE_VERSION,
        "saved_evidence_rule_version": rc.EVIDENCE_RULE_VERSION, "evidence_source": "batch"}


@pytest.mark.parametrize("observed_miss", [False, True])
def test_unknown_index_denominator_overrides_every_prediction(tmp_path, observed_miss):
    cases = [{"bug": TASK.id, "digest": "right", "label": "right", "arguments": {},
              "origins": [{"run": "run/black-5634/one", "arguments": {}}]}]
    records = [{**cases[0], "mode": mode, "origin_run": "run/black-5634/one", "status": "finished", "seconds": 1,
                "review": {"ok": True, "assessment": {"outcome": "test_integrity_compromised"
                           if observed_miss else "insufficient_evidence", "completion": {}}}} for mode in rc.MODES]
    lost = [{"index": "round3/runs.jsonl", "offset": 17, "length": 8, "reason": "short final line"}]
    result = rc.evaluate(records, cases, modes=list(rc.MODES), index_excluded=lost, s0="after")
    assert result["index_excluded"] == lost
    assert all(p["status"] == "incomplete" and p["prediction_met"] is None
               for p in result["predictions"].values())


def test_metadata_exclusions_do_not_change_prediction_denominators(tmp_path):
    cases = [{"bug": TASK.id, "digest": label, "label": label} for label in ("right", "wrong")]
    records = [{**case, "mode": "hook", "status": "finished", "seconds": 1,
                "review": {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}}
               for case in cases]
    result = rc.evaluate(records, cases, modes=["hook"], index_excluded=[
        {"run": "distill/G1/one", "reason": "harness distillation", "affects_denominator": False}])
    assert result["predictions"]["P3"]["status"] == "met"


@pytest.mark.parametrize("row", [None, [], 5, {}, {"event": "other"},
                                  {"event": "ended", "status": "finished"},
                                  {"event": "started", "run": "run/black-5634/one"},
                                  {"event": "started", "kind": "run", "task": TASK.id,
                                   "run": "run/black-5634/one", "arm": []},
                                  {"event": "started", "kind": "run", "task": "foreign-1",
                                   "run": "run/black-5634/one"}])
def test_unknown_row_shapes_refuse_before_output(tmp_path, monkeypatch, capsys, row):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    (source / "runs.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT], docker=Docker()) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("replay_rc:") and "runs.jsonl" in captured.err
    assert not captured.out and not output.exists()


def test_blank_index_line_is_named_but_not_a_missing_candidate(tmp_path):
    (tmp_path / "runs.jsonl").write_bytes(b" \t\r\n")
    omissions = []
    assert rc.enumerate_cases(tmp_path, [TASK], index_excluded=omissions) == ([], [])
    assert omissions == [{"index": "runs.jsonl", "offset": 0, "length": 4,
                          "reason": "blank index line", "affects_denominator": False}]


def test_missing_round_folders_and_indices_are_named(tmp_path, monkeypatch, capsys):
    (tmp_path / "runs.jsonl").write_bytes(b"")
    (tmp_path / "round2").mkdir()
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    assert rc.main(["--source", str(tmp_path), "--registered-commit", COMMIT, "--dry"]) == 0
    plan = json.loads(capsys.readouterr().out)
    absent = {row["index"]: row for row in plan["source_inventory"] if row["status"] == "excluded"}
    assert absent["round3/runs.jsonl"]["reason"] == "optional round folder absent"
    assert absent["round2/runs.jsonl"]["reason"] == "index absent from existing round folder"
    assert any(row["index"] == "round2/runs.jsonl" and row["affects_denominator"]
               for row in plan["index_excluded"])


@pytest.mark.parametrize("problem", ["missing patch", "missing input", "nonmatching patch", "invalid input"])
def test_round_evidence_omissions_are_named(tmp_path, problem):
    rounds = [{"round": 1, "input": None if problem == "invalid input" else {}}]
    patch = None if problem == "missing patch" else b"other" if problem == "nonmatching patch" else b"right"
    archive, runs = seed(tmp_path, first_rounds=rounds, first_round_patch=patch)
    run = runs[0]
    cases, _ = rc.enumerate_cases(tmp_path, [TASK])
    case = next(case for case in cases if case.label == "right")
    assert case.origins[0]["review_excluded"]
    assert all(row["reason"] and row["path"].startswith(run.rel) for row in case.origins[0]["review_excluded"])


@pytest.mark.parametrize("rows", [
    [{"event": "ended", "run": "run/black-5634/orphan", "status": "finished"}],
    [{"event": "started", "kind": "run", "task": "foreign-1", "run": "run/foreign-1/one"}],
    [{"event": "started", "kind": "check", "task": TASK.id, "run": "check/black-5634/one",
      "checked_run": "run/black-5634/absent"}],
])
def test_unused_index_rows_are_named(tmp_path, rows):
    (tmp_path / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    omissions = []
    assert rc.enumerate_cases(tmp_path, [TASK], index_excluded=omissions) == ([], [])
    assert omissions and all(row["reason"] and row["run"] == rows[0]["run"] for row in omissions)


@pytest.mark.parametrize("record", [[], {"status": "finished", "checks": [None]},
                                     {"status": "finished", "checks": None}])
def test_malformed_check_records_refuse_cleanly(tmp_path, monkeypatch, capsys, record):
    archive, _ = seed(tmp_path)
    rows = [json.loads(line) for line in archive.index.read_text(encoding="utf-8").splitlines()]
    check = next(row["run"] for row in rows if row.get("kind") == "check")
    (tmp_path / check / "run.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    assert rc.main(["--source", str(tmp_path), "--registered-commit", COMMIT, "--dry"]) == 1
    assert capsys.readouterr().err.startswith("replay_rc:")


def test_omission_without_optional_collector_still_has_named_record(tmp_path):
    (tmp_path / "runs.jsonl").write_bytes(b'{"event":')
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert cases == excluded == []
    assert excluded.index_excluded[0]["reason"]
    assert excluded.index_excluded[0]["index"] == "runs.jsonl"
    assert rc.evaluate([], cases, modes=["hook"], excluded=excluded)["index_excluded"] == excluded.index_excluded


def test_unused_label_result_remains_named_with_a_good_label(tmp_path):
    archive, _ = seed(tmp_path)
    rows = [json.loads(line) for line in archive.index.read_text(encoding="utf-8").splitlines()]
    check = next(row["run"] for row in rows if row.get("kind") == "check")
    path = tmp_path / check / "run.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["checks"].append({"patch": "final", "patch_applied": False, "passes": True})
    path.write_text(json.dumps(record), encoding="utf-8")
    omissions = []
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK], index_excluded=omissions)
    assert len(cases) == 1
    assert len(excluded) == 1 and excluded[0]["reason"] == "no archived label"
    assert any(row.get("result") == 1 and row["run"] == check and row["reason"] for row in omissions)


@pytest.mark.parametrize("problem", ["duplicate event", "duplicate round"])
def test_overwritten_evidence_refuses(tmp_path, monkeypatch, capsys, problem):
    rounds = [{"round": 1, "input": {}}, {"round": 1, "input": {}}] if problem == "duplicate round" else []
    archive, _ = seed(tmp_path, first_rounds=rounds)
    if problem == "duplicate event":
        archive.append(json.loads(archive.index.read_text(encoding="utf-8").splitlines()[0]))
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    assert rc.main(["--source", str(tmp_path), "--registered-commit", COMMIT, "--dry"]) == 1
    assert capsys.readouterr().err.startswith("replay_rc:")


@pytest.mark.parametrize("site", ["index", "check"])
def test_duplicate_json_fields_cannot_silently_replace_source_evidence(tmp_path, monkeypatch, capsys, site):
    archive, _ = seed(tmp_path)
    if site == "index":
        archive.index.write_bytes(b'{"event":"started","event":"ended","run":"run/black-5634/one",'
                                  b'"status":"finished"}\n')
    else:
        rows = [json.loads(line) for line in archive.index.read_text(encoding="utf-8").splitlines()]
        check = next(row["run"] for row in rows if row.get("kind") == "check")
        (tmp_path / check / "run.json").write_bytes(b'{"status":"finished","checks":[],"checks":[]}')
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    assert rc.main(["--source", str(tmp_path), "--registered-commit", COMMIT, "--dry"]) == 1
    assert "duplicate JSON field" in capsys.readouterr().err


def test_index_gap_cannot_trigger_a_whole_denominator_kill():
    case = {"bug": TASK.id, "digest": "right", "label": "right"}
    record = {**case, "mode": "neighbourhood", "status": "finished", "seconds": 400,
              "review": {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}}
    result = rc.evaluate([record], [case], modes=["neighbourhood"], index_excluded=[
        {"index": "runs.jsonl", "offset": 0, "length": 3, "reason": "short final line"}])
    assert result["predictions"]["P7"]["status"] == "incomplete"
    assert result["predictions"]["P7"]["kill"] is not True
