# SPDX-License-Identifier: Apache-2.0
"""SelfMaintenanceBlock: the registry and MCP door (``g6_invoke``) to self_maintenance's planning, which runs nothing.

``identify_constraint`` names the stage of a measured serial workflow with the least effective capacity;
``plan`` ranks the tickets for that stage within a token budget, as a simulation that reserves nothing. The stage
measurements are the caller's word (``evidence[0]``, ``declared_by: "caller"``): they choose the constraint and never
raise a state. A refusal is a ``blocked-escalated`` reply with ``G6_E_SELF_MAINTENANCE_INPUT``, never a raised error.
Running an agent, reserving a budget and approving a cycle happen only through the Python host API, never over MCP.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.result import Result
from g6.self_maintenance import codes
from g6.self_maintenance.constraints import StageMeasurement, focus_plan, identify_constraint
from g6.self_maintenance.experience import experience_obligations
from g6.self_maintenance.kernel import BudgetBook, BudgetPolicy, MaintenanceTicket

COMPONENT = "self_maintenance"
_OPS: dict[str, str] = {
    "identify_constraint": "identify_constraint(stages): the stage of a measured serial workflow with the least "
                           "effective capacity (capacity x success rate), when every stage was independently observed",
    "plan": "plan(stages, tickets, ticket_stages, day, provider): the tickets for the constraint's stage ranked within "
            "a token budget, as a simulation: no reservation, model call, code run or approval",
}
_NEXT = "Treat the result as a plan to review: it rests on the measurements you sent, and it runs and approves nothing."


class MaintenanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["help", "ops", "identify_constraint", "plan"] = "help"
    day: str = Field("", max_length=10)
    stages: list[dict] = Field(default_factory=list, max_length=40)
    tickets: list[dict] = Field(default_factory=list, max_length=100)
    ticket_stages: dict[str, str] = Field(default_factory=dict, max_length=100)
    provider: Literal["codex", "claude"] = "codex"
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _reply(d: MaintenanceInput, state: str, code: str, message: str, body: dict[str, Any]) -> dict[str, Any]:
    said = {"declared_by": "caller", "source": "caller",
            "summary": "the stage measurements (attempts, successes, capacity, independently_observed) and the "
                       "tickets are the caller's word; they choose the constraint and raise no state",
            "stages": [s.get("name") for s in d.stages if isinstance(s, dict)],
            "independently_observed": [bool(s.get("independently_observed")) for s in d.stages if isinstance(s, dict)]}
    return {"component": COMPONENT, "op": d.op, **body,
            **envelope_fields(reliability_label=state, warning_card=warning_card(code, message, _NEXT),
                              evidence=[said], request_id=d.request_id, run_id=d.run_id)}


@block_contract(
    purpose="name the constraint of a measured workflow and plan the next maintenance ticket within a token budget, "
            "running nothing",
    mitigates_failure_modes=("low_value_first", "unchecked_self_change"),
    verification_method="the constraint is the minimum effective capacity over declared serial stages, named only when "
                        "every stage was independently observed; plans are simulations that reserve no budget",
)
@component_maturity(
    "beta",
    reason="read-only constraint identification and simulated ticket ranking over caller measurements; no scheduler, "
           "and the live agent path refuses in this release",
)
class SelfMaintenanceBlock(AIBlock[MaintenanceInput, dict, dict]):
    """The maintenance planner. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: MaintenanceInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            if not data.stages:
                raise ValueError(f"{data.op} needs stages: each with name, attempted, succeeded and capacity_per_day")
            stages = tuple(StageMeasurement(**r) for r in data.stages)
            if data.op == "identify_constraint":
                found = identify_constraint(stages)
                named = found.bottleneck != "unknown"
                return Result.ok(_reply(
                    data, "qualified-draft" if named else "blocked-escalated", codes.CONSTRAINT,
                    f"the constraint is {found.bottleneck!r}, by your measurements" if named else
                    "no constraint is named: not every stage was independently observed",
                    {"constraint": found.bottleneck, "basis": found.evidence, "throughput_bound": found.throughput_bound,
                     "observations": [list(o) for o in found.observations], "steps": list(found.steps),
                     "user_value_obligations": list(experience_obligations())}))
            if not data.tickets or not data.day:
                raise ValueError("plan needs tickets and day (YYYY-MM-DD) besides stages")
            tickets = tuple(MaintenanceTicket(**r) for r in data.tickets)
            # MCP planning has an empty book: persistent reservations happen only in the
            # separate host coordinator and cannot be forged over the network.
            plan = focus_plan(tickets, stages, data.ticket_stages, book=BudgetBook(),
                              policy=BudgetPolicy(), day=data.day, provider=data.provider)
            state = "qualified-draft" if plan.completion_state == "qualified-draft" else "blocked-escalated"
            return Result.ok(_reply(
                data, state, codes.PLAN, f"a simulated plan: {plan.rationale}",
                {"selected": plan.selected, "provider": plan.provider, "reserved_tokens_proposed": plan.reserved_tokens,
                 "ranked": [list(r) for r in plan.ranked], "refusals": list(plan.refusals),
                 "rationale": plan.rationale,
                 "note": "simulation only: no budget reservation, model calls, code execution or approval"}))
        except (TypeError, ValueError) as exc:
            return Result.ok(_reply(data, "blocked-escalated", codes.INPUT, f"{data.op} refused: {str(exc)[:300]}",
                                    {"reason": str(exc)[:300]}))


__all__ = ["MaintenanceInput", "SelfMaintenanceBlock"]
