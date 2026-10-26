# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Replay a mission graph over recorded values (audit ENG-02). Nothing is run: no operation is called, no model, no network.

The graph is walked in dependency order. A step whose requirements are all available takes its recorded outputs; a step with
no recording, or a recording that gives other values than the node declares, stops there; a gate waits for a person unless a
decision is recorded for it. Whatever needs something that was never available is blocked and says what it needs, and the
trace lists the names the graph asks for that nothing provides and no input supplies (the engineering graph asks for its
design load before it certifies anything). Each step carries the node's ``source_operation`` and ``declared_effect``.

A replay shows the graph's data flow and where it stops. It says nothing about whether any operation would produce the
recorded values, and the values here are placeholders.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

from .flag import require_research
from .ir import MissionIR

REPLAYED, DECIDED = "replayed", "decided"
BLOCKED, NO_RECORDING, MISMATCH, AWAITING = "blocked", "no_recording", "recording_mismatch", "awaiting_person"


@dataclass(frozen=True)
class Step:
    node: str
    source_operation: str
    declared_effect: str
    gate: bool
    status: str
    needs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Trace:
    steps: tuple[Step, ...]
    #: Names a node requires that no node provides and no input supplied (a missing input, or one declared with no value).
    asks: tuple[str, ...]
    #: Always 0: replay calls no operation.
    ran_operations: int = 0


def _check_names(ir: MissionIR, inputs: Mapping[str, Any], decisions: Collection[str], recorded: Mapping[str, Any]) -> None:
    declared = {name for name, _kind in ir.inputs}
    for name in inputs:
        if name not in declared:
            raise ValueError(f"{name!r} is not an input of this mission (it declares {sorted(declared)})")
    gates = {n.id for n in ir.nodes if n.gate}
    work = {n.id for n in ir.nodes if not n.gate}
    for node_id in decisions:
        if node_id not in gates:
            raise ValueError(f"a decision names {node_id!r}, which is no gate of this mission")
    for node_id in recorded:
        if node_id not in work:
            raise ValueError(f"a recording names {node_id!r}, which is no node that runs (a gate has no recorded output)")


def replay(ir: MissionIR, *, inputs: Mapping[str, Any], decisions: Collection[str],
           recorded: Mapping[str, Mapping[str, Any]]) -> Trace:
    """Walk the graph over ``inputs`` (a value of None is no value), the gates ``decisions`` approve and the ``recorded``
    outputs per node; return what happened at each step. Raises ``ValueError`` for a name that is in no part of the graph."""
    require_research()
    _check_names(ir, inputs, decisions, recorded)
    by_id = {n.id: n for n in ir.nodes}
    provider = {name: n.id for n in ir.nodes for name, _kind in n.provides}
    try:
        order = tuple(TopologicalSorter({n.id: {provider[name] for name, _k in n.requires if name in provider}
                                         for n in ir.nodes}).static_order())
    except CycleError as exc:
        raise ValueError(f"the graph loops: {' -> '.join(map(str, exc.args[1]))}") from None
    available = {name: value for name, value in inputs.items() if value is not None}
    steps: list[Step] = []
    for node_id in order:
        node = by_id[node_id]
        base = {"node": node.id, "source_operation": node.source_operation, "declared_effect": node.declared_effect,
                "gate": node.gate}
        missing = tuple(sorted(name for name, _kind in node.requires if name not in available))
        provides = [name for name, _kind in node.provides]
        if missing:
            steps.append(Step(**base, status=BLOCKED, needs=missing))
        elif node.gate:
            decided = node.id in decisions
            available.update({name: DECIDED for name in provides} if decided else {})
            steps.append(Step(**base, status=DECIDED if decided else AWAITING))
        elif node.id not in recorded:
            steps.append(Step(**base, status=NO_RECORDING))
        elif set(recorded[node.id]) != set(provides):
            steps.append(Step(**base, status=MISMATCH))
        else:
            available.update(recorded[node.id])
            steps.append(Step(**base, status=REPLAYED))
    asks = tuple(sorted({name for n in ir.nodes for name, _kind in n.requires
                         if name not in provider and name not in available}))
    return Trace(tuple(steps), asks)
