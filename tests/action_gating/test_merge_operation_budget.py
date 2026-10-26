# SPDX-License-Identifier: Apache-2.0
"""Count caps replace the archive's invented probabilities and fail-open decorator."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from g6.action_gating import ActionGatingBlock, ActionGatingInput, OperationBudget
from g6.action_gating.operation_budget import BudgetRefused
from g6.core.ledger import ChainLedger
from g6.core.registry import get_registry
from g6.mcp import invoke_reply


def test_align_csf_01_csf004_d1_thousand_calls_exhaust_one_run():
    # Removal oracle: OperationBudget.check's "allowed = remaining > 0" reservation condition.
    ledger = ChainLedger()
    budget = OperationBudget({"compute": 7}, ledger=ledger)
    called = []
    @budget.guarded("compute")
    def compute(*, run_id):
        called.append(run_id)
        return len(called)
    refused = 0
    for _ in range(1000):
        try:
            compute(run_id="run-A")
        except BudgetRefused:
            refused += 1
    assert len(called) == 7 and refused == 993
    assert budget.report("run-A")["remaining"]["compute"] == 0
    assert len([e for e in ledger.entries() if e["kind"] == "operation_check"]) == 1000
    assert ledger.verify() == ()
    assert compute(run_id="run-B") == 8


@pytest.mark.parametrize("failure", ["missing", "check", "ledger", "malformed"])
def test_align_csf_02_csf005_dependency_failure_never_calls_body(failure, monkeypatch):
    # Removal oracle: guarded's failed-check refusal before fn(*args, **kwargs).
    ledger = ChainLedger()
    budget = OperationBudget(None if failure == "missing" else {"compute": 1}, ledger=ledger)
    def broken(*args, **kwargs):
        raise OSError("unavailable")
    if failure == "check":
        monkeypatch.setattr(budget, "check", broken)
    elif failure == "ledger":
        monkeypatch.setattr(ledger, "append", broken)
    elif failure == "malformed":
        monkeypatch.setattr(budget, "check", lambda *a: {"decision": "allow"})
    called = []
    @budget.guarded("compute")
    def work(*, run_id):
        called.append(True)
    with pytest.raises(BudgetRefused):
        work(run_id="r")
    assert called == []


def test_align_csf_03_counts_have_no_invented_probability():
    # Removal oracle: OperationBudget.check returns the remaining integer after charging the reservation.
    budget = OperationBudget({"network_request": 2}, ledger=ChainLedger())
    one = budget.check("r", "network_request")
    assert one.decision == "allow" and one.remaining == 1 and one.completion_state == "qualified-draft"
    data = one.to_envelope()
    assert data["confidence"] is None
    assert not {"p_unsafe", "epsilon", "vub"} & data.keys()
    assert data["gate_decision"]["risk"]["confidence"] is None
    assert data["gate_decision"]["risk"]["aggregate_risk"] == "unknown"
    unknown = budget.check("r", "not_an_operation")
    assert unknown.decision == "block" and unknown.code == "G6_E_OPERATION_UNKNOWN"


def test_align_csf_04_policy_is_sensitive_and_dry_run_by_default():
    # Removal oracle: g6.action_gating.__g6_sensitive_ops__ contains budget_policy.
    reply = invoke_reply("action_gating", "budget_policy", '{"policy":{"limits":{"compute":3}}}')
    assert reply["ok"] is False
    budget = OperationBudget({"compute": 1}, ledger=ChainLedger())
    block = ActionGatingBlock(budget=budget)
    preview = block.infer(ActionGatingInput(op="budget_policy", policy={"limits": {"compute": 3}})).unwrap()
    assert preview["dry_run"] is True
    assert budget.check("r", "compute").remaining == 0
    assert budget.check("r", "compute").decision == "block"
    allowed = get_registry().invoke("action_gating", "budget_policy", {"policy": {"limits": {"compute": 3}}},
                                    block_sensitive=False).unwrap()
    assert allowed["dry_run"] is True


def test_policy_apply_is_logged_and_does_not_reset_usage():
    # Removal oracle: set_policy appends a policy event without deleting operation_check entries.
    ledger = ChainLedger()
    budget = OperationBudget({"compute": 1}, ledger=ledger)
    budget.check("r", "compute")
    budget.set_policy({"compute": 2}, dry_run=False)
    assert budget.check("r", "compute").remaining == 0
    reopened = OperationBudget(ledger=ledger)
    assert reopened.check("r", "compute").decision == "block"
    assert any(e["kind"] == "operation_policy" for e in ledger.entries())


def test_check_and_record_use_target_block_interface():
    # Removal oracle: ActionGatingBlock.infer dispatches budget_check to the injected OperationBudget.
    budget = OperationBudget({"compute": 1}, ledger=ChainLedger())
    block = ActionGatingBlock(budget=budget)
    first = block.infer(ActionGatingInput(op="budget_check", run_id="r", operation="compute", dry_run=False)).unwrap()
    assert first["decision"] == "allow" and first["remaining"] == 0
    recorded = block.infer(ActionGatingInput(op="budget_record", run_id="r", operation="compute", dry_run=False)).unwrap()
    assert recorded["recorded"] is True
    assert block.infer(ActionGatingInput(op="budget_check", run_id="r", operation="compute", dry_run=False)).unwrap()["decision"] == "block"
    assert budget.record("r", "compute")["recorded"] is False  # no second observation for one reservation


def test_concurrent_checks_do_not_overspend():
    # Removal oracle: OperationBudget.check holds _lock across count, append and reservation.
    budget = OperationBudget({"compute": 3}, ledger=ChainLedger())
    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda _: budget.check("r", "compute"), range(30)))
    assert sum(d.decision == "allow" for d in decisions) == 3


def test_policy_and_record_write_failures_refuse(monkeypatch):
    # Removal oracle: set_policy and record report ledger errors without applying changes or claiming a record.
    ledger = ChainLedger()
    budget = OperationBudget({"compute": 1}, ledger=ledger)
    budget.check("r", "compute")
    def broken(*args):
        raise OSError("full")
    monkeypatch.setattr(ledger, "append", broken)
    with pytest.raises(BudgetRefused):
        budget.set_policy({"compute": 100}, dry_run=False)
    assert budget.record("r", "compute")["recorded"] is False
    assert budget.report("r")["remaining"]["compute"] == 0


def test_unknown_policy_ops_and_extra_fields_refuse():
    # Removal oracle: BudgetPolicyInput extra='forbid' and OperationBudget policy validation.
    with pytest.raises(ValueError):
        OperationBudget({"typo": 1}, ledger=ChainLedger())
    with pytest.raises(ValueError):
        ActionGatingInput(op="budget_policy", policy={"limits": {"compute": 1}, "approved": True})
    with pytest.raises(ValueError):
        OperationBudget({"compute": True}, ledger=ChainLedger())


def test_deferred_function_bodies_are_refused():
    # Removal oracle: guarded rejects generator and asynchronous functions before decoration.
    budget = OperationBudget({"compute": 1}, ledger=ChainLedger())
    def generator(*, run_id):
        yield run_id
    async def asynchronous(*, run_id):
        return run_id
    for fn in (generator, asynchronous):
        with pytest.raises(TypeError):
            budget.guarded("compute")(fn)


def test_failed_operation_keeps_reservation_spent():
    # Removal oracle: guarded reserves a count before calling fn, never refunds failed execution.
    budget = OperationBudget({"compute": 1}, ledger=ChainLedger())
    @budget.guarded("compute")
    def fails(*, run_id):
        raise ArithmeticError("operation failed")
    with pytest.raises(ArithmeticError):
        fails(run_id="r")
    assert budget.check("r", "compute").decision == "block"


def test_report_read_failure_is_a_coded_refusal(monkeypatch):
    # Removal oracle: report catches ledger read errors and returns G6_E_OPERATION_LEDGER_FAILED.
    ledger = ChainLedger()
    budget = OperationBudget({"compute": 1}, ledger=ledger)
    def broken():
        raise OSError("unavailable")
    monkeypatch.setattr(ledger, "entries", broken)
    report = budget.report("r")
    assert report["completion_state"] == "blocked-escalated"
    assert report["code"] == "G6_E_OPERATION_LEDGER_FAILED"


@pytest.mark.parametrize("failure", ["verify", "entries"])
def test_check_ledger_read_failures_are_closed(failure, monkeypatch):
    # Removal oracle: check's ledger.verify/entries failures return a blocked result.
    ledger = ChainLedger()
    budget = OperationBudget({"compute": 1}, ledger=ledger)
    def broken():
        raise OSError("unavailable")
    monkeypatch.setattr(ledger, failure, broken)
    result = budget.check("r", "compute")
    assert result.decision == "block" and result.code == "G6_E_OPERATION_LEDGER_FAILED"


def test_bad_python_inputs_are_logged_refusals():
    # Removal oracle: check normalises invalid input labels before constructing its GateDecision.
    ledger = ChainLedger()
    budget = OperationBudget({"compute": 1}, ledger=ledger)
    result = budget.check(None, None)
    assert result.decision == "block" and result.code == "G6_E_OPERATION_INPUT_INVALID"
    assert ledger.entries()[-1]["body"]["decision"] == "block"
