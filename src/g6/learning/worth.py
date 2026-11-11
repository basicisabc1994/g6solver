# SPDX-License-Identifier: Apache-2.0
"""A strategy's measured worth: the paired statistic over evaluator receipts (0.3 design 14.2, "measure worth").

Worth is paired benefit and measured cost, from ``PairReceipt`` rows read from the evaluator's ledger: on the same
case, with the same agent, information and budget, did the candidate pass where the baseline failed, or fail where it
passed? It is never an absolute success rate, and a strategy with no receipts is ``unmeasured``, not zero.

The policy (the statistic's bar) is frozen before the first trial: ``bind_policy`` writes its digest in the ledger for
a cohort, and a run under another policy is refused. Regressions and incomplete pairs stay in the result.

Grouping: repeats of one case (one ``case_id`` in one ``cohort_id``) count once, by the majority of the replays that
are evidence; a tie is no credit. A pair is evidence when both arms completed and the strategy was really applied.
Training-split receipts never count.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from g6.core.ledger import ChainLedger, canonical
from g6.learning.paired import sign_test
from g6.learning.paired_eval import PairReceipt
from g6.learning.strategy import NO_STRATEGY, StrategyError

FORMAT = "g6.strategy_worth/1"
POLICY_KIND = "strategy_policy"
POLICY = "G6_E_STRATEGY_POLICY"
STATUSES = ("unmeasured", "insufficient", "measured")
COUNTED_SPLITS = ("heldout", "shadow")


@dataclass(frozen=True)
class WorthPolicy:
    min_complete_cases: int       # fewer complete cases than this is "insufficient"
    min_discordant: int           # fewer cases where the arms disagree than this is "insufficient"
    alpha: float                  # the one-sided sign test's level, already divided by the strategies in the cohort
    max_regressions: int          # critical cases the baseline passed and the candidate failed
    max_cost_ratio: float         # candidate over baseline: dollars where both arms report them, else measured time
    max_incomplete_share: float   # pairs with an arm that did not complete, over all pairs
    format: str = FORMAT

    def __post_init__(self) -> None:
        whole = (self.min_complete_cases, self.min_discordant, self.max_regressions)
        if self.format != FORMAT or not all(type(n) is int and n >= 0 for n in whole) or self.min_complete_cases < 1:
            raise StrategyError(POLICY, "a worth policy counts cases in whole numbers, in its own format")
        if not (0 < self.alpha <= 1 and self.max_cost_ratio > 0 and 0 <= self.max_incomplete_share <= 1):
            raise StrategyError(POLICY, "a worth policy's level, cost ratio and incomplete share are out of range")

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(asdict(self)).encode("utf-8")).hexdigest()


#: The policy frozen for the seed measurement (`validation/strategies/policy.json` holds the same values; a test
#: keeps them equal). The first three are the meta-harness gate's own minimums, so worth and promotion do not disagree:
#: 30 complete held-out cases, 5 discordant, and 0.05 divided by the 16 strategies measured in one cohort.
DEFAULT_POLICY = WorthPolicy(min_complete_cases=30, min_discordant=5, alpha=0.05 / 16, max_regressions=0,
                            max_cost_ratio=1.25, max_incomplete_share=0.2)


@dataclass(frozen=True)
class Worth:
    strategy_digest: str
    status: str                              # unmeasured | insufficient | measured
    cases: int
    complete_cases: int
    incomplete_pairs: int
    wins: int
    losses: int
    net_benefit: float | None                # (wins - losses) / complete_cases
    p_value: float | None                    # paired.sign_test(wins, losses)
    cost_ratio: float | None                 # candidate / baseline, over pairs where both arms report a cost
    time_ratio: float | None                 # candidate / baseline, by the evaluator's clock
    regressions: tuple[str, ...]             # case ids the baseline passed and the candidate failed
    critical_regressions: tuple[str, ...]    # those among them marked critical
    meets_bar: bool
    policy_digest: str


def bind_policy(ledger: ChainLedger, cohort_id: str, policy: WorthPolicy) -> str:
    """Freeze ``policy`` for ``cohort_id`` in the ledger, before the first trial, and answer its digest. A cohort
    already bound to another policy refuses (``G6_E_STRATEGY_POLICY``): a bar is not changed after trials began."""
    try:
        kept = ledger.get(POLICY_KIND, cohort_id)
    except KeyError:
        ledger.append(POLICY_KIND, cohort_id, {"policy_digest": policy.digest, "policy": asdict(policy)})
        return policy.digest
    if kept.get("policy_digest") != policy.digest:
        raise StrategyError(POLICY, f"cohort {cohort_id!r} was begun under policy {kept.get('policy_digest')}; this "
                                    f"policy is {policy.digest}")
    return policy.digest


def _ratio(pairs: Sequence[tuple[float, float]]) -> float | None:
    base = sum(b for b, _ in pairs)
    return (sum(c for _, c in pairs) / base) if pairs and base > 0 else None


def worth(receipts: Sequence[PairReceipt], strategy_digest: str, policy: WorthPolicy, *,
          splits: Sequence[str] = COUNTED_SPLITS, baseline: str = NO_STRATEGY) -> Worth:
    """The worth of one strategy against ``baseline`` from ``receipts`` (as ``paired_eval.receipts`` reads them from
    the ledger). Receipts of another strategy, another baseline or a split outside ``splits`` are not read; the
    training split is never among them."""
    if "training" in splits or set(splits) - set(COUNTED_SPLITS):
        raise ValueError("worth counts the held-out and shadow splits only")
    mine = [r for r in receipts if r.candidate.strategy_digest == strategy_digest
            and r.baseline.strategy_digest == baseline and r.split in splits]
    empty = dict(strategy_digest=strategy_digest, cases=0, complete_cases=0, incomplete_pairs=0, wins=0, losses=0,
                 net_benefit=None, p_value=None, cost_ratio=None, time_ratio=None, regressions=(),
                 critical_regressions=(), meets_bar=False, policy_digest=policy.digest)
    if not mine:
        return Worth(status="unmeasured", **empty)
    cases: dict[tuple[str, str], list[PairReceipt]] = {}
    for receipt in mine:
        cases.setdefault((receipt.cohort_id, receipt.case_id), []).append(receipt)
    wins = losses = complete = 0
    regressions: list[str] = []
    critical: list[str] = []
    for (_, case_id), replays in sorted(cases.items()):
        evidence = [r for r in replays if r.credits]
        if not evidence:
            continue
        complete += 1
        better = sum(bool(r.candidate.success) and not r.baseline.success for r in evidence)
        worse = sum(bool(r.baseline.success) and not r.candidate.success for r in evidence)
        if better > worse:
            wins += 1
        elif worse > better:
            losses += 1
            regressions.append(case_id)
            if any(r.critical for r in replays):
                critical.append(case_id)
    evidence = [r for r in mine if r.credits]
    cost = _ratio([(r.baseline.cost_usd, r.candidate.cost_usd) for r in evidence
                   if r.baseline.cost_measured and r.candidate.cost_measured])
    seconds = _ratio([(r.baseline.seconds, r.candidate.seconds) for r in evidence
                      if r.baseline.seconds is not None and r.candidate.seconds is not None])
    incomplete = sum(not r.complete for r in mine)
    p_value = sign_test(wins, losses) if complete else None
    net = (wins - losses) / complete if complete else None
    enough = complete >= policy.min_complete_cases and wins + losses >= policy.min_discordant
    spend = cost if cost is not None else seconds
    meets = (enough and p_value is not None and p_value <= policy.alpha and net is not None and net > 0
             and len(critical) <= policy.max_regressions and incomplete / len(mine) <= policy.max_incomplete_share
             and spend is not None and spend <= policy.max_cost_ratio)
    return Worth(strategy_digest=strategy_digest, status="measured" if enough else "insufficient", cases=len(cases),
                 complete_cases=complete, incomplete_pairs=incomplete, wins=wins, losses=losses, net_benefit=net,
                 p_value=p_value, cost_ratio=cost, time_ratio=seconds, regressions=tuple(regressions),
                 critical_regressions=tuple(critical), meets_bar=meets, policy_digest=policy.digest)


__all__ = ["COUNTED_SPLITS", "DEFAULT_POLICY", "FORMAT", "POLICY", "POLICY_KIND", "STATUSES", "Worth", "WorthPolicy", "bind_policy",
           "worth"]
