# SPDX-License-Identifier: Apache-2.0
"""Behaviour of ``run_improvement_cycle``: off by default, applies only an auto-promotable decision with apply enabled,
remeasures every applied patch and rolls back on a regression, fails closed, and answers in the library envelope.
Offline, fakes only; each test has its own ``G6_HOME`` and releases the process-wide stop."""
from __future__ import annotations

import pytest

import g6.self_training.improvement_loop as loop
from g6.core import stop
from g6.invariants import assert_envelope
from g6.self_training import (
    HarnessPatch,
    ImprovementCycleResult,
    PatchEvalReport,
    make_no_eval_report,
    run_improvement_cycle,
)

DIFF = "--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-old\n+new\n"


@pytest.fixture(autouse=True)
def _own_home_and_no_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    monkeypatch.delenv("G6_STOP", raising=False)
    stop.release()
    yield
    stop.release()


def _patch(surfaces=("documentation",), **kw) -> HarnessPatch:
    kw.setdefault("agent_id", "agent-1")
    kw.setdefault("diff", DIFF)
    return HarnessPatch(patch_id="patch-1", base_harness_version="v1", target_surfaces=list(surfaces), **kw)


def _passing() -> PatchEvalReport:
    return PatchEvalReport(targeted_failure_fixed=True, regression_pass_rate=1.0, target_metric_improvement=0.1,
                           evaluator_confidence=0.9, independent_eval=True, evaluator_id="independent-reviewer")


class _Applier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, patch):
        self.calls.append("apply")

    def rollback(self, patch):
        self.calls.append("rollback")


def _run(**kw) -> ImprovementCycleResult:
    # Autonomy is opted into here, so these tests reach the apply path; the default (a person decides) has its own test.
    base = dict(sense_fn=lambda: {"score": 1}, propose_fn=lambda sensed: _patch(), evaluate_fn=lambda p: _passing(),
                enabled=True, autonomous_low_risk=True)
    if isinstance(kw.get("apply_fn"), _Applier):
        base["rollback_fn"] = kw["apply_fn"].rollback
    base.update(kw)
    result = run_improvement_cycle(**base)
    assert_envelope(result.to_dict())
    return result


def test_off_by_default_and_sense_fn_is_not_called() -> None:
    def sense_fn():
        raise AssertionError("sense_fn must not run while the cycle is off")

    result = _run(sense_fn=sense_fn, enabled=False)
    assert result.enabled is False and result.stage == "off" and result.code == loop.LOOP_OFF
    assert result.completion_state == "qualified-draft" and not result.applied


def test_auto_promotable_without_apply_fn_stays_propose_only() -> None:
    result = _run()
    assert result.decision.auto_promotable and not result.applied and result.code == loop.PROPOSE_ONLY
    assert result.completion_state == "qualified-draft" and result.patch_in_effect is False


def test_apply_flag_off_prevents_apply_even_with_apply_fn() -> None:
    applier = _Applier()
    result = _run(apply_fn=applier, remeasure_fn=lambda: 1, regressed_fn=lambda b, a: False, apply_enabled=False)
    assert applier.calls == [] and result.code == loop.PROPOSE_ONLY


@pytest.mark.parametrize(("surfaces", "code"), [(["security_boundary"], loop.REVIEW_REQUIRED),
                                                 (["rubric"], loop.REVIEW_REQUIRED)])
def test_decisions_that_need_a_person_are_never_applied(surfaces, code) -> None:
    applier = _Applier()
    result = _run(propose_fn=lambda s: _patch(surfaces), apply_fn=applier, remeasure_fn=lambda: 1,
                  regressed_fn=lambda b, a: False, apply_enabled=True)
    assert applier.calls == [] and result.code == code and result.decision.requires_human
    assert result.completion_state == "qualified-draft"


def test_manual_only_policy_routes_an_r0_patch_to_a_person() -> None:
    applier = _Applier()
    result = _run(apply_fn=applier, remeasure_fn=lambda: 1, regressed_fn=lambda b, a: False, apply_enabled=True,
                  autonomous_low_risk=False)
    assert applier.calls == [] and result.code == loop.REVIEW_REQUIRED


def test_no_evaluation_is_rejected_and_not_applied() -> None:
    applier = _Applier()
    result = _run(evaluate_fn=lambda p: make_no_eval_report(), apply_fn=applier, remeasure_fn=lambda: 1,
                  regressed_fn=lambda b, a: False, apply_enabled=True)
    assert applier.calls == [] and result.code == loop.GATE_REJECTED
    assert result.completion_state == "blocked-escalated"


def test_a_hard_rule_block_is_not_applied() -> None:
    applier = _Applier()
    result = _run(propose_fn=lambda s: _patch(permission_change="expanded"), apply_fn=applier,
                  remeasure_fn=lambda: 1, regressed_fn=lambda b, a: False, apply_enabled=True)
    assert applier.calls == [] and result.code == loop.GATE_BLOCKED


def test_a_regression_rolls_back_and_applied_stays_true() -> None:
    applier = _Applier()
    result = _run(apply_fn=applier, remeasure_fn=lambda: {"score": 0}, regressed_fn=lambda b, a: True,
                  apply_enabled=True)
    assert applier.calls == ["apply", "rollback"]
    assert result.applied and result.rolled_back and result.regressed is True and result.code == loop.ROLLED_BACK
    assert result.completion_state == "blocked-escalated" and result.patch_in_effect is False


def test_a_regressed_fn_that_does_not_answer_true_or_false_counts_as_a_regression() -> None:
    applier = _Applier()
    result = _run(apply_fn=applier, remeasure_fn=lambda: 1, regressed_fn=lambda b, a: None, apply_enabled=True)
    assert applier.calls == ["apply", "rollback"] and result.regressed is None and result.code == loop.ROLLED_BACK


def test_a_remeasure_that_raises_rolls_back() -> None:
    applier = _Applier()

    def remeasure_fn():
        raise RuntimeError("no benchmark")

    result = _run(apply_fn=applier, remeasure_fn=remeasure_fn, regressed_fn=lambda b, a: False, apply_enabled=True)
    assert applier.calls == ["apply", "rollback"] and result.code == loop.REMEASURE_FAILED
    assert result.applied and result.rolled_back and not result.remeasure_ran


def test_applying_without_a_rollback_fn_is_escalated_before_anything_runs() -> None:
    applier = _Applier()
    result = _run(apply_fn=applier, rollback_fn=None, remeasure_fn=lambda: 1, regressed_fn=lambda b, a: False,
                  apply_enabled=True)
    assert applier.calls == [] and not result.applied and result.stage == "configure"
    assert result.code == loop.NO_ROLLBACK and result.completion_state == "blocked-escalated"
    assert result.patch_in_effect is False


class _FailedResult:
    def is_fail(self) -> bool:
        return True


def _raise(patch):
    raise RuntimeError("no git")


@pytest.mark.parametrize("snapshot_fn", [lambda p: None, lambda p: False, lambda p: _FailedResult(), _raise])
def test_a_failed_snapshot_means_nothing_is_applied(snapshot_fn) -> None:
    applier = _Applier()
    result = _run(apply_fn=applier, remeasure_fn=lambda: 1, regressed_fn=lambda b, a: False, apply_enabled=True,
                  snapshot_fn=snapshot_fn)
    assert applier.calls == [] and result.code == loop.SNAPSHOT_FAILED


def test_a_proposer_that_raises_fails_closed_without_raising() -> None:
    def propose_fn(sensed):
        raise RuntimeError("boom")

    result = _run(propose_fn=propose_fn)
    assert result.completion_state == "blocked-escalated" and result.code == loop.CYCLE_FAILED
    assert result.stage == "propose" and any("boom" in r for r in result.reasons) and not result.applied


def test_no_patch_proposed_is_a_qualified_draft() -> None:
    result = _run(propose_fn=lambda s: None)
    assert result.code == loop.NO_PATCH and result.completion_state == "qualified-draft" and result.patch is None


@pytest.mark.parametrize(("kw", "stage"), [({"propose_fn": lambda s: {"target_surfaces": ["documentation"]}}, "propose"),
                                           ({"evaluate_fn": lambda p: {"independent_eval": True}}, "evaluate")])
def test_a_stage_that_returns_the_wrong_type_fails_closed(kw, stage) -> None:
    result = _run(**kw)
    assert result.code == loop.CYCLE_FAILED and result.stage == stage


def test_ids_are_passed_through_or_generated() -> None:
    given = _run(request_id="req-1", run_id="run-1").to_dict()
    assert (given["request_id"], given["run_id"]) == ("req-1", "run-1")
    made = _run().to_dict()
    assert made["request_id"] and made["run_id"]


def test_to_dict_carries_the_envelope_and_the_cycle_fields() -> None:
    applier = _Applier()
    out = _run(apply_fn=applier, remeasure_fn=lambda: {"score": 2}, regressed_fn=lambda b, a: False,
               apply_enabled=True).to_dict()
    for key in ("completion_state", "warning_card", "evidence", "request_id", "run_id", "stage", "applied",
                "rolled_back", "rollback_failed", "patch_in_effect", "decision", "patch", "eval_report", "reasons"):
        assert key in out, key
    assert out["completion_state"] == out["reliability_label"] == "qualified-draft"
    assert out["patch"]["diff"] == DIFF and out["remeasured"] == {"score": 2}
    assert out["warning_card"]["code"] == loop.APPLIED_UNWITNESSED
