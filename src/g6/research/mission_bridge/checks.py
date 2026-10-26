# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The mission-graph validator (integrated audit ENG-01 exit checks). Pure: it reads a ``MissionIR`` and a catalogue.

It reports every problem found, never only the first, as a ``Finding`` with a stable code. It runs nothing and
approves nothing: a graph with no findings has passed this screen, which says the graph is well formed against the
catalogue it was given, not that any conclusion it would reach is true. The product's own plan checks (``parse_plan``,
authority, gate, contract) are applied to a lowered plan by lower.py; this is the earlier screen over the mission.

The ``catalogue`` is any mapping of ``"component.op"`` to an ``OpFact`` (the op's declared side effect and its
authority class); lower.py builds the real one from the shipped census and the ParamContracts.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter

from .flag import require_research
from .ir import EFFECTS, MissionIR, Node

EDGE_TYPE_MISMATCH = "edge_type_mismatch"
MISSING_PREREQUISITE = "missing_prerequisite"
DUPLICATE_PROVIDER = "duplicate_provider"
CYCLE = "cycle"
MISSING_SOURCE_AUTHORITY = "missing_source_authority"
EFFECT_MISDECLARED = "effect_misdeclared"
AUTHORITY_TOO_LOW = "authority_too_low"
STANDARD_DECREASE = "standard_decrease"
UNAPPROVED_EGRESS = "unapproved_egress"
BUDGET_EXCEEDED = "budget_exceeded"
BUDGET_UNKNOWN = "budget_unknown"
MISSING_ROLE = "missing_role"
SELF_VERIFICATION = "self_verification"

#: What each declared side effect needs before an op that has it may be planned on (the product's own table,
#: ``workforce.plans.EFFECT_AUTHORITY``; a test pins them equal).
EFFECT_AUTHORITY = {"read": "A1", "local_write": "A1", "external": "A2", "irreversible": "A2"}
_RANK = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}
_INPUT = "$input"


@dataclass(frozen=True)
class OpFact:
    """What the catalogue says about one operation."""

    effect: str
    authority: str

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise ValueError(f"effect must be one of {EFFECTS}, got {self.effect!r}")
        if self.authority not in _RANK:
            raise ValueError(f"authority must be one of {tuple(_RANK)}, got {self.authority!r}")


@dataclass(frozen=True)
class Finding:
    code: str
    node: str
    detail: str


def _worse(a: str, b: str) -> str:
    return a if EFFECTS.index(a) >= EFFECTS.index(b) else b


def _graph(ir: MissionIR, found: list[Finding]) -> None:
    providers: dict[str, tuple[str, str]] = {}
    for name, kind in ir.inputs:
        providers[name] = (_INPUT, kind)
    for node in ir.nodes:
        for name, kind in node.provides:
            if name in providers:
                found.append(Finding(DUPLICATE_PROVIDER, node.id, f"{name!r} is already provided by {providers[name][0]!r}"))
            else:
                providers[name] = (node.id, kind)
    ids = {n.id for n in ir.nodes}
    deps: dict[str, set[str]] = {n.id: set() for n in ir.nodes}
    for node in ir.nodes:
        for name, kind in node.requires:
            source = providers.get(name)
            if source is None:
                found.append(Finding(MISSING_PREREQUISITE, node.id, f"nothing provides {name!r}"))
                continue
            if source[1] != kind:
                found.append(Finding(EDGE_TYPE_MISMATCH, node.id,
                                     f"{name!r} is required as {kind!r} but {source[0]!r} provides {source[1]!r}"))
            if source[0] in ids:
                deps[node.id].add(source[0])
        if node.verifies and node.verifies not in ids:
            found.append(Finding(MISSING_PREREQUISITE, node.id, f"verifies {node.verifies!r}, which is not a node"))
    try:
        tuple(TopologicalSorter(deps).static_order())
    except CycleError as exc:
        found.append(Finding(CYCLE, "", f"the graph loops: {' -> '.join(map(str, exc.args[1]))}"))


def _authority(ir: MissionIR, catalogue: Mapping[str, OpFact], found: list[Finding]) -> dict[str, str]:
    """Each node's effect as the catalogue has it (declared one where the op is unknown), for the egress rule."""
    effects: dict[str, str] = {}
    for node in ir.nodes:
        fact = catalogue.get(node.source_operation)
        effects[node.id] = node.declared_effect
        if node.gate:
            continue  # a person's decision: no operation to judge
        if fact is None or fact.authority == "A0":
            why = "the catalogue does not know it" if fact is None else "it is class A0 (nothing measured)"
            found.append(Finding(MISSING_SOURCE_AUTHORITY, node.id, f"{node.source_operation}: {why}"))
            continue
        effects[node.id] = _worse(node.declared_effect, fact.effect)
        if fact.effect != node.declared_effect:
            found.append(Finding(EFFECT_MISDECLARED, node.id,
                                 f"declares {node.declared_effect!r} but {node.source_operation} is {fact.effect!r}"))
        need = EFFECT_AUTHORITY[fact.effect]
        if _RANK[fact.authority] < _RANK[need]:
            found.append(Finding(AUTHORITY_TOO_LOW, node.id,
                                 f"{node.source_operation} is {fact.authority}; a {fact.effect} op needs {need}"))
    return effects


def _standard(ir: MissionIR, found: list[Finding]) -> None:
    for node in ir.nodes:
        own = node.standard
        if own is None:
            continue
        dropped = sorted(set(ir.standard.predicates) - set(own.predicates))
        if own.sha256 != ir.standard.sha256:
            found.append(Finding(STANDARD_DECREASE, node.id, "cites a standard hash other than the mission's"))
        elif dropped:
            found.append(Finding(STANDARD_DECREASE, node.id, f"drops the mission's predicate(s) {dropped}"))


def _egress(ir: MissionIR, effects: Mapping[str, str], found: list[Finding]) -> None:
    for node in ir.nodes:
        leaves = effects[node.id] in ("external", "irreversible") or node.egress != "none"
        if not leaves:
            continue
        allowed = {"egress", "egress_sensitive"} if node.data_class != "sensitive" else {"egress_sensitive"}
        if not any(a.node == node.id and a.scope in allowed for a in ir.approvals):
            need = "egress_sensitive" if node.data_class == "sensitive" else "egress"
            found.append(Finding(UNAPPROVED_EGRESS, node.id,
                                 f"{node.data_class} data leaves this machine; it needs an approval of scope {need!r}"))


def _budget(ir: MissionIR, found: list[Finding]) -> None:
    known = sum(n.budget_cents for n in ir.nodes if n.budget_cents is not None)
    if ir.budget_cents is not None and known > ir.budget_cents:
        found.append(Finding(BUDGET_EXCEEDED, "", f"nodes budget {known} cents against a mission budget of {ir.budget_cents}"))
    if ir.charged:
        if ir.budget_cents is None:
            found.append(Finding(BUDGET_UNKNOWN, "", "a charged mission has no budget"))
        found.extend(Finding(BUDGET_UNKNOWN, n.id, "its cost is unknown on a charged mission")
                     for n in ir.nodes if n.budget_cents is None and not n.gate)  # a person's decision has no cost


def _roles(ir: MissionIR, found: list[Finding]) -> None:
    have = {n.role for n in ir.nodes}
    found.extend(Finding(MISSING_ROLE, "", f"no node fills the required role {role!r}")
                 for role in ir.required_roles if role not in have)
    by_id: dict[str, Node] = {n.id: n for n in ir.nodes}
    for node in ir.nodes:
        target = by_id.get(node.verifies) if node.verifies else None
        if target is not None and not node.gate and target.component == node.component:
            found.append(Finding(SELF_VERIFICATION, node.id,
                                 f"checks {target.id!r} with the same component ({node.component}) that made it"))


def validate(ir: MissionIR, catalogue: Mapping[str, OpFact]) -> list[Finding]:
    """Every problem found in the mission graph against the catalogue; an empty list is a pass of this screen only."""
    require_research()
    found: list[Finding] = []
    _graph(ir, found)
    effects = _authority(ir, catalogue, found)
    _standard(ir, found)
    _egress(ir, effects, found)
    _budget(ir, found)
    _roles(ir, found)
    return found
