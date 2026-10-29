# SPDX-License-Identifier: Apache-2.0
"""Self-maintenance controls: isolated budget, measured bottleneck, no self-promotion."""
import pytest

from g6.core.ledger import ChainLedger
from g6.self_maintenance.kernel import BudgetBook, BudgetPolicy, MaintenanceTicket, allowed_source, rank_tickets
from g6.self_maintenance.constraints import (
    StageMeasurement, identify_constraint, focus_plan, SafetyEnvelope, OutcomeObservation, assess_improvement,
)
from g6.self_maintenance.controller import run_one_cycle


def ticket(ident="issue-1"):
    return MaintenanceTicket(ident, "Fix legitimate user-visible error", "failing regression fixture",
                             ("src/g6/code/swe_diagnostics/read.py",),
                             ("tests/code/test_swe_diagnostics.py",), estimated_tokens=6000)


def stages():
    return (StageMeasurement("review", 20, 10, 5, True),
            StageMeasurement("coding", 20, 19, 20, True))


def test_protected_code_and_tests_refused():
    assert allowed_source("src/g6/code/swe_diagnostics/read.py")
    for p in ("src/g6/metaharness/api.py", "src/g6/core/ledger.py", "tests/test_any.py",
              "src/g6/code/../core/a.py", "src/g6/code/swe_diagnostics/evaluator.py"):
        assert not allowed_source(p)
    with pytest.raises(ValueError):
        MaintenanceTicket("bad", "bad", "fake", ("src/g6/core/ledger.py",), ("tests/test_a.py",))


def test_budget_reservation_and_conservative_settlement():
    book, policy = BudgetBook(), BudgetPolicy(max_calls_per_issue=1)
    day = "2026-10-29"
    reservation = book.reserve(ticket(), "codex", policy, day)
    assert book.usage(day)["tokens"] == reservation.reserved_tokens
    # A claim of zero usage by the model cannot refund tokens.
    book.settle(reservation, day, observed_tokens=0, observed_usd=0, receipt_trusted=False)
    assert book.usage(day)["tokens"] == reservation.reserved_tokens
    with pytest.raises(ValueError):
        book.reserve(ticket(), "codex", policy, day)
    assert book.ledger.verify() == ()


def test_budget_persisted_and_stop_when_limited(tmp_path):
    path = tmp_path / "maintenance.jsonl"
    first = BudgetBook(ChainLedger(path))
    policy = BudgetPolicy(day_tokens=16_000, reserve_tokens=10_000)
    first.reserve(ticket(), "codex", policy, "2026-10-29")
    second = BudgetBook(ChainLedger(path))
    assert second.usage("2026-10-29")["tokens"] == 6000
    assert rank_tickets((ticket("issue-2"),), policy=policy, book=second, day="2026-10-29").selected is None


def test_measured_toc_focus_and_missing_independent_evidence():
    v = identify_constraint(stages())
    assert v.bottleneck == "review"
    plan = focus_plan((ticket(),), stages(), {"issue-1": "review"},
                      book=BudgetBook(), policy=BudgetPolicy(), day="2026-10-29")
    assert plan.selected == "issue-1"
    unknown = identify_constraint((StageMeasurement("review", 10, 8, 5, False),))
    assert unknown.bottleneck == "unknown"


def test_safety_is_not_a_tradable_reward():
    baseline = OutcomeObservation(.9, 0, 0, 0, 0, 2, 10, True, 50)
    candidate = OutcomeObservation(1, .01, 0, 0, 0, 1, 20, True, 50)
    result = assess_improvement(baseline, candidate, SafetyEnvelope(.85))
    assert result.state == "blocked-escalated"
    assert result.reason == "hard_constraint_violated"


def test_operator_disabled_does_not_run_agent_or_checker():
    calls = []
    report = run_one_cycle((ticket(),), stages(), {"issue-1": "review"},
                           book=BudgetBook(), policy=BudgetPolicy(), day="2026-10-29",
                           workspace="/not/a/workspace",
                           agent_runner=lambda **kw: calls.append("agent"),
                           independent_checker=lambda *a: calls.append("check"),
                           operator_enabled=False)
    assert report.reason == "simulation_only"
    assert calls == []


def test_mcp_block_is_readonly():
    from g6.self_maintenance.block import SelfMaintenanceBlock, MaintenanceInput
    out = SelfMaintenanceBlock().infer(MaintenanceInput(
        op="identify_constraint", stages=[
            dict(name="review", attempted=20, succeeded=10, capacity_per_day=5,
                 independently_observed=True)
        ])).value
    assert out["constraint"] == "review"
    assert out["completion_state"] == "qualified-draft"
    assert SelfMaintenanceBlock().infer(MaintenanceInput(op="ops")).value["ops"] == [
        "identify_constraint", "plan"
    ]
