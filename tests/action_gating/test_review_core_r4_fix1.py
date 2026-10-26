# SPDX-License-Identifier: Apache-2.0
"""Review regressions: sensitive writes, shared count reservations and honest confidence."""
import multiprocessing
from threading import BrokenBarrierError
from unittest.mock import patch

import pytest
from returns.result import Failure

from g6.action_gating import ActionGatingBlock, ActionGatingInput, OperationBudget, evaluate_action
from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.audit_store import ActionGateAuditStore
from g6.action_gating.safeguards import Interceptor, Outcome, Safeguard
from g6.core.ledger import ChainLedger
from g6.core.llm import FakeLLM
from g6.core.registry import get_registry
from g6.csf import BoundedAgent, ResourceTriple


def test_budget_writes_are_sensitive_and_reports_and_previews_create_nothing(tmp_path, monkeypatch):
    # Removal oracle: sensitive-op declarations, dry-run dispatch and write-only directory creation.
    home = tmp_path / "fresh-home"
    monkeypatch.setenv("G6_HOME", str(home))
    registry = get_registry()
    for populated in (False, True):
        if populated:
            budget = OperationBudget({"compute": 2})
            assert budget.check("r", "compute").decision == "allow"
        before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
        for op in ("budget_check", "budget_record", "budget_policy"):
            params = {"run_id": "r", "operation": "compute", "dry_run": False}
            if op == "budget_policy":
                params["policy"] = {"limits": {"compute": 99}}
            refused = registry.invoke("action_gating", op, params, block_sensitive=True)
            assert isinstance(refused, Failure) and "G6_E_SENSITIVE_COMPONENT_DENIED" in refused.failure()
            assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
        for op in ("budget_check", "budget_record"):
            preview = ActionGatingBlock().infer(ActionGatingInput(op=op, run_id="r", operation="compute")).unwrap()
            assert preview["dry_run"] is True
            assert preview.get("decision") != "allow" and preview.get("recorded") is not True
        report = registry.invoke("action_gating", "budget_report", {"run_id": "r"}, block_sensitive=True).unwrap()
        assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
        if populated:
            assert report["used"]["compute"] == 1 and report["remaining"]["compute"] == 1
        else:
            assert not home.exists()


def _spend_last_slot(path, start, at_append, results):
    import g6.core.ledger as ledger_module

    budget = OperationBudget(ledger=ChainLedger(path))
    real_head = ledger_module._file_head

    def paused_head(target):
        head = real_head(target)
        # Without the outer file lock both writers see this same head before either appends.
        try:
            at_append.wait(timeout=1)
        except BrokenBarrierError:
            pass  # A serialised writer must proceed without its blocked competitor.
        return head

    start.wait(timeout=10)
    with patch.object(ledger_module, "_file_head", paused_head):
        result = budget.check("r", "compute")
    results.put((result.decision, result.code))


def test_two_budget_processes_cannot_spend_the_last_slot(tmp_path):
    # Removal oracle: OperationBudget holds _exclusive_ledger_lock across reload, count and append.
    path = tmp_path / "operations.jsonl"
    original = OperationBudget({"compute": 1}, ledger=ChainLedger(path))
    context = multiprocessing.get_context("spawn")
    start, at_append, results = context.Barrier(2), context.Barrier(2), context.Queue()
    workers = [context.Process(target=_spend_last_slot, args=(path, start, at_append, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        assert all(not worker.is_alive() and worker.exitcode == 0 for worker in workers)
        decisions = [results.get(timeout=2) for _ in workers]
        assert sorted(decisions) == [
            ("allow", "G6_E_OPERATION_RESERVED"), ("block", "G6_E_OPERATION_BUDGET_EXHAUSTED"),
        ]
        ledger = ChainLedger(path)
        assert ledger.verify() == () and ChainLedger.audit(path) == ()
        assert len(ledger.entries()) == 3
        assert original.report("r")["remaining"]["compute"] == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(timeout=5)
        results.close()
        results.join_thread()


def _confidence_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "confidence":
                yield child
            yield from _confidence_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _confidence_values(child)


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("action_type", ["send_email", "unknown_review_fixture", "read_file"])
def test_evaluation_and_audit_have_no_invented_confidence(tmp_path, optional, action_type):
    # Removal oracle: classifiers/qa emit confidence=None and their schemas accept/default to None.
    store = ActionGateAuditStore(str(tmp_path / "audit.db"))
    logger = ActionGateAuditLogger(store=store)
    options = {}
    if optional:
        options = {
            "harm_judge": FakeLLM(default='{"decision":"allow"}'),
            "safeguards": Interceptor([Safeguard("observe", lambda ctx: Outcome.ALLOW)]),
            "csf_model": BoundedAgent(
                states=("start", "loss"), initial_state="start", unsafe_states=frozenset({"loss"}),
                resources=ResourceTriple(N=1),
                transitions=[{"from": "start", "to": "start", "probability": 1.0},
                             {"from": "loss", "to": "loss", "probability": 1.0}],
            ),
            "csf_epsilon": 0.1,
        }
    try:
        result = evaluate_action({"action_type": action_type, "target_resource": "notes.txt"},
                                 audit_logger=logger, **options)
        response = result.to_response_dict()
        assert response["gate_decision"]["risk"]["confidence"] is None
        assert response["gate_decision"]["risk"]["rationale"]
        assert response["gate_decision"]["safety_qa"]["confidence"] is None
        assert all(value is None for value in _confidence_values(response))
        # A routine read needs no audit; explicitly persist its evaluated decision as well.
        record = result.audit_record or logger.write(result.action_capsule, result.gate_decision)
        restored = store.get_by_audit_id(record.audit_id)
        assert restored is not None
        assert restored.risk_assessment.confidence is None and restored.safety_qa.confidence is None
        assert all(value is None for value in _confidence_values(restored.model_dump(mode="json")))
    finally:
        store.close()