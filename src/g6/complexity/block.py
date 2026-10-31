# SPDX-License-Identifier: Apache-2.0
"""ComplexityBlock: the registry and MCP door (``g6_invoke``) to g6.complexity.

Each op reads the file map it is sent, without importing or running it, and answers a ``qualified-draft`` bound to that
tree's fingerprint. A refusal is a ``blocked-escalated`` reply with its own code, never a raised error. What the
caller declares (a requirement's authority, an obligation's normative flag, a state annotation) comes back under
``evidence[0]`` with ``declared_by: "caller"``: it chooses which obligations a change must keep and never raises a
state. No op is sensitive: nothing runs and nothing is written.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.complexity import codes
from g6.complexity.impact import impact_cone
from g6.complexity.inputs import Path, TreeFields, atlas_of, declared
from g6.complexity.ir import CIR
from g6.complexity.simplify import propose_simplifications
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.result import Result

COMPONENT = "complexity"
_OPS: dict[str, str] = {
    "atlas": "atlas(files, package, requirements, obligations, environment_digest): the package's modules, functions, "
             "imports, calls, shared state, effects and unknowns, read from the source without running it, with the "
             "fingerprint that binds them to the tree, intent and environment",
    "impact": "impact(..., changed): every component a change to the named modules or functions can reach through "
              "imports, calls and shared writable state, the normative obligations it must keep, and the uncertainty",
    "simplify": "simplify(..., state_annotations): proposals for state you marked accidental_avoidable, each with the "
                "checks a change must pass; proposals only",
}
_NEXT = "Read unknowns before trusting the map; it is a static reading of the source you sent, not a test of the code."


class StateNote(BaseModel):
    """What the caller says about one piece of state the atlas found: kept as their word."""

    model_config = ConfigDict(extra="forbid")

    essentiality: Literal["essential", "accidental_useful", "accidental_avoidable", "unknown"]
    derivation: str = Field("", max_length=500)
    justification: str = Field("", max_length=500)


class ComplexityInput(TreeFields):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["atlas", "impact", "simplify", "ops", "help"] = "help"
    state_annotations: dict[Path, StateNote] = Field(default_factory=dict, max_length=200)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _rows(items: Any) -> list[dict[str, Any]]:
    return [dataclasses.asdict(item) for item in items]


def _reply(d: ComplexityInput, state: str, code: str, message: str, body: dict[str, Any],
           evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"component": COMPONENT, "op": d.op, **body,
            **envelope_fields(reliability_label=state, warning_card=warning_card(code, message, _NEXT),
                              evidence=evidence if evidence is not None else [declared(d)],
                              request_id=d.request_id, run_id=d.run_id)}


def _refuse(d: ComplexityInput, why: str) -> dict[str, Any]:
    return _reply(d, "blocked-escalated", codes.INPUT_INVALID, f"{d.op} refused: {why}", {"reason": why})


def _map(cir: CIR) -> dict[str, Any]:
    return {"fingerprint": cir.fingerprint, "tree_digest": cir.snapshot.tree_digest,
            "environment_digest": cir.snapshot.environment_digest, "intent_fingerprint": cir.intent.fingerprint,
            "components": list(cir.components), "dependencies": _rows(cir.dependencies), "state": _rows(cir.state),
            "effects": _rows(cir.effects), "findings": _rows(cir.findings), "unknowns": list(cir.unknowns),
            "complete": not cir.unknowns, "vector": cir.vector()}


def _atlas(d: ComplexityInput, cir: CIR) -> dict[str, Any]:
    return _reply(d, "qualified-draft", codes.ATLAS, "a static map of the source you sent, bound to its fingerprint",
                  _map(cir))


def _impact(d: ComplexityInput, cir: CIR) -> dict[str, Any]:
    if not d.changed:
        return _refuse(d, "impact needs changed: the modules or functions the change touches")
    cone = impact_cone(cir, tuple(d.changed))
    return _reply(d, "qualified-draft", codes.IMPACT, "the reverse cone of the change, from static may-facts",
                  {"fingerprint": cir.fingerprint, "changed": list(cone.changed), "impacted": list(cone.impacted),
                   "preservation_obligations": list(cone.preservation_obligations),
                   "uncertainty": list(cone.uncertainty), "parallel_safe": cone.parallel_safe,
                   "complete": cone.complete})


def _simplify(d: ComplexityInput, cir: CIR) -> dict[str, Any]:
    found = {s.id for s in cir.state}
    unknown = sorted(set(d.state_annotations) - found)
    if unknown:
        return _refuse(d, f"state_annotations name state the atlas did not find: {', '.join(unknown[:10])}")
    state = tuple(dataclasses.replace(s, **d.state_annotations[s.id].model_dump()) if s.id in d.state_annotations
                  else s for s in cir.state)
    proposals = propose_simplifications(dataclasses.replace(cir, state=state))
    evidence = [{**declared(d), "state_annotations": {k: v.model_dump() for k, v in sorted(d.state_annotations.items())}}]
    return _reply(d, "qualified-draft", codes.SIMPLIFY, "proposals for state you marked avoidable; nothing is applied",
                  {"fingerprint": cir.fingerprint, "proposals": _rows(proposals)}, evidence)


_RUN: dict[str, Callable[[ComplexityInput, CIR], dict[str, Any]]] = {
    "atlas": _atlas, "impact": _impact, "simplify": _simplify}


@block_contract(
    purpose="map a Python package's modules, shared state and effects from its source, without running it, and list "
            "what a change can reach and which stated obligations it must keep",
    mitigates_failure_modes=("outside_scope", "unmanaged_complexity"),
    verification_method="a static AST reading of the file map sent; each answer carries the fingerprint of the tree, "
                        "intent and environment it read, and every call, import or effect it could not resolve is "
                        "listed as unknown, never assumed absent",
)
@component_maturity(
    "beta",
    reason="a static atlas, reverse impact cone and simplification proposals over a file map, tested on small "
           "packages and hostile input sizes; nothing runs, and every answer is qualified-draft at best",
)
class ComplexityBlock(AIBlock[ComplexityInput, dict, dict]):
    """The change-impact map. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: ComplexityInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            cir = atlas_of(data)
            return Result.ok(_RUN[data.op](data, cir))
        except ValueError as exc:
            return Result.ok(_refuse(data, str(exc)[:500]))


__all__ = ["ComplexityBlock", "ComplexityInput", "StateNote"]
