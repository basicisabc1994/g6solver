# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010 WF-016: ``selection_precision`` counted execution, not precision.

The archive's ``activation._precision`` was "selected routes that ran over routes selected": a mission whose every
selected route ran, each the wrong one, read precision 1.0. ``_archive_precision`` below is that computation;
``test_null_wf016`` puts it back in memory (monkeypatching only) and shows the regression test fails on it. The last test
runs the ported report over a real coding mission's store, opened read only.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from g6.workforce import activation, api, service
from g6.workforce.missions import MissionStore
from g6.workforce.store import VentureStore


def _mission(mid: str, picks: list[tuple[str, str, str]]) -> SimpleNamespace:
    """A finished mission's rows as the report reads them: one scheduler decision per (unit, kind, selected route), and
    every selected route ran (the do step produced an output, the check executed)."""
    units = [SimpleNamespace(key=key, kind=kind) for key, kind, _ in picks]
    decisions = [{"work_unit_id": key, "selected_option": {"id": chosen}, "metadata": {}} for key, _, chosen in picks]
    return SimpleNamespace(mid=mid, rows={
        "schedule": [{"detail": {"decisions": decisions}}], "plans": [{"plan": SimpleNamespace(units=units)}],
        "runs": [{"unit_key": key, "state": "closed", "artifact_id": f"artifact-{key}"} for key, kind, _ in picks
                 if kind == "do"],
        "verdicts": [{"unit_key": key, "executed": True} for key, kind, _ in picks if kind == "verify"],
        "decided": []})


READ = [_mission("m1", [("patch", "do", "patch:chat"), ("check-patch", "verify", "check-patch:planned")]),
        _mission("m2", [("patch", "do", "patch:planned")])]
#: The ground truth says each selection above chose the wrong route.
WRONG = {"m1": {"patch": "patch:person", "check-patch": "check-patch:chat"}, "m2": {"patch": "patch:chat"}}


def _archive_precision(read, labels=None):
    """The archive's metric (activation.py:305-311): selected routes that ran over routes selected."""
    picks = [(r.mid, activation._ran(r, decision)) for r in read for event in r.rows.get("schedule") or []
             for decision in event["detail"].get("decisions") or []]
    return activation._rate(sum(ok for _, ok in picks), len(picks), list(dict.fromkeys(m for m, _ in picks)),
                            "executed", "no_selection")


def test_wf016_every_selected_route_ran_but_was_wrong_reports_no_precision():
    unlabelled = activation._precision(READ, {})
    assert unlabelled["status"] == activation.UNMEASURED and unlabelled["value"] is None
    executed = unlabelled["selections_executed"]
    assert executed["numerator"] == executed["denominator"] == 3 and "value" not in executed
    labelled = activation._precision(READ, WRONG)
    assert labelled["status"] == activation.MEASURED and labelled["value"] == 0.0
    assert (labelled["numerator"], labelled["denominator"]) == (0, 3)


def test_null_wf016(monkeypatch):
    monkeypatch.setattr(activation, "_precision", _archive_precision)
    with pytest.raises(AssertionError):
        test_wf016_every_selected_route_ran_but_was_wrong_reports_no_precision()


def test_wf016_a_ground_truth_labelled_report_gives_a_real_precision():
    """Survival control: one right, one wrong, one unlabelled selection: precision 1 of 2, the third not counted."""
    found = activation._precision(READ, {"m1": {"patch": "patch:chat", "check-patch": "check-patch:chat"}})
    assert (found["status"], found["value"], found["numerator"], found["denominator"]) == (activation.MEASURED, 0.5,
                                                                                         1, 2)
    assert "1 other selection(s) carried no label" in found["why"]
    assert found["selections_executed"]["denominator"] == 3


def _commit(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                                "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_wf016_the_report_reads_a_real_mission(tmp_path, monkeypatch):
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    monkeypatch.delenv("G6_WORKFORCE_CERTIFICATE_POLICY", raising=False)
    wf = api.bind()
    _commit(api.repository_dir(wf) / "sample", {
        "policy.py": 'def label():\n    return "wrong"\n',
        "tests/test_objective.py": 'from policy import label\n\ndef test_label():\n    assert label() == "ready"\n'})
    api.allow_execution(wf, "sample", lambda lines: "report-test approver")
    made = api.create_coding_mission(wf, "Fix the label and preserve its behavior.", "sample", module="policy.py",
                                     function="label", risk="R0")
    mid = made["mission_id"]
    api.confirm(wf, mid, lambda lines: "report-test approver")
    done = api.submit_patch(wf, mid, {"policy.py": 'def label():\n    return "ready"\n'})
    assert done["status"] == "done", done

    reader = MissionStore.reader(VentureStore.reader(Path(wf.db_dir) / "venture.db").for_owner(wf.missions.owner))
    found = activation.report(reader)
    assert found["missions"]["finished"] == 1 and list(found["metrics"]) == list(activation.METRICS)
    precision = found["metrics"]["selection_precision"]
    assert precision["status"] == activation.UNMEASURED and precision["value"] is None
    assert precision["selections_executed"]["denominator"] > 0 and precision["selections_executed"]["sources"] == [mid]
    assert found["metrics"]["completion_state_precision"]["status"] == activation.UNMEASURED

    decisions = [d for e in wf.missions.events(mid, kind="schedule") for d in e["detail"].get("decisions") or []]
    wrong = {mid: {d["work_unit_id"]: d["selected_option"]["id"] + ":another" for d in decisions}}
    labelled = activation.report(reader, route_labels=wrong)["metrics"]["selection_precision"]
    assert (labelled["status"], labelled["value"]) == (activation.MEASURED, 0.0)
    right = {mid: {d["work_unit_id"]: d["selected_option"]["id"] for d in decisions}}
    assert activation.report(reader, route_labels=right)["metrics"]["selection_precision"]["value"] > 0
