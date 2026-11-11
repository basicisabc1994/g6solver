# SPDX-License-Identifier: Apache-2.0
"""Admission of heuristic trials (0.3 design 14.2): only a receipt the paired evaluator wrote to its ledger credits a
strategy; a digest or an outcome a caller asserts supplies nothing, and admission hashes the strategy's bytes again."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from g6.cognitive_bridge import EvaluatedTrial, MetacognitiveMethodSelector, ProblemSignature
from g6.cognitive_bridge.strategy import PAIR_KIND, admit_trial, heuristic_variant, trial_from
from g6.core.ledger import ChainLedger
from g6.learning import paired_eval
from g6.learning.paired_eval import PairedEvaluator, receipts
from g6.learning.schema import AgentReply, AgentTrace, TaskInstance
from g6.learning.strategy import StrategyArtefact, StrategyStore

X = StrategyArtefact(instruction="Check the answer against every stated constraint.", trigger="always", scope="search")
OTHER = dataclasses.replace(X, instruction="Try the smallest case first.")
CONTEXT = ProblemSignature("arithmetic", "exact", "answer", 1, 10)
CASES = [TaskInstance(input=f"What is {n} + {n}?", expected_output=str(2 * n), instance_id=f"case-{n}")
         for n in range(1, 5)]


def helped(prompt: str) -> AgentReply:
    n = int(prompt.rsplit("What is ", 1)[1].split(" ")[0])
    return AgentReply(str(2 * n) if "Optional hints" in prompt else "?", AgentTrace(cost_usd=0.5))


def measured(tmp_path: Path) -> tuple[Any, StrategyStore, ChainLedger, tuple]:
    store = StrategyStore(tmp_path / "strategies")
    store.put(X)
    store.put(OTHER)
    ledger = ChainLedger(tmp_path / "receipts.jsonl")
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=helped, agent_id="stand-in/1", cohort_id="cohort-1",
                                split="heldout", seed=3, budget={"max_calls": 50, "max_seconds": 60, "max_usd": 5},
                                environment_digest="e" * 64, evaluator_ref="test://paired-evaluator")
    found = evaluator.run(CASES, candidate=X.content_digest, replay_id="r1")
    return heuristic_variant(X, id="constraints", estimated_cost=0.5), store, ledger, found


def selector(trials: list[EvaluatedTrial], hook: Any) -> MetacognitiveMethodSelector:
    return MetacognitiveMethodSelector(trials, admit_trial=hook)


def test_the_kind_is_the_evaluators() -> None:
    assert PAIR_KIND == paired_eval.KIND


def test_a_receipts_trial_is_admitted(tmp_path: Path) -> None:
    variant, store, ledger, found = measured(tmp_path)
    trials = [trial_from(receipt, variant, CONTEXT) for receipt in found]
    made = selector(trials, admit_trial(ledger, store, [variant]))
    assert (made.rejected_trials, len(made.trials)) == (0, len(CASES))
    first = trials[0]
    assert (first.trial_id, first.evaluator_ref, first.case_id, first.resolved, first.actual_cost, first.split) == \
        (found[0].pair_id, found[0].pair_id, found[0].case_id, True, 0.5, "heldout")
    assert first.variant_digest == variant.definition_digest and first.oracle_digest == found[0].oracle_digest


def test_a_caller_asserted_trial_is_not_admitted(tmp_path: Path) -> None:
    variant, store, ledger, found = measured(tmp_path)
    asserted = EvaluatedTrial("t-asserted", "case-9", "cohort-1", "arithmetic", "exact", "answer", "constraints", True,
                              0.5, "pair-that-was-never-run", found[0].oracle_digest, "heldout",
                              variant_digest=variant.definition_digest)
    made = selector([asserted], admit_trial(ledger, store, [variant]))
    assert made.rejected_trials == 1 and made.trials == ()
    # and with no hook at all, nothing is admitted either
    assert selector([trial_from(found[0], variant, CONTEXT)], None).rejected_trials == 1


@pytest.mark.parametrize("change", [{"resolved": False}, {"actual_cost": 0.01}, {"case_id": "case-2"},
                                    {"cohort_id": "another-cohort"}, {"split": "shadow"}, {"oracle_digest": "b" * 64},
                                    {"critical_regression": True}, {"trial_id": "renamed"}])
def test_a_trial_that_disagrees_with_its_receipt_is_not_admitted(tmp_path: Path, change: dict) -> None:
    variant, store, ledger, found = measured(tmp_path)
    honest = trial_from(found[0], variant, CONTEXT)
    hook = admit_trial(ledger, store, [variant])
    assert hook(honest) is True
    assert hook(dataclasses.replace(honest, **change)) is False
    assert selector([dataclasses.replace(honest, **change)], hook).rejected_trials == 1


def test_a_trial_for_another_strategy_or_another_name_is_not_admitted(tmp_path: Path) -> None:
    variant, store, ledger, found = measured(tmp_path)
    other = heuristic_variant(OTHER, id="constraints", estimated_cost=0.5)       # the same name, other bytes
    hook = admit_trial(ledger, store, [variant, other])
    honest = trial_from(found[0], variant, CONTEXT)
    assert hook(dataclasses.replace(honest, variant_digest=other.definition_digest)) is False
    assert hook(dataclasses.replace(honest, variant_id="renamed")) is False
    assert admit_trial(ledger, store, [other])(honest) is False
    with pytest.raises(ValueError, match="another strategy"):
        trial_from(found[0], other, CONTEXT)


def test_admission_rehashes_the_strategy_bytes(tmp_path: Path) -> None:
    variant, store, ledger, found = measured(tmp_path)
    honest = trial_from(found[0], variant, CONTEXT)
    hook = admit_trial(ledger, store, [variant])
    assert hook(honest) is True
    path = tmp_path / "strategies" / f"{X.content_digest}.json"
    path.write_bytes(path.read_bytes().replace(b"every stated", b"no stated"))
    assert hook(honest) is False
    assert selector([honest], hook).rejected_trials == 1


def test_an_edited_ledger_admits_nothing(tmp_path: Path) -> None:
    variant, store, ledger, found = measured(tmp_path)
    honest = trial_from(found[0], variant, CONTEXT)
    hook = admit_trial(ledger, store, [variant])
    assert hook(honest) is True
    path = tmp_path / "receipts.jsonl"
    path.write_bytes(path.read_bytes().replace(b"false", b"true", 1))
    assert hook(honest) is False


def test_an_incomplete_pair_is_no_trial(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies")
    store.put(X)
    ledger = ChainLedger(tmp_path / "receipts.jsonl")

    def fails(prompt: str) -> str:
        raise RuntimeError("interrupted")

    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=fails, agent_id="a", cohort_id="c", split="heldout",
                                seed=1, budget={"max_calls": 9, "max_seconds": 9, "max_usd": 9},
                                environment_digest="e" * 64, evaluator_ref="test://e")
    receipt = evaluator.run(CASES[:1], candidate=X.content_digest, replay_id="r1")[0]
    variant = heuristic_variant(X, id="constraints", estimated_cost=0.5)
    with pytest.raises(ValueError, match="no trial"):
        trial_from(receipt, variant, CONTEXT)
    forged = EvaluatedTrial(receipt.pair_id, receipt.case_id, "c", "arithmetic", "exact", "answer", "constraints",
                            True, 0.0, receipt.pair_id, receipt.oracle_digest, "heldout",
                            variant_digest=variant.definition_digest)
    assert admit_trial(ledger, store, [variant])(forged) is False and len(receipts(ledger)) == 1
