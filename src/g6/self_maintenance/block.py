# SPDX-License-Identifier: Apache-2.0
"""Read-only MCP-visible G6 self-evolution planner. Execution uses explicit Python host API."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from g6.core.ai_block import AIBlock
from g6.core.result import Result
from g6.core.maturity import component_maturity
from g6.self_maintenance.kernel import BudgetBook, BudgetPolicy, MaintenanceTicket
from g6.self_maintenance.constraints import StageMeasurement, focus_plan, identify_constraint
from g6.self_maintenance.experience import experience_obligations


class MaintenanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["help", "ops", "identify_constraint", "plan"] = "help"
    day: str = ""
    stages: list[dict] = Field(default_factory=list, max_length=40)
    tickets: list[dict] = Field(default_factory=list, max_length=100)
    ticket_stages: dict[str, str] = Field(default_factory=dict)
    provider: Literal["codex", "claude"] = "codex"


@component_maturity("experimental", reason="read-only plan; no autonomous scheduling or deployment authority")
class SelfMaintenanceBlock(AIBlock[MaintenanceInput, dict, dict]):
    def __init__(self) -> None:
        super().__init__("self_maintenance", {})

    def infer(self, data: MaintenanceInput) -> Result[dict]:
        if data.op in ("ops", "help"):
            return Result.ok({"completion_state": "qualified-draft",
                              "ops": ["identify_constraint", "plan"],
                              "help": "Host-observed capacities, budget ranking, and first-class safety constraints. Read-only."})
        try:
            stages = tuple(StageMeasurement(**r) for r in data.stages)
            if data.op == "identify_constraint":
                result = identify_constraint(stages)
                return Result.ok({"completion_state": "qualified-draft",
                                  "constraint": result.bottleneck, "evidence": result.evidence,
                                  "throughput_bound": result.throughput_bound, "steps": list(result.steps),
                                  "user_value_obligations": list(experience_obligations())})
            tickets = tuple(MaintenanceTicket(**r) for r in data.tickets)
            # MCP planning has an empty book: persistent reservations happen only in the
            # separate host coordinator and cannot be forged over the network.
            plan = focus_plan(tickets, stages, data.ticket_stages, book=BudgetBook(),
                              policy=BudgetPolicy(), day=data.day, provider=data.provider)
            return Result.ok({"completion_state": plan.completion_state, "selected": plan.selected,
                              "provider": plan.provider, "reserved_tokens_proposed": plan.reserved_tokens,
                              "ranked": list(plan.ranked), "refusals": list(plan.refusals),
                              "rationale": plan.rationale,
                              "note": "simulation only: no budget reservation, model calls, code execution or approval"})
        except (TypeError, ValueError) as exc:
            return Result.fail("G6_E_SELF_MAINTENANCE_INPUT: " + str(exc))
