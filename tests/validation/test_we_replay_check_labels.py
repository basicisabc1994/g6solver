# SPDX-License-Identifier: Apache-2.0
"""Writer check labels stay named without changing final-patch denominators."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


def labelled_source(root, extra):
    archive = rc.c.Archive(root)
    for passes in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        run.write("final.patch", b"right" if passes else b"wrong")
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.write_json("rounds.json", [])
        run.finish({}, "finished")
        check = archive.start("check", TASK.id, checked_run=run.rel)
        check.finish({"checks": [extra, {"patch": "final", "patch_applied": True, "passes": passes}]},
                     "finished")


def score(cases, excluded):
    records = [{**case.saved(), "mode": "hook", "status": "finished", "seconds": 1,
                "review": {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}}
               for case in cases]
    return rc.evaluate(records, cases, modes=["hook"], excluded=excluded)


@pytest.mark.parametrize("label", ["base", "round-1", "round-2", "round-123456789"])
@pytest.mark.parametrize("fields", [{}, {"patch_applied": False, "passes": "unreadable"},
                                    {"patch_applied": True, "passes": False}])
def test_known_nonfinal_labels_are_named_metadata(tmp_path, label, fields):
    # A non-final verdict may contradict the final verdict or have no verdict.
    labelled_source(tmp_path, {"patch": label, **fields})
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert len(cases) == 2 and excluded == []
    assert {case.label for case in cases} == {"right", "wrong"}
    omissions = excluded.index_excluded
    assert len(omissions) == 2
    assert all(row["result"] == 0 and row["reason"] and row["index"] == "runs.jsonl"
               and row["offset"] >= 0 and row["length"] > 0 for row in omissions)
    assert all(row["affects_denominator"] is False for row in omissions)
    result = score(cases, excluded)
    assert result["index_excluded"] == omissions
    assert result["predictions"]["P3"]["status"] == "met"
    assert result["predictions"]["P3"]["prediction_met"] is True


@pytest.mark.parametrize("extra", [
    {}, {"patch": None}, {"patch": 1}, {"patch": []}, {"patch": {}},
    *({"patch": label, "patch_applied": True, "passes": True} for label in (
        "", "unknown", "Round-1", "round-", "round-x", "round-0", "round-01", "round--1",
        "round-1.extra", "round-1\n", "round-\u0661", "base ", "FINAL")),
    {"patch": "final", "patch_applied": False, "passes": True},
    {"patch": "final", "patch_applied": True, "passes": "true"},
])
def test_unknown_or_unusable_final_labels_keep_denominator_uncertain(tmp_path, extra):
    labelled_source(tmp_path, extra)
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert len(cases) == 2 and excluded == []
    assert len(excluded.index_excluded) == 2
    assert all(row["result"] == 0 and row["reason"] and row["affects_denominator"] is True
               for row in excluded.index_excluded)
    result = score(cases, excluded)
    assert result["index_excluded"] == excluded.index_excluded
    assert result["predictions"]["P3"]["status"] == "incomplete"
    assert result["predictions"]["P3"]["prediction_met"] is None


@pytest.mark.parametrize("label", ["base", "round-1"])
def test_known_nonfinal_label_cannot_supply_a_final_label(tmp_path, label):
    archive = rc.c.Archive(tmp_path)
    run = archive.start("run", TASK.id)
    run.write("final.patch", b"unlabelled final")
    run.finish({}, "finished")
    check = archive.start("check", TASK.id, checked_run=run.rel)
    check.finish({"checks": [{"patch": label, "patch_applied": True, "passes": True}]}, "finished")
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert cases == []
    assert excluded == [{"bug": TASK.id, "patch": f"{run.rel}/final.patch", "reason": "no archived label"}]
    assert len(excluded.index_excluded) == 1
    assert excluded.index_excluded[0]["affects_denominator"] is False
