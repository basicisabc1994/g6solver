# SPDX-License-Identifier: Apache-2.0
"""A strategy's measured worth: the paired statistic over evaluator receipts (0.3 design 14.2, "measure worth").

Worth is paired benefit and measured cost: on the same case, with the same agent, information and budget, did the
candidate pass where the baseline failed, or fail where it passed? It is never an absolute success rate, and a
strategy with no receipts is ``unmeasured``, not zero.

``worth`` is the one reading, and it takes neither receipts nor a bar from its caller. Credit comes only from the
receipts that read again from what the evaluator kept (``paired_eval.credited``); a row in the ledger alone counts for
nothing but the record of incomplete pairs. The bar is ``DEFAULT_POLICY``, frozen data: ``bind_policy`` writes a
policy's digest in the ledger for a cohort before its first trial, and ``worth`` refuses a cohort that was begun
under another policy, under none, or bound after its trials began (``G6_E_STRATEGY_POLICY``). Every result names the
policy's digest. Regressions and incomplete pairs stay in the result.

What this does not close: the ledger's head is anchored nowhere outside the evaluator's reach, so whoever can write
the ledger file and the kept bytes together can make pairs that read again (``paired_eval``'s own stated limit).

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
from g6.learning.paired_eval import KIND, Evidence, PairReceipt, credited, receipts
from g6.learning.strategy import NO_STRATEGY, StrategyError, StrategyStore

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


def bind_policy(ledger: ChainLedger, cohort_id: str, policy: WorthPolicy = DEFAULT_POLICY) -> str:
    """Freeze ``policy`` for ``cohort_id`` in the ledger, before the first trial, and answer its digest. A cohort
    already bound to another policy refuses, and so does one whose trials already began with none bound
    (``G6_E_STRATEGY_POLICY``): a bar is not set or changed after trials began. Only a cohort bound to the frozen
    ``DEFAULT_POLICY`` is read by ``worth``."""
    try:
        kept = ledger.get(POLICY_KIND, cohort_id)
    except KeyError:
        if any(entry["kind"] == KIND and entry["body"].get("cohort_id") == cohort_id for entry in ledger.entries()):
            raise StrategyError(POLICY, f"cohort {cohort_id!r} already has trials and no policy was bound before "
                                        "them") from None
        ledger.append(POLICY_KIND, cohort_id, {"policy_digest": policy.digest, "policy": asdict(policy)})
        return policy.digest
    if kept.get("policy_digest") != policy.digest:
        raise StrategyError(POLICY, f"cohort {cohort_id!r} was begun under policy {kept.get('policy_digest')}; this "
                                    f"policy is {policy.digest}")
    return policy.digest


def unmeasured(strategy_digest: str) -> Worth:
    """The worth of a strategy nothing is read for: ``unmeasured``, under the frozen policy's digest."""
    return _statistic((), strategy_digest, DEFAULT_POLICY)


def _ratio(pairs: Sequence[tuple[float, float]]) -> float | None:
    base = sum(b for b, _ in pairs)
    return (sum(c for _, c in pairs) / base) if pairs and base > 0 else None


def bound_before_trials(ledger: ChainLedger, cohort_id: str) -> str:
    """The digest of the policy bound for ``cohort_id`` before its first trial, read from the ledger's own order.
    ``G6_E_STRATEGY_POLICY`` when none is bound, or when it was bound after a trial of the cohort."""
    for entry in ledger.entries():
        if entry["kind"] == POLICY_KIND and entry["id"] == cohort_id:
            return str(entry["body"].get("policy_digest", ""))
        if entry["kind"] == KIND and entry["body"].get("cohort_id") == cohort_id:
            break
    raise StrategyError(POLICY, f"cohort {cohort_id!r} has no policy bound before its first trial")


def worth(ledger: ChainLedger, store: StrategyStore, strategy_digest: str, *, evidence: Evidence | None = None,
          splits: Sequence[str] = COUNTED_SPLITS, baseline: str = NO_STRATEGY) -> Worth:
    """The worth of one strategy against ``baseline``, read from the evaluator's ledger and what it kept, under the
    frozen policy. Credit is given only by receipts that read again (``paired_eval.credited``); every cohort that
    has a receipt for this strategy must have been bound to the frozen policy before its first trial, or the reading
    is refused (``G6_E_STRATEGY_POLICY``). The caller supplies no receipt and no bar."""
    record = [r for r in receipts(ledger) if r.candidate.strategy_digest == strategy_digest
              and r.baseline.strategy_digest == baseline and r.split in splits]
    for cohort_id in sorted({r.cohort_id for r in record}):
        bound = bound_before_trials(ledger, cohort_id)
        if bound != DEFAULT_POLICY.digest:
            raise StrategyError(POLICY, f"cohort {cohort_id!r} was begun under policy {bound}, and the frozen policy "
                                        f"is {DEFAULT_POLICY.digest}")
    reads_again = frozenset(r.pair_id for r in credited(ledger, store, evidence=evidence,
                                                         strategy_digest=strategy_digest)) if record else frozenset()
    return _statistic(record, strategy_digest, DEFAULT_POLICY, splits=splits, baseline=baseline, credits=reads_again)


def _statistic(receipts: Sequence[PairReceipt], strategy_digest: str, policy: WorthPolicy, *,
               splits: Sequence[str] = COUNTED_SPLITS, baseline: str = NO_STRATEGY,
               credits: frozenset[str] | None = None) -> Worth:
    """The paired statistic over ``receipts`` under ``policy``: the arithmetic only. It says nothing about where the
    receipts or the bar came from, so it is no reading of worth; ``worth`` is, and hands it the record with the pair
    ids that read again (``credits``). With ``credits`` left out every complete, applied pair counts: that is for the
    arithmetic's own tests. Receipts of another strategy, another baseline or a split outside ``splits`` are not
    read; the training split is never among them."""
    if "training" in splits or set(splits) - set(COUNTED_SPLITS):
        raise ValueError("worth counts the held-out and shadow splits only")

    def counts(receipt: PairReceipt) -> bool:
        return receipt.credits and (credits is None or receipt.pair_id in credits)

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
        evidence = [r for r in replays if counts(r)]
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
    evidence = [r for r in mine if counts(r)]
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


__all__ = ["COUNTED_SPLITS", "DEFAULT_POLICY", "FORMAT", "POLICY", "POLICY_KIND", "STATUSES", "Worth", "WorthPolicy",
           "bind_policy", "bound_before_trials", "unmeasured", "worth"]
