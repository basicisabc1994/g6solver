# SPDX-License-Identifier: Apache-2.0
"""Capability-matched coordination; all competence, identity and completion inputs remain claims."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.errors import unknown_op
from g6.core.maturity import component_maturity
from g6.core.resources import RESOURCE_LIMIT
from g6.core.result import Result
from g6.integrations._boundary import Refusal, json_value, reply
from g6.integrations.hat_orchestrator.state import Agent, Name, Policy, Snapshot, Store, Sync, Task

OPS = ("register_agent", "allocate_task", "create_sync_point", "signal_ready", "check_sync", "complete_task",
       "fail_task", "resolve_authority")
WRITES = frozenset(OPS) - {"check_sync", "resolve_authority"}
SENSITIVE_OPS = tuple(op for op in OPS if op != "check_sync")  # authority escalation writes the gate's audit trail


def consult(gate: Any, action: dict) -> dict:
    try:
        if gate is None:
            from g6.action_gating import evaluate_action
            gate = evaluate_action
        decision = gate(action).gate_decision
        return {"decision": str(decision.decision), "reason_codes": [str(c) for c in decision.reason_codes],
                "message": str(decision.required_user_message or "")}
    except Exception as exc:  # noqa: BLE001 - no decision is never permission
        return {"decision": "block", "reason_codes": ["G6_E_GATE_ERROR"],
                "message": f"The action gate failed ({type(exc).__name__})."}


class HATOrchestratorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: str = "help"
    agent_id: str = Field("", max_length=100)
    capabilities: list[Name] = Field(default_factory=list, max_length=64)
    available: bool = True
    task_id: str = Field("", max_length=100)
    description: str = Field("", max_length=4000)
    required_capabilities: list[Name] = Field(default_factory=list, max_length=64)
    sync_point_id: str = Field("", max_length=100)
    required_agents: list[Name] = Field(default_factory=list, max_length=64)
    agent_a_id: str = Field("", max_length=100)
    agent_b_id: str = Field("", max_length=100)
    action: str = Field("", max_length=100)
    claim: Any = None
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("claim", mode="before")
    @classmethod
    def _json_claim(cls, value: Any) -> Any:
        return json_value(value, 16_384)

    @model_validator(mode="after")
    def _required(self) -> HATOrchestratorInput:
        required = {"register_agent": ("agent_id",), "allocate_task": ("task_id", "required_capabilities"),
                    "create_sync_point": ("sync_point_id", "required_agents"),
                    "signal_ready": ("sync_point_id", "agent_id"), "check_sync": ("sync_point_id",),
                    "complete_task": ("task_id", "agent_id"), "fail_task": ("task_id", "agent_id"),
                    "resolve_authority": ("agent_a_id", "agent_b_id", "action")}
        for field in required.get(self.op, ()):
            if not getattr(self, field):
                raise ValueError(f"{self.op} needs {field}")
        if self.op == "resolve_authority" and self.agent_a_id == self.agent_b_id:
            raise ValueError("authority resolution needs two different agents")
        return self


@block_contract(purpose="Coordinate declared capabilities and escalate contested authority",
                mitigates_failure_modes=("unclear_authority",))
@component_maturity("beta", reason="operator roster and bounded coordination records; qualified-draft claims only")
class HATOrchestratorBlock(AIBlock[HATOrchestratorInput, dict, dict]):
    def __init__(self, name: str = "hat_orchestrator", state: dict | None = None, *, roster: dict | None = None,
                 informational_actions: set[str] | None = None, gate: Any = None) -> None:
        super().__init__(name=name, state=state or {})
        self.store, self.gate = Store(), gate
        self.policy = (Policy(roster=roster, informational_actions=sorted(informational_actions or ()))
                       if roster is not None else None)

    def infer(self, data: HATOrchestratorInput) -> Result[dict]:
        if data.op in {"ops", "help"}:
            return reply(self.name, data, code="G6_E_HAT_INFO", message="Declared capabilities and claims only.",
                         ops=list(OPS), help=list(OPS), sensitive=list(SENSITIVE_OPS))
        if data.op not in OPS:
            return unknown_op(self.name, data.op, OPS)
        try:
            if data.op == "resolve_authority":
                return self._authority(data)
            if data.op == "check_sync" or data.dry_run:
                snapshot = self.store.read()
                fields = self._dispatch(data, snapshot)
                return reply(self.name, data, code="G6_E_HAT_DRY_RUN" if data.op in WRITES else "G6_E_HAT_REPORT",
                             message="Plan only; no record changed." if data.op in WRITES else "Readiness is reported by participants.",
                             dry_run=data.op in WRITES, **fields)
            # the backup of the present state is what the gate needs to read before it allows an overwrite
            gate = consult(self.gate, {"action_type": "write_file", "target_backend": "local",
                                      "target_resource": "hat_orchestrator/state.json",
                                      "checkpoint": str(self.store.checkpoint()),
                                      "description": f"Record {data.op} as declared coordination data",
                                      "action_params": {"operation": data.op, "task_id": data.task_id}})
            if gate["decision"] != "allow":
                return reply(self.name, data, code="G6_E_HAT_GATE_HELD", message="The gate did not allow a state change.",
                             blocked=True, gate=gate)
            with self.store.transaction() as snapshot:
                fields = self._dispatch(data, snapshot)
            return reply(self.name, data, code="G6_E_HAT_RECORDED", message="Recorded as declared; identities and outcomes are not checked.",
                         dry_run=False, gate=gate, **fields)
        except Refusal as exc:
            return reply(self.name, data, code=exc.code, message=exc.reason, action=exc.action, blocked=True)
        except Exception as exc:  # noqa: BLE001 - missing/corrupt state never becomes an empty successful answer
            return reply(self.name, data, code="G6_E_HAT_STATE_FAILED", blocked=True,
                         message=f"State or policy could not be read or recorded ({type(exc).__name__}).",
                         action="Ask the operator to inspect the state and roster; nothing falls back to memory.")

    def _authority(self, data: HATOrchestratorInput) -> Result[dict]:
        policy = self.policy or self.store.policy()
        agents = self.store.read().agents
        if any(agent not in policy.roster or agent not in agents for agent in (data.agent_a_id, data.agent_b_id)):
            raise Refusal("G6_E_HAT_ROSTER_MISSING", "Both registered agents need an operator-assigned rank.")
        a, b = policy.roster[data.agent_a_id], policy.roster[data.agent_b_id]
        real_world = data.action not in policy.informational_actions
        if a == b or real_world:
            escalation = {"action_type": "run_code", "target_backend": "human_review",
                          "target_resource": "contested team authority", "real_world_effect": real_world,
                          "reversibility": "unknown", "blast_radius": "unknown",
                          "description": "Operator ranks tie or the requested action may affect the world.",
                          "action_params": {"action_claim": data.action,
                                            "agents": [data.agent_a_id, data.agent_b_id]}}
            if data.dry_run:
                return reply(self.name, data, code="G6_E_HAT_AUTHORITY_ESCALATED", blocked=True,
                             message="Plan only; a person must resolve this authority question.",
                             dry_run=True, proposed_escalation=escalation, action_executed=False)
            gate = consult(self.gate, escalation)
            # A gate result is advice, never an authenticated human resolution or an execution permit.
            return reply(self.name, data, code="G6_E_HAT_AUTHORITY_ESCALATED", blocked=True,
                         message="A person must resolve this authority question; no action is executed.", gate=gate,
                         action="Ask the operator for a resolution and independently gate any outward action.")
        return reply(self.name, data, code="G6_E_HAT_AUTHORITY_DECLARED",
                     message="Informational precedence from operator-declared ranks; not an action permission.",
                     winner=data.agent_a_id if a > b else data.agent_b_id, basis="operator_declared_rank",
                     ranks={data.agent_a_id: a, data.agent_b_id: b}, action_executed=False)

    @staticmethod
    def _room(records: dict, identifier: str) -> None:
        if identifier in records:
            raise Refusal("G6_E_HAT_DUPLICATE", "This identifier already has a record.")
        if len(records) >= 256:
            raise Refusal(RESOURCE_LIMIT, "At most 256 records per kind; ask the operator to archive records.")

    def _dispatch(self, data: HATOrchestratorInput, state: Snapshot) -> dict:
        if data.op == "register_agent":
            if data.agent_id not in state.agents:
                self._room(state.agents, data.agent_id)
            agent = Agent(agent_id=data.agent_id, capabilities=sorted(set(data.capabilities)), available=data.available)
            state.agents[data.agent_id] = agent
            return {"agent": agent.model_dump()}
        if data.op == "allocate_task":
            self._room(state.tasks, data.task_id)
            required_capabilities = set(data.required_capabilities)
            candidates = sorted(agent.agent_id for agent in state.agents.values()
                                if agent.available and required_capabilities.issubset(agent.capabilities))
            if not candidates:
                raise Refusal("G6_E_HAT_NO_MATCH", "No available agent declares every required capability.")
            task = Task(task_id=data.task_id, assigned_agent_id=candidates[0],
                        required_capabilities=sorted(required_capabilities), description=data.description)
            state.tasks[data.task_id] = task
            return {"task": task.model_dump()}
        if data.op in {"complete_task", "fail_task"}:
            task = state.tasks.get(data.task_id)
            if task is None:
                raise Refusal("G6_E_HAT_TASK_MISSING", "No such task is recorded.")
            if task.status != "allocated" or task.assigned_agent_id != data.agent_id:
                raise Refusal("G6_E_HAT_REPORT_REFUSED", "A report must name the assigned agent and an open task.")
            task.status = "completed_claim" if data.op == "complete_task" else "failed_claim"
            task.reporter_claim, task.result_claim = data.agent_id, data.claim
            return {"task": task.model_dump()}
        if data.op == "create_sync_point":
            self._room(state.sync_points, data.sync_point_id)
            if any(agent not in state.agents for agent in data.required_agents):
                raise Refusal("G6_E_HAT_AGENT_MISSING", "Every sync participant must be registered.")
            state.sync_points[data.sync_point_id] = Sync(sync_point_id=data.sync_point_id,
                                                       required_agents=sorted(set(data.required_agents)))
        sync = state.sync_points.get(data.sync_point_id)
        if sync is None:
            raise Refusal("G6_E_HAT_SYNC_MISSING", "No such sync point is recorded.")
        if data.op == "signal_ready":
            if data.agent_id not in sync.required_agents:
                raise Refusal("G6_E_HAT_PARTICIPANT_REFUSED", "Only a named participant may report readiness.")
            if data.agent_id not in sync.ready_agents:
                sync.ready_agents.append(data.agent_id)
        return {"sync": {**sync.model_dump(), "all_reported_ready": set(sync.required_agents).issubset(sync.ready_agents)}}
