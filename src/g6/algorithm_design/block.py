# SPDX-License-Identifier: Apache-2.0
"""AlgorithmDesignBlock: the registry and MCP door (``g6_invoke``) to g6.algorithm_design.

Each op answers a proposal: catalogue patterns, bundled ontology metadata, a bounded search of type-correct
compositions, or one candidate fused with a change-impact map's frozen intent. A candidate is never approved and
nothing runs. A refusal is a ``blocked-escalated`` reply with its own code, never a raised error. What the caller
declares (established properties, a requirement's authority) is kept as their word under ``evidence[0]`` with
``declared_by: "caller"`` and never raises a state.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field

from g6.algorithm_design import codes
from g6.algorithm_design.catalog import primitives
from g6.algorithm_design.fusion import fuse
from g6.algorithm_design.ir import DesignProblem, DesignSearch
from g6.algorithm_design.knowledge import bundled_ontology, search_bundled_ontology
from g6.algorithm_design.search import search_algorithms
from g6.complexity.impact import impact_cone
from g6.complexity.inputs import TreeFields, atlas_of, declared
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.result import Result

COMPONENT = "algorithm_design"
_OPS: dict[str, str] = {
    "primitives": "primitives(): the catalogue's nine patterns, each with its input and output representation, "
                  "preconditions, postcondition, claimed cost and proof obligations",
    "ontology": "ontology(problem_id, established_properties, limit): the bundled ontology's algorithms for the "
                "problem, with the recognition features you have not established; metadata only",
    "search": "search(representations, target, established_properties, constraints, max_depth, max_expansions, "
              "max_candidates): type-correct compositions of catalogue patterns, proposals only, with every pattern "
              "refused for a missing precondition",
    "fuse": "fuse(representations, target, candidate_id, files, package, requirements, obligations, "
            "environment_digest, changed): one searched candidate bound to the tree's frozen intent and cone, as one "
            "check plan; it approves nothing",
}
_NEXT = "Treat every candidate as a proposal: check its proof obligations and your normative obligations before use."

Name = Annotated[str, Field(min_length=1, max_length=100)]
Text = Annotated[str, Field(max_length=500)]


class AlgorithmDesignInput(TreeFields):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["primitives", "ontology", "search", "fuse", "ops", "help"] = "help"
    representations: list[Name] = Field(default_factory=list, max_length=16)
    target: str = Field("", max_length=100)
    established_properties: list[Name] = Field(default_factory=list, max_length=50)
    constraints: list[Text] = Field(default_factory=list, max_length=50)
    problem_id: str = Field("", max_length=100)
    max_depth: int = Field(3, ge=1, le=8)
    max_expansions: int = Field(128, ge=1, le=10_000)
    max_candidates: int = Field(16, ge=1, le=500)
    limit: int = Field(20, ge=1, le=100)
    candidate_id: str = Field("", max_length=128)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _rows(items: Any) -> list[dict[str, Any]]:
    return [dataclasses.asdict(item) for item in items]


def _said(d: AlgorithmDesignInput) -> dict[str, Any]:
    return {**declared(d), "established_properties": list(d.established_properties)}


def _reply(d: AlgorithmDesignInput, state: str, code: str, message: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"component": COMPONENT, "op": d.op, **body,
            **envelope_fields(reliability_label=state, warning_card=warning_card(code, message, _NEXT),
                              evidence=[_said(d)], request_id=d.request_id, run_id=d.run_id)}


def _refuse(d: AlgorithmDesignInput, code: str, why: str, **body: Any) -> dict[str, Any]:
    return _reply(d, "blocked-escalated", code, f"{d.op} refused: {why}", {"reason": why, **body})


def _search(d: AlgorithmDesignInput) -> DesignSearch:
    problem = DesignProblem(tuple(d.representations), d.target, tuple(d.established_properties),
                            tuple(d.constraints))
    return search_algorithms(problem, max_depth=d.max_depth, max_expansions=d.max_expansions,
                             max_candidates=d.max_candidates)


def _found(found: DesignSearch) -> dict[str, Any]:
    return {"candidates": _rows(found.candidates), "expansions": found.expansions, "truncated": found.truncated,
            "rejected": list(found.rejected), "search_fingerprint": found.fingerprint, "status": found.status}


def _primitives(d: AlgorithmDesignInput) -> dict[str, Any]:
    return _reply(d, "qualified-draft", codes.CATALOGUE, "the fixed catalogue's patterns; a pattern is not proof",
                  {"primitives": _rows(primitives())})


def _ontology(d: AlgorithmDesignInput) -> dict[str, Any]:
    if not d.problem_id.strip():
        return _refuse(d, codes.INPUT_INVALID, "ontology needs problem_id")
    known = sorted(p["id"] for p in bundled_ontology()["problems"])
    if d.problem_id not in known:
        return _refuse(d, codes.INPUT_INVALID, f"no bundled problem is named {d.problem_id!r}", known=known)
    matches = search_bundled_ontology(problem_id=d.problem_id, established_features=tuple(d.established_properties),
                                      limit=d.limit)
    return _reply(d, "qualified-draft", codes.ONTOLOGY, "bundled metadata for the problem; nothing here was run",
                  {"problem_id": d.problem_id, "matches": _rows(matches)})


def _search_op(d: AlgorithmDesignInput) -> dict[str, Any]:
    found = _search(d)
    if not found.candidates:
        return _reply(d, "blocked-escalated", codes.NO_CANDIDATE,
                      "no catalogued composition reaches the target under the properties you established",
                      _found(found))
    return _reply(d, "qualified-draft", codes.PROPOSAL, "candidate compositions, proposals only", _found(found))


def _fuse(d: AlgorithmDesignInput) -> dict[str, Any]:
    if not d.candidate_id.strip():
        return _refuse(d, codes.INPUT_INVALID, "fuse needs candidate_id, the id of a candidate search returned")
    found = _search(d)
    candidate = next((c for c in found.candidates if c.id == d.candidate_id), None)
    if candidate is None:
        return _refuse(d, codes.UNKNOWN_CANDIDATE, f"the search for this problem returned no candidate "
                                                   f"{d.candidate_id!r}", candidates=[c.id for c in found.candidates])
    cir = atlas_of(d)
    cone = impact_cone(cir, tuple(d.changed)) if d.changed else None
    fused = fuse(cir, candidate, required_target=d.target, impact=cone)
    return _reply(d, fused.completion_state, codes.PROPOSAL, "one check plan for the candidate and your obligations; "
                                                             "it approves nothing",
                  {**dataclasses.asdict(fused), "ready_for_unrestricted_action": fused.ready_for_unrestricted_action,
                   "candidate": dataclasses.asdict(candidate), "fingerprint": cir.fingerprint})


_RUN: dict[str, Callable[[AlgorithmDesignInput], dict[str, Any]]] = {
    "primitives": _primitives, "ontology": _ontology, "search": _search_op, "fuse": _fuse}
_NEEDS = {"search": ("representations", "target"), "fuse": ("representations", "target")}


@block_contract(
    purpose="suggest catalogued algorithms for a stated problem, each with its preconditions, claimed cost and proof "
            "obligations, and bind a candidate to what a change must keep, proposing only",
    mitigates_failure_modes=("reinvented_method", "unproven_optimum"),
    verification_method="a bounded, deterministic search over a fixed catalogue that never assumes a precondition the "
                        "caller did not establish; every candidate stays a proposal with its proof obligations",
)
@component_maturity(
    "beta",
    reason="a fixed catalogue, bundled ontology metadata, a bounded type-directed search and fusion with the "
           "change-impact map, tested on catalogued problems and missing preconditions; proposals only, "
           "qualified-draft at best",
)
class AlgorithmDesignBlock(AIBlock[AlgorithmDesignInput, dict, dict]):
    """The algorithm designer. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AlgorithmDesignInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        missing = [f for f in _NEEDS.get(data.op, ()) if not getattr(data, f)]
        if missing:
            return Result.ok(_refuse(data, codes.INPUT_INVALID, f"{data.op} needs {', '.join(missing)}"))
        try:
            return Result.ok(_RUN[data.op](data))
        except ValueError as exc:
            return Result.ok(_refuse(data, codes.INPUT_INVALID, str(exc)[:500]))


__all__ = ["AlgorithmDesignBlock", "AlgorithmDesignInput"]
