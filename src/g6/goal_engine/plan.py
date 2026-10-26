# SPDX-License-Identifier: Apache-2.0
"""``build_plan_tree``: the structure of the subtasks a ``GoalSpec`` declares, and every one it leaves out.

It generates no subtask (audit 013, GE-003: the archive's ``GoalDecomposer`` echoed the caller's subtasks, added an
``[unsolved]`` placeholder when there were none, and stamped the tree ``verified``). Node ids are dotted index paths:
``"root"``, its first subtask ``"root.0"``, that one's second ``"root.0.1"``; nodes are listed with each node before
its subtasks and siblings in declared order, which is the declared order, not a checked execution order.

A declared subtask is left out, with every node below it, and listed with a code when:

* its goal is blank (``G6_E_GOAL_EMPTY``), or one of its guardrails or checkpoints cannot be built: an unregistered
  predicate (``G6_E_GOAL_PREDICATE_UNKNOWN``) or params its factory refuses (``G6_E_GOAL_CONTROL_INVALID``) —
  ``rejected``;
* it is deeper than ``max_depth`` (``G6_E_GOAL_PLAN_TRUNCATED``) — ``truncated``.

Nothing is dropped silently (GE-006). ``depth`` is the deepest node kept, measured, not the cap (GE-009). There is no
solvability gate and no self-check (GE-008, GE-009). The best state is ``qualified-draft``; a refused input or an
unplannable root is ``blocked-escalated``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from g6.goal_engine.codes import (
    GOAL_EMPTY,
    INPUT_INVALID,
    NOT_DECOMPOSED,
    PLAN_DECLARED,
    PLAN_REJECTED,
    PLAN_TRUNCATED,
    CompletionState,
    card,
    evidence,
)
from g6.goal_engine.predicates import ControlError, resolve
from g6.goal_engine.schema import GoalSpec

#: A guard against runaway nesting, not a judgement about how deep a good plan is.
DEFAULT_MAX_DEPTH = 15
_SOURCE = "build_plan_tree"


class PlanNode(BaseModel):
    """One declared goal kept in the plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    parent_id: str | None
    depth: int
    goal: str
    children: tuple[str, ...] = ()


class DroppedNode(BaseModel):
    """A declared goal left out of the plan, with everything below it (``subtree_size`` counts both)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    parent_id: str | None
    depth: int
    goal: str
    code: str
    reason: str
    subtree_size: int


class PlanTree(BaseModel):
    """The declared plan, what was left out of it, and its envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[PlanNode, ...]
    rejected: tuple[DroppedNode, ...] = ()
    truncated: tuple[DroppedNode, ...] = ()
    depth: int
    max_depth: int
    spec_digest: str
    completion_state: CompletionState
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""


def _problem(node: GoalSpec) -> tuple[str, str] | None:
    if not node.goal.strip():
        return GOAL_EMPTY, "the goal is blank"
    for kind, specs in (("guardrail", node.guardrails), ("checkpoint", node.checkpoints)):
        for spec in specs:
            try:
                resolve(spec.predicate, spec.params)
            except ControlError as exc:
                return exc.code, f"{kind} {spec.name!r}: {exc.reason}"
    return None


def _size(node: GoalSpec) -> int:
    count, stack = 0, [node]
    while stack:
        current = stack.pop()
        count += 1
        stack.extend(current.subtasks)
    return count


def _walk(spec: GoalSpec, max_depth: int) -> tuple[list[PlanNode], list[DroppedNode], list[DroppedNode]]:
    """Kept nodes in declared pre-order, then the rejected and the truncated ones."""
    kept: list[tuple[str, str | None, int, str]] = []
    children: dict[str, list[str]] = {}
    rejected: list[DroppedNode] = []
    truncated: list[DroppedNode] = []
    stack: list[tuple[GoalSpec, str, str | None, int]] = [(spec, "root", None, 0)]
    while stack:  # iterative, so nesting depth cannot exhaust the interpreter stack
        node, node_id, parent_id, depth = stack.pop()
        problem = (PLAN_TRUNCATED, f"deeper than max_depth={max_depth}") if depth > max_depth else _problem(node)
        if problem is not None:
            dropped = DroppedNode(node_id=node_id, parent_id=parent_id, depth=depth, goal=node.goal,
                                  code=problem[0], reason=problem[1], subtree_size=_size(node))
            (truncated if problem[0] == PLAN_TRUNCATED else rejected).append(dropped)
            continue
        kept.append((node_id, parent_id, depth, node.goal))
        children[node_id] = []
        if parent_id is not None:
            children[parent_id].append(node_id)
        for index in reversed(range(len(node.subtasks))):
            stack.append((node.subtasks[index], f"{node_id}.{index}", node_id, depth + 1))
    nodes = [PlanNode(node_id=i, parent_id=p, depth=d, goal=g, children=tuple(children[i])) for i, p, d, g in kept]
    return nodes, rejected, truncated


def _depth(nodes: list[PlanNode]) -> int:
    return max(node.depth for node in nodes)


def _state(blocked: bool) -> CompletionState:
    return "blocked-escalated" if blocked else "qualified-draft"


def _summary(nodes: list[PlanNode], rejected: list[DroppedNode],
             truncated: list[DroppedNode]) -> tuple[str, str, str]:
    """The warning card's code, message and next step for a plan that was built."""
    if rejected:
        left_out = sum(d.subtree_size for d in rejected)
        return (PLAN_REJECTED, f"{len(rejected)} declared subtask(s) were rejected, {left_out} node(s) left out in all: "
                + ", ".join(f"{d.node_id} ({d.code})" for d in rejected[:5]),
                "Fix each rejected subtask (see evidence) and build the plan again.")
    if truncated:
        left_out = sum(d.subtree_size for d in truncated)
        return (PLAN_TRUNCATED, f"{left_out} declared node(s) deeper than max_depth were left out, from: "
                + ", ".join(d.node_id for d in truncated[:5]),
                "Raise max_depth or flatten the spec if those subtasks are meant to run.")
    if len(nodes) == 1:
        return (NOT_DECOMPOSED, "the goal declares no subtasks, so the plan is the root alone: this library "
                "generates no subtasks",
                "Declare the subtasks (for example from a model's proposal you have reviewed) and build again.")
    return (PLAN_DECLARED, f"{len(nodes)} node(s) exactly as declared: nothing generated, ordered or checked them",
            "Review the subtasks; call evaluate_controls with the executor's observation before each node's work.")


def _refused(code: str, reason: str, *, max_depth: Any, digest: str, request_id: str, run_id: str) -> PlanTree:
    state = _state(blocked=True)
    valid_int = isinstance(max_depth, int) and not isinstance(max_depth, bool)
    return PlanTree(nodes=(), depth=0, max_depth=max_depth if valid_int else -1, spec_digest=digest,
                    completion_state=state,
                    warning_card=card(state, code, reason, "Correct the goal spec and build the plan again."),
                    evidence=[evidence(code, reason, _SOURCE)], request_id=request_id, run_id=run_id)


def build_plan_tree(spec: GoalSpec, *, max_depth: int = DEFAULT_MAX_DEPTH, request_id: str = "",
                    run_id: str = "") -> PlanTree:
    """The declared subtasks of ``spec`` as a plan, with every rejected and truncated node reported."""
    ids = {"request_id": request_id, "run_id": run_id}
    if not isinstance(spec, GoalSpec):
        return _refused(INPUT_INVALID, f"expected a GoalSpec, got {type(spec).__name__}", max_depth=max_depth,
                        digest="", **ids)
    digest = spec.digest()
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        return _refused(INPUT_INVALID, f"max_depth must be an int >= 0, got {max_depth!r}", max_depth=max_depth,
                        digest=digest, **ids)
    nodes, rejected, truncated = _walk(spec, max_depth)
    dropped = [evidence(d.code, d.reason, _SOURCE, node_id=d.node_id, subtree_size=d.subtree_size)
               for d in (*rejected, *truncated)]
    if not nodes:  # the root itself was rejected
        root = rejected[0]
        return _refused(root.code, f"the root goal cannot be planned: {root.reason}", max_depth=max_depth,
                        digest=digest, **ids)
    code, message, action = _summary(nodes, rejected, truncated)
    state = _state(blocked=False)
    return PlanTree(nodes=tuple(nodes), rejected=tuple(rejected), truncated=tuple(truncated), depth=_depth(nodes),
                    max_depth=max_depth, spec_digest=digest, completion_state=state,
                    warning_card=card(state, code, message, action),
                    evidence=[evidence(code, message, _SOURCE, nodes=len(nodes)), *dropped], **ids)


__all__ = ["DEFAULT_MAX_DEPTH", "DroppedNode", "PlanNode", "PlanTree", "build_plan_tree"]
