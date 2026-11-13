# SPDX-License-Identifier: Apache-2.0
"""Heuristics as a method kind of ``g6.cognitive_bridge`` (0.3 design, 14.2), from the side that owns the strategy:
the adapters that read a strategy artefact, the evaluator's ledger and its receipts.

A heuristic is a ``MethodVariant`` of kind ``heuristic`` whose content digest is the SHA-256 of its strategy
artefact's bytes. It sits beside the evidence plan and never inside it: the bridge's selector drops heuristic variants
before it ranks anything. This module gives the bridge's records what they need as plain data, and reads the bridge's
records by their fields; it imports nothing from the bridge, so the bridge's own boundary rules stay the bridge's.

- ``variant_fields``: the fields of the variant for one artefact: ``MethodVariant(**variant_fields(...))``.
- ``trial_fields``: the fields of the trial one pair receipt gives a variant: ``EvaluatedTrial(**trial_fields(...))``.
- ``admit_trial``: the admission hook. Only a receipt that reads again from what the evaluator kept credits a
  strategy (``g6.learning.paired_eval.credited``); a row in the ledger alone supplies nothing.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any

from g6.core.ledger import ChainLedger
from g6.learning.paired_eval import Evidence, PairReceipt, audit
from g6.learning.strategy import StrategyArtefact, StrategyStore

#: What the bridge's ``MethodVariant`` asks of a heuristic (``g6.cognitive_bridge.metacognition``; a test keeps the
#: two equal).
HEURISTIC = "heuristic"
STRATEGY_METHOD, STRATEGY_ENGINE, STRATEGY_OP = "strategy", "g6.learning", "apply"


def variant_fields(artefact: StrategyArtefact, *, id: str, estimated_cost: float) -> dict[str, Any]:
    """The fields of the heuristic variant for ``artefact``: kind ``heuristic``, method ``strategy``, applied by
    ``g6.learning``'s ``apply``, its representation the artefact's mapping and scope, and its content digest the
    SHA-256 of the artefact's own bytes."""
    return {"id": id, "method": STRATEGY_METHOD, "representation": f"{artefact.mapping}:{artefact.scope}",
            "engine": STRATEGY_ENGINE, "op": STRATEGY_OP, "estimated_cost": estimated_cost, "kind": HEURISTIC,
            "content_digest": hashlib.sha256(artefact.to_bytes()).hexdigest()}


def _regressed(receipt: PairReceipt) -> bool:
    return receipt.critical and receipt.baseline.success is True and receipt.candidate.success is False


def trial_fields(receipt: PairReceipt, variant: Any, context: Any) -> dict[str, Any]:
    """The fields of the trial one pair receipt gives a heuristic variant in a context: its id and evaluator
    reference are the receipt's pair id, and its outcome, cost, case, cohort, split and oracle are the receipt's.
    Refused for a pair that is not evidence, and for a variant the receipt did not measure."""
    if not receipt.credits:
        raise ValueError("a pair with an arm that did not complete, or whose strategy was not applied, is no trial")
    if variant.kind != HEURISTIC or receipt.candidate.strategy_digest != variant.content_digest:
        raise ValueError("the receipt measured another strategy than this variant's")
    return {"trial_id": receipt.pair_id, "case_id": receipt.case_id, "cohort_id": receipt.cohort_id,
            "domain": context.domain, "uncertainty": context.uncertainty, "claim_kind": context.claim_kind,
            "variant_id": variant.id, "resolved": receipt.candidate.success is True,
            "actual_cost": receipt.candidate.cost_usd if receipt.candidate.cost_usd is not None else 0.0,
            "evaluator_ref": receipt.pair_id, "oracle_digest": receipt.oracle_digest, "split": receipt.split,
            "critical_regression": _regressed(receipt), "variant_digest": variant.definition_digest}


def admit_trial(ledger: ChainLedger, store: StrategyStore, variants: Sequence[Any], *,
                evidence: Evidence | None = None) -> Callable[[Any], bool]:
    """The admission hook for heuristic trials (``MetacognitiveMethodSelector(admit_trial=...)``): a trial is
    admitted only when the evaluator's ledger checks out, as its file stands now, and holds a pair receipt under the
    trial's evaluator reference; that receipt reads again from what the evaluator kept (``paired_eval.audit``: the
    task, the oracle, each arm's reply and judge verdicts, and the strategy's bytes, each hashed again, give the pair
    the row states); the strategy it measured is the variant's own content digest; and the trial's variant, outcome,
    cost, case, cohort, split and oracle are what the receipt gives. A row appended by hand, a digest or an outcome a
    caller asserts supplies nothing. ``evidence`` is the evaluator's store; by default the one beside the ledger."""
    known = {variant.definition_digest: variant for variant in variants if variant.kind == HEURISTIC}

    def admit(trial: Any) -> bool:
        variant = known.get(trial.variant_digest)
        if variant is None or variant.id != trial.variant_id:
            return False
        try:
            if ledger.path is not None:
                ledger.reload()            # the file as it stands now: a line edited since it was opened refuses
            read = audit(ledger, store, evidence=evidence, pair_id=trial.evaluator_ref)
            receipt = next((found for found, why in read if found.pair_id == trial.evaluator_ref and not why), None)
            if receipt is None or receipt.candidate.strategy_digest != variant.content_digest:
                return False
            given = trial_fields(receipt, variant, trial)
            return all(getattr(trial, name) == value for name, value in given.items())
        except Exception:                  # an unreadable ledger, an unknown receipt, changed bytes: no admission
            return False

    return admit


__all__ = ["HEURISTIC", "STRATEGY_ENGINE", "STRATEGY_METHOD", "STRATEGY_OP", "admit_trial", "trial_fields",
           "variant_fields"]
