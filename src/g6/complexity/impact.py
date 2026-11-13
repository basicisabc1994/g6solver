# SPDX-License-Identifier: Apache-2.0
"""Reverse dependency closure; missing edges widen uncertainty, never prove isolation."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from g6.complexity.ir import CIR


@dataclass(frozen=True)
class Impact:
    changed: tuple[str, ...]
    impacted: tuple[str, ...]
    preservation_obligations: tuple[str, ...]
    uncertainty: tuple[str, ...]
    parallel_safe: bool = False

    @property
    def complete(self) -> bool:
        return not self.uncertainty


def impact_cone(cir: CIR, changed: tuple[str, ...]) -> Impact:
    if not changed:
        raise ValueError("changed components must be declared")
    changed = tuple(sorted(set(changed)))
    reverse: dict[str, set[str]] = defaultdict(set)
    for edge in cir.dependencies:
        reverse[edge.target].add(edge.source)
    # Any operation with a shared resource, where one side may write, is coupled.
    resource_ops: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in cir.effects:
        resource_ops[e.resource].append((e.operation, e.kind))
    # Each such resource couples all of its operations; it is kept as one group and opened once, not as every pair.
    coupled = {resource: {a for a, _ in users} for resource, users in resource_ops.items()
               if any(kind in ("write", "create", "delete", "unknown") for _, kind in users)}
    groups: dict[str, list[str]] = defaultdict(list)
    for resource in coupled:
        for a in coupled[resource]:
            groups[a].append(resource)
    impacted = set(changed)
    todo = deque(changed)
    opened: set[str] = set()
    while todo:
        node = todo.popleft()
        near = set(reverse.get(node, ()))
        for resource in groups.get(node, ()):
            if resource not in opened:
                opened.add(resource)
                near |= coupled[resource]
        for parent in sorted(near):
            if parent not in impacted:
                impacted.add(parent)
                todo.append(parent)
    unknown = list(cir.unknowns)
    for c in changed:
        if c not in cir.components:
            unknown.append(f"changed_component_not_in_atlas:{c}")
    # No scope map? Preserve ALL normative obligations, rather than dropping an out-of-cone contract.
    obligations = tuple(sorted(o.id for o in cir.intent.obligations if o.normative))
    # Read-only impact analysis does not assert independent parallelizability without an external conflict proof.
    return Impact(changed, tuple(sorted(impacted)), obligations, tuple(sorted(set(unknown))))
