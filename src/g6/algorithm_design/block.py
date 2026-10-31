# SPDX-License-Identifier: Apache-2.0
"""AlgorithmDesignBlock: the registry and MCP door (``g6_invoke``) to g6.algorithm_design.

Each op answers a proposal or one checked answer: catalogue patterns, bundled ontology metadata, cost estimates for a
device the caller describes, a bounded search of type-correct compositions, one candidate fused with a change-impact
map's frozen intent, the list of G6's fixed kernels, or one kernel's answer with how it was checked. A candidate is
never approved, and the only code that runs is G6's own fixed kernels on plain data. A refusal is a
``blocked-escalated`` reply with its own code, never a raised error. What the caller declares (established properties,
a requirement's authority, a device's limits) is kept as their word under ``evidence[0]`` with
``declared_by: "caller"`` and never raises a state.
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.algorithm_design import codes, invocation
from g6.algorithm_design.catalog import primitives
from g6.algorithm_design.checked_kernels import ContractError
from g6.algorithm_design.fusion import fuse
from g6.algorithm_design.ir import DesignProblem, DesignSearch
from g6.algorithm_design.knowledge import bundled_ontology, search_bundled_ontology
from g6.algorithm_design.knowledge_bridge import catalog_plan
from g6.algorithm_design.resources import DeviceBudget, rank_options
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
    "estimate": "estimate(algorithms, dimensions, device): each algorithm's abstract operations, memory and scratch for "
                "your input sizes on the device you describe, and whether it fits; an algorithm with no cost model is "
                "refused, never guessed",
    "fuse": "fuse(representations, target, candidate_id, files, package, requirements, obligations, "
            "environment_digest, changed): one searched candidate bound to the tree's frozen intent and cone, as one "
            "check plan; it approves nothing",
    "list_kernels": "list_kernels(): G6's 22 fixed algorithm kernels, each run through one entry point",
    "run_kernel": "run_kernel(algorithm_id, parameters): one fixed kernel on plain data (at most 16 parameters), with "
                  "its answer, its witness and how that one answer was checked; never a proof about every input",
}
_NEXT = "Treat every candidate as a proposal: check its proof obligations and your normative obligations before use."

Name = Annotated[str, Field(min_length=1, max_length=100)]
Text = Annotated[str, Field(max_length=500)]
Size = Annotated[int, Field(ge=0, le=10**12)]


class DeviceIn(BaseModel):
    """The device a caller describes for ``estimate``: ``DeviceBudget``'s fields, as the caller's word."""

    model_config = ConfigDict(extra="forbid")

    ram_bytes: int = Field(..., ge=1, le=10**15)
    scratch_bytes: int = Field(0, ge=0, le=10**15)
    max_operations: int = Field(..., ge=1, le=10**18)
    cpu_cores: int = Field(1, ge=1, le=4096)
    accelerator: Literal["none", "cuda", "metal", "rocm", "tpu"] = "none"
    checked_accelerator_kernels: list[Name] = Field(default_factory=list, max_length=32)
    allow_pseudopolynomial: bool = True
    allow_exponential: bool = False
    allow_external_memory: bool = True


class AlgorithmDesignInput(TreeFields):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["primitives", "ontology", "search", "estimate", "fuse", "list_kernels", "run_kernel", "ops",
                "help"] = "help"
    algorithms: list[Name] = Field(default_factory=list, max_length=32)
    dimensions: dict[Annotated[str, Field(max_length=20)], Size] = Field(default_factory=dict, max_length=8)
    device: DeviceIn | None = None
    algorithm_id: str = Field("", max_length=100)
    parameters: dict[Annotated[str, Field(max_length=100)], Any] = Field(default_factory=dict, max_length=16)
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
    """The bounded search; with ``problem_id``, the catalogue gains the ontology's reviewed typed primitives for it
    (``knowledge_bridge.catalog_plan``), each still gated by the properties the caller established."""
    problem = DesignProblem(tuple(d.representations), d.target, tuple(d.established_properties),
                            tuple(d.constraints))
    typed = catalog_plan(problem, problem_id=d.problem_id).typed_primitives if d.problem_id else ()
    return search_algorithms(problem, catalog=(*primitives(), *typed), max_depth=d.max_depth,
                             max_expansions=d.max_expansions, max_candidates=d.max_candidates)


def _json(value: Any) -> Any:
    """A kernel's output or witness as JSON data (tuples as lists, sets sorted, keys as text)."""
    def plain(item: Any) -> Any:
        if isinstance(item, (set, frozenset)):
            return sorted(item, key=repr)
        raise TypeError(type(item).__name__)
    return json.loads(json.dumps(value, default=plain))


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


def _estimate(d: AlgorithmDesignInput) -> dict[str, Any]:
    if d.device is None:
        return _refuse(d, codes.INPUT_INVALID, "estimate needs device: its ram_bytes and max_operations at least")
    device = d.device.model_dump()
    device["checked_accelerator_kernels"] = tuple(device["checked_accelerator_kernels"])
    rows = rank_options(tuple(d.algorithms), dict(d.dimensions), DeviceBudget(**device))
    reply = _reply(d, "qualified-draft", codes.ESTIMATE, "hard-coded cost models for the sizes and device you gave; "
                                                         "an estimate, never a measurement or a proof of a bound",
                   {"estimates": [{**dataclasses.asdict(r), "completion_state": r.completion_state} for r in rows]})
    reply["evidence"][0]["device"] = d.device.model_dump()
    return reply


def _list_kernels(d: AlgorithmDesignInput) -> dict[str, Any]:
    return _reply(d, "qualified-draft", codes.CATALOGUE, "G6's fixed kernels, all through one entry point",
                  {"kernels": _rows(invocation.list_kernels())})


def _run_kernel(d: AlgorithmDesignInput) -> dict[str, Any]:
    if not d.algorithm_id.strip():
        return _refuse(d, codes.KERNEL_REFUSED, "run_kernel needs algorithm_id; list_kernels names them")
    try:
        got = invocation.invoke(d.algorithm_id, d.parameters)
    except ContractError as exc:
        return _refuse(d, codes.KERNEL_REFUSED, str(exc)[:500], algorithm_id=d.algorithm_id)
    except AssertionError as exc:   # the kernel's own check of its answer failed: no answer is given
        return _refuse(d, codes.KERNEL_REFUSED, f"the kernel's own check refused its answer: {str(exc)[:300]}",
                       algorithm_id=d.algorithm_id)
    reply = _reply(d, "qualified-draft", codes.KERNEL_CHECKED,
                   f"one answer of a fixed kernel, checked by {got.verification}; not a proof about every input",
                   {"algorithm_id": got.algorithm_id, "output": _json(got.output), "witness": _json(got.witness),
                    "verification": got.verification, "complexity": got.complexity,
                    "limitations": list(got.limitations)})
    reply["evidence"] = [{"source": "g6.algorithm_design.invocation", "kernel": got.algorithm_id,
                          "summary": f"G6's fixed kernel checked this one answer: {got.verification}"}]
    return reply


_RUN: dict[str, Callable[[AlgorithmDesignInput], dict[str, Any]]] = {
    "primitives": _primitives, "ontology": _ontology, "search": _search_op, "estimate": _estimate, "fuse": _fuse,
    "list_kernels": _list_kernels, "run_kernel": _run_kernel}
_NEEDS = {"search": ("representations", "target"), "fuse": ("representations", "target"),
          "estimate": ("algorithms",)}


@block_contract(
    purpose="suggest catalogued algorithms for a stated problem, each with its preconditions, claimed cost and proof "
            "obligations, estimate their cost on a stated device, bind a candidate to what a change must keep, and "
            "run G6's fixed algorithm kernels on plain data, each checking its own answer",
    mitigates_failure_modes=("reinvented_method", "unproven_optimum"),
    verification_method="a bounded, deterministic search over a fixed catalogue that never assumes a precondition the "
                        "caller did not establish; every candidate stays a proposal with its proof obligations; a "
                        "kernel's answer carries the instance check it passed, never a proof about every input",
)
@component_maturity(
    "beta",
    reason="a fixed catalogue, bundled ontology metadata, hard-coded cost models, a bounded type-directed search, "
           "fusion with the change-impact map and 22 fixed kernels with instance checks, tested on their contract "
           "examples, missing preconditions and inputs past each bound; qualified-draft at best",
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
