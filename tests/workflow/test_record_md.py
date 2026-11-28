"""The readable export counts only retained observations."""
from __future__ import annotations

from dataclasses import replace

import pytest

from g6.harness.workflow import lifecycle, policy, record_md
from g6.harness.workflow.record import ProjectRecord
from tests.workflow.kit import OTHER_TASK, PROJECT, TASK, T0, event, sha, started


def review(native, *, failed=(), passed=(), artifact="first", task=TASK):
    return replace(event("a" * 32, "review_finished", native, task=task, seconds=1 if native == "two" else 0,
                         data={"failed_sha256": list(failed), "passed_sha256": list(passed),
                               "reproduced": len(failed), "checks_run": 1, "not_established": 1}),
                   artifact_sha256=sha(artifact))


@pytest.mark.parametrize(("artifact", "task", "passed", "expected"), [
    ("second", TASK, (sha("case"),), 1), ("first", TASK, (sha("case"),), 0),
    ("second", OTHER_TASK, (sha("case"),), 0), ("second", TASK, (sha("renamed"),), 0),
    ("second", TASK, (), 0)])
def test_repairs_need_the_same_case_task_and_a_different_artifact(artifact, task, passed, expected):
    rows = [review("one", failed=(sha("case"),)), review("two", passed=passed, artifact=artifact, task=task)]
    assert record_md.counts(rows)["repaired"] == expected


def test_equal_timestamps_do_not_establish_a_later_review():
    first = review("one", failed=(sha("case"),))
    second = replace(review("two", passed=(sha("case"),), artifact="second"), at=first.at)
    assert record_md.counts([first, second])["repaired"] == 0


def test_coverage_and_findings_are_observations_only():
    rows = [event("a" * 32, "loop_ended", data={"reviews": 2, "reproduced": 1, "repaired": 0,
                 "open": 1, "left_by_agent": 0, "ending": "open", "hook_present": 1}),
            event("b" * 32, "turn_unchecked", data={"reason": "G6_E_NO_BOUNDARY"})]
    shown = record_md.render(rows)
    counts = record_md.counts(rows)
    assert counts["checked"] + counts["unchecked"] == counts["turns"] == 2
    assert "Not labelled by you" in shown and "details no longer kept" in shown
    assert "Value to your work: not measured" in shown
    assert len(shown.splitlines()) <= 60 and shown == record_md.render(rows)


def test_mcp_loop_is_not_an_observed_hook_turn():
    row = event("a" * 32, "loop_ended", data={"reviews": 1, "hook_present": 0, "ending": "clean"})
    c = record_md.counts([row])
    assert c["turns"] == c["checked"] == 0 and c["loops"] == 1


def test_export_inventory_deletion_and_off_notice(home, monkeypatch):
    record = ProjectRecord(PROJECT)
    run = started(record)
    record.append(event(run, "turn_unchecked", data={"reason": "G6_E_NO_BOUNDARY"}))
    path = lifecycle.export(PROJECT, now=T0)
    readable = path.with_name(policy.RECORD_MD)
    assert readable.is_file() and policy.FILES[policy.RECORD_MD] == "exports"
    assert policy.DELETION[policy.RECORD_MD] == "rebuilt"
    lifecycle.delete(PROJECT, scope="run", ident=run, by="fixture", now=T0)
    assert not readable.exists()
    monkeypatch.setenv(policy.RECORD_ENV, "off")
    lifecycle.export(PROJECT, now=T0)
    assert "No record is kept" in readable.read_text(encoding="utf-8")
    assert "G6_WORKFLOW_RECORD=on" in readable.read_text(encoding="utf-8")


def test_four_self_change_lines_require_measurements():
    lines = record_md.self_changes({"current": 3, "recent": [{"generation": 3, "measured": {"before": .5}}]}, {})
    assert len(lines) == 4 and "unmeasured" in lines[0]
    assert "no baseline" in lines[1]
    assert "No self-improvement loop is enrolled here" in record_md.self_changes(None, {})[0]


def test_export_expires_with_inspection(home):
    from datetime import timedelta

    path = lifecycle.export(PROJECT, now=T0)
    lifecycle.expire(PROJECT, now=T0 + timedelta(days=31))
    assert not path.exists() and not path.with_name(policy.RECORD_MD).exists()


def test_export_reply_adds_paths_and_counts_without_repeating_files(home, tmp_path):
    from pathlib import Path

    from g6.mcp import workflow
    from tests.workflow.kit import make_repo

    repo = make_repo(tmp_path / "repo")
    result = workflow.export(str(repo))["result"]
    assert Path(result["record_file"]).is_file()
    assert Path(result["file"]).with_name(policy.RECORD_MD) == Path(result["record_file"])
    assert "counts" in result and "## Coverage" not in str(result)
