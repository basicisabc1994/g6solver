# SPDX-License-Identifier: Apache-2.0
"""Estimate ranking and optional decomposition stay proposals in the existing queue."""
import json

from g6.core.llm import FakeLLM, NoModel
from g6.operator import propose_tasks, rank


def _task(name, value=6, effort=2, likelihood=0.5, created="2026-10-01T00:00:00Z", basis="declared"):
    return {"task_id": name, "description": name, "created_at": created,
            "value": {"amount": value, "basis": basis}, "effort": {"amount": effort, "basis": basis},
            "likelihood": {"amount": likelihood, "basis": basis}}


def test_work_loop_d1_ranks_the_formula_then_age():
    # Removal makes this fail: `scored.sort(key=lambda item: (-item["score"], item["task"]["created_at"]))` in rank.
    report = rank([_task("new", created="2026-10-02T00:00:00Z"), _task("low", value=1), _task("old")])
    assert [r["task"]["task_id"] for r in report["ranked"]] == ["old", "new", "low"]
    assert report["ranked"][0]["score"] == 1.5
    assert report["ranked"][0]["task"]["value"]["basis"] == "declared"
    assert report["completion_state"] == "qualified-draft"


def test_work_loop_02_no_keyword_override():
    # Removal makes this fail: `score = task.value.amount * task.likelihood.amount / task.effort.amount` in rank.
    report = rank([_task("critical outage markdown down", value=1), _task("ordinary", value=5)])
    assert [r["task"]["task_id"] for r in report["ranked"]] == ["ordinary", "critical outage markdown down"]


def test_estimates_without_a_basis_are_refused():
    # Removal makes this fail: `task = TaskProposal.model_validate(raw)` in rank.
    task = _task("work")
    del task["value"]["basis"]
    report = rank([task])
    assert report["completion_state"] == "blocked-escalated" and report["ranked"] == []
    assert report["warning_card"]["code"] == "G6_E_OPERATOR_ESTIMATE"


def test_model_estimates_need_explicit_inclusion_and_effort_is_not_defaulted():
    # Removal makes this fail: `excluded.append(task.task_id)` in rank.
    assert rank([_task("model", basis="model_estimate")])["ranked"] == []
    assert len(rank([_task("model", basis="model_estimate")], include_estimated=True)["ranked"]) == 1
    assert rank([_task("zero", effort=0)])["completion_state"] == "blocked-escalated"
    assert rank([_task("nan", value=float("nan"))])["completion_state"] == "blocked-escalated"


def _model_reply():
    task = _task("scope the policy", basis="model_estimate")
    return json.dumps({"tasks": [{k: v for k, v in task.items() if k not in {"task_id", "created_at"}}]})


def test_work_loop_01_decomposition_is_a_draft_in_the_existing_queue(queue, venture):
    # Removal makes this fail: `rows = queue.propose_tasks(venture_id, drafts, proposed_by="operator:model")`.
    llm = FakeLLM(default=_model_reply())
    report = propose_tasks(queue, venture, "Prepare the policy summary", llm=llm)
    assert len(llm.calls) == 1 and report["completion_state"] == "qualified-draft"
    [row] = queue.list(venture)
    assert row["status"] == "proposed" and row["action_type"] == "task_proposal"
    assert row["meta"]["value"]["basis"] == "model_estimate"
    assert queue.decisions(venture) == [] and queue.list(venture, status="approved") == []


def test_work_loop_03_nonempty_text_is_not_completed_work(queue, venture):
    # Removal makes this fail: `parsed = _Decomposition.model_validate_json(llm.complete(system, goal))` in propose_tasks.
    report = propose_tasks(queue, venture, "Summarise the policy", llm=FakeLLM(default="Done, everything passed"))
    assert report["completion_state"] == "blocked-escalated" and queue.list(venture) == []
    assert propose_tasks(queue, venture, "Summarise", llm=NoModel())["completion_state"] == "blocked-escalated"
    assert propose_tasks(queue, venture, "Summarise")["completion_state"] == "blocked-escalated"


def test_model_cannot_insert_acceptance_or_declared_estimates(queue, venture):
    # Removal makes this fail: `raise ValueError("model estimates must say model_estimate")` in propose_tasks.
    raw = json.loads(_model_reply())
    raw["tasks"][0]["value"]["basis"] = "declared"
    assert propose_tasks(queue, venture, "Summarise", llm=FakeLLM(default=json.dumps(raw)))["completion_state"] == "blocked-escalated"
    raw["tasks"][0]["status"] = "accepted"
    assert propose_tasks(queue, venture, "Summarise", llm=FakeLLM(default=json.dumps(raw)))["completion_state"] == "blocked-escalated"
    assert queue.list(venture) == []


def test_stopped_operator_does_not_call_the_model(queue, venture, monkeypatch):
    # Removal makes this fail: `if stop.engaged() is not None:` in propose_tasks.
    llm = FakeLLM(default=_model_reply())
    monkeypatch.setenv("G6_STOP", "pause")
    assert propose_tasks(queue, venture, "Summarise", llm=llm)["warning_card"]["code"] == "G6_E_STOPPED"
    assert llm.calls == [] and queue.list(venture) == []


def test_estimates_require_numbers_not_booleans_or_numeric_text():
    # Removal makes this fail: `if type(value) not in (int, float):` in Estimate._number.
    for value in (True, "6"):
        assert rank([_task("invalid", value=value)])["completion_state"] == "blocked-escalated"
