# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The CSIR typing judgement over a ``MissionIR`` (tex:348-369) and the six soundness obligations of C1 (tex:605-631).

``Gamma; S; Pi; B |- p : A ~> B ! eps |> C_p``: under the catalogue ``Gamma``, the frozen standard ``S``, the permissions ``Pi``
and the budget ``B``, program ``p`` consumes ``A``, yields ``B``, has effect bound ``eps`` and resource bound ``C_p``. A
``MissionIR`` is a typed DAG, the normal form of the theory's ``p2 o p1`` (an edge) and ``p1 (+) p2`` (two nodes no edge
orders). The obligations, as this kernel enforces them:

- A1 local refinement: each op is known to the catalogue at a measured class and does what it declares;
- A2 faithful composition: every requirement has a provider of the same type and the graph has no cycle. The failure-path
  half (any outcome but ``ok`` halts every dependent and is named) is the mission runner's contract, not the graph's;
- A3 authority and standard preserved: no node lowers the standard, runs below the class its effect needs, sends data
  out without an approval, or leaves a required role unfilled;
- A4 conservative budget: known costs sum within the mission's budget; an unknown cost is unknown, never zero;
- A5 evidence authority not writable by the candidate's author: no node checks the output of its own component;
- A6 no hidden shared state: one provider per name, and no two writers that no edge orders (gap 8 of the plan: the theory
  leaves parallel composition open, so ``(+)`` is allowed only between nodes that do not both write).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .. import checks
from ..checks import Finding, OpFact, validate
from ..flag import require_research
from ..ir import EFFECTS, MissionIR

OBLIGATIONS = ("A1", "A2", "A3", "A4", "A5", "A6")
PARALLEL_WRITERS = "parallel_writers"

#: Every validator code under the obligation it breaks (a test pins that no code is left out).
OBLIGATION_OF: dict[str, str] = {
    checks.EFFECT_MISDECLARED: "A1",
    checks.MISSING_SOURCE_AUTHORITY: "A1",
    checks.MISSING_PREREQUISITE: "A2",
    checks.EDGE_TYPE_MISMATCH: "A2",
    checks.CYCLE: "A2",
    checks.STANDARD_DECREASE: "A3",
    checks.AUTHORITY_TOO_LOW: "A3",
    checks.UNAPPROVED_EGRESS: "A3",
    checks.MISSING_ROLE: "A3",
    checks.BUDGET_EXCEEDED: "A4",
    checks.BUDGET_UNKNOWN: "A4",
    checks.SELF_VERIFICATION: "A5",
    checks.DUPLICATE_PROVIDER: "A6",
    PARALLEL_WRITERS: "A6",
}


@dataclass(frozen=True)
class Resources:
    """A resource vector ``c_f``; only cents today. ``None`` is unknown and composes to unknown (tex:242-266)."""

    cents: int | None

    @staticmethod
    def compose(parts: Iterable[Resources]) -> Resources:
        total = 0
        for part in parts:
            if part.cents is None:
                return Resources(None)
            total += part.cents
        return Resources(total)


@dataclass(frozen=True)
class Judgement:
    mission_sha: str
    #: Obligation -> the findings that break it (every obligation is present; an empty tuple means it holds).
    violations: Mapping[str, tuple[Finding, ...]]
    consumes: tuple[tuple[str, str], ...]
    yields: tuple[tuple[str, str], ...]
    effect: str
    resources: Resources

    @property
    def sound(self) -> bool:
        return not any(self.violations.values())

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({f.code for found in self.violations.values() for f in found}))


def _effective(ir: MissionIR, catalogue: Mapping[str, OpFact]) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ir.nodes:
        effect = node.declared_effect
        fact = catalogue.get(node.source_operation)
        if fact is not None and not node.gate and EFFECTS.index(fact.effect) > EFFECTS.index(effect):
            effect = fact.effect
        out[node.id] = effect
    return out


def _ancestors(ir: MissionIR) -> dict[str, set[str]]:
    providers = {name: node.id for node in ir.nodes for name, _ in node.provides}
    parents = {node.id: {providers[name] for name, _ in node.requires if name in providers} for node in ir.nodes}
    seen: dict[str, set[str]] = {}

    def walk(node_id: str, trail: frozenset[str]) -> set[str]:
        if node_id in seen:
            return seen[node_id]
        found: set[str] = set()
        for parent in parents.get(node_id, ()):
            if parent in trail:  # a cycle; the validator reports it under A2
                continue
            found |= {parent} | walk(parent, trail | {parent})
        seen[node_id] = found
        return found

    for node in ir.nodes:
        walk(node.id, frozenset({node.id}))
    return seen


def _parallel_writers(ir: MissionIR, effects: Mapping[str, str]) -> list[Finding]:
    writers = [n.id for n in ir.nodes if not n.gate and EFFECTS.index(effects[n.id]) >= EFFECTS.index("local_write")]
    above = _ancestors(ir)
    found = []
    for i, a in enumerate(writers):
        for b in writers[i + 1:]:
            if a not in above[b] and b not in above[a]:
                found.append(Finding(PARALLEL_WRITERS, b, f"writes alongside {a!r} with no edge ordering the two"))
    return found


def judge(ir: MissionIR, catalogue: Mapping[str, OpFact]) -> Judgement:
    """The mission typed against the catalogue: its interface, bounds and every obligation it breaks."""
    require_research()
    effects = _effective(ir, catalogue)
    findings = [*validate(ir, catalogue), *_parallel_writers(ir, effects)]
    violations: dict[str, list[Finding]] = {o: [] for o in OBLIGATIONS}
    for finding in findings:
        violations[OBLIGATION_OF[finding.code]].append(finding)
    consumed = {name for node in ir.nodes for name, _ in node.requires}
    yields = tuple(sorted((name, kind) for node in ir.nodes for name, kind in node.provides if name not in consumed))
    worst = max((effects[n.id] for n in ir.nodes), key=EFFECTS.index, default="read")
    return Judgement(
        mission_sha=ir.sha256, violations={o: tuple(found) for o, found in violations.items()}, consumes=ir.inputs,
        yields=yields, effect=worst,
        resources=Resources.compose(Resources(n.budget_cents) for n in ir.nodes if not n.gate))
