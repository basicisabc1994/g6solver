# SPDX-License-Identifier: Apache-2.0
"""Reporting-only audit measures, on synthetic recorded runs; no trial sessions."""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "validation/routing"
spec = importlib.util.spec_from_file_location("routing_audit_measures_score", HERE / "score.py")
score = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = score
spec.loader.exec_module(score)

BUDGET = {"seconds": 900, "model_calls": 20, "questions": 1}
TASKS = [{"id": name, "fixture_digest": "f" * 64, "checks": ["edge", "ordinary"],
          "defects": ["edge", "ordinary"], "budget": BUDGET} for name in ("one", "two", "a", "b")]


def record(task="one", arm="with_g6", **changes):
    row = {"task_id": task, "client": "codex", "arm": arm, "model": "fixed-model",
           "task_digest": score._digest(next(t for t in TASKS if t["id"] == task)),
           "fixture_digest": "f" * 64, "budget": BUDGET, "started": True, "ended": True,
           "completed": True, "final_diff_sha256": "d" * 64, "seconds": 12.5, "cost_usd": 0.25,
           "model_calls": 3, "final_checks": {"edge": True, "ordinary": True},
           "defect_checks": {"edge": False, "ordinary": False}, "g6_claims": ["edge"],
           "reply_states": ["qualified-draft", "defect_detected"], "history_from": [],
           "g6_history_before_sha256": None, "g6_history_after_sha256": None,
           "events_sha256": "e" * 64}
    return {**row, **changes}


def report(rows, arm="with_g6"):
    return score.audit_report(TASKS[:2], rows, client="codex", arm=arm)


def test_measures_confirmed_missed_false_alarm_time_cost_and_calls():
    rows = [record(), record("two", g6_claims=["edge", "ordinary"],
                             defect_checks={"edge": True, "ordinary": False}, seconds=7.5, cost_usd=None)]
    found = report(rows)
    assert found["tasks"][0]["hidden_check_pass"] is True
    assert found["tasks"][0]["defects_found"] == 1
    assert found["tasks"][0]["defects_missed"] == 1
    assert found["tasks"][1]["false_alarms"] == 1
    assert found["overall"]["defects_found"] == 2
    assert found["overall"]["defects_missed"] == 1
    assert found["overall"]["false_alarms"] == 1
    assert found["overall"]["wall_seconds"] == {"total": 20.0, "observed": 2, "missing": 0}
    assert found["overall"]["cost_usd"] == {"total": 0.25, "observed": 1, "missing": 1}
    assert found["overall"]["model_calls"]["total"] == 6
    assert found["claim"] is None and "bar" not in found


def test_abandonment_distinguishes_empty_kept_diff_unrun_and_missing_checks():
    abandoned = record(final_diff_sha256=None, completed=False, final_checks={})
    empty_kept = record("two", final_diff_sha256=score.hashlib.sha256(b"").hexdigest(), g6_claims=[])
    found = report([abandoned, empty_kept])
    assert found["tasks"][0]["abandoned"] is True
    assert found["tasks"][0]["hidden_check_pass"] is False
    assert found["tasks"][1]["abandoned"] is False
    missing = report([])
    assert missing["overall"]["tasks"] == 2 and missing["overall"]["unrun"] == 2
    assert missing["overall"]["abandoned"] == 0
    assert missing["overall"]["cost_usd"]["total"] is None
    assert all(row["hidden_check_pass"] is None for row in missing["tasks"])
    assert report([record(final_checks={})])["tasks"][0]["hidden_check_pass"] is None


def test_calibration_is_per_session_label_with_full_observed_denominator():
    first = record(reply_states=["qualified-draft", "qualified-draft", "supported_within_scope"])
    second = record("two", final_checks={"edge": False, "ordinary": True},
                    reply_states=["qualified-draft", "blocked-escalated", "insufficient_evidence"])
    buckets = report([first, second])["calibration"]
    assert buckets["qualified-draft"] == {"sessions": 2, "passed": 1, "unknown": 0, "pass_share": 0.5}
    assert buckets["blocked-escalated"]["pass_share"] == 0
    assert buckets["supported_within_scope"]["pass_share"] == 1
    assert buckets["verified"]["sessions"] == 0 and buckets["verified"]["pass_share"] is None
    unexpected = report([record(reply_states=["verified"])])
    assert unexpected["unexpected_verified"] == 1


def test_unknown_adjudication_and_claims_do_not_become_zero_false_alarms():
    found = report([record(g6_claims=["edge", "unmapped"], defect_checks={"ordinary": False})])["tasks"][0]
    assert found["unadjudicated_claims"] == 2 and found["false_alarms"] == 0
    assert found["defects_missed"] == 1 and found["unknown_defects"] == 1


def test_ongoing_or_unrun_sessions_cannot_pass_and_uncertain_budgets_stay_visible():
    ongoing = report([record(ended=False, completed=False)])["tasks"][0]
    assert ongoing["hidden_check_pass"] is None and ongoing["ongoing"] is True
    unknown = report([record(model_calls=None, seconds=None)])["tasks"][0]
    assert unknown["budget_observed"] is False
    exceeded = report([record(model_calls=21)])["tasks"][0]
    assert exceeded["budget_exceeded"] is True
    with pytest.raises(score.TaskError):
        report([record(started=False, ended=False, completed=False)])


def test_task_pairs_report_both_arms_without_changing_release_bar():
    before = report([record(arm="alone", g6_claims=[], reply_states=[])], arm="alone")
    after = report([record()], arm="with_g6")
    paired = score.compare_audit(before, after)
    assert len(paired["pairs"]) == 2
    assert paired["pairs"][0]["before"]["defects_found"] == 0
    assert paired["pairs"][0]["after"]["defects_found"] == 1
    assert paired["claim"] is None and "bar" not in paired
    other = copy.deepcopy(after)
    other["client"] = "claude_code"
    with pytest.raises(score.TaskError, match="pair"):
        score.compare_audit(before, other)


@pytest.mark.parametrize("changes", [
    {"seconds": -1}, {"seconds": float("nan")}, {"cost_usd": True}, {"model_calls": -1},
    {"model_calls": 1.5}, {"final_checks": {"edge": "pass"}}, {"fixture_digest": "a" * 64},
    {"budget": {"seconds": 901}}, {"task_digest": "a" * 64},
])
def test_invalid_observations_are_refused(changes):
    with pytest.raises(score.TaskError):
        report([record(**changes)])


def test_duplicate_sessions_and_claims_are_not_extra_evidence():
    with pytest.raises(score.TaskError, match="duplicate"):
        report([record(), record()])
    assert report([record(g6_claims=["edge", "edge"])])["tasks"][0]["defects_found"] == 1
    with pytest.raises(score.TaskError, match="alone"):
        report([record(arm="alone")], arm="alone")


def test_retention_pairs_b_with_a_history_against_identical_b_fresh():
    pair = {"id": "retention.one", "a": "a", "b": "b"}
    a = record("a", g6_history_after_sha256="a" * 64)
    retained = record("b", "retained", history_from=[score._digest(a)], g6_history_before_sha256="a" * 64)
    fresh = record("b", "fresh", final_checks={"edge": False, "ordinary": True})
    result = score.retention_report(TASKS[2:], [pair], [a, retained, fresh], client="codex")
    assert result["pairs"][0]["retained"]["hidden_check_pass"] is True
    assert result["pairs"][0]["fresh"]["hidden_check_pass"] is False
    assert result["pairs"][0]["a_history"] == score._digest(a)
    assert result["net"] == 1 and result["claim"] is None and "bar" not in result
    assert result["arms"]["retained"]["overall"]["hidden_check_pass"] == 1
    absent = score.retention_report(TASKS[2:], [pair], [], client="codex")
    assert absent["pairs"][0]["complete"] is False and absent["net"] is None
    for bad in ({**retained, "history_from": []}, {**fresh, "history_from": [score._digest(a)]},
                {**fresh, "model": "different-model"}, {**retained, "g6_history_before_sha256": "b" * 64},
                {**fresh, "g6_history_before_sha256": "a" * 64}):
        rows = [a, bad, fresh] if bad["arm"] == "retained" else [a, retained, bad]
        with pytest.raises(score.TaskError, match="history|pair|model"):
            score.retention_report(TASKS[2:], [pair], rows, client="codex")


def test_registration_is_frozen_additive_and_has_four_independent_retention_pairs():
    spec = importlib.util.spec_from_file_location("routing_audit_reader", HERE / "audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    registration = audit.load_registration()
    assert len(registration["pairs"]) >= 4
    for pair in registration["pairs"]:
        a, b = (next(t for t in registration["tasks"] if t["id"] == pair[key]) for key in ("a", "b"))
        assert a["fixture"] == b["fixture"] and a["defect_class"] == b["defect_class"]
        assert a["budget"] == b["budget"]
        assert "g6" not in (a["prompt"] + b["prompt"]).lower()
    assert registration["new_bar"] is None


def test_offline_reader_checks_artifact_bytes_and_overrides_self_reported_outcomes():
    import hashlib
    import json
    import uuid

    spec = importlib.util.spec_from_file_location("routing_audit_reader_evidence", HERE / "audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    # An ordinary folder, not pytest's owner-only basetemp; all files remain in the worktree.
    folder = HERE.parents[1] / ".build" / ("audit-evidence-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)

    def artifact(value):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        digest = hashlib.sha256(data).hexdigest()
        (folder / digest).write_bytes(data)
        return digest

    row = record()
    row["events_sha256"] = artifact(b'{"type":"turn.completed"}\n')
    row["final_diff_sha256"] = artifact(b"a kept diff")
    evidence = {"task_id": "one", "task_digest": row["task_digest"],
                "events_sha256": row["events_sha256"], "final_diff_sha256": row["final_diff_sha256"],
                "reviewed_diff_sha256": artifact(b"reviewed snapshot"),
                "final_checks": {"edge": False, "ordinary": True},
                "defect_checks": row["defect_checks"], "g6_claims": [], "reply_states": []}
    row["adjudication_sha256"] = artifact(evidence)
    assert audit.read_record(folder, row)["final_checks"]["edge"] is False
    with pytest.raises(audit.freeze.FrozenError, match="registration"):
        audit.read_record(folder, row, pins={"checks_digest": "c" * 64})
    (folder / row["adjudication_sha256"]).write_bytes(b"substituted")
    with pytest.raises(audit.freeze.FrozenError, match="digest"):
        audit.read_record(folder, row)
    with pytest.raises(audit.freeze.FrozenError, match="digest"):
        audit.read_record(folder, {**row, "adjudication_sha256": "../elsewhere"})


@pytest.mark.parametrize("client", ("codex", "claude_code"))
def test_reader_derives_exact_labels_from_g6_replies_only(client):
    import json

    spec = importlib.util.spec_from_file_location("routing_audit_labels", HERE / "audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    reply = {"completion_state": "qualified-draft", "result": {"assessment": {"outcome": "defect_detected"}}}
    if client == "codex":
        events = [{"type": "item.completed", "item": {"type": "mcp_tool_call", "tool": "g6_review_patch",
                   "result": {"content": [{"text": json.dumps(reply)}]}}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "verified"}}]
    else:
        events = [{"message": {"content": [{"type": "tool_use", "id": "c", "name": "mcp__g6__g6_review_patch"}]}},
                  {"message": {"content": [{"type": "tool_result", "tool_use_id": "c", "content": json.dumps(reply)}]}},
                  {"type": "result", "result": "verified"}]
    assert audit._reply_labels("\n".join(json.dumps(e) for e in events), client) == ["defect_detected", "qualified-draft"]


def test_offline_command_reports_all_scheduled_tasks_without_running_any_arm(monkeypatch, capsys):
    import json
    import uuid

    spec = importlib.util.spec_from_file_location("routing_audit_cli", HERE / "audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    registration = audit.load_registration()
    folder = HERE.parents[1] / ".build" / ("audit-cli-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    records = folder / "records.json"
    records.write_text(json.dumps({"format": "g6.routing_audit_records/1", "pins": registration["pins"],
                                   "records": []}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["audit.py", "--records", str(records)])
    audit.main()
    result = json.loads(capsys.readouterr().out)
    assert result["clients"]["codex"]["arms"]["alone"]["overall"]["unrun"] == 24
    assert result["clients"]["claude_code"]["retention"]["incomplete_pairs"] == 4


def test_retention_without_kept_a_events_is_not_a_history_control():
    a = record("a", events_sha256=None, g6_history_after_sha256="a" * 64)
    kept = record("b", "retained", history_from=[score._digest(a)], g6_history_before_sha256="a" * 64)
    fresh = record("b", "fresh")
    with pytest.raises(score.TaskError, match="history"):
        score.retention_report(TASKS[2:], [{"id": "r", "a": "a", "b": "b"}], [a, kept, fresh], client="codex")
