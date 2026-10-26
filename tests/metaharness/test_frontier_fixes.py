# SPDX-License-Identifier: Apache-2.0
"""The final review's findings on the frontier library (part A), each pinned by a test that failed first."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

from g6.metaharness import EvidencePolicy, Profile, Proposal, Suite, adapt
from g6.metaharness import api as mh_api
from g6.metaharness import schema as S
from g6.metaharness.evaluate import evaluate
from g6.metaharness.executor import model_call_factory
from g6.metaharness.gate import decide
from g6.metaharness.method import Method, TuningRun, strategies_for, tune
from g6.metaharness.method_rca import compare_methods, diagnose_method
from g6.metaharness.pool import EditSpace
from g6.metaharness.propose import WORKING_PROMPT
from g6.metaharness.reflect import ReflectiveStrategy
from g6.metaharness.select import race

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)  # registered first: its dataclasses look their module up while they are built

BASE = Profile(routing={"answer": "local"})
CAREFUL = BASE.model_copy(update={"prompt": "CAREFUL: add the numbers digit by digit."})
FAST = Method(cycles=2, propose_budget=60, select_budget=600, race_block=10, reflect_calls=2)


class Reflector:
    def __init__(self, *replies) -> None:
        self.replies = [json.dumps(r) if isinstance(r, dict) else r for r in replies]

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        return self.replies.pop(0) if self.replies else "{}"


def _selection(wf, n=40):
    return dataclasses.replace(wf, suite=Suite(dev=wf.suite.dev, held_out=wf.suite.held_out,
                                               selection=rig.cases("s", n, 900), provenance=dict(wf.suite.provenance)))


def _mh(tmp_path, wf, *, confirm=True):
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    if confirm:
        mh.confirm("Dana")
    return mh


# C1: repeated ledger rows -------------------------------------------------------------------------------------------

def test_racing_the_same_candidates_twice_is_recorded_twice(tmp_path):
    wf, _ = rig.build(stub=rig.Fixed("0", careful="1"))          # fixed replies: the same race gives the same row
    mh = _mh(tmp_path, _selection(wf))
    proposal = mh.candidate(CAREFUL, proposer="test")["proposal"]
    mh.select([proposal])
    mh.select([proposal])
    assert len(mh.ledger.rows("selection")) == 2


def test_two_identical_tuning_runs_are_both_recorded(tmp_path):
    wf, _ = rig.build(stub=rig.Fixed("0"))
    mh = _mh(tmp_path, _selection(wf))
    for _ in range(3):                                            # runs 2 and 3 propose nothing new: identical rows
        tune(mh, FAST, reflector=Reflector(), space=EditSpace({"retries": [0]}), approve=lambda r: None)
    assert len(mh.ledger.rows("tuning_run")) == 3


# I1: a half-raced winner -------------------------------------------------------------------------------------------

def test_a_race_cut_short_with_several_candidates_live_names_no_winner():
    wf, _ = rig.build(6)
    candidates = [CAREFUL, *(BASE.model_copy(update={"prompt": f"Note {i}."}) for i in range(3))]
    result = race(_selection(wf), model_call_factory(), BASE, candidates, block=10, budget=60, clock=rig.Clock())
    assert result.stopped and result.winner is None


# I2: tune refuses up front, and says why ---------------------------------------------------------------------------

def test_tune_without_a_selection_split_is_blocked_before_spending(tmp_path):
    wf, _ = rig.build()
    mh = _mh(tmp_path, wf)
    run = tune(mh, FAST, reflector=Reflector({"prompt": "CAREFUL"}), space=EditSpace({"retries": [0, 1]}),
               approve=lambda r: "Dana")
    assert run.outcome == "blocked" and any(S.NO_SELECTION_SPLIT in note for note in run.notes)
    assert mh.ledger.rows("proposal") == []


def test_tune_on_an_unconfirmed_workflow_is_blocked_not_raised(tmp_path):
    wf, _ = rig.build()
    mh = _mh(tmp_path, _selection(wf), confirm=False)
    run = tune(mh, FAST, approve=lambda r: None)
    assert run.outcome == "blocked" and any(S.NOT_CONFIRMED in note for note in run.notes)


def test_a_select_cut_short_says_what_it_spent(tmp_path):
    wf, _ = rig.build(6)
    mh = _mh(tmp_path, _selection(wf))
    proposals = [mh.candidate(BASE.model_copy(update={"prompt": f"Note {i}."}), proposer="t")["proposal"]
                 for i in range(4)]
    reply = mh.select(proposals, budget=60)
    assert reply["completion_state"] == "blocked-escalated" and reply["stopped"] and reply["calls_used"] >= 60


# I3: a baseline measures the whole split ---------------------------------------------------------------------------

def test_a_baseline_with_several_looks_measures_every_case(tmp_path):
    wf, _ = rig.build(3, n_held=80, policy=EvidencePolicy(looks=4, max_evaluations=2))
    mh = _mh(tmp_path, wf)
    out = mh.baseline()["baseline"]
    assert out["held_out"]["n"] == 80 and out["dev"]["n"] == len(wf.suite.dev)


# I4: no truncated results in the pool ------------------------------------------------------------------------------

def test_a_result_cut_short_by_the_dev_budget_never_enters_the_pool(tmp_path):
    wf, _ = rig.build(base_acc=0.3, careful_acc=0.95)
    mh = _mh(tmp_path, _selection(wf))
    strategy = ReflectiveStrategy(Reflector({"prompt": CAREFUL.prompt}), EditSpace({"retries": [0, 1]}), calls=1)
    mh.propose(strategy, budget=12)                               # the view takes 10 of 12 dev calls
    assert not strategy.pool.seen(CAREFUL)
    assert len(strategy.pool.members) == 1 and len(strategy.pool.results(BASE)) == len(wf.suite.dev)


# I5: reflected values stay inside the declared space ---------------------------------------------------------------

def test_reflected_values_outside_the_space_are_dropped_and_never_raise(tmp_path):
    wf, _ = rig.build(base_acc=0.1, careful_acc=0.85)            # the base keeps failing cases, so it is reflected on
    mh = _mh(tmp_path, _selection(wf))
    space = EditSpace({"retries": [0, 1], "routing": [{"answer": "local"}], "extra_checks": [[]]})
    strategy = ReflectiveStrategy(Reflector({"prompt": CAREFUL.prompt}, {"routing": {"answer": "gpt-x"}},
                                            {"retries": 3}, {"extra_checks": ["nope"]}), space, calls=4, merge_every=0)
    reply = mh.propose(strategy)
    assert reply["completion_state"] == "qualified-draft"
    assert [p.candidate.prompt for p in reply["proposals"]] == [CAREFUL.prompt]
    assert not any(p.retries == 3 for p, _ in strategy.pool.members)


# I6: false accepts that fell or stayed the same never refuse -------------------------------------------------------

def _fa_report(wf, base: int, candidate: int, upper: float, mean: float):
    report = evaluate(wf, BASE, CAREFUL, model_call_factory(), clock=rig.Clock())
    estimate = {"base_mean": base / report.n, "candidate_mean": candidate / report.n, "diff_mean": mean,
                "diff_upper_95": upper, "candidate_upper_95": 1.0}
    return replace(report, success={"wins": 20, "losses": 0, "ties": report.n - 20, "discordant": 20,
                                    "p_one_sided": 1e-6},
                   estimates={**report.estimates, "false_accepts": estimate},
                   false_accepts={"base": base, "candidate": candidate})


def _decide(wf, report):
    confirmed = {"workflow_digest": wf.digest(), "judging_sha256": "j" * 64, "held_out_digest": wf.suite.digest("held_out")}
    return decide(report, Proposal(BASE.digest(), CAREFUL, "bo"), wf, confirmed=confirmed, judging_sha256="j" * 64,
                  base=BASE)


def test_equal_or_fewer_false_accepts_never_refuse():
    wf, _ = rig.build(8, n_held=70)
    assert _decide(wf, _fa_report(wf, 5, 5, upper=0.075, mean=0.0)).verdict == "recommend"
    assert _decide(wf, _fa_report(wf, 6, 5, upper=0.064, mean=-1 / 70)).verdict == "recommend"
    rise = _decide(wf, _fa_report(wf, 5, 9, upper=0.12, mean=4 / 70))
    assert rise.code == S.OBJECTIVE_REGRESSED


# I7: method comparisons resample clusters --------------------------------------------------------------------------

def test_a_gain_carried_by_one_workflow_is_not_accepted_when_runs_cluster():
    gains = {"w1": 0.10, "w2": 0.01, "w3": -0.02, "w4": 0.005}
    pairs, clusters = [], []
    for name, gain in gains.items():
        for i in range(6):
            pairs.append((0.5, 0.5 + gain + i * 1e-4))
            clusters.append(name)
    assert compare_methods(pairs)["accept"]                        # pairs treated as independent: too generous
    assert not compare_methods(pairs, clusters=clusters)["accept"]


# I8: recovery's remedies seed the pool -----------------------------------------------------------------------------

def test_recovery_remedies_enter_the_shared_pool(tmp_path):
    wf, _ = rig.build(base_acc=0.3)
    wf = _selection(wf)
    mh = _mh(tmp_path, wf)
    strategies, _ = strategies_for(Method(use_reflection=False), wf)
    mh.propose(strategies[0], budget=200)
    pool = strategies[0].pool
    assert pool.seen(BASE.model_copy(update={"prompt": WORKING_PROMPT}))


# re-graded: the method diagnosis reads the gate's own level -------------------------------------------------------

def test_underpowered_reads_the_level_the_gate_used():
    run = TuningRun("m", "w", "b", champion="a", outcome="refused",
                    decision={"verdict": "refuse", "code": "G6_E_META_NO_GAIN", "reasons": []},
                    confirmation={"success": {"wins": 14, "losses": 6, "p_one_sided": 0.05}, "level": 0.01, "n": 60})
    assert "underpowered" not in [c.code for c in diagnose_method([run]).causes]   # 0.05 is not within 3x of 0.01


# re-graded: a person can re-confirm after G6's judging code changes -----------------------------------------------

def test_confirming_again_after_an_upgrade_re_pins_the_judging_code(tmp_path, monkeypatch):
    wf, _ = rig.build(3, base_acc=0.3, careful_acc=0.97)
    mh = _mh(tmp_path, wf)
    monkeypatch.setattr(mh_api, "judging_sha256", lambda: "upgraded" * 8)
    proposal = mh.candidate(CAREFUL, proposer="bo")["proposal"]
    report = mh.evaluate(proposal)["report"]
    assert mh.decide(report, proposal)["warning_card"]["code"] == S.PROTECTED
    mh.confirm("Dana")
    assert mh.decide(report, proposal)["decision"].verdict == "recommend"
