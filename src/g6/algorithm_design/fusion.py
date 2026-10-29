# SPDX-License-Identifier: Apache-2.0
"""Fuse top-down CIR preservation obligations with bottom-up candidate algorithms.

A match is an inspectable plan for checks, never evidence of a correct implementation.
"""
from __future__ import annotations

from dataclasses import dataclass

from g6.algorithm_design.ir import DesignCandidate
from g6.complexity.impact import Impact
from g6.complexity.ir import CIR


@dataclass(frozen=True)
class FusionResult:
    candidate_id: str
    snapshot_digest: str
    intent_digest: str
    matched_target: bool
    normative_obligations: tuple[str, ...]
    algorithm_proof_obligations: tuple[str, ...]
    uncertainty: tuple[str, ...]
    check_plan: tuple[str, ...]
    completion_state: str = "qualified-draft"
    approval: bool = False

    @property
    def ready_for_unrestricted_action(self) -> bool:
        return False  # no algorithm proposal can grant execution, merge or deployment rights


def fuse(cir: CIR, candidate: DesignCandidate, *, required_target: str,
         impact: Impact | None = None) -> FusionResult:
    matched = candidate.target == required_target
    normative = set(o.id for o in cir.intent.obligations if o.normative)
    if impact is not None:
        normative.update(impact.preservation_obligations)
    unknown = set(cir.unknowns)
    if impact is not None:
        unknown.update(impact.uncertainty)
    if not matched:
        unknown.add(f"target_mismatch:{candidate.target}!={required_target}")
    if not normative:
        unknown.add("no_authoritative_obligations")
    # Claims and properties are still unproven; the existing gate must evaluate real receipts.
    unknown.add("candidate_not_independently_verified")
    checks = tuple(sorted(normative | set(candidate.proof_obligations)))
    return FusionResult(candidate.id, cir.snapshot.tree_digest, cir.intent.fingerprint,
                        matched, tuple(sorted(normative)), candidate.proof_obligations,
                        tuple(sorted(unknown)), checks,
                        completion_state="qualified-draft" if matched else "blocked-escalated")
