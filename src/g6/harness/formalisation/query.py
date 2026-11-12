# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Named engine questions with explicit polarity (G6 0.3 design, sections 3.4 and 15.4).

An engine answers the query it was given. What that answer means depends on the question asked, so every request
records the question, its quantifier, the assumptions, the bounds and the exact text the engine received, and every
result keeps the engine's own verdict beside G6's reading of it:

- ``witness`` (exists): SAT is a witness, with the model; UNSAT is "no witness for this encoding".
- ``valid_under`` (forall): the engine searches the assumptions with the obligation negated. SAT is a
  counterexample; UNSAT supports the entailment under those assumptions. Whether the assumptions can hold at all is
  a separate ``witness`` question, asked by the caller.
- ``differ`` (forall): SAT of the domain with the two relations' difference distinguishes them; UNSAT is
  equivalence under those assumptions only.

Unknown, a timeout, an unavailable engine and a refused input are never a decision: their readings are
``undecided``, ``unavailable`` and ``refused``, the engine's own ``G6_E_*`` code is kept, and ``decided`` is false.
The SMT questions call ``g6.formal.z3_bridge.check_sat``; no solver is driven here.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from g6.formal import z3_bridge
from g6.formal.envelope import SOLVER_TIMEOUT, SOLVER_UNAVAILABLE, SOLVER_UNKNOWN

READINGS = ("witness", "no_witness_for_this_encoding", "counterexample", "entailed_under_assumptions",
            "distinguished", "equivalent_under_assumptions", "undecided", "unavailable", "refused")
DECIDED = frozenset(READINGS[:6])
QUESTIONS = ("witness", "validity", "difference", "lean_theorem", "tla_property", "datalog_goal")


@dataclass(frozen=True)
class EngineQuery:
    """One request to an engine: the named question, its quantifier, and exactly what the engine received."""

    question: Literal["witness", "validity", "difference", "lean_theorem", "tla_property", "datalog_goal"]
    quantifier: Literal["exists", "forall"]
    assumptions: tuple[str, ...]
    bounds: tuple[tuple[str, int], ...]
    native_query: str
    engine: str
    engine_version: str
    statement_sha256: str

    def __post_init__(self) -> None:
        if self.question not in QUESTIONS or self.quantifier not in ("exists", "forall"):
            raise ValueError("an engine query names its question and its quantifier")


@dataclass(frozen=True)
class QueryResult:
    """An engine's own verdict (``native``) and G6's reading of it for the question asked."""

    query: EngineQuery
    native: str
    reading: str
    witness: Mapping[str, Any] | None = None
    code: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.reading not in READINGS:
            raise ValueError(f"a reading is one of {', '.join(READINGS)}")

    @property
    def decided(self) -> bool:
        return self.reading in DECIDED

    def to_dict(self) -> dict:
        return {"question": self.query.question, "quantifier": self.query.quantifier,
                "assumptions": list(self.query.assumptions), "bounds": [list(b) for b in self.query.bounds],
                "native_query": self.query.native_query, "engine": self.query.engine,
                "engine_version": self.query.engine_version, "statement_sha256": self.query.statement_sha256,
                "native": self.native, "reading": self.reading,
                "witness": None if self.witness is None else dict(self.witness), "code": self.code,
                "reason": self.reason}


def undecided_reading(code: str) -> str:
    """The reading of an engine result that did not decide, from the engine's own code."""
    if code == SOLVER_UNAVAILABLE:
        return "unavailable"
    return "undecided" if code in (SOLVER_UNKNOWN, SOLVER_TIMEOUT) else "refused"


def _completed(model: Mapping[str, Any], sorts: Mapping[str, str]) -> dict[str, Any]:
    """The model with a value for every declared Int and Bool symbol. Z3 leaves out a symbol only when any value of
    it satisfies the query, so 0 or false is as much a part of the witness as any other."""
    out = dict(model)
    for name, sort in sorts.items():
        if name not in out and sort in ("Int", "Bool"):
            out[name] = 0 if sort == "Int" else False
    return out


def _ask(question: str, quantifier: str, sorts: Mapping[str, str], searched: Sequence[str],
         assumptions: Sequence[str], on_sat: str, on_unsat: str, statement_sha256: str,
         timeout: float) -> QueryResult:
    terms = list(searched) or ["true"]
    found = z3_bridge.check_sat(terms[0], dict(sorts), terms[1:], timeout=timeout)
    artifact = found.artifact
    native_query = artifact.checks[0].query if artifact and artifact.checks else ""
    query = EngineQuery(question=question, quantifier=quantifier, assumptions=tuple(assumptions), bounds=(),
                        native_query=native_query, engine="z3",
                        engine_version=artifact.solver_version if artifact else "",
                        statement_sha256=statement_sha256)
    if found.satisfiable is True:
        return QueryResult(query, "sat", on_sat, witness=_completed(found.model, sorts))
    if found.satisfiable is False:
        return QueryResult(query, "unsat", on_unsat)
    native = artifact.checks[0].result if artifact and artifact.checks else found.code
    return QueryResult(query, native, undecided_reading(found.code), code=found.code, reason=found.reason)


def witness(sorts: Mapping[str, str], conjuncts: Sequence[str], *, statement_sha256: str,
            timeout: float = 10.0) -> QueryResult:
    """Does the conjunction have a witness? SAT: ``witness`` with the model. UNSAT: ``no_witness_for_this_encoding``."""
    return _ask("witness", "exists", sorts, conjuncts, conjuncts, "witness", "no_witness_for_this_encoding",
                statement_sha256, timeout)


def valid_under(sorts: Mapping[str, str], assumptions: Sequence[str], obligation: str, *, statement_sha256: str,
                timeout: float = 10.0) -> QueryResult:
    """Is the obligation valid under the assumptions? The search is for the assumptions with the obligation negated:
    SAT is a ``counterexample``, UNSAT is ``entailed_under_assumptions`` (which says nothing when the assumptions
    cannot hold: ask ``witness`` of them apart)."""
    return _ask("validity", "forall", sorts, [*assumptions, f"(not {obligation})"], assumptions, "counterexample",
                "entailed_under_assumptions", statement_sha256, timeout)


def differ(sorts: Mapping[str, str], domain: Sequence[str], a: str, b: str, *, statement_sha256: str,
           timeout: float = 10.0) -> QueryResult:
    """Do two relations differ in the domain? SAT of the domain with their difference is ``distinguished``, with the
    assignment; UNSAT is ``equivalent_under_assumptions``, under this domain only."""
    return _ask("difference", "forall", sorts, [*domain, f"(xor {a} {b})"], domain, "distinguished",
                "equivalent_under_assumptions", statement_sha256, timeout)


__all__ = ["DECIDED", "QUESTIONS", "READINGS", "EngineQuery", "QueryResult", "differ", "undecided_reading",
           "valid_under", "witness"]
