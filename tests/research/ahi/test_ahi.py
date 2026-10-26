# SPDX-License-Identifier: Apache-2.0
"""AHI contracts and lifecycle, including a mechanical VLE-1 slice (ECHA spec §7).

The slice shows only that a candidate guard can reject an injected API-contract regression and let an
authorised migration through. It makes no claim of learned uplift: that needs the held-out,
pre-registered protocol of §8.
"""
from __future__ import annotations

import inspect

import pytest

from g6.research.ahi import (
    AdaptationProposal,
    Control,
    FailureCase,
    Funnel,
    LearnedComponent,
    decide_promotion,
    validate_locally,
)
from g6.research.echa import ResearchDisabled

H1, H2 = "a" * 64, "b" * 64


@pytest.fixture(autouse=True)
def _research_on(monkeypatch):
    monkeypatch.setenv("G6_ECHA_RESEARCH", "1")


def proposal(**over):
    base = dict(proposal_id="p1", proposer="learner", source_case_ids=("c1",),
                target_component="harness.checks", intended_change="add api_contract_regression_guard to code_change",
                predicted_benefit="catches removed/renamed legacy parameters", falsifiers=("misses a renamed kwarg",),
                test_plan="controls N1, P1; held-out repos R2-R4", candidate_hash=H1, baseline_hash=H2,
                transfer_hypothesis="other Python libraries' public functions", cost_cap=1.0,
                rollback="remove the guard from the check registry")
    base.update(over)
    return AdaptationProposal(**base)


# --- the candidate guard: replay legacy client calls against the new implementation -------------
def api_contract_guard(case):
    """Flag when a legacy call no longer binds to the new signature, unless the removal was declared."""
    sig = inspect.signature(case["new_impl"])
    declared = set(case.get("declared_removals", ()))
    for call in case["legacy_calls"]:
        if set(call) & declared:
            continue
        try:
            sig.bind(**call)
        except TypeError:
            return True
    return False


def old_api(path, timeout=10): ...
def renamed_kwarg(path, timeout_s=10): ...                       # injected defect
def with_shim(path, timeout_s=10, timeout=None): ...            # authorised migration, compatible
def removed_timeout(path): ...                                  # authorised + declared removal

LEGACY = [{"path": "a"}, {"path": "a", "timeout": 5}]
NEG = Control("N1 renamed kwarg", "negative", {"new_impl": renamed_kwarg, "legacy_calls": LEGACY}, "task-author")
POS = Control("P1 shim migration", "positive", {"new_impl": with_shim, "legacy_calls": LEGACY}, "task-author")
DECOY = Control("P2 declared removal", "positive",
                {"new_impl": removed_timeout, "legacy_calls": LEGACY, "declared_removals": ["timeout"]}, "task-author")


def test_vle1_mechanical_slice_reaches_shadow_only():
    case = FailureCase("c1", "legacy client call still binds", "evaluator-only legacy suite", "high",
                       "verification_gap", "visible tests never exercise the old kwarg",
                       ("the legacy client is itself out of contract",), {"call": {"path": "a", "timeout": 5}},
                       "public functions with keyword parameters")
    assert case.root_cause == "verification_gap"
    p = proposal()
    v = validate_locally(p, api_contract_guard, [NEG, POS, DECOY])
    assert v.status == "local_validated", v.reasons
    d = decide_promotion(p, v, policy_version="ahi-0.1", reviewer="owner", owner_confirmed=True,
                         regression="pass", budget="pass", security="pass")
    assert d.disposition == "shadow_approved" and v.digest in d.evidence_hashes


def test_too_broad_guard_is_rejected_by_the_positive_control():
    def flag_everything(case):
        return True
    assert validate_locally(proposal(), flag_everything, [NEG, POS]).status == "rejected"


def test_guard_that_misses_the_defect_is_rejected():
    assert validate_locally(proposal(), lambda case: False, [NEG, POS]).status == "rejected"


def test_crashing_or_non_bool_guard_blocks():
    def boom(case):
        raise RuntimeError("x")
    assert validate_locally(proposal(), boom, [NEG, POS]).status == "blocked"
    assert validate_locally(proposal(), lambda c: "yes", [NEG, POS]).status == "blocked"


def test_controls_must_be_both_kinds_and_independent():
    assert validate_locally(proposal(), api_contract_guard, [NEG]).status == "blocked"
    own = Control("N2", "negative", NEG.case, "Learner")
    assert validate_locally(proposal(), api_contract_guard, [own, POS]).status == "blocked"


@pytest.mark.parametrize("over,reason", [
    ({"regression": "unknown"}, "regression verdict unknown"),
    ({"security": "fail"}, "security check failed"),
    ({"reviewer": "learner"}, "the reviewer is the proposer"),
    ({"reviewer": " "}, "no reviewer"),
    ({"owner_confirmed": False}, "owner has not confirmed"),
])
def test_gate_refuses(over, reason):
    p = proposal()
    v = validate_locally(p, api_contract_guard, [NEG, POS])
    kw = dict(policy_version="v", reviewer="owner", owner_confirmed=True, regression="pass", budget="pass",
              security="pass") | over
    d = decide_promotion(p, v, **kw)
    assert d.disposition in ("blocked", "rejected") and any(reason in r for r in d.reasons)


def test_gate_blocks_changes_to_what_judges_it():
    p = proposal(touched_paths=("tests/fixtures/legacy_oracle.py",))
    v = validate_locally(p, api_contract_guard, [NEG, POS])
    d = decide_promotion(p, v, policy_version="v", reviewer="owner", owner_confirmed=True,
                         regression="pass", budget="pass", security="pass")
    assert d.disposition == "blocked"


def test_gate_needs_this_proposals_validation():
    p, other = proposal(), proposal(proposal_id="p2")
    v = validate_locally(other, api_contract_guard, [NEG, POS])
    kw = dict(policy_version="v", reviewer="owner", owner_confirmed=True, regression="pass", budget="pass",
              security="pass")
    assert decide_promotion(p, v, **kw).disposition == "blocked"
    assert decide_promotion(p, None, **kw).disposition == "blocked"
    with pytest.raises(ValueError):
        decide_promotion(p, v, **(kw | {"budget": "ok"}))


@pytest.mark.parametrize("over", [
    {"falsifiers": ()}, {"candidate_hash": H2}, {"cost_cap": float("nan")}, {"rollback": ""},
    {"candidate_hash": "xyz"}, {"status": "production"},
])
def test_proposal_refuses_missing_or_bad_fields(over):
    with pytest.raises(ValueError):
        proposal(**over)


def test_failure_case_needs_alternatives_and_known_cause():
    args = ["c", "p", "w", "high", "verification_gap", "h", ("alt",), {"x": 1}, "scope"]
    FailureCase(*args)
    with pytest.raises(ValueError):
        FailureCase(*args[:4], "bad_luck", *args[5:])
    with pytest.raises(ValueError):
        FailureCase(*args[:6], (), *args[7:])


def test_funnel_effective_needs_counterfactual_and_order():
    f = Funnel()
    with pytest.raises(ValueError):
        f.record("reachable", "log:1")
    f.record("present", "pkg")
    f.record("reachable", "import ok")
    f.record("activated", "trace:7")
    with pytest.raises(ValueError):
        f.record("effective", "trace:7")
    f.record("effective", "trace:7", counterfactual_ref="ablation:run-3")
    assert f.counts == {"present": 1, "reachable": 1, "activated": 1, "effective": 1}


def test_learned_component_must_admit_limitations():
    kw = dict(component_id="api_guard", version="0.1", implementation_hash=H1, activation_trigger="code_change",
              defect_signature="legacy kwarg unbound", change_scope="checks only", test_provenance="N1,P1,P2",
              rollback_target="none")
    with pytest.raises(ValueError):
        LearnedComponent(**kw)
    LearnedComponent(**kw, known_limitations=("positional-only legacy calls not replayed",))


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("G6_ECHA_RESEARCH")
    with pytest.raises(ResearchDisabled):
        validate_locally(proposal(), api_contract_guard, [NEG, POS])
