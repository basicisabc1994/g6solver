# SPDX-License-Identifier: Apache-2.0
"""Heuristics as a method kind (0.3 design, 14.2): a strategy artefact of ``g6.learning`` as a ``MethodVariant`` of
kind ``heuristic``, beside the evidence plan and never inside it.

A heuristic variant names the bytes of its strategy (``content_digest``), so its definition digest, and with it every
trial that can credit it, is bound to what is applied. The selector drops heuristic variants before it ranks anything:
no obligation is met or removed by one.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Sequence

from .metacognition import (HEURISTIC, STRATEGY_ENGINE, STRATEGY_METHOD, STRATEGY_OP, EvaluatedTrial, MethodVariant,
                            ProblemSignature, _eligible)
from .snapshot import number, publish, snapshot, text


def heuristic_variant(artefact: Any, *, id: str, estimated_cost: float) -> MethodVariant:
    """The variant for one strategy artefact (``g6.learning.strategy.StrategyArtefact``): kind ``heuristic``, method
    ``strategy``, applied by ``g6.learning``'s ``apply``, its representation the artefact's mapping and scope, and its
    content digest the SHA-256 of the artefact's own bytes, computed here from those bytes."""
    data = artefact.to_bytes()
    if type(data) is not bytes:
        raise ValueError("a strategy artefact gives its bytes")
    representation = f"{text(artefact.mapping, where='artefact.mapping')}:{text(artefact.scope, where='artefact.scope')}"
    return publish(MethodVariant(id=text(id, where="id"), method=STRATEGY_METHOD, representation=representation,
                                 engine=STRATEGY_ENGINE, op=STRATEGY_OP,
                                 estimated_cost=number(estimated_cost, where="estimated_cost"), kind=HEURISTIC,
                                 content_digest=hashlib.sha256(data).hexdigest()),
                   where="heuristic_variant")


#: The ledger kind the paired evaluator writes (``g6.learning.paired_eval.KIND``; a test keeps the two equal).
PAIR_KIND = "strategy_pair"


def _evidence(body: Any) -> bool:
    """Whether a receipt's body is evidence about its candidate: both arms completed and the strategy was applied."""
    try:
        return (body["baseline"]["state"] == "completed" and body["candidate"]["state"] == "completed"
                and body["candidate"]["applied"] is True)
    except (KeyError, TypeError):
        return False


def trial_from(receipt: Any, variant: MethodVariant, context: ProblemSignature) -> EvaluatedTrial:
    """The trial one pair receipt (``g6.learning.paired_eval.PairReceipt``) gives a heuristic variant in a context:
    its id and evaluator reference are the receipt's pair id, and its outcome, cost, case, cohort, split and oracle
    are the receipt's. Refused for a pair that is not evidence, and for a variant the receipt did not measure."""
    body = receipt.to_body()
    if not _evidence(body):
        raise ValueError("a pair with an arm that did not complete, or whose strategy was not applied, is no trial")
    if variant.kind != HEURISTIC or body["candidate"]["strategy_digest"] != variant.content_digest:
        raise ValueError("the receipt measured another strategy than this variant's")
    regressed = bool(body["baseline"]["success"]) and not body["candidate"]["success"]
    return publish(EvaluatedTrial(
        trial_id=text(body["pair_id"], where="pair_id"), case_id=body["case_id"], cohort_id=body["cohort_id"],
        domain=context.domain, uncertainty=context.uncertainty, claim_kind=context.claim_kind, variant_id=variant.id,
        resolved=bool(body["candidate"]["success"]), actual_cost=float(body["candidate"]["cost_usd"] or 0.0),
        evaluator_ref=body["pair_id"], oracle_digest=body["oracle_digest"], split=body["split"],
        critical_regression=bool(body.get("critical")) and regressed, variant_digest=variant.definition_digest),
        where="trial_from")


def admit_trial(ledger: Any, store: Any, variants: Sequence[MethodVariant]) -> Callable[[EvaluatedTrial], bool]:
    """The admission hook for heuristic trials: a trial is admitted only when the evaluator's ledger (a
    ``g6.core.ledger.ChainLedger``) checks out and holds a pair receipt under the trial's evaluator reference, that
    receipt is evidence, the strategy it measured resolves in the store (bytes hashed again) and is the variant's own
    content digest, and the trial's variant, outcome, cost, case, cohort, split and oracle are what the receipt gives.
    A digest or an outcome a caller asserts supplies nothing."""
    known = {variant.definition_digest: variant for variant in variants if variant.kind == HEURISTIC}

    def admit(trial: EvaluatedTrial) -> bool:
        variant = known.get(trial.variant_digest)
        if variant is None or variant.id != trial.variant_id:
            return False
        try:
            if ledger.path is not None:
                ledger.reload()            # the file as it stands now: a line edited since it was opened refuses
            if ledger.verify():
                return False
            body = ledger.get(PAIR_KIND, trial.evaluator_ref)
            digest = body["candidate"]["strategy_digest"]
            if not _evidence(body) or digest != variant.content_digest:
                return False
            if hashlib.sha256(store.resolve(digest).to_bytes()).hexdigest() != digest:
                return False
            regressed = bool(body["baseline"]["success"]) and not body["candidate"]["success"]
            return (trial.trial_id, trial.case_id, trial.cohort_id, trial.split, trial.oracle_digest, trial.resolved,
                    trial.actual_cost, trial.critical_regression) == (
                body["pair_id"], body["case_id"], body["cohort_id"], body["split"], body["oracle_digest"],
                bool(body["candidate"]["success"]), float(body["candidate"]["cost_usd"] or 0.0),
                bool(body.get("critical")) and regressed)
        except Exception:      # an unreadable ledger, an unknown receipt, changed bytes: no admission
            return False

    return admit


#: A strategy row's fields: what ``rank_strategies`` writes, and all a row may carry. None is a success proxy or a
#: score: a row is the paired statistic (``g6.learning.worth.Worth``) for one strategy's bytes, or "unmeasured".
STRATEGY_ROW_FIELDS = ("variant_id", "content_digest", "status", "cases", "complete_cases", "incomplete_pairs", "wins",
                       "losses", "net_benefit", "p_value", "cost_ratio", "time_ratio", "regressions",
                       "critical_regressions", "meets_bar", "policy_digest")
_ORDER = {"measured": 1, "insufficient": 2, "unmeasured": 3}


def rank_strategies(context: ProblemSignature, variants: Sequence[MethodVariant], *, ledger: Any, store: Any,
                    policy: Any) -> tuple[dict[str, Any], ...]:
    """One row for each heuristic variant the context can afford, ranked by measured paired worth: those that are
    measured and meet the frozen policy's bar first, by net benefit and then by cost; then the other measured ones;
    then the insufficient; then the unmeasured, in id order. Worth is read from the evaluator's ledger and from nowhere
    else, for the bytes the store resolves under the variant's content digest (a strategy whose bytes do not resolve
    is unmeasured). It returns rows only: nothing is selected, applied or executed, and no row enters a selection."""
    from g6.learning.paired_eval import receipts
    from g6.learning.worth import worth

    context = snapshot(context, where="context")
    variants = snapshot(variants, where="variants")
    heuristics = [v for v in variants if v.kind == HEURISTIC and _eligible(v, context)]
    if len({v.id for v in heuristics}) != len(heuristics):
        raise ValueError("duplicate strategy variant ids")
    kept = receipts(ledger)
    rows = []
    for variant in heuristics:
        try:
            resolved = hashlib.sha256(store.resolve(variant.content_digest).to_bytes()).hexdigest()
        except ValueError:
            resolved = ""
        found = worth(kept if resolved == variant.content_digest else (), variant.content_digest, policy)
        row = {"variant_id": variant.id, "content_digest": variant.content_digest,
               **{name: getattr(found, name) for name in STRATEGY_ROW_FIELDS[2:]}}
        row["regressions"], row["critical_regressions"] = list(found.regressions), list(found.critical_regressions)
        rows.append(row)

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        first = 0 if row["status"] == "measured" and row["meets_bar"] else _ORDER[row["status"]]
        spend = row["cost_ratio"] if row["cost_ratio"] is not None else row["time_ratio"]
        return (first, -(row["net_benefit"] or 0.0) if first < 2 else 0.0,
                spend if first < 2 and spend is not None else float("inf"), row["variant_id"])

    return publish(tuple(sorted(rows, key=key)), where="rank_strategies")


def rank_seeds(context: ProblemSignature) -> tuple[dict[str, Any], ...]:
    """The 16 seed strategies for a context, as ``rank_strategies`` rows, from the receipts kept under the person's G6
    home (``strategies/receipts.jsonl``, with the strategy files beside it) when a measurement run wrote them there;
    with none, every seed is ``unmeasured``."""
    from g6.core.ledger import ChainLedger
    from g6.core.storage import g6_home
    from g6.learning.strategy import StrategyStore, seed_artefacts
    from g6.learning.worth import DEFAULT_POLICY

    home = g6_home() / "strategies"
    seeds = seed_artefacts()
    variants = [heuristic_variant(artefact, id=name, estimated_cost=0.0) for name, artefact in sorted(seeds.items())]
    path = home / "receipts.jsonl"
    ledger = ChainLedger(path) if path.is_file() else ChainLedger()
    return rank_strategies(context, variants, ledger=ledger, store=StrategyStore(home), policy=DEFAULT_POLICY)


__all__ = ["PAIR_KIND", "STRATEGY_ROW_FIELDS", "admit_trial", "heuristic_variant", "rank_seeds", "rank_strategies",
           "trial_from"]
