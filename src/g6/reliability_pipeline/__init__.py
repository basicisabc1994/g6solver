# SPDX-License-Identifier: Apache-2.0
"""g6.reliability_pipeline — task intake, stage values, the curated failure-mode skills and the premortem's bounded replay checks.

g6.reliability_pipeline — the intake slice of the reliability pipeline: stage values, task intake, the curated
failure-mode skills and the premortem's bounded replay-check constructor.

Capability label: ``implemented``.

Public pieces: ``Stage`` / ``StageState`` / ``StageResult`` (``stages``), ``run_intake`` and ``replay_question``
(``intake``), ``SKILLS`` and ``relevant`` (``failure_modes``), ``construct_replay_check`` (``premortem``) and the
rule-based ``FeasibilityAssessor`` (``feasibility``).

Limits: feasibility triage is structural rules plus word-list tripwire signals that never hard-block alone (no LLM
path is shipped; the tripwire misses reworded high-stakes tasks); the replay constructor recognises one
narrow requirement phrasing and its checks are task-derived and nonindependent; the archive's four-phase solver,
escalation, grounding and learning lanes are not part of this slice (audit 009 §11).

Optional: ``distill_run`` and ``reconsume`` (``metacognition``) store and recall past run experiences over an
``ExperienceStore`` you pass in. A recalled experience is cited only when the store knows its run, no confidence is
reported, and recall changes no run: its result is disclosure only (audit 009, RP-007).

Names are loaded on first use, so importing the package loads none of its modules.

Not ported from the archive (audit 009): metacognition's default ``experience_loop`` block and its op adapter, the
GUI preflight that stored a prime without using it, and the ``method_proposal`` past-cases leg.
"""
from __future__ import annotations

import importlib
from typing import Any

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only (the docstring's capability label)
__g6_requires__: tuple[str, ...] = ("intent_engine",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = (
    "unclear_success", "context_loss", "unsafe_autonomous_action", "unverified_reliability",
    "missing_domain_standard", "wrong_risk_tier", "budget_overrun", "unavailable_tool", "hidden_assumption",
    "source_gap", "verification_gap", "handoff_gap", "learning_gap",
)
__g6_introduces__: tuple[str, ...] = ()

_LAZY = {
    "Stage": "stages", "StageState": "stages", "StageResult": "stages",
    "run_intake": "intake", "replay_question": "intake",
    "SKILLS": "failure_modes", "relevant": "failure_modes",
    "construct_replay_check": "premortem",
    "FeasibilityAssessor": "feasibility",
    "Experience": "metacognition", "ExperienceStore": "metacognition",
    "distill_run": "metacognition", "reconsume": "metacognition",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'g6.reliability_pipeline' has no attribute {name!r}")
    value = getattr(importlib.import_module(f"g6.reliability_pipeline.{module}"), name)
    globals()[name] = value
    return value
