# SPDX-License-Identifier: Apache-2.0
"""The one labelled result of g6.causal.

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
the model accepts, because an answer about a causal graph rests on that graph, which no data can confirm.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["qualified-draft", "blocked-escalated"]


class CausalResult(BaseModel):
    """One op's answer, labelled by :func:`g6.causal.envelope.finish`.

    ``engines`` names what ran (``pure_python``, and ``dowhy`` when it ran too). ``answer`` is the op's answer: the
    d-separation, the backdoor paths, the adjustment sets, the instruments or the identification. ``estimate`` and
    ``confidence_interval`` are ``estimate_effect``'s; ``refutations`` are DoWhy's refuters with G6's verdict on each,
    ``recomputation`` is G6's own least-squares estimate for linear regression, and ``agreement`` what the pure-Python
    algorithms and DoWhy were compared on. A blocked result keeps no answer and no estimate; a refuted one keeps its
    numbers in ``refutations``, ``recomputation`` and the evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    engines: list[str] = Field(default_factory=list)
    answer: dict[str, Any] = Field(default_factory=dict)
    estimate: float | None = None
    confidence_interval: list[float] | None = None
    refutations: list[dict[str, Any]] = Field(default_factory=list)
    recomputation: dict[str, Any] = Field(default_factory=dict)
    agreement: dict[str, Any] = Field(default_factory=dict)
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


__all__ = ["CausalResult", "State"]
