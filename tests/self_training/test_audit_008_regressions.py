# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 008 (docs/transition/audits/008_self_training.md). Written before the fixes."""
from __future__ import annotations

import math

import pytest

from g6.self_training import (
    BLOCK,
    PROMOTE_AUTO,
    REJECT,
    REQUIRE_APPROVAL,
    HarnessPatch,
    PatchEvalReport,
    govern_patch_promotion,
)


def _passing(**kw) -> PatchEvalReport:
    base = dict(targeted_failure_fixed=True, regression_pass_rate=1.0, safety_violations=0,
                injection_resilience_not_worse=True, target_metric_improvement=0.1,
                evaluator_confidence=0.9, independent_eval=True, evaluator_id="evaluator-b")
    base.update(kw)
    return PatchEvalReport(**base)


def _docs_patch(**kw) -> HarnessPatch:
    base = dict(patch_id="p1", agent_id="generator-a", target_surfaces=["documentation"], diff="+ a docs line\n")
    base.update(kw)
    return HarnessPatch(**base)


# ST-018: the decision is bound to the concrete change; a patch carrying none is never promoted autonomously.
def test_st018_the_decision_is_bound_to_the_change_and_an_empty_change_needs_a_person() -> None:
    from g6.self_training import decision_matches, patch_digest

    patch = _docs_patch()
    d = govern_patch_promotion(patch, _passing(), autonomous_low_risk=True)
    assert d.decision == PROMOTE_AUTO and d.patch_digest == patch_digest(patch) and decision_matches(d, patch)
    assert d.to_dict()["patch_digest"] == d.patch_digest
    assert not decision_matches(d, _docs_patch(diff="+ a different line\n"))
    empty = govern_patch_promotion(_docs_patch(diff=""), _passing(), autonomous_low_risk=True)
    assert empty.decision == "require_approval" and any("ST-018" in r for r in empty.reasons)


# ST-001 / ST-007— declared numbers can authorise a promotion, never a `verified` label.
def test_auto_promotion_from_declared_report_is_not_verified() -> None:
    d = govern_patch_promotion(_docs_patch(), _passing(), autonomous_low_risk=True)
    assert d.decision == PROMOTE_AUTO
    assert d.reliability_label == "qualified-draft"


# ST-002 — an unknown generator cannot be shown to differ from the evaluator.
def test_empty_generator_id_is_not_independent_evaluation() -> None:
    d = govern_patch_promotion(_docs_patch(agent_id=""), _passing(evaluator_id="x"), autonomous_low_risk=True)
    assert d.decision == REQUIRE_APPROVAL


def test_same_identity_with_different_whitespace_or_case_is_not_independent() -> None:
    d = govern_patch_promotion(_docs_patch(agent_id="Gen-A"), _passing(evaluator_id=" gen-a "), autonomous_low_risk=True)
    assert d.decision == REQUIRE_APPROVAL


# ST-003 — non-finite or out-of-range scores are rejected, never treated as passing.
@pytest.mark.parametrize("field", ["regression_pass_rate", "target_metric_improvement", "evaluator_confidence"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, 1.5, -0.1])
def test_invalid_scores_are_rejected(field: str, bad: float) -> None:
    if field == "target_metric_improvement" and bad == -0.1:
        pytest.skip("a negative improvement is a valid (failing) measurement")
    d = govern_patch_promotion(_docs_patch(), _passing(**{field: bad}))
    assert d.decision == REJECT


# ST-005 — what the patch touches decides, not what it declares; the gate guards itself.
@pytest.mark.parametrize("path", [
    "src/g6/self_training/patch_promotion_gate.py",
    "src/g6/safety_engine/policy_engine.py",
    "src/g6/action_gating/native_ops.py",
    "src/g6/core/reliability_envelope.py",
    "tests/self_training/test_patch_promotion_gate.py",
])
def test_patch_touching_protected_paths_is_blocked(path: str) -> None:
    d = govern_patch_promotion(_docs_patch(touched_paths=[path]), _passing())
    assert d.decision == BLOCK
    assert any("protected" in r for r in d.reasons)


def test_promotion_gate_is_an_off_limits_surface() -> None:
    d = govern_patch_promotion(_docs_patch(target_surfaces=["promotion_gate"]), _passing(), autonomous_low_risk=True)
    assert d.decision not in (PROMOTE_AUTO, "canary")


def test_unprotected_touched_path_still_promotes() -> None:
    d = govern_patch_promotion(_docs_patch(touched_paths=["docs/guide.md"]), _passing(), autonomous_low_risk=True)
    assert d.decision == PROMOTE_AUTO


# --- audit 024 REL-004 lesson applied to this gate: resolve paths before matching -------------
@pytest.mark.parametrize("path", [
    "src/g6/core/./reliability_envelope.py",     # './' segment hid the fragment
    "docs/../../evil.py",                        # escapes the repository
    "/etc/passwd",                               # absolute path
    "",                                          # unnamed path
])
def test_unresolvable_or_hidden_protected_paths_block(path):
    from g6.self_training import HarnessPatch, PatchEvalReport, govern_patch_promotion

    ev = PatchEvalReport(targeted_failure_fixed=True, regression_pass_rate=1.0, target_metric_improvement=0.05,
                         evaluator_confidence=0.9, independent_eval=True, evaluator_id="reviewer")
    d = govern_patch_promotion(HarnessPatch(agent_id="proposer", target_surfaces=["documentation"],
                                            touched_paths=[path]), ev)
    assert d.decision == "block"
