# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Tests for the risk-tiered harness-patch promotion gate (agentic_self_training.md §H/§I).

Invariants (the family's thesis — "harnesses learn", NOT "agents self-modify"):
  * a patch that expands permissions / weakens safety / removes human review / is irreversible
    is hard-BLOCKED regardless of how good its evaluation looks (DOWN-only, fail-closed);
  * a patch that fails any §H promotion criterion is REJECTED (cannot promote at any tier);
  * off-limits surfaces (governance/security/billing/human-approval/tool-perm/safety/kill-switch)
    can never be promoted autonomously;
  * a patch generator may not be its own sole judge (no independent eval => never auto);
  * only an R0 patch with a full, independent passing evaluation is ever auto-'verified'.
"""
from __future__ import annotations

import pytest
from g6.self_training import (
    BLOCK,
    CANARY,
    PROMOTE_AUTO,
    REJECT,
    REQUIRE_APPROVAL,
    REQUIRE_EXPERT_REVIEW,
    REQUIRE_SECURITY_APPROVAL,
    HarnessPatch,
    PatchEvalReport,
    classify_patch_risk,
    govern_patch_promotion,
)


def _passing_eval() -> PatchEvalReport:
    """A full, independent, passing evaluation (every §H criterion satisfied)."""
    return PatchEvalReport(
        targeted_failure_fixed=True,
        regression_pass_rate=1.0,
        safety_violations=0,
        injection_resilience_not_worse=True,
        target_metric_improvement=0.10,
        evaluator_confidence=0.9,
        independent_eval=True,
        evaluator_id="independent-reviewer",
    )


def _patch(surfaces, **kw) -> HarnessPatch:
    # A known generator identity: with an empty one independence cannot be shown (audit 008, ST-002).
    kw.setdefault("agent_id", "generator")
    kw.setdefault("diff", "--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-old\n+new\n")   # ST-018: the change itself
    return HarnessPatch(target_surfaces=list(surfaces), **kw)


@pytest.mark.parametrize("evaluator", ["", " ", "generator"])
def test_independent_boolean_without_distinct_evaluator_never_auto_promotes(evaluator):
    report = _passing_eval()
    report.evaluator_id = evaluator
    result = govern_patch_promotion(_patch(["documentation"], agent_id="generator"), report, autonomous_low_risk=True)
    assert result.decision == REQUIRE_APPROVAL
    assert not result.auto_promotable


# --- risk tiering (§I) -----------------------------------------------------


@pytest.mark.parametrize("surface,expected", [
    ("documentation", "R0"),
    ("prompt", "R1"),
    ("examples", "R1"),
    ("rubric", "R2"),
    ("routing_rule", "R2"),
    ("skill_file", "R2"),
    ("tool_permission", "R3"),
    ("security_boundary", "R3"),
    ("kill_switch", "R4"),
])
def test_surface_base_tier(surface, expected):
    assert classify_patch_risk(_patch([surface])) == expected


def test_unknown_surface_is_fail_closed_r3():
    # An unrecognised surface is treated as sensitive, never R0.
    assert classify_patch_risk(_patch(["something_new"])) == "R3"


def test_tier_is_max_over_surfaces():
    assert classify_patch_risk(_patch(["documentation", "rubric"])) == "R2"


def test_irreversible_external_action_floors_at_r4():
    assert classify_patch_risk(_patch(["prompt"], external_action_impact="irreversible")) == "R4"


def test_prohibited_is_r5():
    assert classify_patch_risk(_patch(["prompt"], prohibited=True)) == "R5"


# --- hard safety rules (§I rules 1-7) -- always BLOCK, regardless of eval ---


@pytest.mark.parametrize("kw,needle", [
    ({"permission_change": "expanded"}, "permissions"),
    ({"safety_policy_change": "weaken"}, "safety policy"),
    ({"removes_human_review": True}, "human review"),
    ({"reversible": False}, "reversible"),
    ({"treats_untrusted_content_as_instructions": True}, "untrusted"),
])
def test_hard_rule_blocks_even_with_perfect_eval(kw, needle):
    patch = _patch(["prompt"], **kw)
    d = govern_patch_promotion(patch, _passing_eval())
    assert d.decision == BLOCK
    assert d.reliability_label == "blocked-escalated"
    assert d.auto_promotable is False
    assert any(needle in r for r in d.reasons)


def test_r5_prohibited_is_blocked():
    d = govern_patch_promotion(_patch(["prompt"], prohibited=True), _passing_eval())
    assert d.decision == BLOCK
    assert d.risk_tier == "R5"


# --- §H promotion criteria -- a single miss => REJECT ----------------------


@pytest.mark.parametrize("field,value", [
    ("targeted_failure_fixed", False),
    ("regression_pass_rate", 0.97),
    ("safety_violations", 1),
    ("injection_resilience_not_worse", False),
    ("target_metric_improvement", 0.02),
    ("evaluator_confidence", 0.74),
])
def test_criteria_miss_is_rejected(field, value):
    ev = _passing_eval()
    setattr(ev, field, value)
    d = govern_patch_promotion(_patch(["prompt"]), ev)
    assert d.decision == REJECT
    assert d.reliability_label == "blocked-escalated"
    assert d.auto_promotable is False


def test_no_target_surfaces_is_rejected():
    d = govern_patch_promotion(HarnessPatch(), _passing_eval())
    assert d.decision == REJECT
    assert any("no target surfaces" in r for r in d.reasons)


# --- risk-tier ladder (§I) -- happy paths ----------------------------------


def test_r0_full_independent_eval_auto_promotes_as_qualified_draft():
    d = govern_patch_promotion(_patch(["documentation"]), _passing_eval(), autonomous_low_risk=True)
    assert d.decision == PROMOTE_AUTO
    assert d.reliability_label == "qualified-draft"  # declared evidence never yields verified (ST-001)
    assert d.auto_promotable is True
    assert d.requires_human is False


def test_r1_canaries_as_qualified_draft():
    d = govern_patch_promotion(_patch(["prompt"]), _passing_eval(), autonomous_low_risk=True)
    assert d.decision == CANARY
    assert d.reliability_label == "qualified-draft"
    assert d.auto_promotable is True


def test_r2_requires_human_approval():
    d = govern_patch_promotion(_patch(["rubric"]), _passing_eval())
    assert d.decision == REQUIRE_APPROVAL
    assert d.reliability_label == "qualified-draft"
    assert d.requires_human is True


def test_r3_requires_security_approval():
    # An R3 reached via a reversible external action on an otherwise-safe surface.
    d = govern_patch_promotion(
        _patch(["routing_rule"], external_action_impact="reversible"), _passing_eval())
    assert d.decision == REQUIRE_SECURITY_APPROVAL
    assert d.reliability_label == "qualified-draft"


def test_r4_requires_expert_review_no_autonomy():
    d = govern_patch_promotion(
        _patch(["prompt"], external_action_impact="irreversible"), _passing_eval())
    assert d.decision == REQUIRE_EXPERT_REVIEW
    assert d.auto_promotable is False


# --- independent-evaluation gate (§H gotcha) -------------------------------


def test_no_independent_eval_never_auto_promotes():
    ev = _passing_eval()
    ev.independent_eval = False
    d = govern_patch_promotion(_patch(["documentation"]), ev, autonomous_low_risk=True)   # would be R0 auto otherwise
    assert d.decision == REQUIRE_APPROVAL
    assert d.auto_promotable is False
    assert any("sole judge" in r for r in d.reasons)


# --- promotion-policy autonomy switch (§6 AdaptiveAgentSpec) ----------------


def test_manual_only_policy_routes_low_risk_to_approval():
    for d in (govern_patch_promotion(_patch(["documentation"]), _passing_eval(), autonomous_low_risk=False),
              govern_patch_promotion(_patch(["documentation"]), _passing_eval())):   # manual-only is the default
        assert d.decision == REQUIRE_APPROVAL
        assert d.auto_promotable is False


# --- off-limits surfaces (blog principles.html) ----------------------------


def test_off_limits_governance_requires_its_r3_tiers_security_approval():
    # A non-weakening governance change still can never be autonomous, and its R3 tier's review applies.
    d = govern_patch_promotion(_patch(["governance"]), _passing_eval(), autonomous_low_risk=True)
    assert (d.risk_tier, d.decision) == ("R3", REQUIRE_SECURITY_APPROVAL)
    assert any("off-limits" in r for r in d.reasons)


def test_off_limits_security_boundary_requires_security_approval():
    d = govern_patch_promotion(_patch(["security_boundary"]), _passing_eval())
    assert d.decision == REQUIRE_SECURITY_APPROVAL


def test_off_limits_tool_permission_surface_requires_security_approval():
    # Touching the tool-permission surface (without expanding) is still security-gated.
    d = govern_patch_promotion(_patch(["tool_permission"]), _passing_eval())
    assert d.decision == REQUIRE_SECURITY_APPROVAL


def test_off_limits_kill_switch_requires_expert_review():
    d = govern_patch_promotion(_patch(["kill_switch"]), _passing_eval())
    assert d.decision == REQUIRE_EXPERT_REVIEW


def test_weakening_an_off_limits_surface_is_blocked_not_just_gated():
    # Touching safety_policy is off-limits; WEAKENING it is a hard block (stronger than the gate).
    d = govern_patch_promotion(
        _patch(["safety_policy"], safety_policy_change="weaken"), _passing_eval())
    assert d.decision == BLOCK


# --- label-safety / DOWN-only invariant ------------------------------------


def test_only_r0_auto_promote_is_ever_verified():
    # Sweep a representative matrix; 'verified' must be reachable ONLY via PROMOTE_AUTO.
    samples = [
        govern_patch_promotion(_patch(["prompt"]), _passing_eval()),
        govern_patch_promotion(_patch(["rubric"]), _passing_eval()),
        govern_patch_promotion(_patch(["governance"]), _passing_eval()),
        govern_patch_promotion(_patch(["prompt"], permission_change="expanded"), _passing_eval()),
        govern_patch_promotion(_patch(["prompt"]), PatchEvalReport()),
    ]
    for d in samples:
        if d.reliability_label == "verified":
            assert d.decision == PROMOTE_AUTO


def test_to_dict_shape():
    d = govern_patch_promotion(_patch(["documentation"]), _passing_eval())
    out = d.to_dict()
    assert set(out) == {"decision", "risk_tier", "reliability_label",
                        "requires_human", "auto_promotable", "reasons", "patch_digest"}
