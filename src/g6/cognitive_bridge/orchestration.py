# SPDX-License-Identifier: Apache-2.0
"""Compose cognitive method learning, G6's typed solver router and assurance.

Planning never runs a tool. Approved execution goes through G6's existing
registry, which retains its own controls. A separately owned evaluator must
then check the produced bytes against frozen professional criteria. Neither
step upgrades the result to G6's `verified` label.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from g6.core.reliability_envelope import warning_card

from .adaptation import detect_failure_modes
from .assurance import (DomainStandard, EvidenceArtifact,
                        FrozenVerifier, IndependentAssuranceEvaluator,
                        ProfessionalAttestation, canonical_json, sha256)
from .codes import ASSURANCE_BLOCKED, EXECUTION_NOT_APPROVED, NOT_PROMOTED, refusal
from .epistemic import CrossMethodConflictGate, ScopedAssertion
from .metacognition import (MethodVariant, MetacognitiveMethodSelector,
                            ProblemSignature, SelectionDecision)
from .mixed_methods import ENGINE_SPECS, ClaimIR, MethodPlan, MethodRouter, MixedMethodExecutor


@dataclass(frozen=True)
class CompiledResearch:
    original_claim: ClaimIR
    executable_plan: MethodPlan
    decision: SelectionDecision
    standard_digest: str
    context: ProblemSignature
    selected_model_hashes: Mapping[str, str]
    completion_state: str

    def to_dict(self) -> dict[str, Any]:
        return {"claim_id": self.original_claim.id,
                "completion_state": self.completion_state,
                "risk_tier": self.context.risk_tier,
                "standard_digest": self.standard_digest,
                "metacognitive_selection": self.decision.to_dict(),
                "executable_plan": self.executable_plan.to_dict(),
                "selected_model_hashes": dict(self.selected_model_hashes),
                "activation": "none; planned only"}


class CognitiveAssuranceOrchestrator:
    def __init__(self, selector: MetacognitiveMethodSelector, *, registry: Any = None):
        self.selector, self.registry = selector, registry

    def compile(self, *, claim: ClaimIR, context: ProblemSignature,
                standard: DomainStandard, variants: Sequence[MethodVariant],
                variant_models: Mapping[str, Mapping[str, Any]],
                max_supplemental: int = 0) -> CompiledResearch:
        if context.risk < standard.risk or context.domain != standard.id:
            raise ValueError("the problem context cannot weaken its standard's risk tier or domain")
        mandatory = {m for criterion in standard.criteria for m in criterion.required_methods}
        requested = set(claim.evidence_needs)
        if not mandatory.issubset(requested):
            raise ValueError("the claim omits reasoning methods mandatory under its frozen standard")
        if not requested:
            raise ValueError("claim requires at least one method")
        # Existing G6 method schemas, not a new collection of arbitrary tools.
        selection = self.selector.select(context, variants, required_methods=sorted(requested),
                                         max_optional=max_supplemental)
        models: dict[str, dict[str, Any]] = {}
        model_hashes: dict[str, str] = {}
        for row in selection.ordered_variants:
            # Supplemental investigations are advisory; they need their own
            # explicitly revised claim and consent before execution. Never
            # replace a required representation with an optional candidate.
            if row["obligation"] != "mandatory":
                continue
            method = row["method"]
            vid = row["variant_id"]
            source = variant_models.get(vid)
            if not isinstance(source, Mapping):
                continue
            # A method's chosen representation becomes declarative, reviewed
            # input to the canonical router; no arbitrary generated code.
            model = dict(source)
            variant_op = row["op"]
            if variant_op != ENGINE_SPECS[method]["op"]:
                if "variant_op" in model and model["variant_op"] != variant_op:
                    raise ValueError("candidate model operation disagrees with metacognitive selection")
                model["variant_op"] = variant_op
            elif "variant_op" in model and model["variant_op"] != variant_op:
                raise ValueError("candidate requested nonselected operation")
            models[method] = model
            model_hashes[vid] = sha256(canonical_json(model))
        # The original claim may include a model as a fallback only if the
        # selected candidate explicitly has the same model digest. Otherwise
        # we refuse; model substitution cannot happen under the hood.
        new_claim = ClaimIR(id=claim.id, statement=claim.statement,
                            evidence_needs=tuple(claim.evidence_needs), models=models,
                            source_refs=claim.source_refs, origin=claim.origin)
        plan = MethodRouter().plan([new_claim])
        missing = bool(selection.missing or plan.missing)
        return CompiledResearch(claim, plan, selection, standard.digest,
                                context, model_hashes,
                                "blocked-escalated" if missing else "qualified-draft")

    def execute(self, compiled: CompiledResearch, *, approved: bool = False,
                allow: Iterable[str] = ()) -> dict[str, Any]:
        """Run the approved plan through G6's checked door; ``allow`` is the person's allow for a sensitive op."""
        if not approved or self.registry is None or compiled.completion_state != "qualified-draft":
            return refusal(EXECUTION_NOT_APPROVED, "approved, fully specified plan and connected G6 registry required")
        # Ensure the approved plan is the same canonical plan before touching
        # any engine: MixedMethodExecutor re-derives the steps and refuses edits.
        out = MixedMethodExecutor(self.registry, allow=allow).execute(compiled.executable_plan, approved=True)
        return {**out, "selection_policy_digest": compiled.decision.policy_digest,
                "standard_digest": compiled.standard_digest,
                "claim_id": compiled.original_claim.id}

    @staticmethod
    def execution_artifact(run_result: Mapping[str, Any], *, actor: str,
                           claim_id: str) -> EvidenceArtifact:
        if not actor or not claim_id:
            raise ValueError("actor and claim must be supplied")
        payload = canonical_json(dict(run_result))
        return EvidenceArtifact(id=f"execution:{claim_id}", content=payload, kind="report",
                                claimed_by=actor, source_ref=f"g6:execution:{sha256(payload)}",
                                claim_id=claim_id)

    def assess(self, compiled: CompiledResearch, *, standard: DomainStandard,
               run_result: Mapping[str, Any], evaluator: IndependentAssuranceEvaluator,
               verifiers: Mapping[str, FrozenVerifier],
               artifacts: Mapping[str, EvidenceArtifact], documentation: Mapping[str, str],
               candidate_actor: str,
               attestation: ProfessionalAttestation | None = None,
               scoped_assertions: Sequence[ScopedAssertion] = (),
               epistemic_gate: CrossMethodConflictGate | None = None) -> dict[str, Any]:
        if standard.digest != compiled.standard_digest:
            raise ValueError("standard changed since the plan was compiled")
        for item in artifacts.values():
            if item.claim_id != compiled.original_claim.id:
                raise ValueError("evidence belongs to a different claim")
        dossier = evaluator.evaluate(standard=standard, artifacts=artifacts, verifiers=verifiers,
                                     documentation=documentation, candidate_actor=candidate_actor,
                                     attestation=attestation)
        backend_ok = run_result.get("completion_state") == "qualified-draft"
        # A cross-engine disagreement is an adjudication obligation, not a
        # scalar to average away. Without an outside admission gate, submitted
        # assertions block rather than lending support to the dossier.
        conflict_report = None
        if scoped_assertions:
            conflict_report = (epistemic_gate or CrossMethodConflictGate()).reconcile(scoped_assertions)
        epistemic_ok = conflict_report is None or conflict_report["completion_state"] == "qualified-draft"
        faults = detect_failure_modes(dossier)
        passed = backend_ok and epistemic_ok and dossier.payload["completion_state"] == "qualified-draft"
        warning = "Independent external custody and G6 door replay are NOT established here."
        # The state is completion_state alone (never a second state field); the card says why, with a stable code.
        why = ((["a method step was blocked"] if not backend_ok else [])
               + (["the cross-method assertions were not reconciled"] if not epistemic_ok else [])
               + list(dossier.payload["blockers"]))
        card = (warning_card(NOT_PROMOTED, "the checks this process ran passed. " + warning) if passed else
                warning_card(ASSURANCE_BLOCKED, "; ".join(why)[:400]))
        return {
            "completion_state": "qualified-draft" if passed else "blocked-escalated",
            "warning_card": card,
            "evidence": [{"source": f"dossier:{dossier.content_sha256}",
                          "summary": f"assurance dossier for claim {compiled.original_claim.id}"}],
            "request_id": uuid.uuid4().hex[:12],
            "run_id": str(run_result.get("run_id", "")),
            "method_results": dict(run_result),
            "metacognitive_selection": compiled.decision.to_dict(),
            "dossier": dossier.to_dict(),
            "failure_mode_findings": [vars(x) for x in faults],
            "epistemic_reconciliation": conflict_report,
            "next_action": ("submit frozen artifact/check bundle to independent G6 promotion door"
                            if passed else "address failed method/acceptance checks; do not promote"),
            "warning": warning,
        }


__all__ = ["CompiledResearch", "CognitiveAssuranceOrchestrator"]
