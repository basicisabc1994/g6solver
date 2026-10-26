# SPDX-License-Identifier: Apache-2.0
"""One sequential confirmation (frontier design §4.2): a clear result stops early, a small real gain gets more cases,
and α is spent once over the looks."""
from __future__ import annotations

import importlib.util
import random
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from g6.metaharness import EvidencePolicy, Profile, Proposal
from g6.metaharness import schema as S
from g6.metaharness.evaluate import evaluate, sequential_boundaries
from g6.metaharness.executor import model_call_factory
from g6.metaharness.gate import decide
from g6.reasoning.compare import sign_test_p

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)  # registered first: its dataclasses look their module up while they are built

BASE = Profile(routing={"answer": "local"})
CAREFUL = Profile(routing={"answer": "local"}, prompt="CAREFUL: add the two numbers digit by digit.")
BRIEF = Profile(routing={"answer": "local"}, prompt="Be brief.")
SEQUENTIAL = EvidencePolicy(looks=4, max_evaluations=1)


def _gate(wf, base, cand):
    report = evaluate(wf, base, cand, model_call_factory(), clock=rig.Clock())
    confirmed = {"workflow_digest": wf.digest(), "judging_sha256": "j" * 64, "held_out_digest": wf.suite.digest("held_out")}
    return decide(report, Proposal(base.digest(), cand, "bo"), wf, confirmed=confirmed, judging_sha256="j" * 64,
                  base=base), report


def _upper_99(n: int, p: float) -> int:
    """The smallest k with P(Binomial(n, p) > k) <= 0.01 (pmf by recurrence: no huge binomial coefficients)."""
    pmf, acc = (1 - p) ** n, 0.0
    for k in range(n + 1):
        acc += pmf
        if acc >= 0.99:
            return k
        pmf *= (n - k) / (k + 1) * p / (1 - p)
    return n


def test_boundaries_spend_most_of_alpha_at_the_last_look():
    assert sequential_boundaries(0.05, 1) == (0.05,)
    b = sequential_boundaries(0.05, 4)
    assert len(b) == 4 and list(b) == sorted(b) and all(0 < x < 0.05 for x in b)
    assert b[0] < 0.001 and b[-1] > 0.035                 # O'Brien-Fleming: little early, most at the end
    assert sequential_boundaries(0.05, 4) == b            # seeded: the same every time


def test_the_boundaries_hold_alpha_with_the_sign_test():
    """A/A on paired outcomes, without the harness: the sequential sign-test rule fires at most at rate alpha."""
    rng, reps, n, looks, fired = random.Random(11), 4000, 80, 4, 0
    bounds = sequential_boundaries(0.05, looks)
    for _ in range(reps):
        wins = losses = 0
        for k in range(looks):
            for _ in range(n // looks):
                if rng.random() < 0.3:                        # a discordant pair, equally likely either way
                    wins, losses = (wins + 1, losses) if rng.random() < 0.5 else (wins, losses + 1)
            if wins > losses and sign_test_p(wins, losses) <= bounds[k]:
                fired += 1
                break
    assert fired <= _upper_99(reps, 0.05), fired


def test_one_look_is_todays_rule():
    wf, _ = rig.build(3, n_held=40, base_acc=0.3, careful_acc=0.97)
    decision, report = _gate(wf, BASE, CAREFUL)
    assert "look" not in report.success and "boundary" not in report.success
    assert report.n == 40 and decision.verdict == "recommend"


def test_a_clear_gain_stops_early():
    wf, _ = rig.build(5, n_held=160, base_acc=0.3, careful_acc=0.97, policy=SEQUENTIAL)
    decision, report = _gate(wf, BASE, CAREFUL)
    assert decision.verdict == "recommend", decision.reasons
    assert report.success["look"] < 4 and report.n < 160 and not report.partial


def test_futility_stops_a_candidate_that_is_not_ahead():
    wf, _ = rig.build(6, n_held=160, base_acc=0.85, careful_acc=0.4, policy=SEQUENTIAL)
    decision, report = _gate(wf, BASE, CAREFUL)
    assert decision.verdict == "refuse" and report.n < 160 and report.success["look"] >= 2


def test_sequential_aa_recommends_no_more_often_than_alpha():
    seeds, recommended = 300, 0
    for seed in range(seeds):
        wf, _ = rig.build(seed, n_held=60, policy=SEQUENTIAL)
        recommended += _gate(wf, BASE, BRIEF)[0].verdict == "recommend"
    assert recommended <= _upper_99(seeds, SEQUENTIAL.threshold), recommended


# False accepts by non-inferiority (V6b-F5: a zero-margin count rule fires on noise) ---------------------------------

def _winning_report(wf):
    _, report = _gate(wf, BASE, CAREFUL)
    return replace(report, success={"wins": 20, "losses": 0, "ties": report.n - 20, "discordant": 20,
                                    "p_one_sided": 1e-6})


def _fa(report, upper: float, base: int, candidate: int):
    estimate = {"base_mean": base / report.n, "candidate_mean": candidate / report.n,
                "diff_mean": (candidate - base) / report.n, "diff_upper_95": upper, "candidate_upper_95": 1.0}
    return replace(report, estimates={**report.estimates, "false_accepts": estimate},
                   false_accepts={"base": base, "candidate": candidate})


def test_the_report_carries_false_accepts_as_a_paired_estimate():
    wf, _ = rig.build(8)
    _, report = _gate(wf, BASE, CAREFUL)
    est = report.estimates["false_accepts"]
    assert est["base_mean"] == pytest.approx(report.false_accepts["base"] / report.n, abs=1e-6)
    assert est["candidate_mean"] == pytest.approx(report.false_accepts["candidate"] / report.n, abs=1e-6)


def test_a_small_noisy_rise_inside_the_margin_passes():
    wf, _ = rig.build(8)
    decision, _ = _gate_report(wf, _fa(_winning_report(wf), upper=0.03, base=2, candidate=3))
    assert decision.verdict == "recommend", decision.reasons


def test_a_rise_beyond_the_margin_is_refused():
    wf, _ = rig.build(8)
    decision, _ = _gate_report(wf, _fa(_winning_report(wf), upper=0.08, base=2, candidate=5))
    assert decision.code == S.OBJECTIVE_REGRESSED and "false accepts" in decision.reasons[0]


def test_a_real_gain_that_lowers_false_accepts_passes_this_rule():
    wf, _ = rig.build(9, base_acc=0.3, careful_acc=0.97)
    decision, report = _gate(wf, BASE, CAREFUL)
    assert report.estimates["false_accepts"]["diff_upper_95"] < 0 and decision.verdict == "recommend"


def _gate_report(wf, report):
    confirmed = {"workflow_digest": wf.digest(), "judging_sha256": "j" * 64, "held_out_digest": wf.suite.digest("held_out")}
    return decide(report, Proposal(BASE.digest(), CAREFUL, "bo"), wf, confirmed=confirmed, judging_sha256="j" * 64,
                  base=BASE), report


def test_sequential_finds_a_planted_gain_nearly_as_often_on_fewer_cases():
    seeds, found_seq, found_one, cases_seq = 120, 0, 0, 0
    for seed in range(seeds):
        seq, _ = rig.build(seed, n_held=80, policy=SEQUENTIAL)
        one, _ = rig.build(seed, n_held=80, policy=EvidencePolicy(max_evaluations=1))
        d_seq, r_seq = _gate(seq, BASE, CAREFUL)
        found_seq += d_seq.verdict == "recommend"
        cases_seq += r_seq.n
        found_one += _gate(one, BASE, CAREFUL)[0].verdict == "recommend"
    assert found_seq >= 0.9 * found_one and found_one > seeds // 2
    assert cases_seq < 0.8 * 80 * seeds                   # stopping early saves held-out cases
