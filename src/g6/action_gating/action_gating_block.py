# SPDX-License-Identifier: Apache-2.0
"""ActionGatingBlock — pre-flight decision for a proposed action. It never executes anything.

The archive's block subclassed the simulation-only ``MotorControlBlock``, ran the parent's
``infer`` first and attached the gate decision afterwards as metadata (audit 002, AG-004),
and a second import path aliased the name to the parent directly (AG-005). This block is the
single canonical gate: it evaluates the action and returns the decision; executing the action
is the caller's job, and only after an ``allow``.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from g6.action_gating.decision_engine import evaluate_action
from g6.action_gating.explain import render_user_risk_message
from g6.action_gating.operation_budget import OperationBudget
from g6.action_gating.scope import ReviewScope
from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.result import Result

#: Refusal codes: no action was given; the action does not make a capsule (a value its field cannot take).
E_ACTION_REQUIRED = "G6_E_ACTION_GATING_ACTION_REQUIRED"
E_ACTION_INVALID = "G6_E_ACTION_GATING_ACTION_INVALID"

_OPS: dict[str, str] = {
    "evaluate": "evaluate(action, scope=review scope, optional): normalise, classify and decide allow / confirm / "
                "review / block; a review scope can only tighten",
    "explain": "explain(action, scope=optional): the plain-language risk message for the decision",
    "budget_check": "budget_check(run_id, operation, dry_run=True): preview or reserve a count; sensitive",
    "budget_record": "budget_record(run_id, operation, dry_run=True): preview or record a reservation; sensitive",
    "budget_report": "budget_report(run_id): counts and remaining limits",
    "budget_policy": "budget_policy(policy={limits}, dry_run=True): preview or change the person's count policy; sensitive",
}


class BudgetPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: dict[str, StrictInt] = Field(max_length=100)


class ActionGatingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelt parameter is refused, never dropped

    op: Literal["evaluate", "explain", "budget_check", "budget_record", "budget_report", "budget_policy",
                "ops", "help"] = "evaluate"
    run_id: str = Field("", max_length=200)
    operation: str = Field("", max_length=100)
    policy: BudgetPolicyInput | None = None
    dry_run: bool = True
    action: dict[str, Any] = Field(default_factory=dict)
    scope: ReviewScope | None = None  # declared context: allowed task patterns, forbidden actions, ceiling, expiry


@component_maturity(
    "beta",
    reason="deterministic pre-flight gate over caller-declared action facts; executor is simulation-only",
)
class ActionGatingBlock(AIBlock[ActionGatingInput, dict, dict]):
    """Decide whether a proposed action may proceed. Advisory unless the caller obeys it."""
    __g6_block_contract__ = BlockContract(
        name="action_gating", purpose="Assess an action; never execute or authorize a real merge by itself.",
        inputs_required_per_op={"evaluate": ("action",), "explain": ("action",)},
        outputs_per_op={"evaluate": ("gate_decision", "completion_state"), "explain": ("message",)},
        verification_method="model", bad_at=("authenticating action facts", "enforcing another executor"),
        side_effects=("budget operations change local counters when authorized",),
        guarantees=("evaluate does not execute the proposed action",))


    def __init__(self, name: str = "action_gating", state: dict | None = None,
                 *, budget: OperationBudget | None = None) -> None:
        super().__init__(name=name, state=state or {})
        self._budget = budget

    def infer(self, data: ActionGatingInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if data.op.startswith("budget_"):
            return self._budget_infer(data)
        if not data.action:
            return Result.fail(f"[{E_ACTION_REQUIRED}] {data.op}: action is required")
        try:
            evaluation = evaluate_action(data.action, scope=data.scope)
        except (ValueError, TypeError) as exc:
            return Result.fail(f"[{E_ACTION_INVALID}] {data.op}: {exc}")
        if data.op == "explain":
            message = render_user_risk_message(evaluation.action_capsule, evaluation.gate_decision)
            return Result.ok({"component": self.name, "op": "explain", "message": message})
        return Result.ok({"component": self.name, "op": "evaluate", **evaluation.to_response_dict()})

    def _budget_infer(self, data: ActionGatingInput) -> Result[dict]:
        try:
            if data.op == "budget_policy" and data.policy is None:
                return Result.fail("[G6_E_OPERATION_POLICY_MISSING] supply policy limits")
            if data.op != "budget_policy" and not data.run_id.strip():
                return Result.fail("[G6_E_OPERATION_INPUT_INVALID] run_id is required")
            budget = self._budget if self._budget is not None else OperationBudget()
            if data.op == "budget_check" and not data.dry_run:
                out = budget.check(data.run_id, data.operation).to_envelope()
            else:
                if data.op in {"budget_check", "budget_record"} and data.dry_run:
                    snapshot = budget.report(data.run_id)
                    out = {"dry_run": True, "applied": False, "recorded": False, "decision": "block",
                           "remaining": snapshot["remaining"].get(data.operation, 0),
                           "completion_state": "blocked-escalated",
                           "code": snapshot.get("code", "G6_E_OPERATION_DRY_RUN")}
                elif data.op == "budget_policy":
                    out = budget.set_policy(data.policy.limits, dry_run=data.dry_run)
                elif data.op == "budget_record":
                    out = budget.record(data.run_id, data.operation)
                else:
                    out = budget.report(data.run_id)
                code = out.get("code", "G6_E_OPERATION_BUDGET")
                out = {**out, **envelope_fields(reliability_label=out["completion_state"],
                    warning_card=warning_card(code, "Operation counts only; no action approval.", "Inspect the policy."),
                    evidence=[{"kind": "operation_budget", "op": data.op}], request_id=uuid4().hex, run_id=data.run_id)}
            return Result.ok({"component": self.name, "op": data.op, **out})
        except (ValueError, TypeError):
            return Result.fail("[G6_E_OPERATION_INPUT_INVALID] invalid count policy or request")
        except Exception:
            return Result.fail("[G6_E_OPERATION_LEDGER_FAILED] count ledger unavailable")

__all__ = ["ActionGatingBlock", "ActionGatingInput"]
