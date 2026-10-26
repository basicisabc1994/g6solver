# SPDX-License-Identifier: Apache-2.0
"""The one labelled result of g6.lean.

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
the model accepts, because Lean's kernel accepting a proof of a statement nobody has inspected is not a verified claim.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["qualified-draft", "blocked-escalated"]


class LeanResult(BaseModel):
    """One op's answer, labelled by :func:`g6.lean.envelope.finish`.

    ``theorem`` is the name asked for; ``kind``, ``statement``, ``statement_explicit`` and ``axioms`` are what Lean
    itself reported about it (``#print``, ``#check`` as printed and again with every name in full and every numeral's
    type, ``#print axioms``); ``statement_matched`` says whether the expected statement matched (``None`` when none was
    given). ``messages`` are Lean's messages on the caller's own lines, with the caller's line numbers; ``refusals``
    are what G6's screen refused before Lean ran, each with its code, line and column.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    lean_version: str = ""
    lean_target: str = ""
    executable: str = ""
    theorem: str = ""
    kind: str = ""
    statement: str = ""
    statement_explicit: str = ""
    expected_statement: str = ""
    statement_matched: bool | None = None
    axioms: list[str] | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    refusals: list[dict[str, Any]] = Field(default_factory=list)
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


__all__ = ["LeanResult", "State"]
