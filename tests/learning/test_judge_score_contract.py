# SPDX-License-Identifier: Apache-2.0
"""An unusable judge verdict never becomes a passing answer or paired credit; no model calls."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from g6.cognitive_bridge import EvaluatedTrial, MethodVariant, ProblemSignature
from g6.core.ledger import ChainLedger, canonical
from g6.learning.method_kind import admit_trial, trial_fields, variant_fields
from g6.learning.paired_eval import KIND, Evidence, PairedEvaluator, credited
from g6.learning.schema import TaskInstance
from g6.learning.scoring import score_instance
from g6.learning.strategy import StrategyArtefact, StrategyStore


INVALID = ['NaN', '"NaN"', 'Infinity', '"Infinity"', '-Infinity', '"-Infinity"', '1e400',
           '"1e400"', '-0.01', '1.01', 'null', 'true', 'false', '[]', '{}', '"bad"']
CASE = TaskInstance("Return right.", "right", "case-1", metadata={"rubric": "Only right passes."})


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("mode", ["rubric", "llm_judge", "rubric_judge"])
@pytest.mark.parametrize("threshold", [0.0, 0.8])
def test_invalid_judge_verdict_never_passes(value, mode, threshold):
    case = replace(CASE, metadata={"score_mode": mode, "score_threshold": threshold})
    score, passed, signals = score_instance(case, "wrong", judge=lambda _: '{"score":' + value + '}')
    assert (score, passed) == (0.0, False)
    assert signals["judge_error"].startswith("judge_failed:")


@pytest.mark.parametrize("reply", ['{}', '[]', 'null', 'not JSON'])
def test_missing_judge_verdict_is_no_pass_even_at_zero_threshold(reply):
    case = replace(CASE, metadata={"rubric": "Only right passes.", "score_threshold": 0.0})
    score, passed, signals = score_instance(case, "wrong", judge=lambda _: reply)
    assert (score, passed) == (0.0, False)
    assert signals["judge_error"]


@pytest.mark.parametrize("value, expected, passed", [('0', 0.0, False), ('0.79', 0.79, False),
                                                   ('0.8', 0.8, True), ('1', 1.0, True), ('"0.9"', 0.9, True)])
def test_finite_in_range_verdict_preserves_threshold(value, expected, passed):
    score, success, signals = score_instance(CASE, "answer", judge=lambda _: '{"score":' + value + '}')
    assert (score, success, signals["judge_error"]) == (expected, passed, "")


def _rig(root: Path, judge):
    strategy = StrategyArtefact(instruction="Check every constraint.", trigger="always", scope="search")
    store = StrategyStore(root / "strategies")
    store.put(strategy)
    ledger = ChainLedger(root / "pairs.jsonl")
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=lambda _: "wrong", judge=judge,
                                agent_id="stand-in/1", cohort_id="score-contract", split="heldout", seed=3,
                                budget={"max_calls": 2, "max_seconds": 60, "max_usd": 1},
                                environment_digest="e" * 64, evaluator_ref="test://score-contract")
    return evaluator, strategy, store, ledger


@pytest.mark.parametrize("value", INVALID)
def test_invalid_verdict_leaves_live_pair_unmeasured(tmp_path, value):
    evaluator, strategy, store, ledger = _rig(tmp_path, lambda _: '{"score":' + value + '}')
    receipt, = evaluator.run([CASE], candidate=strategy.content_digest, replay_id="run-1")
    assert receipt.candidate.success is None and receipt.baseline.success is None
    assert not receipt.complete
    assert credited(ledger, store) == ()


@pytest.mark.parametrize("value", INVALID)
def test_rederivation_and_admission_refuse_old_success_with_invalid_verdict(tmp_path, value):
    evaluator, strategy, store, ledger = _rig(tmp_path, lambda _: '{"score":1}')
    receipt, = evaluator.run([CASE], candidate=strategy.content_digest, replay_id="run-1")
    variant = MethodVariant(**variant_fields(strategy, id="strategy", estimated_cost=0.5))
    context = ProblemSignature("arithmetic", "exact", "answer", 1, 10)
    trial = EvaluatedTrial(**trial_fields(receipt, variant, context))
    assert credited(ledger, store) == (receipt,)
    assert admit_trial(ledger, store, [variant])(trial) is True
    evidence = Evidence.beside(ledger)
    kept = json.loads(evidence.get(receipt.candidate.evidence))
    kept["judge"] = [[asked, '{"score":' + value + '}'] for asked, _ in kept["judge"]]
    changed = replace(receipt, candidate=replace(receipt.candidate,
                                               evidence=evidence.put(canonical(kept).encode("utf-8"))))
    old = ChainLedger(tmp_path / "old-success.jsonl")
    old.append(KIND, changed.pair_id, changed.to_body())
    assert credited(old, store, evidence=evidence) == ()
    assert admit_trial(old, store, [variant], evidence=evidence)(trial) is False
