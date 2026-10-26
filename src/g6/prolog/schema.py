# SPDX-License-Identifier: Apache-2.0
"""The one labelled result of g6.prolog.

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
the model accepts, because an answer SWI-Prolog derives from a program nobody has inspected is not a verified claim.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["qualified-draft", "blocked-escalated"]


class PrologResult(BaseModel):
    """One op's answer, labelled by :func:`g6.prolog.envelope.finish`.

    ``answers`` are what SWI-Prolog derived, in its order: each one's ``bindings`` (each named variable of the goal and
    its value, written as Prolog text, a variable left free as ``_V1``, ``_V2`` and so on) and ``text``, the whole
    answer as one term, which the re-check read back. ``exhausted`` is true only when the search finished below
    ``max_solutions``; ``stopped_by`` names what stopped it otherwise. ``succeeded`` is ``prove``'s answer.
    ``predicates`` are the program's own (name, arity, clauses and first line); ``problems`` are what was refused or
    did not read, each with its line and column (counted from 0, as SWI-Prolog counts). ``recheck`` is the second
    process's verdict on every answer and, when the search finished, on its end (index -1), or on the failure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    swipl_version: str = ""
    executable: str = ""
    goal: str = ""
    answers: list[dict[str, Any]] | None = None
    exhausted: bool | None = None
    succeeded: bool | None = None
    stopped_by: str = ""
    predicates: list[dict[str, Any]] | None = None
    problems: list[dict[str, Any]] = Field(default_factory=list)
    recheck: dict[str, Any] | None = None
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


__all__ = ["PrologResult", "State"]
