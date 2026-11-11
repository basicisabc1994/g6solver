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
                            ProblemSignature)
from .snapshot import number, publish, text


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


__all__ = ["PAIR_KIND", "admit_trial", "heuristic_variant", "trial_from"]
