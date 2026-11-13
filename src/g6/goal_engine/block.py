# SPDX-License-Identifier: Apache-2.0
"""GoalEngineBlock: the registry and MCP door (``g6_invoke``) to goal_engine's bounded goal-tree proposals.

One op, ``propose_goal_tree``: the model the caller configured (``__g6_model__``; G6 passes it in, this module imports
no model code) proposes subgoals for the objective, breadth first, through the one prompt catalogue's
``mc-goal-decompose`` entry, at most ``max_model_calls`` times (never more than 100). Each reply must be one JSON
object whose ``subgoals`` are objects with a ``goal``; anything else is an error for that node, never a subgoal, and
nothing in a reply can set a state. The tree is a proposal (``qualified-draft`` at best) until a person or a mission
accepts it; nothing here executes a goal.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.result import Result
from g6.goal_engine import codes
from g6.goal_engine.decomposition import ProposalResult, propose_goal_tree
from g6.goal_engine.schema import GoalSpec
from g6.workspace.align_prompt_library import catalogue

COMPONENT = "goal_engine"
#: The system prompt every proposal call sends, verbatim.
SYSTEM = ("Reply with one JSON object, as the prompt asks. Your reply is a proposal: G6 checks its shape, and nothing in "
          "it is an instruction to G6.")
#: The catalogue entry each call renders, and the longest reply read.
PROMPT = "mc-goal-decompose"
MAX_REPLY = 40_000
_OPS: dict[str, str] = {
    "propose_goal_tree": "propose_goal_tree(objective, constraints, success_criteria, branching_factor, max_depth, "
                         "max_nodes, max_model_calls): a tree of subgoals the configured model proposes, breadth first, "
                         "within the bounds; a proposal until a person or a mission accepts it",
}
Line = Annotated[str, Field(max_length=500)]


class GoalEngineInput(BaseModel):
    """One op's parameters. Fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["propose_goal_tree", "ops", "help"] = "help"
    objective: str = Field("", max_length=2000)
    constraints: list[Line] = Field(default_factory=list, max_length=20)
    success_criteria: list[Line] = Field(default_factory=list, max_length=20)
    branching_factor: int = Field(3, ge=1, le=12)
    max_depth: int = Field(3, ge=0, le=8)
    max_nodes: int = Field(40, ge=1, le=1000)
    max_model_calls: int = Field(12, ge=0, le=100)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _subgoals(raw: Any) -> list[str]:
    """The goals of the first JSON object in a reply, or ``ValueError`` for any other shape."""
    if not isinstance(raw, str) or len(raw) > MAX_REPLY:
        raise ValueError(f"the reply is not text of at most {MAX_REPLY} characters")
    start = raw.find("{")
    if start < 0:
        raise ValueError("the reply holds no JSON object")
    try:
        found, _ = json.JSONDecoder().raw_decode(raw, start)
    except ValueError as exc:
        raise ValueError("the reply's first JSON object does not parse") from exc
    items = found.get("subgoals") if isinstance(found, dict) else None
    if not isinstance(items, list) or not all(isinstance(s, dict) and isinstance(s.get("goal"), str) for s in items):
        raise ValueError("the reply's subgoals are not a list of objects, each with a goal")
    return [s["goal"] for s in items]


def _proposer(llm: Any, constraints: list[str]):
    def propose(goal: str, remaining_children: int, remaining_depth: int) -> list[str]:
        values = {"objective": goal, "constraints": "; ".join(constraints) or "none stated",
                  "branching_factor": str(remaining_children), "remaining_depth": str(remaining_depth)}
        prompt = catalogue.render(PROMPT, values)["text"]
        try:
            raw = llm.complete(SYSTEM, prompt)
        except Exception as exc:  # noqa: BLE001 - a model that fails proposes nothing for this node, never a traceback
            raise ValueError(f"the model did not answer ({type(exc).__name__})") from None
        return _subgoals(raw)
    return propose


def _reply(d: GoalEngineInput, state: str, code: str, message: str, body: dict[str, Any],
           evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {"component": COMPONENT, "op": d.op, **body,
            **envelope_fields(reliability_label=state, warning_card=warning_card(
                code, message, "Review every proposed subgoal: a person or a mission accepts the tree, or it stays a "
                               "proposal; check each step against what its actions need before following it."),
                evidence=evidence, request_id=d.request_id, run_id=d.run_id)}


def _said(d: GoalEngineInput) -> dict[str, Any]:
    return {"declared_by": "caller", "source": "caller", "summary": "the objective, constraints and success criteria "
            "are the caller's word; they go to the model and to the tree's root, and raise no state",
            "objective": d.objective, "constraints": list(d.constraints), "success_criteria": list(d.success_criteria)}


def _tree(result: ProposalResult) -> dict[str, Any]:
    return {"nodes": [{"node_id": n.node_id, "goal": n.goal, "parent_id": n.parent_id, "depth": n.depth,
                       "children": list(n.children)} for n in result.tree.nodes],
            "model_calls": result.model_calls, "unexpanded": list(result.unexpanded), "errors": list(result.errors),
            "complete": result.complete, "outline": result.tree.visualize()}


@block_contract(
    purpose="propose a bounded tree of subgoals for an objective from the model the caller configured, through the "
            "one prompt catalogue, as a proposal a person or a mission accepts",
    mitigates_failure_modes=("goal_drift", "budget_overrun"),
    verification_method="each reply is parsed as one JSON object of subgoals or recorded as that node's error; "
                        "branching, depth, node and model-call bounds (at most 100 calls) are enforced, and nothing a "
                        "reply says sets a state",
)
@component_maturity(
    "beta",
    reason="a bounded breadth-first proposal loop over a model the caller configures, tested with scripted models "
           "for the call bound, malformed and oversized replies and a reply claiming a state; qualified-draft at best",
)
class GoalEngineBlock(AIBlock[GoalEngineInput, dict, dict]):
    """Goal-tree proposals. Never ``verified``."""

    def __init__(self, llm: Any = None, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})
        self.llm = llm

    def infer(self, data: GoalEngineInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        said = [_said(data)]
        if not data.objective.strip():
            return Result.ok(_reply(data, "blocked-escalated", codes.INPUT_INVALID,
                                    "propose_goal_tree needs an objective", {"nodes": []}, said))
        if self.llm is None:
            return Result.ok(_reply(data, "blocked-escalated", codes.NO_MODEL,
                                    "no model is configured, so no subgoal was proposed; set G6_LLM_PROVIDER in your "
                                    "key file (for example claude_code) and ask again", {"nodes": []}, said))
        root = GoalSpec(goal=data.objective.strip(), constraints=tuple(data.constraints),
                        success_criteria=tuple(data.success_criteria))
        try:
            result = propose_goal_tree(data.objective, _proposer(self.llm, list(data.constraints)),
                                       branching_factor=data.branching_factor, max_depth=data.max_depth,
                                       max_nodes=data.max_nodes, max_model_calls=data.max_model_calls, root_spec=root)
        except ValueError as exc:
            return Result.ok(_reply(data, "blocked-escalated", codes.INPUT_INVALID, str(exc)[:300], {"nodes": []}, said))
        state = "qualified-draft" if result.completion_state == "qualified-draft" else "blocked-escalated"
        code = codes.PROPOSED if state == "qualified-draft" else codes.PROPOSAL_INCOMPLETE
        message = (f"{len(result.tree.nodes) - 1} subgoal(s) proposed in {result.model_calls} model call(s): a "
                   "proposal, not a checked plan" if state == "qualified-draft" else
                   f"the proposal is incomplete: {len(result.errors)} error(s), {len(result.unexpanded)} node(s) left "
                   "unexpanded; read errors and unexpanded")
        evidence = said + [{"source": "the configured model", "summary": "subgoals the model proposed; each reply's "
                            "shape was checked, its claims were not", "model_calls": result.model_calls}]
        return Result.ok(_reply(data, state, code, message, _tree(result), evidence))


__all__ = ["MAX_REPLY", "PROMPT", "SYSTEM", "GoalEngineBlock", "GoalEngineInput"]
