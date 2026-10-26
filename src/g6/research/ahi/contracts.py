# SPDX-License-Identifier: Apache-2.0
"""Typed contracts from ECHA spec §4.4–4.5. Every constructor refuses a missing required field.

``AdaptationProposal`` and ``Control`` are product code now (``g6.core.adaptation_gate``, graduated, audit 037).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from g6.core.adaptation_gate import PROPOSAL_STATUSES, AdaptationProposal, Control
from g6.research.echa.records import hex64_of, text_of, texts_of

__all__ = ["FUNNEL_STAGES", "PROPOSAL_STATUSES", "ROOT_CAUSES", "AdaptationProposal", "Control", "FailureCase",
           "Funnel", "LearnedComponent"]

# §4.4 root-cause taxonomy, in the spec's order.
ROOT_CAUSES = (
    "misstated_standard", "capability_wall", "wrong_algorithm", "missing_component", "unreachable_component",
    "not_activated", "ineffective_component", "verification_gap", "compromised_validator", "bad_remediation",
    "budget_or_environment", "distribution_shift_or_gaming",
)
FUNNEL_STAGES = ("present", "reachable", "activated", "effective")


@dataclass(frozen=True)
class FailureCase:
    case_id: str
    failing_predicate: str
    independent_witness: str          # who/what observed the failure, independent of the generator
    severity: str
    root_cause: str                   # one of ROOT_CAUSES
    root_cause_hypothesis: str
    alternative_explanations: tuple[str, ...]
    reproducible_case: Any
    applicability_scope: str

    def __post_init__(self) -> None:
        for name in ("case_id", "failing_predicate", "independent_witness", "severity",
                     "root_cause_hypothesis", "applicability_scope"):
            text_of(name, getattr(self, name))
        if self.root_cause not in ROOT_CAUSES:
            raise ValueError(f"root_cause must be one of {', '.join(ROOT_CAUSES)}")
        texts_of("alternative_explanations", list(self.alternative_explanations), required=True)
        if self.reproducible_case in (None, "", {}, []):
            raise ValueError("reproducible_case is required")


@dataclass(frozen=True)
class LearnedComponent:
    component_id: str
    version: str
    implementation_hash: str
    activation_trigger: str
    defect_signature: str
    change_scope: str
    test_provenance: str
    rollback_target: str
    required_permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    known_limitations: tuple[str, ...] = ()
    contraindications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("component_id", "version", "activation_trigger", "defect_signature", "change_scope",
                     "test_provenance", "rollback_target"):
            text_of(name, getattr(self, name))
        hex64_of("implementation_hash", self.implementation_hash)
        if not self.known_limitations:
            raise ValueError("known_limitations is required: a learned component with none is unexamined")


@dataclass
class Funnel:
    """§4.5 present → reachable → activated → effective, counted from evidence references.

    A later stage cannot exceed an earlier one, and ``effective`` needs a counterfactual reference (a
    controlled comparison or fault injection that changed an outcome), never just a log line.
    """

    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FUNNEL_STAGES, 0))
    evidence: dict[str, list[str]] = field(default_factory=lambda: {s: [] for s in FUNNEL_STAGES})

    def record(self, stage: str, evidence_ref: str, *, counterfactual_ref: str = "") -> None:
        if stage not in FUNNEL_STAGES:
            raise ValueError(f"stage must be one of {', '.join(FUNNEL_STAGES)}")
        text_of("evidence_ref", evidence_ref)
        index = FUNNEL_STAGES.index(stage)
        if index and self.counts[stage] + 1 > self.counts[FUNNEL_STAGES[index - 1]]:
            raise ValueError(f"{stage} cannot exceed {FUNNEL_STAGES[index - 1]}")
        if stage == "effective" and not counterfactual_ref.strip():
            raise ValueError("effective requires a counterfactual_ref, not an invocation log")
        self.counts[stage] += 1
        self.evidence[stage].append(f"{evidence_ref}|{counterfactual_ref}" if counterfactual_ref else evidence_ref)
