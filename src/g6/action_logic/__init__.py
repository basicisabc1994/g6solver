# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Action-logic: deterministic controls from the study of how complex decisions fail: act in small steps, watch for warnings, revise on disconfirming evidence.

Action-logic: the Dörner closed-loop-control floor (G6_Action_Logic.md).

A deterministic, LLM-free control set that makes the *Logic of Failure* failure structure
executable, expressed in the action gate's severity vocabulary (``notice < review < block``).

Capability label: ``implemented``. Every function here is a pure, deterministic
predicate over *caller-declared* facts. Calling it does not enforce anything by itself:
enforcement exists only where a gate calls it and obeys the answer.

How the controls are used:

- Inside ``g6.action_gating`` (``decide_gate``): ballistic-action prevention (G6-INV-05),
  coordinated interventions (G6-INV-08) and weak-signal / threshold-breach review (§9) — but
  only when the caller supplies the corresponding context (verification contract,
  coordination context, world state). The hypothesis controls (AT-08/AT-12) are called there
  only when ``G6_ACTION_LOGIC_HYPOTHESIS_GATE`` is on (default off) and a hypothesis context
  is supplied.
- Not called by any gate: the crisis-monitoring invariant (G6-INV-06,
  ``check_crisis_monitoring``). Use it directly; do not describe it as enforced.

Limits (audit 003): materiality, active actions, world state and hypothesis evidence are
declared by the caller, not observed. ``sequence_if_separable`` refuses single-action budget
breaches, non-idempotent duplicate effects and reused action ids; a time window is an ISO-8601
interval or an opaque bucket token, and anything else is treated as overlapping.

The decision lifecycle state machine (``g6.core.decision_record``) and objective contract
(``g6.core.objective_contract``) are re-exported here for one-stop access.
"""
__g6_component__ = True
__g6_block__ = "ActionLogicBlock"
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unchecked_big_step", "ignored_warning")
__g6_introduces__: tuple[str, ...] = ()

from g6.action_logic.block import ActionLogicBlock
from g6.action_logic.controls import (
    E_BALLISTIC,
    E_COLLISION,
    E_CRISIS_MONITORING,
    E_NO_HORIZON,
    E_WEAK_SIGNAL,
    assess_material_action,
    assess_weak_signals,
    check_crisis_monitoring,
    detect_conflicts,
    evaluate_coordination,
    evaluate_verification_contract,
    is_deteriorating,
    sequence_if_separable,
)
from g6.action_logic.hypotheses import (
    E_DISCONFIRMING_PENDING,
    E_FALSIFIED_REUSED,
    apply_disconfirming_evidence,
    disconfirming_evidence_severity,
    reauthorisation_severity,
    reauthorisation_severity_from_registry,
    update_on_outcome,
)
from g6.action_logic.schema import (
    SEVERITY_RANK,
    ActionConflict,
    ActionIntent,
    ActionLogicAssessment,
    ControlVariable,
    Hypothesis,
    HypothesisStatus,
    MonitoringCoverage,
    Outcome,
    VerificationContract,
    WorldState,
    raise_severity,
)
from g6.core.decision_record import (
    DecisionRecord,
    DecisionState,
    advance,
    can_transition,
)
from g6.core.objective_contract import ObjectiveContract

__all__ = [  # noqa: RUF022 - grouped by module; the registry block stays first
    # registry block: first, because the registry loads the first AIBlock in __all__
    "ActionLogicBlock",
    # schema
    "SEVERITY_RANK",
    "raise_severity",
    "VerificationContract",
    "ActionIntent",
    "ActionConflict",
    "ControlVariable",
    "WorldState",
    "MonitoringCoverage",
    "Hypothesis",
    "HypothesisStatus",
    "Outcome",
    "ActionLogicAssessment",
    # controls
    "E_BALLISTIC",
    "E_COLLISION",
    "E_NO_HORIZON",
    "E_WEAK_SIGNAL",
    "E_CRISIS_MONITORING",
    "evaluate_verification_contract",
    "detect_conflicts",
    "evaluate_coordination",
    "sequence_if_separable",
    "is_deteriorating",
    "assess_weak_signals",
    "check_crisis_monitoring",
    "assess_material_action",
    # hypotheses / reflection
    "E_DISCONFIRMING_PENDING",
    "E_FALSIFIED_REUSED",
    "update_on_outcome",
    "apply_disconfirming_evidence",
    "disconfirming_evidence_severity",
    "reauthorisation_severity",
    "reauthorisation_severity_from_registry",
    # core primitives (re-exported)
    "ObjectiveContract",
    "DecisionRecord",
    "DecisionState",
    "advance",
    "can_transition",
]
