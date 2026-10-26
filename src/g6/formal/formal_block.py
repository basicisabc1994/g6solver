# SPDX-License-Identifier: Apache-2.0
"""FormalBlock: the registry and MCP door (``g6_invoke``) to g6.formal's checks. In process: Z3 and pure Python only.

Each op calls one public function of the package and returns its labelled result unchanged, with ``component`` and
``op``: the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:func:`g6.formal.envelope.apply_envelope`, so a refusal (a term outside the grammar, an unknown sort, no Z3 here) is a
``blocked-escalated`` result, never a raised error. Unknown ops and unknown fields are refused by the typed input
(``G6_E_INPUT_VALIDATION``), so no field can name another engine (audit 017 FM-002) or declare a confidence (FM-009).
No op runs code, writes a file or reaches the network, so none is sensitive.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.formal import four_valued, z3_depth
from g6.formal.contract import check_contract
from g6.formal.propositional import check_propositional
from g6.formal.z3_bridge import (
    DEFAULT_TIMEOUT_S,
    MAX_TIMEOUT_S,
    check_entailment,
    check_sat,
    check_validity,
    find_counterexample,
)

_MAX_TEXT = 200_000
_MAX_ITEMS = 1_000
_SORTS = "sorts={name: Int|Real|Bool|String|(_ BitVec n)|(Array S T)}"

_OPS: dict[str, str] = {
    "check_contract": "check_contract(properties, premises, sorts): check each property, refuse vacuity and surface conflict",
    "check_sat": f"check_sat(formula, {_SORTS}, assertions=[...]): satisfiable, with a model",
    "check_validity": "check_validity(premises=[...], formula=conclusion, sorts): valid, vacuous (the premises cannot "
                      "hold), circular, or a counterexample",
    "check_entailment": "check_entailment(formula, other, sorts): does formula entail other",
    "find_counterexample": "find_counterexample(formula=claim, premises=constraints, sorts): a model in which the "
                           "claim is false",
    "check_propositional": "check_propositional(formula, assertions): a Boolean formula decided by the built-in DPLL",
    "optimise": "optimise(objectives=[[minimise|maximise, term]], assertions=constraints, sorts, bounds={name: "
                "[lower, upper]}): the lexicographic optimum, confirmed by a bound and an attainment check",
    "unsat_core": "unsat_core(named={name: term}, sorts): the assertions that conflict, shrunk to a minimal core",
    "simplify": "simplify(formula=term, sorts): z3's simplified term, confirmed equivalent",
    "eliminate_quantifiers": "eliminate_quantifiers(formula, sorts): an equivalent quantifier-free formula",
    "count_models": "count_models(formula, assertions, sorts, cap): how many models over the declared symbols, exact "
                    "or at least cap",
    "check_equivalence": "check_equivalence(formula, other, premises, sorts): equivalent, or a distinguishing "
                         "assignment",
    "belnap_eval": "belnap_eval(formula, values={atom: T|F|B|N}): the four-valued value (true, false, both, neither)",
    "belnap_table": "belnap_table(formula): the four-valued truth table over the formula's atoms",
    "modal_eval": "modal_eval(formula, worlds={world: {atom: T|F|B|N}}, access=[[from, to]], world, "
                  "modality=necessarily|possibly, modal_frame): necessity or possibility over a Kripke frame",
    "frame_check": "frame_check(worlds, access, modal_frame=alethic|epistemic|deontic|temporal): the frame conditions "
                   "met and missed, with a witness",
}


class FormalInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["check_contract", "check_sat", "check_validity", "check_entailment", "find_counterexample", "check_propositional",
                "optimise", "unsat_core", "simplify", "eliminate_quantifiers", "count_models", "check_equivalence",
                "belnap_eval", "belnap_table", "modal_eval", "frame_check", "ops", "help"] = "check_sat"
    properties: list[str] = Field(default_factory=list, max_length=32)
    require_property: bool = True
    formula: str = Field("", max_length=_MAX_TEXT)
    other: str = Field("", max_length=_MAX_TEXT)
    sorts: dict[str, str] = Field(default_factory=dict, max_length=_MAX_ITEMS)
    assertions: list[str] = Field(default_factory=list, max_length=_MAX_ITEMS)
    premises: list[str] = Field(default_factory=list, max_length=_MAX_ITEMS)
    named: dict[str, str] = Field(default_factory=dict, max_length=z3_depth.MAX_NAMED)
    objectives: list[list[str]] = Field(default_factory=list, max_length=z3_depth.MAX_OBJECTIVES)
    bounds: dict[str, list[Any]] = Field(default_factory=dict, max_length=_MAX_ITEMS)
    cap: int = Field(z3_depth.DEFAULT_CAP, ge=1, le=z3_depth.MAX_CAP)
    values: dict[str, str] = Field(default_factory=dict, max_length=_MAX_ITEMS)
    worlds: dict[str, dict[str, str]] = Field(default_factory=dict, max_length=four_valued.MAX_WORLDS)
    access: list[list[str]] = Field(default_factory=list, max_length=four_valued.MAX_ACCESS)
    world: str = Field("", max_length=200)
    modality: Literal["necessarily", "possibly"] = "necessarily"
    modal_frame: str = Field("", max_length=40)
    timeout: float = Field(DEFAULT_TIMEOUT_S, gt=0, le=MAX_TIMEOUT_S)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: FormalInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


_RUN: dict[str, Callable[[FormalInput], Any]] = {
    "check_contract": lambda d: check_contract(d.properties, premises=d.premises, sorts=d.sorts,
                                               require_property=d.require_property, timeout=d.timeout, **_ids(d)),
    "check_sat": lambda d: check_sat(d.formula, d.sorts, d.assertions, timeout=d.timeout, **_ids(d)),
    "check_validity": lambda d: check_validity(d.premises, d.formula, sorts=d.sorts, timeout=d.timeout, **_ids(d)),
    "check_entailment": lambda d: check_entailment(d.formula, d.other, sorts=d.sorts, timeout=d.timeout, **_ids(d)),
    "find_counterexample": lambda d: find_counterexample(d.formula, d.premises, sorts=d.sorts, timeout=d.timeout,
                                                         **_ids(d)),
    "check_propositional": lambda d: check_propositional(d.formula, d.sorts, d.assertions, **_ids(d)),
    "optimise": lambda d: z3_depth.optimise(d.objectives, d.assertions, d.sorts, bounds=d.bounds or None,
                                            timeout=d.timeout, **_ids(d)),
    "unsat_core": lambda d: z3_depth.unsat_core(d.named, d.sorts, timeout=d.timeout, **_ids(d)),
    "simplify": lambda d: z3_depth.simplify(d.formula, d.sorts, timeout=d.timeout, **_ids(d)),
    "eliminate_quantifiers": lambda d: z3_depth.eliminate_quantifiers(d.formula, d.sorts, timeout=d.timeout,
                                                                      **_ids(d)),
    "count_models": lambda d: z3_depth.count_models(d.formula, d.sorts, d.assertions, cap=d.cap, timeout=d.timeout,
                                                    **_ids(d)),
    "check_equivalence": lambda d: z3_depth.check_equivalence(d.formula, d.other, premises=d.premises, sorts=d.sorts,
                                                              timeout=d.timeout, **_ids(d)),
    "belnap_eval": lambda d: four_valued.evaluate(d.formula, d.values, **_ids(d)),
    "belnap_table": lambda d: four_valued.truth_table(d.formula, **_ids(d)),
    "modal_eval": lambda d: four_valued.modal_evaluate(d.formula, d.worlds, d.access, d.world, modality=d.modality,
                                                       modal_frame=d.modal_frame, **_ids(d)),
    "frame_check": lambda d: four_valued.check_frame(d.worlds, d.access, d.modal_frame, **_ids(d)),
}


@component_maturity(
    "beta",
    reason="Z3 and pure-Python logic in process; each answer re-checked where a check exists; qualified-draft at best",
)
class FormalBlock(AIBlock[FormalInput, dict, dict]):
    """Decide a logic question exactly and say what the answer rests on. Never ``verified``."""

    def __init__(self, name: str = "formal", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: FormalInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["FormalBlock", "FormalInput"]
