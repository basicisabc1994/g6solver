# SPDX-License-Identifier: Apache-2.0
"""The maintenance cycle's approvals and its live agent (P1, PR #7; Review Focus 1 and 2): no runner or checker runs
without a person's queue decision bound to exactly this ticket, provider and day; the live agent path refuses before
any runtime exists, even for an approved cycle; and the approver's name is recorded."""
from __future__ import annotations

import pytest

from g6.operator import ActionQueue, OperatorStore
from g6.self_maintenance.codes import LIVE_AGENT_OFF, SelfMaintenanceRefused
from g6.self_maintenance.constraints import StageMeasurement
from g6.self_maintenance.controller import cycle_binding, run_existing_agent, run_one_cycle
from g6.self_maintenance.kernel import BudgetBook, BudgetPolicy, MaintenanceTicket

DAY = "2026-10-29"


def ticket(ident="issue-1"):
    return MaintenanceTicket(ident, "Fix legitimate user-visible error", "failing regression fixture",
                             ("src/g6/code/swe_diagnostics/read.py",), ("tests/code/test_swe_diagnostics.py",),
                             estimated_tokens=6000)


STAGES = (StageMeasurement("review", 20, 10, 5, True), StageMeasurement("coding", 20, 19, 20, True))


@pytest.fixture
def q(tmp_path):
    store = OperatorStore(tmp_path / "operator.db")
    scope = store.for_owner("dana")
    queue = ActionQueue(scope)
    queue.venture = scope.create_venture("maintenance", caps={"model": 1.0, "cash": 0.0})["venture_id"]
    yield queue
    store.close()


def _approved(q, binding, *, decide=True):
    item = q.request_run(q.venture, binding=binding, summary="one maintenance cycle", requested_by="g6:self_maintenance")
    if decide:
        q.approve(q.venture, item["action_id"], reviewer="ana")
    return item["action_id"]


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / "copy"
    (folder / ".git").mkdir(parents=True)
    return folder


def test_the_live_agent_refuses_before_any_runtime_exists(monkeypatch):                  # Review Focus 1
    built = []
    monkeypatch.setattr("g6.agents.agent_codex.CodexRuntime", lambda *a, **k: built.append("codex"))
    monkeypatch.setattr("g6.agents.agent_claude.ClaudeCodeRuntime", lambda *a, **k: built.append("claude"))
    for provider in ("codex", "claude"):
        with pytest.raises(SelfMaintenanceRefused) as refused:
            run_existing_agent(provider=provider, workspace="w", goal="g", allowed_paths=(), deadline_s=1, dry_run=False)
        assert refused.value.code == LIVE_AGENT_OFF == "G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF"
    assert built == []


def test_an_approved_cycle_with_the_live_runner_is_refused_and_settles_its_reserve(q, workspace):
    book = BudgetBook()
    approval = _approved(q, cycle_binding(ticket(), "codex", DAY))
    report = run_one_cycle((ticket(),), STAGES, {"issue-1": "review"}, book=book, policy=BudgetPolicy(), day=DAY,
                           workspace=str(workspace), agent_runner=run_existing_agent,
                           independent_checker=lambda *a: pytest.fail("no checker may run"),
                           queue=q, venture_id=q.venture, approval=approval)
    assert (report.state, report.reason) == ("blocked-escalated", "live_agent_off")
    assert book.usage(DAY)["unsettled"] == []


@pytest.mark.parametrize("approval,reason", [("none", "simulation_only"), ("proposed", "simulation_only"),
                                             ("other_ticket", "approval_mismatch")])   # Review Focus 2
def test_no_runner_or_checker_runs_without_a_matching_approval(q, workspace, approval, reason):
    calls = []
    given = {"none": "",
             "proposed": _approved(q, cycle_binding(ticket(), "codex", DAY), decide=False),
             "other_ticket": _approved(q, cycle_binding(ticket("issue-2"), "codex", DAY))}[approval]
    report = run_one_cycle((ticket(),), STAGES, {"issue-1": "review"}, book=BudgetBook(), policy=BudgetPolicy(),
                           day=DAY, workspace=str(workspace), agent_runner=lambda **kw: calls.append("agent"),
                           independent_checker=lambda *a: calls.append("check"), queue=q, venture_id=q.venture,
                           approval=given)
    assert report.reason == reason and calls == [] and report.approved_by == ""


def test_an_approved_cycle_records_its_approver(q, workspace):
    class Agent:
        state, usage = "qualified-draft", {"tokens": 100}

    def checker(path, t):
        return {"changed_files": list(t.files), "test_integrity": "intact", "verdict": "passed",
                "receipt_ids": ["r-1"]}

    approval = _approved(q, cycle_binding(ticket(), "codex", DAY))
    report = run_one_cycle((ticket(),), STAGES, {"issue-1": "review"}, book=BudgetBook(), policy=BudgetPolicy(),
                           day=DAY, workspace=str(workspace), agent_runner=lambda **kw: Agent(),
                           independent_checker=checker, queue=q, venture_id=q.venture, approval=approval)
    assert (report.state, report.reason, report.approved_by) == ("qualified-draft", "candidate_checked_in_scope", "ana")
    assert report.evidence == ("r-1",)
