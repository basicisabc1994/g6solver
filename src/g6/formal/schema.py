# SPDX-License-Identifier: Apache-2.0
"""The typed input and the labelled results of g6.formal (audit 017 §11: the archive's ``schema.py``, trimmed).

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
these models accept, because nothing in this package is a proof explained to and inspected by a person. A result is
built as ``blocked-escalated`` with no warning card; only :func:`g6.formal.envelope.apply_envelope` labels it.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["qualified-draft", "blocked-escalated"]


class SolverCheck(BaseModel):
    """One query exactly as the solver read it, and the solver's answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    result: Literal["sat", "unsat", "unknown"]


class ProofArtifact(BaseModel):
    """What was asked, of which solver and version, through which queries, with which answers.

    ``statement``, ``sorts``, ``assumptions`` and ``goal`` determine the queries, and :func:`g6.formal.recheck` rebuilds
    them and runs them again. The archive's artifact was an unkeyed SHA-256 over its own payload, which shows only that
    nobody edited it after hashing (audit 017 FM-001); there is no hash here to trust.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    question: Literal["satisfiable", "valid"]
    statement: str
    sorts: dict[str, str] = Field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    goal: str = ""
    solver: str
    solver_version: str
    checks: tuple[SolverCheck, ...]


class _Labelled(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    backend: str
    #: A ``G6_E_*`` code when no usable decision was reached (refused, unavailable, unreadable, undecided, vacuous,
    #: circular), with the reason in words.
    code: str = ""
    reason: str = ""
    artifact: ProofArtifact | None = None
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""

    def certificate(self) -> dict[str, Any]:
        """What the evidence carries beyond the artifact: the model, core, term or count the answer rests on."""
        return {}

    def note(self) -> str:
        """A sentence the warning card must carry about this answer, beyond its label."""
        return ""


class FormalResult(_Labelled):
    """A satisfiability decision. ``satisfiable`` is ``None`` when the solver did not decide; ``model`` is a
    satisfying assignment when it is ``True``."""

    satisfiable: bool | None = None
    model: dict[str, Any] = Field(default_factory=dict)


class OptimumResult(FormalResult):
    """A lexicographic optimum. ``satisfiable`` says whether the constraints can hold; ``optimal`` is ``True`` only
    when Z3's optimum passed both checks on fresh solvers: the artifact's bound (no feasible point is better, decided
    as a validity question) and ``attainment`` (a feasible point reaches every value). Each objective is
    ``{direction, term, value, lower, upper}``, values as SMT-LIB text."""

    optimal: bool = False
    objectives: list[dict[str, str]] = Field(default_factory=list)
    attainment: SolverCheck | None = None

    def certificate(self) -> dict[str, Any]:
        return {"objectives": self.objectives, "model": self.model,
                "attainment": self.attainment.model_dump() if self.attainment else None}


class CoreResult(FormalResult):
    """An unsat core: the names of assertions that cannot hold together (``satisfiable`` is ``False``), confirmed
    unsatisfiable on a fresh solver (the artifact). ``minimal`` is ``True`` when dropping any one of them was decided
    satisfiable (the ``minimality`` checks), ``None`` when that was not decided."""

    core: list[str] = Field(default_factory=list)
    minimal: bool | None = None
    minimality: list[SolverCheck] = Field(default_factory=list)

    def certificate(self) -> dict[str, Any]:
        return {"core": self.core, "minimal": self.minimal, "model": self.model,
                "minimality": [c.model_dump() for c in self.minimality]}


class CountResult(FormalResult):
    """Models over the declared symbols, enumerated up to ``cap``. ``exact`` is ``True`` when a fresh solver decided
    that no model outside ``models`` exists (the artifact); ``cap_hit`` when one does, so ``count`` is a lower bound."""

    count: int = 0
    exact: bool = False
    cap: int = 0
    cap_hit: bool = False
    models: list[dict[str, Any]] = Field(default_factory=list)

    def certificate(self) -> dict[str, Any]:
        return {"count": self.count, "exact": self.exact, "cap": self.cap, "cap_hit": self.cap_hit,
                "models": self.models}

    def note(self) -> str:
        if self.cap_hit:
            return (f"The cap of {self.cap} was hit and another model exists, so {self.count} is a lower bound, not "
                    "the count.")
        return ""


class TermResult(_Labelled):
    """A term rewritten by Z3 (``simplify``, ``eliminate_quantifiers``): ``result_term`` is ``equivalent`` to ``term``
    only when a fresh solver decided ``(= term result_term)`` valid (the artifact); ``counterexample`` distinguishes
    them when it is not. ``quantifier_free`` is set by quantifier elimination."""

    term: str = ""
    result_term: str = ""
    equivalent: bool = False
    quantifier_free: bool | None = None
    counterexample: dict[str, Any] | None = None

    def certificate(self) -> dict[str, Any]:
        return {"term": self.term, "result_term": self.result_term, "quantifier_free": self.quantifier_free,
                "counterexample": self.counterexample}


class FourValuedResult(_Labelled):
    """A four-valued (Belnap) evaluation by :mod:`g6.formal.four_valued`: ``value`` is T, F, B (both) or N (neither)
    for one formula, ``table`` a truth table, ``frame`` a modal frame's properties. ``computed`` is ``True`` once the
    evaluator ran on the inputs. ``vacuous``: a necessity held only because the world sees no other world."""

    value: Literal["T", "F", "B", "N", ""] = ""
    formula: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    table: list[dict[str, str]] = Field(default_factory=list)
    frame: dict[str, Any] = Field(default_factory=dict)
    vacuous: bool = False
    computed: bool = False

    def certificate(self) -> dict[str, Any]:
        return {"formula": self.formula, "inputs": self.inputs, "value": self.value, "table": self.table,
                "frame": self.frame, "vacuous": self.vacuous}


class ValidityResult(_Labelled):
    """``premises ⊨ conclusion``, decided as UNSAT(premises ∧ ¬conclusion), with the premises checked for consistency.

    ``valid`` is ``True`` only when the solver decided it and the premises are satisfiable. ``vacuous``: the premises
    are unsatisfiable, so they entail anything, and ``valid`` is ``False``. ``conclusion_assumed``: every conjunct of
    the conclusion is literally a premise or a conjunct of one, so the check is circular (audit 017 FM-014, and the
    check_proof half of FM-003). ``counterexample`` is a model of premises ∧ ¬conclusion.
    """

    valid: bool = False
    vacuous: bool = False
    conclusion_assumed: bool = False
    counterexample: dict[str, Any] | None = None


class LogicalQuery(BaseModel):
    """One satisfiability question for :func:`g6.formal.solve`.

    Unknown fields are refused, so a ``backend``, ``operation`` or ``source_payload`` naming another engine, or a
    caller-declared confidence, cannot ride along (audit 017 FM-002, FM-009). ``variables`` maps each symbol to its
    sort for ``z3``; for ``propositional`` only the names are used.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    formula: str = Field(min_length=1)
    solver: Literal["z3", "propositional"] = "z3"
    variables: dict[str, str] = Field(default_factory=dict)
    assertions: list[str] = Field(default_factory=list)
    timeout_sec: float = Field(default=10.0, gt=0, le=600)
    request_id: str = ""
    run_id: str = ""


__all__ = ["CoreResult", "CountResult", "FormalResult", "FourValuedResult", "LogicalQuery", "OptimumResult",
           "ProofArtifact", "SolverCheck", "State", "TermResult", "ValidityResult"]
