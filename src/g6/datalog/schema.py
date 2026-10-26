# SPDX-License-Identifier: Apache-2.0
"""The one labelled result of g6.datalog.

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
the model accepts, because the model of a caller's program is not a proof a person has inspected.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["qualified-draft", "blocked-escalated"]


class DatalogResult(BaseModel):
    """One op's answer, labelled by :func:`g6.datalog.envelope.finish`.

    ``engines`` names the engines that ran (``pure_python``, and ``z3`` when it ran too). ``relations`` is what
    ``evaluate`` derived, ``queries`` what ``query`` answered (each answer with its derivation), ``explanation`` what
    ``explain`` found and ``report`` what ``check`` found. ``check`` is G6's own check of the model, in pure Python;
    ``problems`` lists what was refused, each with its line and column when it has one. A blocked result keeps no
    relations, answers or explanation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    engines: list[str] = Field(default_factory=list)
    relations: dict[str, Any] = Field(default_factory=dict)
    queries: list[dict[str, Any]] = Field(default_factory=list)
    explanation: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    check: dict[str, Any] = Field(default_factory=dict)
    problems: list[dict[str, Any]] = Field(default_factory=list)
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


__all__ = ["DatalogResult", "State"]
