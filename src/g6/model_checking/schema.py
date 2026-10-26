# SPDX-License-Identifier: Apache-2.0
"""The one labelled result of g6.model_checking, and the typed symbolic state model.

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
the model accepts, because a model checker's run on a caller's model is not a proof a person has inspected.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["qualified-draft", "blocked-escalated"]

#: Shape limits on what is parsed; the package's own, smaller limits are refused with their own codes.
STRUCTURAL_LIMIT = 1_000
TEXT_LIMIT = 100_000


class StateModel(BaseModel):
    """A symbolic state model: state variables with their sorts, and SMT-LIB terms over them.

    ``init`` is over the state variables; ``transition`` is over them and their next-state copies, written ``next_x``
    for a variable ``x``; each named invariant is over the state variables. Fields the model does not have are refused.
    """

    model_config = ConfigDict(extra="forbid")

    variables: dict[str, str] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)
    init: str = Field("", max_length=TEXT_LIMIT)
    transition: str = Field("", max_length=TEXT_LIMIT)
    invariants: dict[str, str] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)


class ModelCheckResult(BaseModel):
    """One op's answer, labelled by :func:`g6.model_checking.envelope.finish`.

    ``engine`` names what ran (``z3``, ``tlc`` or ``sany``; empty when nothing did) and ``verdict`` what it found.
    ``depth`` is the bound asked for, ``explored_depth`` how far the search went (less when every path ends sooner, in
    which case ``all_paths_end``) and ``k`` the k of a k-induction. ``trace`` is a list of states; ``trace_kind`` says
    what it is: a ``counterexample`` from the initial states, an ``induction_step`` (which need not start in a reachable
    state, so it is not a counterexample), a path to a ``deadlock`` or a ``witness`` that reaches the goal. ``recheck``
    is G6's own check of the answer on fresh solvers; ``tla`` is what TLC or SANY reported. A refused answer keeps no
    trace, except a violation, whose trace is its evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    engine: str = ""
    engine_version: str = ""
    verdict: str = ""
    depth: int | None = None
    explored_depth: int | None = None
    all_paths_end: bool = False
    k: int | None = None
    violated: list[str] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    trace_kind: str = ""
    recheck: dict[str, Any] = Field(default_factory=dict)
    model: dict[str, Any] = Field(default_factory=dict)
    tla: dict[str, Any] = Field(default_factory=dict)
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


__all__ = ["ModelCheckResult", "State", "StateModel"]
