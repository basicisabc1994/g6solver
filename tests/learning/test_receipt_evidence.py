# SPDX-License-Identifier: Apache-2.0
"""Credit is read again from what the evaluator kept (0.3 design 14.2, "admit paired credit").

A row in the receipt ledger is a claim. ``ChainLedger.append`` is public and takes no key, so a well-formed row can be
written by anyone who can reach the ledger, and a whole chain can be written again. What gives credit is therefore not
the row: it is the task, the oracle, each arm's reply and each judge verdict, kept by the evaluator beside its ledger
under their SHA-256, from which the pair is built and scored again. A row whose kept bytes are missing, changed, another
arm's, or score to something else gives no credit. Offline: stand-in agents, no model."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from g6.cognitive_bridge import EvaluatedTrial, MethodVariant, ProblemSignature
from g6.core.ledger import ChainLedger, canonical
from g6.learning.method_kind import admit_trial, trial_fields, variant_fields
from g6.learning.paired_eval import KIND, PairedEvaluator, PairReceipt, apply, receipts
from g6.learning.schema import AgentReply, AgentTrace, TaskInstance
from g6.learning.strategy import NO_STRATEGY, StrategyArtefact, StrategyStore

X = StrategyArtefact(instruction="Check the answer against every stated constraint.", trigger="always", scope="search")
Y = dataclasses.replace(X, instruction="Try the smallest case first.")
CONTEXT = ProblemSignature("arithmetic", "exact", "answer", 1, 10)
CASES = [TaskInstance(input=f"What is {n} + {n}?", expected_output=str(2 * n), instance_id=f"case-{n}",
                      metadata={"topic": "sums"}) for n in range(1, 5)]
BUDGET = {"max_calls": 50, "max_seconds": 60, "max_usd": 5}


def variant_of(artefact: StrategyArtefact, name: str) -> MethodVariant:
    return MethodVariant(**variant_fields(artefact, id=name, estimated_cost=0.5))


def trial(receipt: PairReceipt, variant: MethodVariant) -> EvaluatedTrial:
    return EvaluatedTrial(**trial_fields(receipt, variant, CONTEXT))


def helped(prompt: str) -> AgentReply:
    """Right with the hint, wrong without."""
    n = int(prompt.rsplit("What is ", 1)[1].split(" ")[0])
    return AgentReply(str(2 * n) if "Optional hints" in prompt else "?", AgentTrace(cost_usd=0.5))


def never(prompt: str) -> AgentReply:
    return AgentReply("?", AgentTrace(cost_usd=0.5))


def measured(tmp_path: Path, agent: Any = helped, cases: Any = CASES, **changed: Any
             ) -> tuple[StrategyStore, ChainLedger, tuple[PairReceipt, ...]]:
    store = StrategyStore(tmp_path / "strategies")
    store.put(X)
    store.put(Y)
    ledger = ChainLedger(tmp_path / "receipts.jsonl")
    given = dict(store=store, ledger=ledger, agent=agent, agent_id="stand-in/1", cohort_id="cohort-1", split="heldout",
                 seed=3, budget=BUDGET, environment_digest="e" * 64, evaluator_ref="test://paired-evaluator")
    given.update(changed)
    return store, ledger, PairedEvaluator(**given).run(cases, candidate=X.content_digest, replay_id="r1")


def kept_beside(ledger: ChainLedger) -> Path:
    return Path(str(ledger.path) + ".evidence")


def pair_id(receipt: PairReceipt, attempt: int = 0) -> str:
    """The id the evaluator gives a pair: of its cohort, replay, case, the two strategies and the attempt."""
    named = [receipt.cohort_id, receipt.replay_id, receipt.case_id, receipt.candidate.strategy_digest,
             receipt.baseline.strategy_digest, attempt]
    return hashlib.sha256(canonical(named).encode("utf-8")).hexdigest()[:32]


# --- the finding: each of these gave credit on S5 as first built -------------------------------------------------

def test_a_receipt_appended_by_hand_is_not_admitted(tmp_path: Path) -> None:
    """The public, unkeyed door: a well-formed pair for a strategy that was never run, appended to the real ledger."""
    store, ledger, found = measured(tmp_path)
    mine, other = variant_of(X, "constraints"), variant_of(Y, "smallest")
    arm = dataclasses.replace(found[0].candidate, strategy_digest=Y.content_digest,
                              prompt_digest=apply(Y, CASES[0].input, {"topic": "sums"}).prompt_digest)
    by_hand = dataclasses.replace(found[0], candidate=arm)
    by_hand = dataclasses.replace(by_hand, pair_id=pair_id(by_hand))
    ledger.append(KIND, by_hand.pair_id, by_hand.to_body())
    assert ledger.verify() == () and by_hand in receipts(ledger)           # the chain checks out; the row is there
    hook = admit_trial(ledger, store, [mine, other])
    assert hook(trial(found[0], mine)) is True                             # control: the pair that was measured
    assert hook(trial(by_hand, other)) is False, (
        "a pair receipt nobody measured was appended through ChainLedger.append, and its trial is admitted as credit")


def test_a_measured_receipt_copied_into_another_replay_is_not_admitted(tmp_path: Path) -> None:
    store, ledger, found = measured(tmp_path)
    mine = variant_of(X, "constraints")
    copy = dataclasses.replace(found[0], replay_id="r2")
    copy = dataclasses.replace(copy, pair_id=pair_id(copy))
    ledger.append(KIND, copy.pair_id, copy.to_body())
    hook = admit_trial(ledger, store, [mine])
    assert hook(trial(found[0], mine)) is True
    assert hook(trial(copy, mine)) is False, "one measured pair, written a second time, is credited twice"


def test_a_ledger_written_again_with_a_flipped_outcome_admits_nothing(tmp_path: Path) -> None:
    """The whole chain rewritten, so it checks out: the candidate arm's failure now reads as a success."""
    store, ledger, found = measured(tmp_path, agent=never)
    assert [r.candidate.success for r in found] == [False] * len(CASES)
    again = ChainLedger(tmp_path / "again.jsonl")
    flipped = [dataclasses.replace(r, candidate=dataclasses.replace(r.candidate, success=True)) for r in found]
    for receipt in flipped:
        again.append(KIND, receipt.pair_id, receipt.to_body())
    ledger.path.write_bytes(again.path.read_bytes())
    rewritten = ChainLedger(ledger.path)
    assert rewritten.verify() == () and receipts(rewritten) == tuple(flipped)
    mine = variant_of(X, "constraints")
    hook = admit_trial(rewritten, store, [mine])
    admitted = [hook(trial(receipt, mine)) for receipt in flipped]
    assert admitted == [False] * len(CASES), (
        "every reply was wrong; the ledger file was written again saying the candidate arm passed, the chain checks "
        f"out, and {sum(admitted)} of {len(CASES)} such pairs are admitted as credit")


def test_what_a_receipt_names_is_kept_beside_the_ledger_and_credit_needs_it(tmp_path: Path) -> None:
    store, ledger, found = measured(tmp_path)
    folder = kept_beside(ledger)
    kept = {path.name: path for path in folder.iterdir()} if folder.is_dir() else {}
    assert kept, "the evaluator kept nothing beside its ledger from which a receipt could be read again"
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == name for name, path in kept.items())
    first = found[0]
    named = {first.task_digest, first.information_digest, first.oracle_digest, first.baseline.evidence,
             first.candidate.evidence}
    assert named <= set(kept)
    assert kept[first.task_digest].read_bytes() == CASES[0].input.encode("utf-8")
    record = json.loads(kept[first.candidate.evidence].read_bytes())
    assert (record["reply"], record["case_id"], record["replay_id"], record["strategy_digest"]) == \
        ("2", "case-1", "r1", X.content_digest)
    mine = variant_of(X, "constraints")
    hook = admit_trial(ledger, store, [mine])
    honest = trial(first, mine)
    assert hook(honest) is True
    for name in sorted(named):
        data = kept[name].read_bytes()
        kept[name].write_bytes(data + b" ")                                # the bytes no longer hash to their name
        assert hook(honest) is False, name
        kept[name].unlink()                                                # and then they are gone
        assert hook(honest) is False, name
        kept[name].write_bytes(data)
        assert hook(honest) is True


# --- the root: one reading of credit, from the kept bytes --------------------------------------------------------

def test_credited_is_the_receipts_that_read_again_and_audit_says_why_the_others_do_not(tmp_path: Path) -> None:
    from g6.learning.paired_eval import audit, credited

    store, ledger, found = measured(tmp_path)
    assert credited(ledger, store) == found and [why for _, why in audit(ledger, store)] == [""] * len(CASES)
    arm = dataclasses.replace(found[0].candidate, strategy_digest=Y.content_digest,
                              prompt_digest=apply(Y, CASES[0].input, {"topic": "sums"}).prompt_digest)
    by_hand = dataclasses.replace(found[0], candidate=arm)
    by_hand = dataclasses.replace(by_hand, pair_id=pair_id(by_hand))
    renamed = dataclasses.replace(found[1], pair_id="made-by-hand")
    second = dataclasses.replace(found[2], pair_id=pair_id(found[2], attempt=1))
    for receipt in (by_hand, renamed, second):
        ledger.append(KIND, receipt.pair_id, receipt.to_body())
    assert credited(ledger, store) == found
    assert credited(ledger, store, strategy_digest=Y.content_digest) == ()
    whys = dict((receipt.pair_id, why) for receipt, why in audit(ledger, store))
    assert all(whys[receipt.pair_id] == "" for receipt in found)
    assert all(whys[receipt.pair_id] for receipt in (by_hand, renamed, second))
    assert len(receipts(ledger)) == len(CASES) + 3                         # the record keeps every row


def test_a_changed_strategy_file_and_an_unmatched_trigger_give_no_credit(tmp_path: Path) -> None:
    from g6.learning.paired_eval import credited

    store, ledger, found = measured(tmp_path)
    path = tmp_path / "strategies" / f"{X.content_digest}.json"
    data = path.read_bytes()
    path.write_bytes(data.replace(b"every stated", b"no stated"))
    assert credited(ledger, store) == ()
    path.write_bytes(data)
    assert credited(ledger, store) == found
    narrow = dataclasses.replace(X, trigger="topic:products")
    store.put(narrow)
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=helped, agent_id="stand-in/1", cohort_id="cohort-1",
                                split="heldout", seed=3, budget=BUDGET, environment_digest="e" * 64,
                                evaluator_ref="test://paired-evaluator")
    unmatched = evaluator.run(CASES[:2], candidate=narrow.content_digest, replay_id="r1")
    assert all(r.complete and not r.credits for r in unmatched)
    assert credited(ledger, store, strategy_digest=narrow.content_digest) == ()


def test_a_judged_reply_is_scored_again_from_the_kept_verdict_and_the_judge_is_not_asked_twice(tmp_path: Path) -> None:
    from g6.learning.paired_eval import credited

    asked: list[str] = []

    def judge(text: str) -> str:
        asked.append(text)
        return json.dumps({"score": 1.0 if "Candidate answer:\nfour" in text else 0.0, "reason": "compared"})

    judged = [TaskInstance(input="Write two plus two as a word.", expected_output="four", instance_id="words-1",
                           metadata={"rubric": "The answer is the word four.", "topic": "sums"})]
    store, ledger, found = measured(
        tmp_path, agent=lambda prompt: "four" if "Optional hints" in prompt else "4", cases=judged, judge=judge)
    assert (found[0].baseline.success, found[0].candidate.success) == (False, True) and len(asked) == 2
    assert credited(ledger, store) == found and len(asked) == 2, "reading credit asked the judge again"
    record_path = kept_beside(ledger) / found[0].baseline.evidence
    record = json.loads(record_path.read_bytes())
    assert len(record["judge"]) == 1 and json.loads(record["judge"][0][1])["score"] == 0.0
    # a row that says the baseline arm passed too does not score that way from the kept verdict
    again = ChainLedger(tmp_path / "again.jsonl")
    lied = dataclasses.replace(found[0], baseline=dataclasses.replace(found[0].baseline, success=True))
    again.append(KIND, lied.pair_id, lied.to_body())
    ledger.path.write_bytes(again.path.read_bytes())
    assert credited(ChainLedger(ledger.path), store) == ()


def test_the_evaluator_reuses_no_arm_it_cannot_read_again(tmp_path: Path) -> None:
    """A row appended by hand is not taken for a finished pair, and its baseline arm is not shared."""
    from g6.learning.paired_eval import credited

    sent: list[str] = []

    def recording(prompt: str) -> AgentReply:
        sent.append(prompt)
        return helped(prompt)

    store = StrategyStore(tmp_path / "strategies")
    store.put(X)
    ledger = ChainLedger(tmp_path / "receipts.jsonl")
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=recording, agent_id="stand-in/1",
                                cohort_id="cohort-1", split="heldout", seed=3, budget=BUDGET,
                                environment_digest="e" * 64, evaluator_ref="test://paired-evaluator")
    real = evaluator.run(CASES[:1], candidate=X.content_digest, replay_id="r1")[0]
    assert len(sent) == 2
    by_hand = dataclasses.replace(real, case_id="case-2", task_digest=hashlib.sha256(CASES[1].input.encode()).hexdigest())
    by_hand = dataclasses.replace(by_hand, pair_id=pair_id(by_hand))
    ledger.append(KIND, by_hand.pair_id, by_hand.to_body())
    found = evaluator.run(CASES[:2], candidate=X.content_digest, replay_id="r1")
    assert len(sent) == 4, "case-2 was never run: its hand-written row was taken for a finished pair"
    assert found[0] == real and found[1].order != "baseline_shared" and found[1].pair_id == pair_id(by_hand, attempt=1)
    assert credited(ledger, store) == (real, found[1])


def test_an_in_memory_ledger_keeps_its_evidence_with_the_evaluator(tmp_path: Path) -> None:
    from g6.learning.paired_eval import Evidence, credited

    store = StrategyStore(tmp_path / "strategies")
    store.put(X)
    ledger, evidence = ChainLedger(), Evidence()
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=helped, agent_id="stand-in/1", cohort_id="cohort-1",
                                split="heldout", seed=3, budget=BUDGET, environment_digest="e" * 64,
                                evaluator_ref="test://paired-evaluator", evidence=evidence)
    found = evaluator.run(CASES[:2], candidate=X.content_digest, replay_id="r1")
    assert credited(ledger, store, evidence=evidence) == found
    assert credited(ledger, store) == ()                                   # with nothing kept, nothing is credited
    with pytest.raises(KeyError):
        evidence.get("0" * 64)
    assert NO_STRATEGY == found[0].baseline.strategy_digest
