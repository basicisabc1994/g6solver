# SPDX-License-Identifier: Apache-2.0
"""The arithmetic of a strategy's worth (0.3 design 14.2, "measure worth"): the paired statistic, never an absolute
success rate, with repeats grouped by case, regressions and incomplete pairs kept, and the policy frozen before the
first trial. These tests hand ``_statistic`` receipts and a policy to check its arithmetic; the reading a person gets
(``worth``) takes neither, and tests/learning/test_worth_reading.py holds its tests."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger
from g6.learning.paired import sign_test
from g6.learning.paired_eval import Arm, PairReceipt
from g6.learning.strategy import NO_STRATEGY, StrategyError
from g6.learning.worth import DEFAULT_POLICY, POLICY, Worth, WorthPolicy, bind_policy
from g6.learning.worth import _statistic as worth       # the arithmetic only; see the module docstring

S, OTHER = "5" * 64, "6" * 64
EASY = WorthPolicy(min_complete_cases=4, min_discordant=2, alpha=0.2, max_regressions=0, max_cost_ratio=1.25,
                   max_incomplete_share=0.2)


def arm(digest: str, success: bool | None, *, state: str = "completed", seconds: float | None = 1.0,
        cost: float | None = 0.01, applied: bool | None = None) -> Arm:
    done = state == "completed"
    return Arm(state, success if done else None, seconds, cost, cost is not None, digest, "0" * 64,
               (digest != NO_STRATEGY) if applied is None else applied)


def pair(case: str, base: bool | None, cand: bool | None, *, replay: str = "r1", split: str = "heldout",
         strategy: str = S, critical: bool = False, cohort: str = "cohort-1", **arms: object) -> PairReceipt:
    candidate = arms.pop("candidate", None) or arm(strategy, cand, **arms)       # type: ignore[arg-type]
    return PairReceipt(pair_id=f"{cohort}-{replay}-{case}-{strategy[:4]}", cohort_id=cohort, case_id=case,
                       replay_id=replay, split=split, task_digest="1" * 64, agent_id="a", information_digest="2" * 64,
                       oracle_digest="3" * 64, environment_digest="4" * 64,
                       budget=(("max_calls", 9.0), ("max_seconds", 9.0), ("max_usd", 9.0)), seed=1,
                       order="baseline_first", permitted_difference="7" * 64, baseline=arm(NO_STRATEGY, base),
                       candidate=candidate, evaluator_ref="test://e", critical=critical)


def wins(n: int, start: int = 0) -> list[PairReceipt]:
    return [pair(f"w{i}", False, True) for i in range(start, start + n)]


def ties(n: int) -> list[PairReceipt]:
    return [pair(f"t{i}", True, True) for i in range(n)]


def test_no_receipts_is_unmeasured_not_zero() -> None:
    found = worth([], S, EASY)
    assert found.status == "unmeasured" and found.net_benefit is None and found.p_value is None
    assert (found.cases, found.complete_cases, found.wins, found.losses, found.meets_bar) == (0, 0, 0, 0, False)
    assert worth(wins(6), OTHER, EASY).status == "unmeasured"          # another strategy's receipts are not this one's
    assert not {"success_rate", "score", "smoothed_success_proxy", "accuracy"} & {
        field.name for field in dataclasses.fields(Worth)}


def test_worth_is_paired_benefit_not_an_absolute_rate() -> None:
    """Passing every case is worth nothing when the baseline passes them too."""
    assert worth(ties(40), S, EASY).net_benefit == 0.0 and not worth(ties(40), S, EASY).meets_bar
    found = worth([*wins(6), *ties(2)], S, EASY)
    assert (found.status, found.cases, found.complete_cases, found.wins, found.losses) == ("measured", 8, 8, 6, 0)
    assert found.net_benefit == 0.75 and found.p_value == sign_test(6, 0) and found.meets_bar
    assert found.cost_ratio == 1.0 and found.time_ratio == 1.0 and found.policy_digest == EASY.digest


def test_too_few_cases_or_too_few_discordant_is_insufficient() -> None:
    assert worth(wins(3), S, EASY).status == "insufficient" and not worth(wins(3), S, EASY).meets_bar
    one_discordant = worth([*wins(1), *ties(9)], S, EASY)
    assert one_discordant.status == "insufficient" and one_discordant.complete_cases == 10
    assert worth(wins(29), S, DEFAULT_POLICY).status == "insufficient"
    assert worth(wins(30), S, DEFAULT_POLICY).meets_bar


def test_repeats_of_a_case_count_once() -> None:
    repeated = [pair("c1", False, True, replay=f"r{i}") for i in range(10)]
    found = worth([*repeated, *wins(4)], S, EASY)
    assert (found.cases, found.complete_cases, found.wins) == (5, 5, 5)
    assert worth(repeated, S, EASY).status == "insufficient"           # ten replays of one case are one case


def test_a_tied_case_gives_no_credit() -> None:
    split_case = [pair("c1", False, True, replay="r1"), pair("c1", True, False, replay="r2")]
    found = worth([*split_case, *wins(4)], S, EASY)
    assert (found.complete_cases, found.wins, found.losses, found.regressions) == (5, 4, 0, ())
    majority = [pair("c1", False, True, replay="r1"), pair("c1", False, True, replay="r2"),
                pair("c1", True, False, replay="r3")]
    assert worth([*majority, *wins(4)], S, EASY).wins == 5


def test_incomplete_pairs_are_reported_and_can_fail_the_bar() -> None:
    broken = [pair(f"x{i}", False, None, state="interrupted", seconds=None, cost=None) for i in range(3)]
    found = worth([*wins(8), *broken], S, EASY)
    assert (found.cases, found.complete_cases, found.incomplete_pairs) == (11, 8, 3)
    assert found.status == "measured" and not found.meets_bar          # 3 of 11 is over the 0.2 share
    assert worth([*wins(8), *broken[:2]], S, EASY).meets_bar           # 2 of 10 is within it
    for state in ("missing", "refused", "interrupted"):
        one = pair("x", False, None, state=state, seconds=None, cost=None)
        assert worth([one], S, EASY).incomplete_pairs == 1 and worth([one], S, EASY).complete_cases == 0


def test_a_pair_whose_strategy_was_not_applied_is_no_evidence() -> None:
    unapplied = [pair(f"u{i}", False, True, applied=False) for i in range(6)]
    found = worth(unapplied, S, EASY)
    assert (found.cases, found.complete_cases, found.wins, found.status) == (6, 0, 0, "insufficient")


def test_regressions_are_kept_and_named() -> None:
    found = worth([*wins(8), pair("lost", True, False)], S, EASY)
    assert found.regressions == ("lost",) and found.critical_regressions == () and found.losses == 1
    assert found.meets_bar                                              # an ordinary regression is kept, and counted
    critical = worth([*wins(30), pair("lost", True, False, critical=True)], S, EASY)
    assert critical.critical_regressions == ("lost",) and not critical.meets_bar, "whatever the wins"
    lenient = dataclasses.replace(EASY, max_regressions=1)
    assert worth([*wins(30), pair("lost", True, False, critical=True)], S, lenient).meets_bar


def test_cost_uses_measured_arms_only() -> None:
    unmeasured = [pair(f"w{i}", False, True, cost=None) for i in range(6)]
    found = worth(unmeasured, S, EASY)
    assert found.cost_ratio is None and found.time_ratio == 1.0
    mixed = worth([*unmeasured, pair("m", False, True, cost=0.03)], S, EASY)
    assert mixed.cost_ratio == 3.0                                       # the one pair where both arms report a cost
    no_time = [pair(f"w{i}", False, True, cost=None, seconds=None) for i in range(6)]
    assert worth(no_time, S, EASY).time_ratio is None and not worth(no_time, S, EASY).meets_bar


def test_a_cheaper_but_no_better_strategy_does_not_meet_the_bar() -> None:
    cheaper = [pair(f"t{i}", True, True, cost=0.001, seconds=0.1) for i in range(40)]
    found = worth(cheaper, S, EASY)
    assert found.cost_ratio == pytest.approx(0.1) and found.net_benefit == 0.0 and not found.meets_bar


def test_a_better_but_too_costly_strategy_does_not_meet_the_bar() -> None:
    dear = [pair(f"w{i}", False, True, cost=0.02) for i in range(8)]
    assert worth(dear, S, EASY).cost_ratio == 2.0 and not worth(dear, S, EASY).meets_bar
    slow = [pair(f"w{i}", False, True, cost=None, seconds=2.0) for i in range(8)]
    assert worth(slow, S, EASY).time_ratio == 2.0 and not worth(slow, S, EASY).meets_bar
    assert worth([pair(f"w{i}", False, True, cost=0.012) for i in range(8)], S, EASY).meets_bar


def test_training_split_receipts_never_count() -> None:
    training = [pair(f"w{i}", False, True, split="training") for i in range(40)]
    assert worth(training, S, EASY).status == "unmeasured"
    with pytest.raises(ValueError):
        worth(training, S, EASY, splits=("training",))
    shadow = [pair(f"w{i}", False, True, split="shadow") for i in range(6)]
    assert worth(shadow, S, EASY).meets_bar and worth(shadow, S, EASY, splits=("heldout",)).status == "unmeasured"


def test_another_baseline_is_another_comparison() -> None:
    against_parent = [dataclasses.replace(r, baseline=arm(OTHER, False)) for r in wins(6)]
    assert worth(against_parent, S, EASY).status == "unmeasured"
    assert worth(against_parent, S, EASY, baseline=OTHER).meets_bar


def test_the_policy_is_frozen_before_the_first_trial(tmp_path: Path) -> None:
    ledger = ChainLedger(tmp_path / "receipts.jsonl")
    assert bind_policy(ledger, "cohort-1", EASY) == EASY.digest
    assert bind_policy(ledger, "cohort-1", dataclasses.replace(EASY)) == EASY.digest          # the same policy again
    looser = dataclasses.replace(EASY, alpha=0.5)
    with pytest.raises(StrategyError) as caught:
        bind_policy(ledger, "cohort-1", looser)
    assert caught.value.code == POLICY and looser.digest != EASY.digest
    assert bind_policy(ledger, "cohort-2", looser) == looser.digest                            # another cohort is free
    assert ChainLedger(tmp_path / "receipts.jsonl").get("strategy_policy", "cohort-1")["policy_digest"] == EASY.digest
    for bad in ({"alpha": 0.0}, {"min_complete_cases": 0}, {"max_incomplete_share": 1.5}, {"format": "other/1"},
                {"min_discordant": 2.5}):
        with pytest.raises(StrategyError):
            dataclasses.replace(EASY, **bad)
