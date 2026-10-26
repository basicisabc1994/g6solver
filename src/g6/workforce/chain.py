# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""How a component's reply is read as a verdict (vendored from the archive's ``venture.chain``: only ``StepResult``
and ``verdict``, which the workforce's contracts use; the venture operator's executable chain rows are not ported)."""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    ok: bool
    reason: str = ""
    evidence: dict | None = None


def _reason_of(envelope: dict) -> str:
    err = envelope.get("error") if isinstance(envelope, dict) else None
    if isinstance(err, dict):
        return " ".join(str(err.get(k, "")) for k in ("code", "message") if err.get(k)) or "ok=False"
    return str(err or envelope.get("degradation_reason") or "ok=False")[:200]


_AMBIGUOUS_STATUS = frozenset({"unknown", "timeout", "error", "degraded"})
#: Fields that carry a component's explicit answer; a degraded result without one measured nothing.
_VERDICT_FIELDS = ("satisfiable", "valid", "decision", "passes", "questions", "situation", "completion_state")


def verdict(envelope: dict) -> tuple[str, bool, str]:
    """(status, ok, reason) read from the COMPONENT's result, never from the envelope's ok alone (Codex
    P1 on 54b58fbf04: z3 'unknown' came back ok=True with satisfiable=None and an error). An error, an
    unknown/timeout answer, satisfiable/valid=None, or a degraded result that carries no answer at all
    is not measured; a blocked-escalated result is a verdict of no; only a clean answer is ok. The
    envelope's own `degraded` (the invoker's contract warnings) and a result that is degraded but
    answers (the regulated gate marks every verdict "a human must decide") are read by their answer."""
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return "not_measured", False, _reason_of(envelope if isinstance(envelope, dict) else {})
    result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
    if result.get("answer_kind") == "computed":  # formal_methods: satisfiable was never asked (W3 formal1 D1)
        result = {k: v for k, v in result.items() if k != "satisfiable"}
    err = result.get("error")
    if err:
        return "not_measured", False, f"component error: {str(err)[:160]}"
    if "satisfiable" in result and result["satisfiable"] is None:
        return "not_measured", False, "solver answered unknown"
    if "valid" in result and result["valid"] is None:
        return "not_measured", False, "validity unknown"
    status = str(result.get("status") or "").lower()
    if status in _AMBIGUOUS_STATUS:
        return "not_measured", False, f"component status {status}"
    answered = any(k in result for k in _VERDICT_FIELDS)
    if result.get("degraded") and not answered:
        return "not_measured", False, f"degraded: {str(result.get('degradation_reason') or 'no answer')[:160]}"
    if not result and (envelope.get("degraded") or envelope.get("completion_state") == "blocked-escalated"):
        return "not_measured", False, f"no result: {str(envelope.get('degradation_reason') or 'empty')[:160]}"
    if result.get("completion_state") == "blocked-escalated":
        reason = result.get("block_reason") or result.get("degradation_reason") or result.get("warning_card")
        return "ran", False, str(reason or "component reported blocked-escalated")[:200]
    return "ran", True, ""
