# SPDX-License-Identifier: Apache-2.0
"""Bounded proposal-only goal decomposition using the existing goal_engine contracts.

The model is a proposer, never an approver or evaluator. The concrete plan is
built by g6.goal_engine.build_plan_tree; goal and criteria remain caller claims.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import json
from collections.abc import Callable, Iterable

from g6.goal_engine.plan import PlanTree, build_plan_tree
from g6.goal_engine.schema import GoalSpec


@dataclass(frozen=True)
class GoalNode:
    node_id: str
    goal: str
    parent_id: str | None
    depth: int
    children: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposalResult:
    tree: GoalTree
    spec: GoalSpec
    plan: PlanTree
    model_calls: int
    unexpanded: tuple[str, ...]
    errors: tuple[str, ...]
    completion_state: str

    @property
    def complete(self) -> bool:
        """Structural completeness only; no claim of goal solvability."""
        return not self.unexpanded and not self.errors and not self.plan.rejected and not self.plan.truncated


class GoalTree:
    """Deterministic bounded hierarchy, with unique dotted IDs instead of ambiguous goal-name keys."""

    def __init__(self, objective: str, *, branching_factor: int = 3,
                 max_depth: int = 3, max_nodes: int = 40):
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective must be nonblank")
        if (isinstance(branching_factor, bool) or not 1 <= branching_factor <= 12
                or isinstance(max_depth, bool) or not 0 <= max_depth <= 8
                or isinstance(max_nodes, bool) or not 1 <= max_nodes <= 1000):
            raise ValueError("invalid depth, branching or node budget")
        self.branching_factor = branching_factor
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.root_id = "root"
        self._nodes: dict[str, GoalNode] = {"root": GoalNode("root", objective.strip(), None, 0)}

    @property
    def root(self) -> GoalNode:
        return self._nodes[self.root_id]

    @property
    def nodes(self) -> tuple[GoalNode, ...]:
        return tuple(self._nodes.values())

    def get(self, node_id: str) -> GoalNode:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise ValueError(f"unknown node id: {node_id!r}") from None

    def add_subgoals(self, parent_id: str, goals: Iterable[str]) -> tuple[str, ...]:
        """Atomic addition: never silently truncate and never duplicate a sibling."""
        parent = self.get(parent_id)
        proposed = tuple(goals)
        if parent.depth >= self.max_depth:
            raise ValueError("max_depth exceeded")
        if not proposed or any(not isinstance(g, str) or not g.strip() or len(g) > 2000 for g in proposed):
            raise ValueError("subgoals must be nonempty, nonblank strings of at most 2000 chars")
        normalized = tuple(g.strip() for g in proposed)
        siblings = {self.get(k).goal for k in parent.children}
        if len(set(normalized)) != len(normalized) or siblings.intersection(normalized):
            raise ValueError("duplicate sibling subgoals")
        if len(parent.children) + len(normalized) > self.branching_factor:
            raise ValueError("branching_factor exceeded")
        if len(self._nodes) + len(normalized) > self.max_nodes:
            raise ValueError("max_nodes exceeded")
        offset = len(parent.children)
        identifiers = tuple(f"{parent_id}.{i}" for i in range(offset, offset + len(normalized)))
        for node_id, goal in zip(identifiers, normalized):
            self._nodes[node_id] = GoalNode(node_id, goal, parent_id, parent.depth + 1)
        self._nodes[parent_id] = replace(parent, children=parent.children + identifiers)
        return identifiers

    def get_subgoals(self, node_id: str) -> tuple[GoalNode, ...]:
        return tuple(self.get(n) for n in self.get(node_id).children)

    def get_parent_goal(self, node_id: str) -> GoalNode | None:
        parent = self.get(node_id).parent_id
        return self.get(parent) if parent is not None else None

    def get_goals_at_depth(self, depth: int) -> tuple[GoalNode, ...]:
        return tuple(n for n in self.nodes if n.depth == depth)

    def is_terminal_node(self, node_id: str) -> bool:
        return not self.get(node_id).children

    def depth_first_search(self, start_id: str, target_id: str) -> tuple[str, ...]:
        self.get(start_id)
        stack = [(start_id, (start_id,))]
        while stack:
            ident, path = stack.pop()
            if ident == target_id:
                return path
            stack.extend((child, path + (child,)) for child in reversed(self.get(ident).children))
        return ()

    def generate_goal_stack(self) -> tuple[str, ...]:
        """Postorder (children before parents), NOT authorization to execute."""
        order: list[str] = []
        stack = [(self.root_id, False)]
        while stack:
            ident, ready = stack.pop()
            if ready:
                order.append(ident)
            else:
                stack.append((ident, True))
                stack.extend((k, False) for k in reversed(self.get(ident).children))
        return tuple(order)

    def visualize(self) -> str:
        lines = []
        stack = [self.root_id]
        while stack:
            ident = stack.pop()
            n = self.get(ident)
            lines.append("  " * n.depth + f"- [{ident}] {n.goal}")
            stack.extend(reversed(n.children))
        return "\n".join(lines)

    def to_goal_spec(self, *, root_spec: GoalSpec | None = None) -> GoalSpec:
        """Preserve root controls; generated child nodes carry no invented controls."""
        if root_spec is not None and (root_spec.goal.strip() != self.root.goal or root_spec.subtasks):
            raise ValueError("root_spec must match objective and have no existing subtasks")

        def convert(ident: str) -> GoalSpec:
            node = self.get(ident)
            children = tuple(convert(k) for k in node.children)
            if ident == "root" and root_spec is not None:
                return root_spec.model_copy(update={"subtasks": children})
            return GoalSpec(goal=node.goal, subtasks=children)

        return convert(self.root_id)


def _parse(raw: str | list[str]) -> list[str]:
    if isinstance(raw, str):
        if len(raw) > 40000:
            raise ValueError("proposal output exceeds 40000 characters")
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("subgoal proposal must be a JSON array of strings") from exc
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError("subgoal proposal must be a JSON array of strings")
    return raw


def propose_goal_tree(objective: str, proposer: Callable[[str, int, int], str | list[str]], *,
                      branching_factor: int = 3, max_depth: int = 3, max_nodes: int = 40,
                      max_model_calls: int = 12, root_spec: GoalSpec | None = None) -> ProposalResult:
    """Provider-neutral BFS; budget failures are listed, never silently cropped.

    proposer(goal, remaining_children, remaining_depth) returns a JSON array or list.
    No prompts or tools are executed by this module; it is only a proposal boundary.
    """
    if isinstance(max_model_calls, bool) or not 0 <= max_model_calls <= 100:
        raise ValueError("invalid model call budget")
    tree = GoalTree(objective, branching_factor=branching_factor,
                    max_depth=max_depth, max_nodes=max_nodes)
    pending = deque(["root"])
    calls = 0
    skipped: list[str] = []
    errors: list[str] = []
    while pending:
        ident = pending.popleft()
        node = tree.get(ident)
        if node.depth >= max_depth:
            continue
        if calls >= max_model_calls or len(tree.nodes) >= max_nodes:
            skipped.append(ident)
            continue
        calls += 1
        try:
            proposals = _parse(proposer(node.goal, tree.branching_factor, max_depth - node.depth))
            if not proposals:
                # A model's empty list cannot establish the goal was decomposed sufficiently.
                skipped.append(ident)
                continue
            ids = tree.add_subgoals(ident, proposals)
            pending.extend(ids)
        except (ValueError, TypeError) as exc:
            errors.append(f"{ident}: {type(exc).__name__}: {exc}")
    spec = tree.to_goal_spec(root_spec=root_spec)
    plan = build_plan_tree(spec, max_depth=max_depth)
    state = "blocked-escalated" if errors or skipped or plan.rejected or plan.truncated else "qualified-draft"
    return ProposalResult(tree, spec, plan, calls, tuple(skipped), tuple(errors), state)


__all__ = ["GoalNode", "GoalTree", "ProposalResult", "propose_goal_tree"]
