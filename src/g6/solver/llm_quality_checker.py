# SPDX-License-Identifier: Apache-2.0
"""Two-phase quality check of an answer, with three outcomes: ``pass``, ``fail`` or ``unchecked``.

Phase 1 is the deterministic red-flag scan; a flag is ``fail``. Phase 2 asks a model whether the answer is a
genuine attempt at the goal. No model, a model call that raises, or a reply without a boolean ``acceptable`` is
``unchecked``, never acceptance (audit 028, SV-006: the archive returned ``acceptable: True`` with a constant
confidence of 0.5 or 0.6 in those cases, and read any reply lacking "false" as acceptable). No confidence number is
reported, because nothing here measures one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from g6.core.llm import LLMCaller, LLMError, extract_json
from g6.solver.red_flag_detector import red_flags
from g6.solver.schema import NamedCheck, Verdict

_SYSTEM = (
    "You check one output of a problem solver. Is it a genuine, substantive attempt at the goal: relevant, not a "
    "refusal or evasion, not filler or repetition? Return only JSON: "
    '{"acceptable": true|false, "issues": ["..."]}.')


@dataclass(frozen=True)
class QualityVerdict:
    verdict: Verdict
    method: Literal["red_flag", "model", "no_model", "model_error", "unparseable"]
    issues: tuple[str, ...] = ()

    def as_check(self, name: str = "quality") -> NamedCheck:
        """As a named check. Only the red-flag phase is deterministic; a model's verdict is a judgment."""
        kind = "deterministic" if self.method == "red_flag" else "model_judgment"
        return NamedCheck(name=name, kind=kind, verdict=self.verdict,
                          detail=f"{self.method}: {'; '.join(self.issues)}" if self.issues else self.method)


class LLMQualityChecker:
    def __init__(self, llm: LLMCaller | None = None) -> None:
        self.llm = llm

    def check(self, text: str, goal: str, *, step_name: str = "final_answer", step_number: int = 0) -> QualityVerdict:
        flags = red_flags(text, step_number=step_number)
        if flags:
            return QualityVerdict("fail", "red_flag", tuple(f"{f.name}: {f.detail}" for f in flags))
        if self.llm is None:
            return QualityVerdict("unchecked", "no_model", ("no model supplied; only the red-flag scan ran",))
        payload = {"goal": goal[:2000], "step": step_name, "output": text[:4000]}
        try:
            raw = self.llm.complete(_SYSTEM, json.dumps(payload), max_tokens=400)
        except Exception as exc:  # noqa: BLE001 - the check did not run; say so instead of passing
            return QualityVerdict("unchecked", "model_error", (f"{type(exc).__name__}: {str(exc)[:200]}",))
        try:
            reply = extract_json(raw)
        except LLMError:
            reply = None
        acceptable = reply.get("acceptable") if isinstance(reply, dict) else None
        if not isinstance(acceptable, bool):
            return QualityVerdict("unchecked", "unparseable", ("the reply has no boolean 'acceptable'",))
        issues = reply.get("issues") if isinstance(reply.get("issues"), list) else []
        return QualityVerdict("pass" if acceptable else "fail", "model", tuple(str(i)[:200] for i in issues[:10]))


__all__ = ["LLMQualityChecker", "QualityVerdict"]
