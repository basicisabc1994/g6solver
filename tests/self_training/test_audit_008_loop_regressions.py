# SPDX-License-Identifier: Apache-2.0
"""Audit 008 regression tests for the improvement cycle, the scorecard and the diagnosis floor, named after each finding.

The archive cannot run here, so each check states what the archive did. Each check is a function run twice: against
this package (it must pass), and with the archive's behaviour put back in memory by monkeypatching (it must fail), so
none of them is a null instrument. Survival controls show the fixes still let good work through. Every test has its
own ``G6_HOME`` and releases the process-wide stop, so nothing leaks into the rest of the suite. Offline, fakes only.
"""
from __future__ import annotations

import ast
import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pytest

import g6.self_training as st
import g6.self_training.diagnosis as diagnosis_mod
import g6.self_training.improvement_loop as loop
import g6.self_training.persistence as persistence
import g6.self_training.rubrics as rubrics
import g6.self_training.schema as schema
from g6.core import stop
from g6.invariants import assert_envelope, check_envelope
from g6.self_training import (
    EVALUATOR_CONFIDENCE_MIN,
    PROMOTE_AUTO,
    TIER_NAMES,
    ComponentRubric,
    HarnessPatch,
    MaturityScore,
    PatchEvalReport,
    PromotionRefused,
    RubricAssertion,
    RubricProvenance,
    RubricScore,
    ScorecardStore,
    run_improvement_cycle,
)

PACKAGE = Path(st.__file__).parent
DIFF = "--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-old\n+new\n"
NOTICES = (" ".join(("Proprietary", "and", "confidential")), " ".join(("All", "rights", "reserved")))
NEW_FILES = ("__init__.py", "diagnosis.py", "improvement_loop.py", "persistence.py", "rubrics.py", "schema.py")


@pytest.fixture(autouse=True)
def _own_home_and_no_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6_home"))
    monkeypatch.delenv("G6_STOP", raising=False)
    stop.release()
    yield
    stop.release()


def _patch(surfaces=("documentation",), **kw) -> HarnessPatch:
    kw.setdefault("agent_id", "generator-a")
    kw.setdefault("diff", DIFF)
    return HarnessPatch(patch_id="p1", target_surfaces=list(surfaces), **kw)


def _passing() -> PatchEvalReport:
    return PatchEvalReport(targeted_failure_fixed=True, regression_pass_rate=1.0, target_metric_improvement=0.1,
                           evaluator_confidence=0.9, independent_eval=True, evaluator_id="evaluator-b")


class _Harness:
    """A tiny system the cycle changes: its score is the measurement."""

    def __init__(self, before: float = 0.5, after: float = 0.8) -> None:
        self.score, self.after, self.log = before, after, []
        self.before = before

    def apply(self, patch: HarnessPatch) -> None:
        self.before = self.score
        self.score = self.after
        self.log.append("apply")

    def rollback(self, patch: HarnessPatch) -> None:
        self.score = self.before
        self.log.append("rollback")


def _cycle(harness: _Harness, **kw):
    # Autonomy is opted into here so the cycle reaches the apply path (by default a person decides every patch).
    base = dict(sense_fn=lambda: harness.score, propose_fn=lambda sensed: _patch(),
                evaluate_fn=lambda patch: _passing(), apply_fn=harness.apply, rollback_fn=harness.rollback,
                remeasure_fn=lambda: harness.score, regressed_fn=lambda before, after: after < before, enabled=True,
                apply_enabled=True, autonomous_low_risk=True)
    base.update(kw)
    return run_improvement_cycle(**base)


def _raises(exc: type[BaseException], fn) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


def _sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(PACKAGE.glob("*.py"))}


# --- ST-006: a rollback that raises is reported as applied, with rollback_failed, and escalated ----------------------

def check_st006() -> None:
    state = {"patched": False}

    def apply_fn(patch):
        state["patched"] = True

    def rollback_fn(patch):
        raise RuntimeError("the rollback store is unavailable")

    def remeasure_fn():
        raise RuntimeError("the benchmark crashed")

    result = run_improvement_cycle(sense_fn=lambda: 0.5, propose_fn=lambda s: _patch(), evaluate_fn=lambda p: _passing(),
                                   apply_fn=apply_fn, rollback_fn=rollback_fn, remeasure_fn=remeasure_fn,
                                   regressed_fn=lambda b, a: False, enabled=True, apply_enabled=True,
                                   autonomous_low_risk=True)
    assert state["patched"] is True, "the fake did not apply"
    assert result.applied is True, "the patch was applied, and the result must say so"
    assert result.rollback_attempted and result.rollback_failed and not result.rolled_back
    assert result.completion_state == "blocked-escalated" and result.code == loop.ROLLBACK_FAILED
    assert result.patch_in_effect is None
    assert any("rollback raised" in r for r in result.reasons)
    out = result.to_dict()
    assert_envelope(out)
    assert out["warning_card"]["code"] == loop.ROLLBACK_FAILED and out["applied"] is True and out["rollback_failed"]


def test_st006_apply_ok_remeasure_raises_rollback_raises_reports_applied_and_rollback_failed() -> None:
    check_st006()


def test_st006_check_catches_the_archive_swallowed_rollback(monkeypatch) -> None:
    def archive_apply_phase(r, patch, apply_fn, rollback_fn, remeasure_fn, regressed_fn):
        # improvement_loop.py:149-165: a local `applied`, the rollback's failure swallowed (`except: pass`), the error
        # re-raised; the outer handler (:186-199) then returned applied=False while the patch stayed applied. (The
        # archive's apply_fn returned its rollback; rollback_fn stands in for it here.)
        rollback, rolled_back = None, False
        try:
            apply_fn(patch)
            rollback = lambda: rollback_fn(patch)  # noqa: E731
            remeasured = remeasure_fn()
            if regressed_fn(r.sensed, remeasured):
                rollback()
        except Exception:
            if rollback is not None and not rolled_back:
                try:
                    rollback()
                except Exception:
                    pass
            raise
        return r

    monkeypatch.setattr(loop, "_apply_phase", archive_apply_phase)
    with pytest.raises(AssertionError):
        check_st006()


def test_st006_rollback_raising_after_a_regression_is_also_reported_as_applied() -> None:
    def rollback_fn(patch):
        raise OSError("disk full")

    result = _cycle(_Harness(), rollback_fn=rollback_fn, regressed_fn=lambda b, a: True)
    assert (result.applied, result.rollback_failed, result.rolled_back) == (True, True, False)
    assert result.code == loop.ROLLBACK_FAILED and result.completion_state == "blocked-escalated"


def test_st006_an_apply_that_raises_says_the_patch_may_be_partly_applied() -> None:
    def apply_fn(patch):
        raise RuntimeError("half written")

    harness = _Harness()
    result = _cycle(harness, apply_fn=apply_fn)
    assert result.code == loop.APPLY_FAILED and result.completion_state == "blocked-escalated"
    assert "partly applied" in result.message
    # Review B1-6: the cycle holds the rollback, so it rolls the partly applied patch back itself.
    assert harness.log == ["rollback"] and result.rolled_back and result.patch_in_effect is False
    assert_envelope(result.to_dict())


# --- ST-015: the exported cycle reads the process-wide stop before sensing and between phases ------------------------

def check_st015() -> None:
    calls: list[str] = []
    stop.engage("an owner pause")
    try:
        result = run_improvement_cycle(sense_fn=lambda: calls.append("sense"),
                                       propose_fn=lambda s: calls.append("propose") or _patch(),
                                       evaluate_fn=lambda p: _passing(), enabled=True)
    finally:
        stop.release()
    assert calls == [], "sense_fn ran while G6 was stopped"
    assert result.completion_state == "blocked-escalated" and result.code == stop.STOPPED == "G6_E_STOPPED"
    assert "an owner pause" in result.message and result.stage == "sense"
    assert_envelope(result.to_dict())


def test_st015_with_the_stop_engaged_run_improvement_cycle_does_not_call_sense_fn() -> None:
    check_st015()


def test_st015_check_catches_the_archive_loop_that_never_read_the_stop(monkeypatch) -> None:
    # improvement_loop.py:89-105: only SelfTrainingBlock (self_training_block.py:1280) read the kill switch.
    monkeypatch.setattr(loop, "_stop_reason", lambda: None)
    with pytest.raises(AssertionError):
        check_st015()


def check_st015_between_phases() -> None:
    harness = _Harness()

    def evaluate_fn(patch):
        stop.engage("stopped while evaluating")
        return _passing()

    result = _cycle(harness, evaluate_fn=evaluate_fn)
    stop.release()
    assert harness.log == [], "the patch was applied after the stop was engaged"
    assert result.code == stop.STOPPED and result.stage == "apply" and not result.applied


def test_st015_a_stop_engaged_mid_cycle_stops_before_the_next_phase() -> None:
    check_st015_between_phases()


def test_st015_between_phases_check_catches_the_archive(monkeypatch) -> None:
    monkeypatch.setattr(loop, "_stop_reason", lambda: None)
    with pytest.raises(AssertionError):
        check_st015_between_phases()


def test_st015_a_stop_during_sensing_means_nothing_is_proposed() -> None:
    calls: list[str] = []

    def sense_fn():
        calls.append("sense")
        stop.engage("stopped while sensing")
        return 0.5

    result = run_improvement_cycle(sense_fn=sense_fn, propose_fn=lambda s: calls.append("propose") or _patch(),
                                   evaluate_fn=lambda p: _passing(), enabled=True)
    assert calls == ["sense"] and result.code == stop.STOPPED and result.stage == "propose"


def test_st015_the_environment_stop_is_read_too(monkeypatch) -> None:
    monkeypatch.setenv("G6_STOP", "maintenance")
    result = _cycle(_Harness())
    assert result.code == stop.STOPPED and "maintenance" in result.message and not result.applied


# --- ST-007 (touched): no apply without a remeasure; a cycle result is never verified --------------------------------

def check_st007_remeasure_required() -> None:
    harness = _Harness()
    sensed: list[int] = []
    result = run_improvement_cycle(sense_fn=lambda: sensed.append(1), propose_fn=lambda s: _patch(["prompt"]),
                                   evaluate_fn=lambda p: _passing(), apply_fn=harness.apply,
                                   rollback_fn=harness.rollback, enabled=True, apply_enabled=True,
                                   autonomous_low_risk=True)
    assert harness.log == [], "a patch was applied with no remeasure"
    assert sensed == [] and result.code == loop.REMEASURE_REQUIRED and result.completion_state == "blocked-escalated"


def test_st007_an_r1_patch_with_apply_enabled_and_no_remeasure_is_not_applied() -> None:
    check_st007_remeasure_required()


def test_st007_check_catches_the_archive_optional_remeasure(monkeypatch) -> None:
    # improvement_loop.py:152-157: remeasure_fn and regressed_fn were optional; without them the patch stayed applied.
    monkeypatch.setattr(loop, "_remeasure_missing", lambda remeasure_fn, regressed_fn: False)
    with pytest.raises(AssertionError):
        check_st007_remeasure_required()


def _scenario_results() -> list[loop.ImprovementCycleResult]:
    return [
        run_improvement_cycle(sense_fn=lambda: 0, propose_fn=lambda s: _patch(), evaluate_fn=lambda p: _passing()),
        run_improvement_cycle(sense_fn=lambda: 0, propose_fn=lambda s: _patch(), evaluate_fn=lambda p: _passing(),
                              enabled=True, autonomous_low_risk=True),
        run_improvement_cycle(sense_fn=lambda: 0, propose_fn=lambda s: _patch(), evaluate_fn=lambda p: _passing(),
                              enabled=True),
        _cycle(_Harness()),
        _cycle(_Harness(after=0.1)),
        _cycle(_Harness(), propose_fn=lambda s: _patch(["rubric"])),
        _cycle(_Harness(), evaluate_fn=lambda p: PatchEvalReport()),
    ]


def check_never_verified(results) -> None:
    states = [r.completion_state for r in results]
    assert all(s in ("qualified-draft", "blocked-escalated") for s in states), states
    for r in results:
        assert not check_envelope(r.to_dict()), check_envelope(r.to_dict())


def test_st001_st007_no_cycle_result_is_ever_verified() -> None:
    check_never_verified(_scenario_results())


def test_st001_st007_check_catches_the_archive_label_pass_through(monkeypatch) -> None:
    # improvement_loop.py:182 returned decision.reliability_label, and the archive gate labelled promote_auto
    # "verified" (patch_promotion_gate.py:53-55).
    gate = loop.govern_patch_promotion

    def archive_gate(patch, report, **kw):
        d = gate(patch, report, **kw)
        return dataclasses.replace(d, reliability_label="verif" + "ied") if d.decision == PROMOTE_AUTO else d

    monkeypatch.setattr(loop, "govern_patch_promotion", archive_gate)
    monkeypatch.setattr(loop, "_cap", lambda state: state)
    with pytest.raises(AssertionError):
        check_never_verified(_scenario_results())


# --- ST-018 (touched): the cycle applies only the patch the gate decided on ------------------------------------------

def check_st018_binding() -> None:
    harness = _Harness()

    def snapshot_fn(patch):
        patch.diff += "+ and one more line nobody evaluated\n"
        return True

    result = _cycle(harness, snapshot_fn=snapshot_fn)
    assert harness.log == [], "a patch changed after the decision was applied"
    assert result.code == loop.DECISION_MISMATCH and not result.applied


def test_st018_a_patch_changed_after_the_decision_is_not_applied() -> None:
    check_st018_binding()


def test_st018_check_catches_an_unbound_apply(monkeypatch) -> None:
    # The archive's HarnessPatch carried no change, so there was nothing to bind the decision to.
    monkeypatch.setattr(loop, "decision_matches", lambda decision, patch: True)
    with pytest.raises(AssertionError):
        check_st018_binding()


# --- survival controls: the cycle still improves things -------------------------------------------------------------

@pytest.mark.parametrize(("surface", "decision"), [("documentation", "promote_auto"), ("prompt", "canary")])
def test_survival_an_improving_auto_promotable_patch_is_applied_kept_and_a_qualified_draft(surface, decision) -> None:
    harness = _Harness(before=0.5, after=0.8)
    result = _cycle(harness, propose_fn=lambda s: _patch([surface]))
    assert result.decision.decision == decision
    assert harness.log == ["apply"] and harness.score == 0.8
    assert result.applied and result.remeasure_ran and result.regressed is False and not result.rollback_attempted
    assert result.patch_in_effect is True and result.stage == "done"
    assert result.completion_state == "qualified-draft" and result.code == loop.APPLIED_UNWITNESSED
    out = result.to_dict()
    assert_envelope(out)
    assert [e["source"] for e in out["evidence"]] == ["govern_patch_promotion", "remeasure_fn"]


def test_survival_a_regressing_patch_is_rolled_back_and_the_harness_restored() -> None:
    harness = _Harness(before=0.5, after=0.2)
    result = _cycle(harness)
    assert harness.log == ["apply", "rollback"] and harness.score == 0.5
    assert result.applied and result.rolled_back and result.patch_in_effect is False
    assert result.code == loop.ROLLED_BACK and result.completion_state == "blocked-escalated"


# --- ST-009: the tier is the latest evidenced one; a flag holds it and promote never clears it ----------------------

def _audit(component: str = "c", *, score_all: bool = True, reviewed: bool = True, invoke_ok: bool = True):
    rubric = ComponentRubric(component=component, op="infer",
                             assertions=[RubricAssertion(field="total", check="gte", expected=0)],
                             provenance=RubricProvenance(reviewed_by="a reviewer" if reviewed else ""))
    output = {"total": 3} if score_all else {"total": -1}
    return rubrics.audit_output(component, output=output, rubric=rubric, invoke_ok=invoke_ok,
                                invoke_error="" if invoke_ok else "RuntimeError: boom")


def check_st009_flag_laundering(store: ScorecardStore) -> None:
    store.append_audit(_audit())
    assert store.get_score("c").current_tier == 5
    store.flag("c", "a user reported a wrong total")
    try:
        store.promote("c", 4)
    except PromotionRefused:
        pass
    assert _raises(PromotionRefused, lambda: store.promote("c", 5)), "promoted past an unresolved flag"
    score = store.get_score("c")
    assert score.flagged and score.flag_reason == "a user reported a wrong total", "the flag was cleared"


def test_st009_flag_then_t4_then_t5_promote_is_refused_and_the_flag_survives(tmp_path) -> None:
    check_st009_flag_laundering(ScorecardStore(tmp_path / "card"))


def test_st009_check_catches_the_archive_flag_laundering(tmp_path, monkeypatch) -> None:
    def archive_promote(self, component, tier):
        # self_training_block.py:551-618 gated only tier >= 5 (reading `flagged`); persistence.py:137-147 then
        # rewrote the score with flagged=False, so promote(c, 4) cleared the flag for promote(c, 5).
        if tier >= 5:
            history, score = self.audit_history(component), self.get_score(component)
            if not history or not history[-1].get("passed_threshold") or score is None or score.flagged:
                raise PromotionRefused("archive evidence gate")
        base = self._scores.get(component) or MaturityScore(component=component)
        self._scores[component] = base.model_copy(update={"current_tier": tier, "tier_name": TIER_NAMES[tier],
                                                          "flagged": False, "flag_reason": ""})
        return self._scores[component]

    monkeypatch.setattr(ScorecardStore, "promote", archive_promote)
    with pytest.raises(AssertionError):
        check_st009_flag_laundering(ScorecardStore(tmp_path / "card"))


def check_st009_latest_evidence(store: ScorecardStore) -> None:
    store.append_audit(_audit())
    for _ in range(3):
        store.append_audit(_audit(invoke_ok=False))
    score = store.get_score("c")
    assert score.current_tier == 2 and score.tier_source == "audit", score
    assert score.max_tier_seen == 5 and score.audit_count == 4 and score.pass_count == 1
    assert store.get_scorecard().above_threshold == 0
    store.flag("d", "under review")
    store.append_audit(_audit("d"))
    assert store.get_score("d").current_tier == 0, "a flagged component's tier rose on an audit"


def test_st009_audit_t5_then_three_failing_audits_reports_the_latest_tier(tmp_path) -> None:
    check_st009_latest_evidence(ScorecardStore(tmp_path / "card"))


def test_st009_check_catches_the_archive_ratchet(tmp_path, monkeypatch) -> None:
    # persistence.py:92: new_tier = max(existing.current_tier, result.tier)
    monkeypatch.setattr(persistence, "_next_tier",
                        lambda existing, result: max(existing.current_tier, result.tier) if existing else result.tier)
    with pytest.raises(AssertionError):
        check_st009_latest_evidence(ScorecardStore(tmp_path / "card"))


def test_survival_st009_a_flag_resolved_with_evidence_lets_a_passing_component_be_promoted(tmp_path) -> None:
    store = ScorecardStore(tmp_path / "card")
    store.flag("c", "wrong total")
    assert _raises(ValueError, lambda: store.resolve_flag("c", "  "))
    store.resolve_flag("c", "fixed in the rounding change; re-audited")
    store.append_audit(_audit())
    promoted = store.promote("c", 6)
    assert promoted.current_tier == 6 and promoted.tier_name == "promoted" and promoted.tier_source == "promotion"
    assert "re-audited" in promoted.flag_resolution
    assert ScorecardStore(tmp_path / "card").get_score("c") == promoted   # it was saved


# --- ST-010: an empty rubric is unevaluable and earns at most T3; tiers are named for shape -------------------------

def check_st010_empty_rubrics() -> None:
    reviewed = RubricProvenance(reviewed_by="a reviewer")
    empty = ComponentRubric(component="c", op="infer", provenance=reviewed)
    weightless = ComponentRubric(component="c", op="infer", provenance=reviewed,
                                 assertions=[RubricAssertion(field="x", check="exists", weight=0)])
    for rubric in (empty, weightless):
        scored = rubrics.evaluate_rubric(rubric, {"x": 1})
        assert scored.evaluable is False and scored.score == 0.0, scored
        audit = rubrics.audit_output("c", output={"x": 1}, rubric=rubric)
        assert audit.tier <= 3 and not audit.passed_threshold and not audit.evaluable, audit


def test_st010_rubric_with_assertions_empty_scores_unevaluable_and_tier_at_most_t3() -> None:
    check_st010_empty_rubrics()


def test_st010_check_catches_the_archive_perfect_empty_score(monkeypatch) -> None:
    def archive_evaluate_rubric(rubric, output):
        # rubrics.py:111-117: no assertions, or total weight 0, returned 1.0
        if not rubric.assertions or sum(a.weight for a in rubric.assertions) == 0:
            return RubricScore(evaluable=True, score=1.0, reviewed=rubric.reviewed)
        raise AssertionError("not reached by this check")

    monkeypatch.setattr(rubrics, "evaluate_rubric", archive_evaluate_rubric)
    with pytest.raises(AssertionError):
        check_st010_empty_rubrics()


def check_st010_unreviewed_and_absent_rubrics() -> None:
    echo = ComponentRubric(component="c", op="infer", assertions=[RubricAssertion(field="echo", check="not_empty")])
    assert rubrics.audit_output("c", output={"echo": "the input, echoed"}, rubric=echo).tier <= 3, "unreviewed echo"
    assert rubrics.audit_output("c", output={"anything": 1}, rubric=None).tier <= 3, "no rubric"


def test_st010_an_unreviewed_echo_rubric_or_no_rubric_earns_at_most_t3() -> None:
    check_st010_unreviewed_and_absent_rubrics()


def test_st010_check_catches_the_archive_tiering(monkeypatch) -> None:
    def archive_classify_tier(*, invoke_ok, invoke_error="", rubric_score=None):
        # self_training_block.py:51-75: no rubric -> T4 "integrated"; score >= 0.8 -> T5 "validated", reviewed or not
        if not invoke_ok:
            return 2
        if rubric_score is None:
            return 4
        s = rubric_score.score
        return 5 if s >= 0.8 else 4 if s >= 0.5 else 3 if s >= 0.2 else 2

    monkeypatch.setattr(rubrics, "classify_tier", archive_classify_tier)
    with pytest.raises(AssertionError):
        check_st010_unreviewed_and_absent_rubrics()


def check_st010_labels() -> None:
    names = set(schema.TIER_NAMES.values())
    assert not names & {"validated", "production", "integrated", "functional"}, names
    assert all("shape" in schema.TIER_NAMES[t] for t in schema.SHAPE_CHECK_TIERS)
    assert RubricScore(evaluable=True).kind == "shape"
    assert not list(PACKAGE.rglob("*.json")), "the package ships data (the archive shipped a frozen baseline)"


def test_st010_tiers_are_named_for_shape_and_no_baseline_ships() -> None:
    check_st010_labels()


def test_st010_check_catches_the_archive_tier_names(monkeypatch) -> None:
    # schema.py:22-30
    monkeypatch.setattr(schema, "TIER_NAMES", {0: "stub", 1: "structural", 2: "mechanical", 3: "functional",
                                               4: "integrated", 5: "validated", 6: "production"})
    with pytest.raises(AssertionError):
        check_st010_labels()


def test_survival_st010_a_non_empty_reviewed_rubric_still_scores() -> None:
    rubric = ComponentRubric(component="c", op="infer", provenance=RubricProvenance(reviewed_by="a reviewer"),
                             assertions=[RubricAssertion(field="total", check="gte", expected=0),
                                         RubricAssertion(field="items", check="length_gte", expected=1)])
    half = rubrics.audit_output("c", output={"total": 3, "items": []}, rubric=rubric)
    full = rubrics.audit_output("c", output={"total": 3, "items": [1]}, rubric=rubric)
    assert (half.score, half.tier, half.tier_name) == (0.5, 4, "shape-partial")
    assert (full.score, full.tier, full.tier_name, full.passed_threshold) == (1.0, 5, "shape-checked", True)


# --- ST-012: no hard-coded number reads as a confidence -------------------------------------------------------------

def _diagnoses() -> list:
    cases = [(5, 1.0, ""), (3, 0.0, ""), (4, 0.6, ""), (2, 0.1, ""), (2, 0.0, "ModuleNotFoundError: x"),
             (0, 0.0, "No AIBlock subclass found"), (2, 0.0, "TIMEOUT after 30s"), (2, 0.0, "a paraphrased failure")]
    found = []
    for tier, score, error in cases:
        for evaluable in (True, False):
            for reviewed in (True, False):
                found.append(diagnosis_mod.diagnose("c", error, tier, score, evaluable=evaluable, reviewed=reviewed))
    return found


def check_st012(found) -> None:
    for d in found:
        numbers = {k: v for k, v in vars(d).items() if isinstance(v, int | float) and not isinstance(v, bool)}
        assert not numbers, f"{d.failure_class} carries {numbers}, which can be read as a confidence"
        assert "confidence" not in vars(d)
    assert not hasattr(st, "CompetenceOracle") and not (PACKAGE / "competence_oracle.py").exists()


def test_st012_no_hard_coded_confidence_can_satisfy_evaluator_confidence_min_or_ready_for_promotion() -> None:
    check_st012(_diagnoses())


def test_st012_check_catches_the_archive_hard_coded_confidences(monkeypatch) -> None:
    @dataclass(frozen=True)
    class ArchiveDiagnosis:
        component: str
        failure_class: str
        description: str
        suggested_fix: str
        confidence: float
        blocking: bool = True

    def archive_diagnose(component, error, tier, score, **_):
        # diagnosis.py:98-139: confidence=1.0 / 0.9 / 0.8 / 0.85 / 0.5
        if tier >= 5:
            return ArchiveDiagnosis(component, "none", "passes", "", 1.0, False)
        return ArchiveDiagnosis(component, "runtime_error", "general failure", "inspect", 0.85 if error else 0.5)

    monkeypatch.setattr(diagnosis_mod, "diagnose", archive_diagnose)
    with pytest.raises(AssertionError):
        check_st012(_diagnoses())
    assert 1.0 >= EVALUATOR_CONFIDENCE_MIN   # the archive's 1.0 would have met the gate's minimum


def _identifiers(source: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.keyword | ast.arg) and getattr(node, "arg", None):
            found.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            found.add(node.name)
    return found


def _confidence_constants(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        pairs = []
        if isinstance(node, ast.keyword) and node.arg:
            pairs = [(node.arg, node.value)]
        elif isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if ("confidence" in name.lower() and name != "EVALUATOR_CONFIDENCE_MIN" and isinstance(value, ast.Constant)
                    and isinstance(value.value, int | float) and value.value >= EVALUATOR_CONFIDENCE_MIN):
                found.append(f"{name}={value.value}")
    return found


def check_st012_sources(sources: dict[str, str]) -> None:
    for name, text in sources.items():
        assert not _confidence_constants(text), (name, _confidence_constants(text))
        assert not _identifiers(text) & {"ready_for_promotion", "CompetenceOracle"}, name


def test_st012_no_package_source_hard_codes_a_passing_confidence() -> None:
    check_st012_sources(_sources())


def test_st012_source_check_catches_the_archive_constants() -> None:
    for archive in ('Diagnosis(component=c, failure_class="none", confidence=1.0)\n',
                    "ready = derived_tier >= 5 and confidence >= 0.6\nr = Report(ready_for_promotion=ready)\n"):
        with pytest.raises(AssertionError):
            check_st012_sources({"archive.py": archive})


# --- ST-019: the store is what it says it is, in a directory you name -----------------------------------------------

def check_st019(tmp_path: Path) -> None:
    for missing in (None, "", "   "):
        assert _raises(ValueError, lambda m=missing: ScorecardStore(m)), f"ScorecardStore({missing!r}) had a default"
    store = ScorecardStore(tmp_path / "card")
    store.append_audit(_audit())
    store.flag("c", "check")
    assert sorted(p.name for p in (tmp_path / "card").iterdir()) == ["audit_log.jsonl", "scorecard.json"]
    doc = (persistence.__doc__ or "") + (ScorecardStore.__doc__ or "")
    assert "audit_log.jsonl" in doc and "scorecard.json" in doc and "append-only" in doc
    assert "Not SQLite" in doc and "no default directory" in doc
    relative = ScorecardStore("named_cards")
    assert relative.store_dir == (tmp_path / "g6_home" / "named_cards").resolve()


def test_st019_store_is_jsonl_and_json_in_an_explicit_directory_with_no_home_default(tmp_path) -> None:
    check_st019(tmp_path)


def test_st019_check_catches_the_archive_home_default(tmp_path, monkeypatch) -> None:
    # persistence.py:27,35: Path(store_dir or os.path.expanduser("~/.g6/self_training")); home moved into tmp_path here
    monkeypatch.setattr(persistence, "_resolve_store_dir",
                        lambda store_dir: Path(store_dir) if store_dir else tmp_path / "home" / ".g6" / "self_training")
    with pytest.raises(AssertionError):
        check_st019(tmp_path)


# --- ST-013 / ST-016 / ST-017 (touched): nothing states verified, calls out, or carries the archive's notice --------

def check_never_states_verified(sources: dict[str, str]) -> None:
    stated = {name: [n.lineno for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Constant) and n.value == "verified"]
              for name, text in sources.items()}
    assert not any(stated.values()), stated


def test_st013_no_code_path_in_the_package_states_verified() -> None:
    check_never_states_verified(_sources())


def test_st013_check_catches_the_archive_literal() -> None:
    # self_training_block.py:563, one of 14 unconditional literals
    with pytest.raises(AssertionError):
        check_never_states_verified({"block.py": 'out = Output(op="promote", completion_state="verified")\n'})


_OUTBOUND = {"subprocess", "socket", "urllib", "http", "requests", "httpx", "anthropic", "openai", "mvp"}


def check_no_outbound(sources: dict[str, str]) -> None:
    found = {}
    for name, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            bad = [n for n in names if n.split(".")[0] in _OUTBOUND or n in ("g6.core.llm", "g6.core.model_calls")]
            if bad:
                found.setdefault(name, []).extend(bad)
    assert not found, found
    assert not hasattr(loop, "make_codex_harness_proposer")


def test_st016_the_package_imports_no_model_network_or_subprocess_module() -> None:
    check_no_outbound(_sources())


def test_st016_check_catches_the_archive_codex_proposer() -> None:
    # improvement_loop.py:12, 215-216: CodexLLMCaller().complete ran `codex exec`
    for archive in ("from mvp.agent_codex.codex_llm_caller import CodexLLMCaller\n", "import subprocess\n"):
        with pytest.raises(AssertionError):
            check_no_outbound({"archive.py": archive})


def check_licence_headers(sources: dict[str, str]) -> None:
    for name, text in sources.items():
        assert text.startswith("# SPDX-License-Identifier: Apache-2.0\n"), name
        assert not any(n in text for n in NOTICES), name


def test_st017_files_added_by_this_port_carry_spdx_and_no_proprietary_notice() -> None:
    check_licence_headers({n: t for n, t in _sources().items() if n in NEW_FILES})


def test_st017_check_catches_the_archive_header() -> None:
    header = f"# Copyright (c) 2026 Owner. {NOTICES[1]}.\n# {NOTICES[0]}. See LICENSE for terms.\n"
    with pytest.raises(AssertionError):
        check_licence_headers({"archive.py": header})


def test_st011_the_unwired_training_stack_is_not_ported() -> None:
    dropped = ("self_training_block.py", "state_machine.py", "unified_loop.py", "improve_adapter.py", "grounding.py",
               "agentic_runtime.py", "csf_self_mod_verify.py", "competence_oracle.py")
    assert not [n for n in dropped if (PACKAGE / n).exists()]
