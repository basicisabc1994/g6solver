# SPDX-License-Identifier: Apache-2.0
"""g6.intent_engine — the intent contract a task is held to, and the deterministic elicitor that fills it.

Capability label: ``implemented``.

``IntentContract`` is the persisted intent schema (goal, success and failure criteria, non-goals, risk tolerance,
checkpoints); ``IntentElicitor.elicit`` merges answers into a contract and lists the fields still missing.

Limits: the elicitor is a field-completeness checklist, not an understanding of the request; its ``confidence`` is a
completeness heuristic, never a semantic or verification score. The archive's registry block, pattern catalogue and
MCP tool are not ported (audit 005 §11).
"""
from g6.intent_engine.checks import Check, check_output, harness_checks
from g6.intent_engine.elicitor import IntentElicitor
from g6.intent_engine.interpret import apply_intent_floor, interpret
from g6.intent_engine.schema import (
    Assumption,
    CheckpointSpec,
    GoalInput,
    IntentContract,
    IntentQuestion,
    IntentSession,
)

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only (the docstring's capability label)
__g6_requires__: tuple[str, ...] = ("harness",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("wrong_problem", "missing_user_constraint")
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["Check", "check_output", "harness_checks", "interpret", "apply_intent_floor", "Assumption", "CheckpointSpec", "GoalInput", "IntentContract", "IntentElicitor", "IntentQuestion",
           "IntentSession"]
