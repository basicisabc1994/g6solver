# SPDX-License-Identifier: Apache-2.0
"""Opt-in model review of a gate decision. It can only make the decision stricter (audit 004 CAG-008).

The gate never imports this module, so no model is asked unless you call ``tighten`` with an ``LLMCaller``. The
model may raise the verdict within the decision's own vocabulary and add one finding. A weaker, unknown or
unparseable reply leaves the deterministic decision as it was; a failed call does too, marked ``reviewer="degraded"``.
A model's opinion is heuristic review, so a tightened decision is still at most ``qualified-draft``.
"""
from __future__ import annotations

import json
from dataclasses import replace

from g6.coding_agent_gate.gate import VERDICT_RANK, Finding, GateDecision, stricter_of, strictest
from g6.core.llm import LLMCaller, extract_json

_SYSTEM = ("You are a conservative reviewer of a coding agent's work. A deterministic gate already decided. You may "
           "only make the decision stricter. Reply with one JSON object: {\"verdict\": one of [%s] (least to most "
           "strict), \"finding\": one sentence naming the problem, or \"\"}.")


def tighten(decision: GateDecision, llm: LLMCaller, *, context: str = "") -> GateDecision:
    """Ask ``llm`` to review ``decision``; return it unchanged or stricter, never weaker."""
    words = sorted(VERDICT_RANK[decision.op], key=VERDICT_RANK[decision.op].__getitem__)
    user = json.dumps({"op": decision.op, "verdict": decision.verdict, "context": context[:6000],
                       "findings": [f.message for f in decision.findings]})
    try:
        reply = extract_json(llm.complete(_SYSTEM % ", ".join(words), user, max_tokens=400))
    except Exception:  # noqa: BLE001 - any caller failure leaves the deterministic floor standing
        return replace(decision, reviewer="degraded")
    if not isinstance(reply, dict):
        return replace(decision, reviewer="degraded")
    proposed = stricter_of(decision.verdict, str(reply.get("verdict") or ""), vocabulary=decision.op)
    if proposed == decision.verdict:
        return replace(decision, reviewer="applied")
    note = str(reply.get("finding") or "").strip()[:300] or "The model reviewer judged the work riskier than the rules."
    findings = (*decision.findings, Finding("model_reviewer", proposed, note))
    return replace(decision, verdict=strictest(decision.op, findings), findings=findings, reviewer="applied")


__all__ = ["tighten"]
