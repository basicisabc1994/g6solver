# SPDX-License-Identifier: Apache-2.0
"""The reading of worth (0.3 design 14.2, "measure worth"): it is a function of the evaluator's ledger, of what the
evaluator kept beside it, and of the frozen bar, and of nothing its caller hands in. A looser bar cannot be passed, a
cohort begun under another bar or under none is refused, and rows appended by hand count for nothing. Offline:
stand-in agents, no model."""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any

import pytest

from g6.cognitive_bridge import MethodVariant, ProblemSignature
from g6.core.ledger import ChainLedger, canonical
from g6.learning.method_kind import rank_strategies, variant_fields
from g6.learning.paired_eval import KIND, PairedEvaluator, PairReceipt, apply
from g6.learning.schema import AgentReply, AgentTrace, TaskInstance
from g6.learning.strategy import StrategyArtefact, StrategyError, StrategyStore
from g6.learning.worth import DEFAULT_POLICY, POLICY, WorthPolicy, bind_policy

GOOD = StrategyArtefact(instruction="Check the answer against every stated constraint.", trigger="always", scope="search")
NEW = dataclasses.replace(GOOD, instruction="Try the smallest case first.")
CONTEXT = ProblemSignature("arithmetic", "exact", "answer", 1, 10)
LOOSER = WorthPolicy(min_complete_cases=1, min_discordant=1, alpha=0.5, max_regressions=9, max_cost_ratio=10.0,
                     max_incomplete_share=1.0)
CASES = [TaskInstance(input=f"What is {n} + {n}?", expected_output=str(2 * n), instance_id=f"case-{n:02d}")
         for n in range(1, 41)]


def agent(prompt: str) -> AgentReply:
    """Right with GOOD's hint; with no hint, right on the first half of the cases."""
    n = int(prompt.rsplit("What is ", 1)[1].split(" ")[0])
    right = GOOD.instruction in prompt or n <= 20
    return AgentReply(str(2 * n) if right else "?", AgentTrace(cost_usd=0.01))


def measured(tmp_path: Path, n: int = 40, bound: WorthPolicy | None = DEFAULT_POLICY
             ) -> tuple[list[MethodVariant], StrategyStore, ChainLedger, tuple[PairReceipt, ...]]:
    store = StrategyStore(tmp_path / "strategies")
    store.put(GOOD)
    store.put(NEW)
    ledger = ChainLedger(tmp_path / "receipts.jsonl")
    if bound is not None:
        bind_policy(ledger, "cohort-1", bound)
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=agent, agent_id="stand-in/1", cohort_id="cohort-1",
                                split="heldout", seed=3, budget={"max_calls": 999, "max_seconds": 60, "max_usd": 5},
                                environment_digest="e" * 64, evaluator_ref="test://paired-evaluator")
    found = evaluator.run(CASES[:n], candidate=GOOD.content_digest, replay_id="r1")
    variants = [MethodVariant(**variant_fields(NEW, id="a-new", estimated_cost=0)),
                MethodVariant(**variant_fields(GOOD, id="c-good", estimated_cost=0))]
    return variants, store, ledger, found


def ranked(variants: Any, ledger: ChainLedger, store: StrategyStore, **door: Any) -> dict[str, dict[str, Any]]:
    """``rank_strategies`` with ``door`` handed in where the function still takes it; without, where it does not."""
    try:
        rows = rank_strategies(CONTEXT, variants, ledger=ledger, store=store, **door)
    except TypeError:
        rows = rank_strategies(CONTEXT, variants, ledger=ledger, store=store)
    return {row["variant_id"]: row for row in rows}


def test_control_a_cohort_bound_to_the_frozen_bar_before_its_trials_is_read(tmp_path: Path) -> None:
    variants, store, ledger, _ = measured(tmp_path)
    rows = ranked(variants, ledger, store, policy=DEFAULT_POLICY)
    good = rows["c-good"]
    assert (good["status"], good["cases"], good["complete_cases"], good["wins"], good["losses"]) == \
        ("measured", 40, 40, 20, 0)
    assert good["meets_bar"] and good["net_benefit"] == 0.5 and good["policy_digest"] == DEFAULT_POLICY.digest
    assert rows["a-new"]["status"] == "unmeasured"


def test_a_caller_cannot_hand_in_a_looser_bar(tmp_path: Path) -> None:
    variants, store, ledger, _ = measured(tmp_path, n=8)        # eight cases: the frozen bar asks for thirty
    rows = ranked(variants, ledger, store, policy=LOOSER)
    assert not any(row["meets_bar"] for row in rows.values()), (
        "eight cases were measured, the frozen bar asks for thirty, and a bar handed in by the caller gives meets_bar")
    assert {row["policy_digest"] for row in rows.values()} == {DEFAULT_POLICY.digest}
    assert rows["c-good"]["status"] == "insufficient"


def test_rows_appended_by_hand_give_no_worth(tmp_path: Path) -> None:
    """Forty well-formed winning pairs for a strategy that was never run, appended through the ledger's public door."""
    variants, store, ledger, found = measured(tmp_path, n=8)
    for n in range(40):
        arm = dataclasses.replace(found[4].candidate, strategy_digest=NEW.content_digest,
                                  prompt_digest=apply(NEW, f"forged task {n}", {}).prompt_digest)
        row = dataclasses.replace(found[4], case_id=f"forged-{n:02d}", candidate=arm)
        named = [row.cohort_id, row.replay_id, row.case_id, NEW.content_digest, row.baseline.strategy_digest, 0]
        row = dataclasses.replace(row, pair_id=hashlib.sha256(canonical(named).encode("utf-8")).hexdigest()[:32])
        ledger.append(KIND, row.pair_id, row.to_body())
    assert ledger.verify() == ()
    new = ranked(variants, ledger, store, policy=DEFAULT_POLICY)["a-new"]
    assert not new["meets_bar"] and new["wins"] == 0 and new["complete_cases"] == 0, (
        f"nobody ran this strategy; forty rows appended by hand give it {new['wins']} wins and meets_bar "
        f"{new['meets_bar']}")
    assert new["cases"] == 40                                   # the rows stay in the record, and credit nothing


@pytest.mark.parametrize("bound", [None, LOOSER], ids=["no_bar_bound", "another_bar_bound"])
def test_a_cohort_not_begun_under_the_frozen_bar_is_not_read(tmp_path: Path, bound: WorthPolicy | None) -> None:
    variants, store, ledger, _ = measured(tmp_path, bound=bound)
    try:
        rows = ranked(variants, ledger, store, policy=DEFAULT_POLICY)
    except StrategyError as refusal:
        assert refusal.code == POLICY                           # refusing the reading is correct
    else:
        assert not any(row["meets_bar"] for row in rows.values()), (
            "the cohort's trials were not begun under the frozen bar, and its strategy is read as meeting it")
    if bound is None:                                           # and a bar cannot be bound once trials began
        with pytest.raises(StrategyError) as late:
            bind_policy(ledger, "cohort-1", DEFAULT_POLICY)
        assert late.value.code == POLICY


def test_the_reading_takes_a_ledger_and_a_store_and_no_receipt_or_bar() -> None:
    import inspect

    from g6.learning import worth as worth_module

    assert list(inspect.signature(worth_module.worth).parameters) == [
        "ledger", "store", "strategy_digest", "evidence", "splits", "baseline"]
    assert set(inspect.signature(rank_strategies).parameters) == {"context", "variants", "ledger", "store", "evidence"}


def test_a_kept_reply_that_changed_takes_its_win_away(tmp_path: Path) -> None:
    from g6.learning.worth import worth

    variants, store, ledger, found = measured(tmp_path)
    assert worth(ledger, store, GOOD.content_digest).wins == 20
    won = next(r for r in found if r.candidate.success and not r.baseline.success)
    kept = Path(str(ledger.path) + ".evidence") / won.candidate.evidence
    kept.write_bytes(kept.read_bytes().replace(b'"reply":"', b'"reply":"not '))
    after = worth(ledger, store, GOOD.content_digest)
    assert (after.wins, after.complete_cases, after.cases) == (19, 39, 40) and after.policy_digest == DEFAULT_POLICY.digest
