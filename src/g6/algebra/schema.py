# SPDX-License-Identifier: Apache-2.0
"""The one labelled result of g6.algebra.

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
the model accepts, because a computer algebra system's answer, even one G6 re-checked, is not a proof a person has
inspected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["qualified-draft", "blocked-escalated"]
Status = Literal["passed", "refuted", "undecided"]


@dataclass(frozen=True)
class Recheck:
    """G6's re-check of one answer: ``passed`` (every method agreed), ``refuted`` (one found the answer wrong, named in
    ``failures``) or ``undecided`` (it could not decide, said in ``why``; never reported as passed). ``points`` is how
    many sample points a numeric method used, ``tolerance`` the agreement it asked for."""

    status: Status
    methods: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    why: str = ""
    points: int = 0
    tolerance: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "methods": list(self.methods), "failures": list(self.failures), "why": self.why,
                "points": self.points, "tolerance": self.tolerance, "details": self.details}


class AlgebraResult(BaseModel):
    """One op's answer, labelled by :func:`g6.algebra.envelope.finish`.

    ``input`` is G6's own reading of what was asked (each expression rendered back from G6's parse). ``answer`` is the
    answer as text in SymPy's canonical form (or SageMath's) and ``latex`` the same in LaTeX; ``answers`` lists the
    parts of an answer that has several (solutions, eigenvalues, prime factors). ``complete`` says, for ``solve`` and
    ``eigenvalues``, whether the answer is the whole set (``None`` where it does not apply) and ``completeness`` says it
    in words. ``recheck`` is G6's independent re-check: its ``status`` (``passed``, ``refuted`` or ``undecided``), the
    methods, the points and tolerance, and any failures. ``refusals`` are what G6's parser refused, each with its code,
    the field, the line and the column.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    engine: str = ""
    engine_version: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    answer: str = ""
    latex: str = ""
    answers: list[dict[str, Any]] = Field(default_factory=list)
    complete: bool | None = None
    completeness: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    recheck: dict[str, Any] = Field(default_factory=dict)
    refusals: list[dict[str, Any]] = Field(default_factory=list)
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


__all__ = ["AlgebraResult", "Recheck", "State", "Status"]
