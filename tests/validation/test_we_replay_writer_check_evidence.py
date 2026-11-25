# SPDX-License-Identifier: Apache-2.0
"""Check-local final evidence and the round stems emitted by run.check."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


def final(passes=True):
    return {"patch": "final", "patch_applied": True, "passes": passes}


def run_with_checks(archive, passes, results, *, sibling):
    run = archive.start("run", TASK.id, arm="B")
    run.write("final.patch", b"right" if passes else b"wrong")
    run.write("prompt.txt", "Issue #496: synthetic issue")
    run.write_json("rounds.json", [])
    run.finish({}, "finished")
    if sibling:
        archive.start("check", TASK.id, checked_run=run.rel).finish({"checks": [final(passes)]}, "finished")
    check = archive.start("check", TASK.id, checked_run=run.rel)
    check.finish({"checks": results}, "finished")
    return run, check


def evaluation(cases, excluded, modes=("hook",)):
    records = [{**case.saved(), "mode": mode, "status": "finished", "seconds": 1,
                "review": {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}}
               for case in cases for mode in modes if mode != "agent"]
    return rc.evaluate(records, cases, modes=list(modes), excluded=excluded)


@pytest.mark.parametrize("label", [
    "round-0", "round-01", "ROUND-1", "Round-1", "round-1 ", "round- 1", "round-+1",
    "round-1_0", "round-\u0661", "round-1-extra.part", "round-1--tail", "nested/round-01",
])
def test_round_stems_from_real_writer_are_named_metadata(tmp_path, monkeypatch, label):
    if label.startswith(("ROUND-", "Round-")) and os.name != "nt":
        pytest.skip("case-insensitive Windows writer glob")
    archive = rc.c.Archive(tmp_path)
    archive.start("validate", TASK.id).finish({"fail_to_pass": ["test_fix"], "pass_to_pass": []}, "finished")
    monkeypatch.setattr(rc.we, "_oracle_run", lambda *a, patch, **k: {
        "patch_applied": True, "results": {"test_fix": "passed" if patch.read_bytes() == b"right" else "failed"}})
    for passes in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        run.write("final.patch", b"right" if passes else b"wrong")
        run.write(f"rounds/{label}.patch", b"earlier")
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.write_json("rounds.json", [])
        run.finish({}, "finished")
        written = rc.we.check(SimpleNamespace(archive=archive), TASK, run.rel)
        assert [result["patch"] for result in written["checks"]] == [Path(label).name, "final"]
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert len(cases) == 2 and excluded == []
    # The validation control contributes its started/ended metadata rows, as
    # it did before this repair. Only check results carry a result ordinal.
    assert len(excluded.index_excluded) == 4
    controls = [row for row in excluded.index_excluded if row["kind"] == "validate"]
    assert len(controls) == 2 and {row["event"] for row in controls} == {"started", "ended"}
    assert len({row["run"] for row in controls}) == 1
    assert all(row["run"].startswith(f"validate/{TASK.id}/") and row["reason"] == "oracle validation"
               and "result" not in row for row in controls)
    results = [row for row in excluded.index_excluded if "result" in row]
    assert len(results) == 2 and len({row["run"] for row in results}) == 2
    assert all(row["kind"] == "check" and row["event"] == "started" and row["result"] == 0
               and row["run"].startswith(f"check/{TASK.id}/") and row["reason"] for row in results)
    assert all(row["index"] == "runs.jsonl" and row["offset"] >= 0 and row["length"] > 0
               and row["affects_denominator"] is False for row in excluded.index_excluded)
    assert evaluation(cases, excluded)["predictions"]["P3"]["status"] == "met"


@pytest.mark.parametrize("sibling", [False, True])
@pytest.mark.parametrize("results", [
    [], [{"patch": "base"}], [{"patch": "round-2"}],
    [final(), final()], [final(), final(False)], [final(False), final(False)],
    [final(), {"patch": "final", "patch_applied": False, "passes": True}],
    [final(), {"patch": "final", "patch_applied": True, "passes": "true"}],
], ids=["empty", "base-only", "round-only", "duplicate", "opposite", "wrong-duplicates", "unapplied", "unreadable"])
def test_each_check_requires_exactly_one_final_result(tmp_path, results, sibling):
    archive = rc.c.Archive(tmp_path)
    _, bad = run_with_checks(archive, True, results, sibling=sibling)
    run_with_checks(archive, False, [final(False)], sibling=False)
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    gaps = [row for row in excluded.index_excluded if row["run"] == bad.rel
            and row["reason"] == "archived check does not have exactly one final-patch result"]
    assert len(gaps) == 1
    assert gaps[0]["final_results"] == sum(result.get("patch") == "final" for result in results)
    assert gaps[0]["affects_denominator"] is True
    assert gaps[0]["index"] == "runs.jsonl" and gaps[0]["offset"] >= 0 and gaps[0]["length"] > 0
    assert all(f"{bad.rel}/run.json" not in origin["label_sources"] for case in cases for origin in case.origins)
    conflict = results == [final(), final(False)] or sibling and results == [final(False), final(False)]
    if sibling and not conflict:
        assert len(cases) == 2 and excluded == []
    else:
        assert len(cases) == 1 and len(excluded) == 1
    result = evaluation(cases, excluded, rc.MODES)
    assert result["index_excluded"] == excluded.index_excluded
    assert all(prediction["status"] == "incomplete" and prediction["prediction_met"] is None
               for prediction in result["predictions"].values())
    if conflict:
        assert excluded[0]["reason"] == "conflicting archived labels"


def test_valid_independent_checks_still_conflict(tmp_path):
    archive = rc.c.Archive(tmp_path)
    run, _ = run_with_checks(archive, True, [final()], sibling=False)
    archive.start("check", TASK.id, checked_run=run.rel).finish({"checks": [final(False)]}, "finished")
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert cases == []
    assert excluded[0]["reason"] == "conflicting archived labels"


def test_a_round_stem_collision_cannot_be_final_evidence(tmp_path, monkeypatch):
    archive = rc.c.Archive(tmp_path)
    run_with_checks(archive, True, [final()], sibling=True)
    # The current writer's round prefix and final stem are disjoint. Pin the
    # fail-closed guard in case a later writer changes that relationship.
    monkeypatch.setattr(rc, "_writer_round_label", lambda label: label == "final")
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert cases == [] and excluded[0]["reason"] == "no archived label"
    collisions = [row for row in excluded.index_excluded if row["reason"] == "ambiguous final-patch label"]
    assert len(collisions) == 2 and all(row["affects_denominator"] for row in collisions)
