# SPDX-License-Identifier: Apache-2.0
"""Read-only field-trial feed. The existing selector's outside admission hook owns trust."""
from dataclasses import dataclass

from g6.cognitive_bridge.metacognition import EvaluatedTrial, MethodVariant
from g6.core.ledger import digest
from g6.distill.records import LibraryEntry

from .records import MethodOutcome


@dataclass(frozen=True)
class SelectorFeed:
    variants: tuple[MethodVariant, ...]
    trials: tuple[EvaluatedTrial, ...]


def selector_feed(entries, outcomes) -> SelectorFeed:
    entries = tuple(LibraryEntry.model_validate_json(e.model_dump_json()) for e in entries)
    active = {e.entry_id: e for e in entries if e.status in {'probation', 'admitted', 'demoted'}}
    variants = tuple(MethodVariant(id=e.entry_id, method='library_check', representation=e.candidate_id,
        engine='distill', op='check', estimated_cost=e.evidence.cost.p95_s, kind='library_method')
        for e in sorted(active.values(), key=lambda e: e.entry_id))
    by_id = {v.id: v for v in variants}
    trials = []
    for outcome in outcomes:
        row = MethodOutcome.model_validate_json(outcome.model_dump_json())
        variant = by_id.get(row.entry_id)
        if variant is None:
            continue
        trials.append(EvaluatedTrial(trial_id=digest(row.model_dump(mode='json')), case_id=row.case_id,
            cohort_id=row.cohort_id, domain=row.domain, uncertainty=row.uncertainty, claim_kind=row.claim_kind,
            variant_id=variant.id, variant_digest=variant.definition_digest, resolved=row.resolved,
            actual_cost=row.cost, evaluator_ref=row.evaluator_ref, oracle_digest=row.oracle_digest,
            split='shadow' if row.split == 'field' else 'heldout', critical_regression=row.critical_regression))
    return SelectorFeed(variants, tuple(trials))
