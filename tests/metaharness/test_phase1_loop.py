# SPDX-License-Identifier: Apache-2.0
"""Meta-harness Phase 1: measure, decide, approve, roll back (META_HARNESS_PLAN.md section 6, T1-1..T1-14)."""
from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger, LedgerError
from g6.harness import Check, Memory, run
from g6.invariants import check_envelope
from g6.metaharness import (
    EvidencePolicy,
    Profile,
    Proposal,
    Suite,
    SuiteCase,
    adapt,
    decide,
    evaluate,
    model_call_factory,
    power,
)
from g6.metaharness import api as mh_api
from g6.metaharness import schema as S
from g6.metaharness.evaluate import standard_for
from g6.metaharness.store import ProfileStore, _store_rules

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)  # registered first: its dataclasses look their module up while they are built

BASE = Profile(routing={"answer": "local"})
CAREFUL = Profile(routing={"answer": "local"}, prompt="CAREFUL: add the two numbers digit by digit.")
BRIEF = Profile(routing={"answer": "local"}, prompt="Be brief.")


def _confirmed(wf) -> dict:
    return {"workflow_digest": wf.digest(), "judging_sha256": "j" * 64, "held_out_digest": wf.suite.digest("held_out")}


def _gate(wf, base, cand, *, clock=None, report=None):
    report = report or evaluate(wf, base, cand, model_call_factory(), clock=clock or rig.Clock())
    proposal = Proposal(base.digest(), cand, "bo")
    return decide(report, proposal, wf, confirmed=_confirmed(wf), judging_sha256="j" * 64, base=base), report


def _upper_99(n: int, p: float) -> int:
    """The smallest k with P(Binomial(n, p) > k) <= 0.01."""
    acc = 0.0
    for k in range(n + 1):
        acc += math.comb(n, k) * p ** k * (1 - p) ** (n - k)
        if acc >= 0.99:
            return k
    return n


def _interval_99(n: int, p: float) -> tuple[int, int]:
    acc, lo = 0.0, 0
    for k in range(n + 1):
        acc += math.comb(n, k) * p ** k * (1 - p) ** (n - k)
        if acc >= 0.005 and lo == 0:
            lo = k
        if acc >= 0.995:
            return lo, k
    return lo, n


def _harness(tmp_path, seed=7, **kw):
    wf, stub = rig.build(seed, **kw)
    return adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock()), wf, stub


def _recommended(tmp_path, seed=7):
    mh, wf, stub = _harness(tmp_path, seed)
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft"
    proposal = mh.candidate(CAREFUL, proposer="bo")["proposal"]
    report = mh.evaluate(proposal)["report"]
    decided = mh.decide(report, proposal)
    assert decided["decision"].verdict == "recommend", decided["decision"].reasons
    return mh, wf, proposal, report, decided["decision"]


# T1-1 -------------------------------------------------------------------------------------------------------------

def test_t1_1_a_held_out_case_also_in_dev_is_refused(tmp_path) -> None:
    wf, _ = rig.build()
    by_id = replace(wf, suite=Suite(wf.suite.dev, (*wf.suite.held_out, wf.suite.dev[0]), wf.suite.provenance))
    by_input = replace(wf, suite=Suite(wf.suite.dev, (*wf.suite.held_out, SuiteCase("hx", wf.suite.dev[1].input, "0")),
                                       wf.suite.provenance))
    for leaky in (by_id, by_input):
        mh = adapt(leaky, workspace=tmp_path / leaky.suite.held_out[-1].case_id)
        assert mh.envelope["warning_card"]["code"] == S.NOT_HELD_OUT
        assert mh.confirm("Ada") is mh.envelope and mh.run("What is 1+1?") is mh.envelope


# T1-2 -------------------------------------------------------------------------------------------------------------

def test_t1_2_an_expected_value_in_a_predicate_is_refused(tmp_path) -> None:
    wf, _ = rig.build()
    answer = wf.suite.held_out[0].expected
    leaky = replace(wf, checks=(rig.NUMBER, Check(f"the answer is {answer} or close", lambda o: True, check_id="c2")))
    assert adapt(leaky, workspace=tmp_path).envelope["warning_card"]["code"] == S.ANSWER_IN_PREDICATE
    assert adapt(wf, workspace=tmp_path / "ok").envelope["completion_state"] == "qualified-draft"  # survival


# T1-3 -------------------------------------------------------------------------------------------------------------

def test_t1_3_too_few_cases_or_too_few_disagreements_is_insufficient(clock) -> None:
    small, _ = rig.build(n_held=20)
    assert _gate(small, BASE, CAREFUL, clock=clock)[0].code == S.INSUFFICIENT_EVIDENCE
    agree, _ = rig.build(base_acc=1.0, careful_acc=1.0)
    decision, report = _gate(agree, BASE, CAREFUL, clock=clock)
    assert report.success["discordant"] == 0 and decision.code == S.INSUFFICIENT_EVIDENCE


@pytest.fixture
def clock():
    return rig.Clock()


# T1-4: the null-instrument control (A/A) ---------------------------------------------------------------------------

def test_t1_4_a_a_two_profiles_that_behave_the_same_are_rarely_recommended() -> None:
    seeds, recommended = 500, 0
    for seed in range(seeds):
        wf, _ = rig.build(seed, n_held=30)
        decision, _ = _gate(wf, BASE, BRIEF)
        recommended += decision.verdict == "recommend"
    assert recommended <= _upper_99(seeds, EvidencePolicy().threshold), recommended


def test_t1_4_an_identical_candidate_is_refused_outright(clock) -> None:
    wf, _ = rig.build()
    assert _gate(wf, BASE, Profile(routing={"answer": "local"}), clock=clock)[0].code == S.NO_GAIN


# T1-5: the survival control (a planted improvement) -----------------------------------------------------------------

def test_t1_5_a_planted_improvement_is_recommended_at_the_predicted_rate() -> None:
    seeds, n, base_acc, careful_acc = 200, 30, 0.5, 0.85
    d = base_acc * (1 - careful_acc) + careful_acc * (1 - base_acc)
    predicted = power(n, d, careful_acc - base_acc, EvidencePolicy().threshold, EvidencePolicy().min_discordant)
    recommended = 0
    for seed in range(seeds):
        wf, _ = rig.build(seed, n_held=n, base_acc=base_acc, careful_acc=careful_acc)
        recommended += _gate(wf, BASE, CAREFUL)[0].verdict == "recommend"
    lo, hi = _interval_99(seeds, predicted)
    assert lo <= recommended <= hi, (recommended, predicted)
    assert recommended > 0  # the instrument can say yes


# T1-6 -------------------------------------------------------------------------------------------------------------

def test_t1_6_a_suite_that_cannot_tell_the_base_from_nothing_refuses(clock) -> None:
    anything = Check("any text", lambda o: True, check_id="any")
    wf, _ = rig.build(checks=(anything,), oracle_fn=lambda out, expected: True)
    decision, report = _gate(wf, BASE, CAREFUL, clock=clock)
    assert report.negative_control["null_success"] == report.n and decision.code == S.SUITE_CANNOT_DISCRIMINATE


# T1-7, T1-8 (gate rules on recorded reports) -------------------------------------------------------------------------

def _winning_report(wf, clock):
    _, report = _gate(wf, BASE, CAREFUL, clock=clock)
    return replace(report, success={"wins": 20, "losses": 0, "ties": report.n - 20, "discordant": 20,
                                    "p_one_sided": 1e-6})


def test_t1_7_more_compute_needs_a_win_over_the_compute_matched_control(clock) -> None:
    wf, _ = rig.build()
    report = _winning_report(wf, clock)
    more = dict(report.estimates, model_calls={**report.estimates["model_calls"], "candidate_mean": 2.0,
                                               "base_mean": 1.0, "diff_upper_95": 0.0})
    tied = {"repeat": 2, "wins": 3, "losses": 3, "ties": 34, "discordant": 6, "p_one_sided": 0.66}
    decision, _ = _gate(wf, BASE, CAREFUL, report=replace(report, estimates=more, control=tied))
    assert decision.code == S.NOT_COMPUTE_MATCHED
    beaten = {"repeat": 2, "wins": 15, "losses": 0, "ties": 25, "discordant": 15, "p_one_sided": 3e-5}
    decision, _ = _gate(wf, BASE, CAREFUL, report=replace(report, estimates=more, control=beaten))
    assert decision.code != S.NOT_COMPUTE_MATCHED  # survival: a win over the control passes this rule


def test_t1_7_the_control_arm_runs_when_the_candidate_uses_more_calls(clock) -> None:
    wf, _ = rig.build()

    def twice(ctx):
        def execute(contract):
            ctx.caller("answer").complete("first look", contract.task)
            return ctx.caller("answer").complete("CAREFUL second look", contract.task)
        return execute if "TWICE" in ctx.profile.prompt else model_call_factory()(ctx)

    report = evaluate(wf, BASE, Profile(routing={"answer": "local"}, prompt="TWICE"), twice, clock=clock)
    assert report.estimates["model_calls"]["candidate_mean"] == 2.0 and report.control["repeat"] == 2


def test_t1_8_success_up_but_cost_up_beyond_its_tolerance_is_refused(clock) -> None:
    wf, _ = rig.build()
    report = _winning_report(wf, clock)
    slower = dict(report.estimates, wall_clock_s={"base_mean": 1.0, "candidate_mean": 1.2, "diff_mean": 0.2,
                                                  "diff_upper_95": 0.2, "candidate_upper_95": 1.3})
    assert _gate(wf, BASE, CAREFUL, report=replace(report, estimates=slower))[0].code == S.OBJECTIVE_REGRESSED
    # false accepts: non-inferiority with a margin (frontier design 4.2, V6b-F5), not a zero-margin count rule
    rise = dict(report.estimates, false_accepts={**report.estimates["false_accepts"], "diff_mean": 7 / report.n,
                                                 "diff_upper_95": 0.2})
    fa = replace(report, false_accepts={"base": 2, "candidate": 9}, estimates=rise)
    assert _gate(wf, BASE, CAREFUL, report=fa)[0].code == S.OBJECTIVE_REGRESSED


# T1-9 -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [{"checks": []}, {"drop_checks": ["number"]}, {"oracle": "x"},
                                 {"extra_checks": ["not-offered"]}, {"routing": {"answer": "paid-model"}}])
def test_t1_9_a_profile_cannot_express_a_change_to_what_judges_it(tmp_path, bad) -> None:
    mh, _, _ = _harness(tmp_path)
    mh.confirm("Ada")
    got = mh.candidate({"routing": {"answer": "local"}} | bad, proposer="bo")
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == S.SCHEMA


def test_t1_9_a_workflow_changed_after_confirmation_blocks(tmp_path, clock) -> None:
    mh, wf, _ = _harness(tmp_path)
    mh.confirm("Ada")
    changed = replace(wf, checks=(rig.NUMBER, Check("short", lambda o: len(o) < 9, check_id="short")))
    again = adapt(changed, workspace=tmp_path / "ws")
    assert again.baseline()["warning_card"]["code"] == S.STANDARD_CHANGED
    stale = {**_confirmed(wf), "workflow_digest": "0" * 64}
    report = evaluate(wf, BASE, CAREFUL, model_call_factory(), clock=clock)
    decision = decide(report, Proposal(BASE.digest(), CAREFUL, "bo"), wf, confirmed=stale, judging_sha256="j" * 64,
                      base=BASE)
    assert decision.verdict == "block" and decision.code == S.STANDARD_CHANGED


# T1-10 ------------------------------------------------------------------------------------------------------------

def test_t1_10_only_a_named_person_other_than_the_proposer_activates(tmp_path) -> None:
    mh, _, _, _, decision = _recommended(tmp_path)
    for who, code in (("", S.APPROVAL_REQUIRED), ("auto:ci", S.APPROVAL_REQUIRED), ("Bo", S.APPROVER_IS_PROPOSER)):
        got = mh.activate(decision, accepted_by=who)
        assert got["warning_card"]["code"] == code, who
    forged = replace(decision, candidate_digest="0" * 64)
    assert mh.activate(forged, accepted_by="Ada")["warning_card"]["code"] == S.APPROVAL_REQUIRED
    assert len(mh.store.versions()) == 1  # nothing was activated by any refusal
    done = mh.activate(decision, accepted_by="Ada")
    assert done["completion_state"] == "qualified-draft" and done["head_version"] == 1
    assert mh.run("What is 2+2?")["profile_digest"] == CAREFUL.digest()


def test_t1_10_the_store_itself_refuses_an_activation_without_a_person(tmp_path) -> None:
    store = ProfileStore(tmp_path / "s.jsonl")
    store.confirm(workflow_digest="w", judging_sha256="j", held_out_digest="h", accepted_by="Ada", at="t")
    store.append_version("initial", BASE, at="t", accepted_by="Ada")
    for who in ("", "auto:bot"):
        with pytest.raises(LedgerError):
            store.append_version("activate", CAREFUL, at="t", accepted_by=who, decision_digest="d", proposer="bo")
    with pytest.raises(LedgerError):
        store.append_version("activate", CAREFUL, at="t", accepted_by="Ada", decision_digest="")
    with pytest.raises(LedgerError):  # the acceptor is the proposer
        store.append_version("activate", CAREFUL, at="t", accepted_by="bo", decision_digest="d", proposer="Bo")
    assert len(store.versions()) == 1


# T1-11 ------------------------------------------------------------------------------------------------------------

def test_t1_11_the_stop_halts_every_stage_and_changes_nothing(tmp_path, monkeypatch) -> None:
    mh, _, proposal, report, decision = _recommended(tmp_path)
    files = {p: p.read_bytes() for p in (tmp_path / "ws").glob("*.jsonl")}
    monkeypatch.setenv("G6_STOP", "1")
    calls = [lambda: mh.candidate(CAREFUL, proposer="bo"), mh.baseline, lambda: mh.evaluate(proposal),
             lambda: mh.decide(report, proposal), lambda: mh.activate(decision, accepted_by="Ada"),
             lambda: mh.run("What is 1+1?"), lambda: mh.confirm("Ada")]
    for call in calls:
        got = call()
        assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == "G6_E_STOPPED"
    assert {p: p.read_bytes() for p in files} == files
    fresh, _, _ = _harness(tmp_path / "fresh")
    assert fresh.confirm("Ada")["warning_card"]["code"] == "G6_E_STOPPED"


# T1-12 ------------------------------------------------------------------------------------------------------------

def test_t1_12_rollback_appends_and_only_to_an_approved_version(tmp_path, monkeypatch) -> None:
    mh, _, _, _, decision = _recommended(tmp_path)
    mh.activate(decision, accepted_by="Ada")                                  # v1: careful
    assert mh.rollback(0, accepted_by="Ada")["head_version"] == 2            # v2 -> v0
    assert mh.run("What is 2+2?")["profile_digest"] == BASE.digest()
    monkeypatch.setenv("G6_STOP", "1")
    assert mh.rollback(1, accepted_by="Ada")["head_version"] == 3            # a rollback of the rollback, while stopped
    monkeypatch.delenv("G6_STOP")
    assert mh.rollback(2, accepted_by="Ada")["warning_card"]["code"] == S.APPROVAL_REQUIRED   # v2 was never approved
    assert mh.rollback(0, accepted_by="auto:x")["warning_card"]["code"] == S.APPROVAL_REQUIRED
    kinds = [v["kind"] for v in mh.store.versions()]
    assert kinds == ["initial", "activate", "rollback", "rollback"] and mh.store.verify() == ()


def test_t1_12_the_store_refuses_a_demotion_that_skips_back(tmp_path) -> None:
    seen = {("workflow", "w"): {}, ("profile", "p"): {},
            ("version", "0"): {"version": 0, "kind": "initial", "profile_digest": "p"},
            ("version", "1"): {"version": 1, "kind": "activate", "profile_digest": "p"},
            ("version", "2"): {"version": 2, "kind": "activate", "profile_digest": "p"}}
    demote = {"version": 3, "kind": "demote", "profile_digest": "p", "accepted_by": "monitor"}
    assert _store_rules("version", "3", demote | {"target_version": 1}, seen) == []
    assert any("only to the version approved before" in p
               for p in _store_rules("version", "3", demote | {"target_version": 0}, seen))


# T1-13 ------------------------------------------------------------------------------------------------------------

def test_t1_13_every_return_is_a_valid_envelope_and_none_is_verified(tmp_path) -> None:
    mh, wf, proposal, report, decision = _recommended(tmp_path)
    envelopes = [mh.envelope, mh.inspect(), mh.report(), mh.candidate({"x": 1}, proposer="bo"),
                 mh.decide(report, proposal), mh.activate(decision, accepted_by="Bo"),
                 mh.activate(decision, accepted_by="Ada"), mh.rollback(0, accepted_by="Ada"), mh.baseline()]
    live = mh.run("What is 3+4?")
    for env in envelopes:
        assert check_envelope(env) == [], env.get("warning_card")
        assert env["completion_state"] != "verified" and env.get("reliability_label") != "verified"
    plain = run(standard_for(wf, BASE, "What is 3+4?"), lambda c: live["output"], memory=Memory(":memory:"))
    assert check_envelope(live) == [] and live["completion_state"] == plain["completion_state"]


# T1-14 ------------------------------------------------------------------------------------------------------------

def test_t1_14_judging_code_changed_after_confirmation_blocks(tmp_path, monkeypatch) -> None:
    mh, wf, _ = _harness(tmp_path)
    mh.confirm("Ada")
    proposal = mh.candidate(CAREFUL, proposer="bo")["proposal"]
    report = mh.evaluate(proposal)["report"]
    monkeypatch.setattr(mh_api, "judging_sha256", lambda: "f" * 64)
    got = mh.decide(report, proposal)
    assert got["warning_card"]["code"] == S.PROTECTED and got["decision"].verdict == "block"


# held-out reuse and the evaluation budget ---------------------------------------------------------------------------

def test_the_held_out_split_has_an_evaluation_limit(tmp_path) -> None:
    wf, _ = rig.build(policy=EvidencePolicy(max_evaluations=2))
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    mh.baseline()                                                   # 1
    proposal = mh.candidate(CAREFUL, proposer="bo")["proposal"]
    assert mh.evaluate(proposal)["completion_state"] == "qualified-draft"   # 2
    assert mh.evaluate(proposal)["warning_card"]["code"] == S.HOLDOUT_REUSED


def test_a_partial_evaluation_is_never_used_for_a_decision(tmp_path) -> None:
    mh, _, _ = _harness(tmp_path)
    mh.confirm("Ada")
    proposal = mh.candidate(CAREFUL, proposer="bo")["proposal"]
    got = mh.evaluate(proposal, budget=6)
    assert got["completion_state"] == "blocked-escalated" and got["report"].partial
    assert mh.decide(got["report"], proposal)["warning_card"]["code"] == S.BUDGET_EXCEEDED


def test_the_report_tags_its_claims(tmp_path) -> None:
    mh, _, _, _, decision = _recommended(tmp_path)
    mh.activate(decision, accepted_by="Ada")
    mh.run("What is 5+5?")
    rep = mh.report()
    assert all(c["holds"] for c in rep["checked"]) and rep["measured"] and rep["assumed"]


def test_the_store_and_ledger_are_hash_chained_files(tmp_path) -> None:
    mh, _, _, _, decision = _recommended(tmp_path)
    mh.activate(decision, accepted_by="Ada")
    for name in ("store.jsonl", "ledger.jsonl"):
        assert ChainLedger.audit(tmp_path / "ws" / name) == ()


def test_a_relative_workspace_keeps_the_store_the_ledger_and_the_harness_memory_in_one_folder(tmp_path,
                                                                                               monkeypatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    wf, _ = rig.build(7)
    mh = adapt(wf, workspace="relative-ws", clock=rig.Clock())
    assert mh.workspace == (tmp_path / "relative-ws").resolve() and mh.workspace.is_absolute()
    mh.confirm("Ada")
    mh.run("What is 2+3?")
    assert {"store.jsonl", "ledger.jsonl", "harness.db"} <= {p.name for p in (tmp_path / "relative-ws").iterdir()}
    assert not (tmp_path / "home" / "relative-ws").exists()


def test_a_risky_task_runs_only_once_a_person_accepts_it(tmp_path) -> None:   # V6-F2
    from g6.metaharness.evaluate import run_case
    from g6.metaharness.executor import context_for
    from g6.metaharness.propose import dev_run

    wf, _ = rig.build()
    risky = replace(wf, task="Check the card payment fee. {input} Reply with the number only.")
    accepted = replace(risky, accept_risky="Ada")
    case = wf.suite.dev[0]

    def states(w):
        ctx = context_for(w, BASE, theories=None, clock=rig.Clock())
        record = run_case(w, model_call_factory()(ctx), BASE, case, "B", calls=lambda: ctx.calls)
        _, dev, _ = dev_run(w, model_call_factory(), BASE, case, theories=None, clock=rig.Clock())
        return record.completion_state, dev["completion_state"]

    assert states(risky) == ("blocked-escalated", "blocked-escalated")    # intake asks a person, for every profile
    assert states(accepted) == ("qualified-draft", "qualified-draft")
    assert risky.digest() != accepted.digest()                             # accepting it changes the standard
    assert [c for c, _ in replace(risky, accept_risky="auto:x").problems()] == [S.WORKFLOW_INVALID]
    mh = adapt(accepted, workspace=tmp_path / "ws", clock=rig.Clock())
    assert mh.confirm("Ada")["accept_risky"] == "Ada"
    assert mh.run(case.input)["completion_state"] == "qualified-draft"
