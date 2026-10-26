# SPDX-License-Identifier: Apache-2.0
"""The promotion gate and the cycle after the skill review: a decision is never weaker than its risk tier's review,
autonomy is the caller's opt-in, a decision is bound to every field of the patch, and the cycle never applies a patch
it has no way to undo. Offline, fakes only; each test has its own ``G6_HOME`` and releases the process-wide stop."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import g6.self_training as st
import g6.self_training.improvement_loop as loop
from g6.core import stop
from g6.self_training import (
    CANARY,
    PROMOTE_AUTO,
    REQUIRE_APPROVAL,
    REQUIRE_EXPERT_REVIEW,
    REQUIRE_SECURITY_APPROVAL,
    HarnessPatch,
    PatchEvalReport,
    decision_matches,
    govern_patch_promotion,
    patch_digest,
    run_improvement_cycle,
)
from g6.self_training.patch_promotion_gate import _SURFACE_BASE_TIER

DIFF = "--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-old\n+new\n"
_TIER_REVIEW = {"R0": PROMOTE_AUTO, "R1": CANARY, "R2": REQUIRE_APPROVAL, "R3": REQUIRE_SECURITY_APPROVAL,
                "R4": REQUIRE_EXPERT_REVIEW}
_STRICTNESS = {PROMOTE_AUTO: 0, CANARY: 1, REQUIRE_APPROVAL: 2, REQUIRE_SECURITY_APPROVAL: 3, REQUIRE_EXPERT_REVIEW: 4}


@pytest.fixture(autouse=True)
def _own_home_and_no_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    monkeypatch.delenv("G6_STOP", raising=False)
    stop.release()
    yield
    stop.release()


def _patch(surfaces=("prompt",), **kw) -> HarnessPatch:
    kw.setdefault("agent_id", "generator")
    kw.setdefault("diff", DIFF)
    return HarnessPatch(patch_id="p1", target_surfaces=list(surfaces), **kw)


def _passing(**kw) -> PatchEvalReport:
    base = dict(targeted_failure_fixed=True, regression_pass_rate=1.0, target_metric_improvement=0.1,
                evaluator_confidence=0.9, independent_eval=True, evaluator_id="evaluator")
    base.update(kw)
    return PatchEvalReport(**base)


# --- a decision is never weaker than its risk tier's review ---------------------------------------------------------

@pytest.mark.parametrize(("surface", "tier", "expected"), [
    ("promotion_gate", "R4", REQUIRE_EXPERT_REVIEW), ("promotion_thresholds", "R4", REQUIRE_EXPERT_REVIEW),
    ("evaluator", "R4", REQUIRE_EXPERT_REVIEW), ("governance", "R3", REQUIRE_SECURITY_APPROVAL),
    ("billing", "R3", REQUIRE_SECURITY_APPROVAL), ("human_approval_rule", "R3", REQUIRE_SECURITY_APPROVAL),
    ("safety_policy", "R3", REQUIRE_SECURITY_APPROVAL),
])
def test_an_off_limits_surface_gets_at_least_its_tiers_review(surface, tier, expected) -> None:
    d = govern_patch_promotion(_patch([surface]), _passing(), autonomous_low_risk=True)
    assert (d.risk_tier, d.decision) == (tier, expected)
    assert d.requires_human and not d.auto_promotable
    assert any("off-limits" in r for r in d.reasons)


@pytest.mark.parametrize(("surfaces", "effects", "tier", "expected"), [
    (["prompt"], {"external_action_impact": "irreversible"}, "R4", REQUIRE_EXPERT_REVIEW),
    (["routing_rule"], {"external_action_impact": "reversible"}, "R3", REQUIRE_SECURITY_APPROVAL),
    (["rubric"], {}, "R2", REQUIRE_APPROVAL),
    (["documentation"], {}, "R0", REQUIRE_APPROVAL),
])
def test_without_an_independent_evaluation_the_tiers_review_still_applies(surfaces, effects, tier, expected) -> None:
    d = govern_patch_promotion(_patch(surfaces, **effects), _passing(independent_eval=False), autonomous_low_risk=True)
    assert (d.risk_tier, d.decision) == (tier, expected)
    assert any("sole judge" in r for r in d.reasons)


def test_no_decision_is_weaker_than_its_risk_tiers_review() -> None:
    reports = (_passing(), _passing(independent_eval=False), _passing(evaluator_id="generator"))
    for surface in sorted(_SURFACE_BASE_TIER):
        for report in reports:
            for autonomous in (True, False):
                for diff in (DIFF, ""):
                    d = govern_patch_promotion(_patch([surface], diff=diff), report, autonomous_low_risk=autonomous)
                    assert _STRICTNESS[d.decision] >= _STRICTNESS[_TIER_REVIEW[d.risk_tier]], (
                        surface, report.independent_eval, autonomous, bool(diff), d.risk_tier, d.decision)


# --- autonomy is the caller's opt-in ----------------------------------------------------------------------------

def test_by_default_a_prompt_patch_with_a_passing_independent_evaluation_needs_a_person() -> None:
    d = govern_patch_promotion(_patch(["prompt"]), _passing())
    assert (d.decision, d.requires_human, d.auto_promotable) == (REQUIRE_APPROVAL, True, False)
    assert govern_patch_promotion(_patch(["prompt"]), _passing(), autonomous_low_risk=True).decision == CANARY
    assert govern_patch_promotion(_patch(["documentation"]), _passing(), autonomous_low_risk=True).decision == \
        PROMOTE_AUTO


def test_by_default_the_cycle_applies_nothing_without_a_person() -> None:
    calls: list[str] = []
    result = run_improvement_cycle(sense_fn=lambda: 1, propose_fn=lambda s: _patch(), evaluate_fn=lambda p: _passing(),
                                   enabled=True, apply_enabled=True, apply_fn=lambda p: calls.append("apply"),
                                   rollback_fn=lambda p: calls.append("rollback"), remeasure_fn=lambda: 1,
                                   regressed_fn=lambda before, after: False)
    assert calls == [] and result.code == loop.REVIEW_REQUIRED and not result.applied
    assert result.decision.decision == REQUIRE_APPROVAL


# --- the decision is bound to every field of the patch ------------------------------------------------------------

def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, list):
        return [*value, "changed"]
    return f"{value}-changed"


def test_the_digest_covers_every_field_of_the_patch() -> None:
    patch = _patch(touched_paths=["prompts/answer.txt"])
    decision = govern_patch_promotion(patch, _passing(), autonomous_low_risk=True)
    assert decision_matches(decision, patch)
    for f in dataclasses.fields(HarnessPatch):
        edited = dataclasses.replace(patch, **{f.name: _changed(getattr(patch, f.name))})
        assert patch_digest(edited) != patch_digest(patch), f.name
        assert not decision_matches(decision, edited), f.name


def test_list_order_does_not_change_the_digest() -> None:
    a = _patch(["prompt", "examples"], touched_paths=["a.txt", "b.txt"])
    assert patch_digest(a) == patch_digest(dataclasses.replace(a, target_surfaces=["examples", "prompt"],
                                                               touched_paths=["b.txt", "a.txt"]))


def test_a_snapshot_that_expands_permissions_after_the_decision_stops_the_apply() -> None:
    calls: list[str] = []

    def snapshot_fn(patch):
        patch.permission_change = "expanded"
        return True

    result = run_improvement_cycle(sense_fn=lambda: 1, propose_fn=lambda s: _patch(), evaluate_fn=lambda p: _passing(),
                                   enabled=True, apply_enabled=True, autonomous_low_risk=True, snapshot_fn=snapshot_fn,
                                   apply_fn=lambda p: calls.append("apply"),
                                   rollback_fn=lambda p: calls.append("rollback"), remeasure_fn=lambda: 1,
                                   regressed_fn=lambda before, after: False)
    assert calls == [] and result.code == loop.DECISION_MISMATCH and not result.applied


# --- a patch is never applied without a way to undo it ------------------------------------------------------------

def test_applying_without_a_rollback_fn_runs_nothing() -> None:
    calls: list[str] = []

    def sense_fn():
        calls.append("sense")
        return 1

    result = run_improvement_cycle(sense_fn=sense_fn, propose_fn=lambda s: _patch(), evaluate_fn=lambda p: _passing(),
                                   enabled=True, apply_enabled=True, autonomous_low_risk=True,
                                   apply_fn=lambda p: calls.append("apply"), remeasure_fn=lambda: 1,
                                   regressed_fn=lambda before, after: False)
    assert calls == [] and result.code == loop.NO_ROLLBACK and not result.applied
    assert result.completion_state == "blocked-escalated" and result.patch_in_effect is False


def test_the_rollback_fn_undoes_a_regressing_patch() -> None:
    state = {"prompt": "old"}
    result = run_improvement_cycle(sense_fn=lambda: 1, propose_fn=lambda s: _patch(), evaluate_fn=lambda p: _passing(),
                                   enabled=True, apply_enabled=True, autonomous_low_risk=True,
                                   apply_fn=lambda p: state.update(prompt="new"),
                                   rollback_fn=lambda p: state.update(prompt="old"), remeasure_fn=lambda: 0,
                                   regressed_fn=lambda before, after: after < before)
    assert state == {"prompt": "old"} and result.applied and result.rolled_back and result.code == loop.ROLLED_BACK


# --- no source names the hosted product's modules -----------------------------------------------------------------

def test_no_source_in_the_package_names_the_hosted_products_modules() -> None:
    found = [p.name for p in sorted(Path(st.__file__).parent.glob("*.py")) if "mvp." in p.read_text(encoding="utf-8")]
    assert found == []
