# SPDX-License-Identifier: Apache-2.0
"""Governed self-modification: the promotion authority for proposed harness changes, one governed improvement cycle, and a maturity scorecard.

Governed self-modification: the promotion authority for proposed harness changes, one governed improvement cycle,
and a maturity scorecard of shape checks.

Capability label: ``implemented``. ``govern_patch_promotion`` is deterministic and
down-only: hard safety rules and a failed evaluation can only tighten the decision. Its inputs
(evaluation scores, evaluator identity, declared effects) are supplied by the caller; the gate
cannot observe them, so a decision is never stronger than ``qualified-draft`` (audit 008).

Two pure inputs to the gate (audit 024 §11): ``classify_change_scope`` (may the change touch these
paths at all: deletions, paths out of the repository or the component, and pytest configuration
are blocked; protected fragments need a person) and ``regression_delta`` (did the change fail, drop
or skip a test that ran before; ``unevaluable`` is never read as no regression).

The improvement cycle (audit 008 §11): ``run_improvement_cycle`` runs sense, propose, evaluate, gate, apply, remeasure
and rollback with callables you inject. It is off unless ``enabled=True``, applies only an ``auto_promotable``
decision of ``govern_patch_promotion`` (which needs ``autonomous_low_risk=True``; by default every patch needs a
person) and only with ``apply_enabled=True`` and an ``apply_fn``, and then requires a ``rollback_fn``, a
``remeasure_fn`` and a ``regressed_fn`` before anything runs. It reads the process-wide stop before each phase
(``G6_E_STOPPED``), and reports a rollback that raised as applied, with ``rollback_failed``. Its best result is
``qualified-draft``; ``ImprovementCycleResult.to_dict()`` is the library envelope.

The scorecard: ``evaluate_rubric`` scores a component's output against shape assertions (a field exists, is
non-empty, has a type or a bound), which is shape, not quality; a rubric with no assertions or no weight is
unevaluable. ``audit_output`` / ``classify_tier`` give a tier T0-T5, at most T3 without a reviewed rubric that
evaluates the output; T6 only by promotion. ``ScorecardStore`` keeps an append-only JSONL audit log and a JSON
scorecard in a directory you name; its tier is the latest evidenced one, held while a flag is unresolved, and
``promote`` never clears a flag. ``diagnose`` classifies a shortfall by fixed rules and says which rule fired, with
no confidence number.

Not ported from the archive (audit 008 §11): the ``SelfTrainingBlock`` and its MCP servers; the Codex proposer
(``make_codex_harness_proposer``), the LLM planner, grounding and every other model, network or subprocess path
(ST-016); the CSF wrapper (ST-008); ``competence_oracle``, whose only user was the block and whose confidence and
``ready_for_promotion`` were ad hoc numbers (ST-012); the training state machine, unified loop and improve adapter,
which reported improvements nothing made (ST-011); the pre-evolution snapshot manager (pass a ``snapshot_fn``); the
``G6_SELF_IMPROVE_LOOP`` / ``G6_SELF_IMPROVE_APPLY`` switches (the cycle takes arguments); the ``~/.g6`` default
store; and the shipped rubrics, strategies and frozen ``baseline_audit.json`` that called 170 components
"production" (ST-010).
"""
from g6.self_training.cases import BenchmarkCase, run_cases
from g6.self_training.change_scope import (
    ChangeScope,
    ScopeDecision,
    ScopeVerdict,
    classify_change_scope,
)
from g6.self_training.diagnosis import Diagnosis, diagnose, diagnose_audit
from g6.self_training.improvement_loop import (
    ImprovementCycleResult,
    make_no_eval_report,
    run_improvement_cycle,
)
from g6.self_training.patch_promotion_gate import (
    BLOCK,
    CANARY,
    EVALUATOR_CONFIDENCE_MIN,
    OFF_LIMITS_SURFACES,
    PROMOTE_AUTO,
    REGRESSION_PASS_RATE_MIN,
    REJECT,
    REQUIRE_APPROVAL,
    REQUIRE_EXPERT_REVIEW,
    REQUIRE_SECURITY_APPROVAL,
    SAFE_SURFACES,
    TARGET_METRIC_IMPROVEMENT_MIN,
    HarnessPatch,
    PatchEvalReport,
    PromotionDecision,
    classify_patch_risk,
    decision_matches,
    govern_patch_promotion,
    patch_digest,
)
from g6.self_training.persistence import PromotionRefused, ScorecardStore
from g6.self_training.regression_delta import (
    RegressionVerdict,
    RunSummary,
    failing_node_ids,
    regression_delta,
)
from g6.self_training.rubrics import (
    RubricRegistry,
    audit_output,
    classify_tier,
    evaluate_assertion,
    evaluate_rubric,
)
from g6.self_training.schema import (
    PUBLICATION_THRESHOLD,
    TIER_NAMES,
    AuditResult,
    ComponentRubric,
    ComponentScorecard,
    MaturityScore,
    RubricAssertion,
    RubricProvenance,
    RubricScore,
)

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unchecked_self_change", "broke_existing_functionality")
__g6_introduces__: tuple[str, ...] = ("runner_in_process",)

__all__ = [
    "BenchmarkCase", "run_cases",
    "BLOCK", "CANARY", "EVALUATOR_CONFIDENCE_MIN", "OFF_LIMITS_SURFACES", "PROMOTE_AUTO",
    "REGRESSION_PASS_RATE_MIN", "REJECT", "REQUIRE_APPROVAL", "REQUIRE_EXPERT_REVIEW",
    "REQUIRE_SECURITY_APPROVAL", "SAFE_SURFACES", "TARGET_METRIC_IMPROVEMENT_MIN", "HarnessPatch",
    "PatchEvalReport", "PromotionDecision", "classify_patch_risk", "decision_matches", "govern_patch_promotion",
    "patch_digest",
    "ChangeScope", "ScopeDecision", "ScopeVerdict", "classify_change_scope",
    "RegressionVerdict", "RunSummary", "failing_node_ids", "regression_delta",
    "ImprovementCycleResult", "make_no_eval_report", "run_improvement_cycle",
    "PUBLICATION_THRESHOLD", "TIER_NAMES", "AuditResult", "ComponentRubric", "ComponentScorecard", "MaturityScore",
    "RubricAssertion", "RubricProvenance", "RubricScore",
    "RubricRegistry", "audit_output", "classify_tier", "evaluate_assertion", "evaluate_rubric",
    "PromotionRefused", "ScorecardStore",
    "Diagnosis", "diagnose", "diagnose_audit",
]
