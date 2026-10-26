# SPDX-License-Identifier: Apache-2.0
"""The one labelled result of g6.graph_reasoning.

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
the model accepts, because the interpretation of a caller's graph and rules is not a proof a person has inspected.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["qualified-draft", "blocked-escalated"]


class GraphReasoningResult(BaseModel):
    """One op's answer, labelled by :func:`g6.graph_reasoning.envelope.finish`.

    ``engines`` names the engines that ran (``pure_python``, and ``pyreason`` when it ran too). ``interpretation`` is
    what ``reason`` found (every atom whose interval is not the unknown ``[0, 1]``), ``answer`` what ``query`` found,
    ``explanation`` what ``explain`` found and ``report`` what ``check`` found. ``check`` is G6's own check of the
    interpretation, in pure Python; ``conflict`` is the conflict a rule or fact ran into, with its derivation;
    ``problems`` lists what was refused, each with its line and column when it has one. A blocked result keeps no
    interpretation, answer or explanation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    engines: list[str] = Field(default_factory=list)
    interpretation: list[dict[str, Any]] = Field(default_factory=list)
    answer: dict[str, Any] = Field(default_factory=dict)
    explanation: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    check: dict[str, Any] = Field(default_factory=dict)
    conflict: dict[str, Any] = Field(default_factory=dict)
    problems: list[dict[str, Any]] = Field(default_factory=list)
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


__all__ = ["GraphReasoningResult", "State"]
