# SPDX-License-Identifier: Apache-2.0
"""Compose cognitive method learning, G6's typed solver router and assurance.

Planning never runs a tool. Approved execution goes through G6's existing
registry, which retains its own controls. A separately owned evaluator must
then check the produced bytes against frozen professional criteria. Neither
step upgrades the result to G6's `verified` label.

What runs is what was compiled: each selected model is a deep copy pinned by its digest, the plan's steps are pinned
too, and execution refuses when either no longer matches. What an assessment credits is what this orchestrator ran: its
own record of each run, step by step through G6's door, never a caller's run result or label.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from g6.core.reliability_envelope import warning_card

from .adaptation import detect_failure_modes
from .assurance import (DomainStandard, EvidenceArtifact,
                        FrozenVerifier, InProcessAssuranceEvaluator,
                        ProfessionalAttestation, canonical_json, sha256)
from .codes import (ASSURANCE_BLOCKED, COMPILE_BLOCKED, EXECUTION_NOT_APPROVED, NOT_PROMOTED, PLAN_TAMPERED, refusal,
                    settled)
from .epistemic import CrossMethodConflictGate, ScopedAssertion
from .metacognition import (MethodVariant, MetacognitiveMethodSelector,
                            ProblemSignature, SelectionDecision)
from .mixed_methods import (ENGINE_SPECS, ClaimIR, MethodPlan, MethodRouter, MixedMethodExecutor, step_digest)


def _plan_digest(plan: MethodPlan) -> str:
    return sha256(canonical_json([asdict(step) for step in plan.steps]))


@dataclass(frozen=True)
class CompiledResearch:
    original_claim: ClaimIR
    executable_plan: MethodPlan
    decision: SelectionDecision
    standard_digest: str
    context: ProblemSignature
    selected_model_hashes: Mapping[str, str]
    refused: Mapping[str, Any] | None  # the refusal (codes.refusal) when a mandatory method is not fully planned
    plan_digest: str = ""               # the steps as compiled; execution refuses when they no longer match

    @property
    def completion_state(self) -> str:
        return settled(self.refused)["completion_state"]

    def changed_since_compiled(self) -> str:
        """Why the plan no longer matches what was compiled and pinned (a selected model's digest, the steps'
        digest), or ``""`` when it still does."""
        models = {need: model for claim in self.executable_plan.claims for need, model in claim.models.items()}
        for row in self.decision.ordered_variants:
            pin = self.selected_model_hashes.get(row["variant_id"])
            if pin is None:
                continue
            try:
                digest = sha256(canonical_json(dict(models[row["method"]])))
            except (KeyError, TypeError, ValueError):
                digest = ""
            if digest != pin:
                return f"the compiled {row['method']} model no longer matches the digest pinned at compilation"
        try:
            steps = _plan_digest(self.executable_plan)
        except (TypeError, ValueError):
            steps = ""
        return "" if steps and steps == self.plan_digest else "the compiled steps no longer match their pin"

    def to_dict(self) -> dict[str, Any]:
        return settled(self.refused, claim_id=self.original_claim.id,
                       risk_tier=self.context.risk_tier,
                       standard_digest=self.standard_digest,
                       metacognitive_selection=self.decision.to_dict(),
                       executable_plan=self.executable_plan.to_dict(),
                       selected_model_hashes=dict(self.selected_model_hashes),
                       activation="none; planned only")


class CognitiveAssuranceOrchestrator:
    def __init__(self, selector: MetacognitiveMethodSelector, *, registry: Any = None):
        self.selector, self.registry = selector, registry
        #: This orchestrator's own record of each run it executed: run id -> (the plan's pinned digest, the run's
        #: bytes). An assessment credits a run only from here. It is in memory: after a restart nothing is credited.
        self._runs: dict[str, tuple[str, bytes]] = {}

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
            # input to the canonical router; no arbitrary generated code. It is
            # a deep copy, so the caller's dictionaries cannot change what runs.
            try:
                model = json.loads(canonical_json(dict(source)))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"the {method} model of {vid!r} is not declarative JSON data") from exc
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
        gaps = ([f"no selected variant for {', '.join(selection.missing)}"] if selection.missing else []) + (
            [f"no typed model for {', '.join(m['method'] for m in plan.missing)}"] if plan.missing else [])
        return CompiledResearch(claim, plan, selection, standard.digest, context, MappingProxyType(model_hashes),
                                refusal(COMPILE_BLOCKED, "; ".join(gaps)) if gaps else None, _plan_digest(plan))

    def execute(self, compiled: CompiledResearch, *, approved: bool = False,
                allow: Iterable[str] = ()) -> dict[str, Any]:
        """Run the approved plan through G6's checked door; ``allow`` is the person's allow for a sensitive op."""
        if not approved or self.registry is None or compiled.completion_state != "qualified-draft":
            return refusal(EXECUTION_NOT_APPROVED, "approved, fully specified plan and connected G6 registry required")
        changed = compiled.changed_since_compiled()
        if changed:  # checked here, then the executor runs the copies it validates
            return refusal(PLAN_TAMPERED, changed, steps=[], method_execution_coverage=0.0)
        # Ensure the approved plan is the same canonical plan before touching
        # any engine: MixedMethodExecutor re-derives the steps and refuses edits.
        out = MixedMethodExecutor(self.registry, allow=allow).execute(compiled.executable_plan, approved=True)
        result = {**out, "selection_policy_digest": compiled.decision.policy_digest,
                  "standard_digest": compiled.standard_digest,
                  "claim_id": compiled.original_claim.id}
        if isinstance(result.get("run_id"), str):
            self._runs[result["run_id"]] = (compiled.plan_digest, canonical_json(result))
        return result

    def _executed(self, compiled: CompiledResearch, run_result: Mapping[str, Any]) -> dict[str, Any] | None:
        """This orchestrator's own record of the run ``run_result`` names, when it ran this compiled plan and the
        caller's copy is that record unchanged; else None."""
        record = self._runs.get(str(run_result.get("run_id", ""))) if isinstance(run_result, Mapping) else None
        if record is None or record[0] != compiled.plan_digest or compiled.changed_since_compiled():
            return None
        try:
            if canonical_json(dict(run_result)) != record[1]:
                return None
        except (TypeError, ValueError):
            return None
        return json.loads(record[1])

    @staticmethod
    def _every_step_ran(compiled: CompiledResearch, executed: Mapping[str, Any]) -> bool:
        """Each compiled step ran through G6's door as compiled (the same step digest) and came back qualified."""
        ran = {step.get("id"): step for step in executed.get("steps", []) if isinstance(step, Mapping)}
        return (executed.get("completion_state") == "qualified-draft" and bool(compiled.executable_plan.steps)
                and all(ran.get(step.id, {}).get("completion_state") == "qualified-draft"
                        and ran[step.id].get("step_digest") == step_digest(step)
                        for step in compiled.executable_plan.steps))

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
               run_result: Mapping[str, Any], evaluator: InProcessAssuranceEvaluator,
               verifiers: Mapping[str, FrozenVerifier],
               artifacts: Mapping[str, EvidenceArtifact], documentation: Mapping[str, str],
               candidate_actor: str,
               attestation: ProfessionalAttestation | None = None,
               scoped_assertions: Sequence[ScopedAssertion] = (),
               epistemic_gate: CrossMethodConflictGate | None = None) -> dict[str, Any]:
        if standard.digest != compiled.standard_digest:
            raise ValueError("standard changed since the plan was compiled")
        # The compiled plan is the caller's object: what it covers is read again here, not taken from compile().
        uncovered = sorted({m for criterion in standard.criteria for m in criterion.required_methods}
                           - {step.method for step in compiled.executable_plan.steps})
        for item in artifacts.values():
            if item.claim_id != compiled.original_claim.id:
                raise ValueError("evidence belongs to a different claim")
        dossier = evaluator.evaluate(standard=standard, artifacts=artifacts, verifiers=verifiers,
                                     documentation=documentation, candidate_actor=candidate_actor,
                                     attestation=attestation)
        # Whether the compiled steps ran is read from this orchestrator's own record, never from the caller's label.
        executed = self._executed(compiled, run_result)
        backend_ok = executed is not None and self._every_step_ran(compiled, executed)
        # A cross-engine disagreement is an adjudication obligation, not a
        # scalar to average away. Without an outside admission gate, submitted
        # assertions block rather than lending support to the dossier.
        conflict_report = None
        if scoped_assertions:
            conflict_report = (epistemic_gate or CrossMethodConflictGate()).reconcile(scoped_assertions)
        epistemic_ok = conflict_report is None or conflict_report["completion_state"] == "qualified-draft"
        faults = detect_failure_modes(dossier)
        passed = (backend_ok and not uncovered and epistemic_ok
                  and dossier.payload["completion_state"] == "qualified-draft")
        warning = "Independent external custody and G6 door replay are NOT established here."
        # The state is completion_state alone (never a second state field); the card says why, with a stable code.
        why =(([f"the compiled plan has no step for the standard's mandatory {', '.join(uncovered)}"] if uncovered
                else [])
               + (["no run of this compiled plan by this orchestrator matches the run result"] if executed is None
                  else ["a compiled step did not run through G6's door as compiled"] if not backend_ok else [])
               + (["the cross-method assertions were not reconciled"] if not epistemic_ok else [])
               + list(dossier.payload["blockers"]))
        fields = {
            "evidence": [{"source": f"dossier:{dossier.content_sha256}",
                          "summary": f"assurance dossier for claim {compiled.original_claim.id}"}],
            "request_id": uuid.uuid4().hex[:12],
            # G6's own record of the run, after the door; a caller's run result is never echoed
            "run_id": executed["run_id"] if executed else "",
            "method_results": executed or {},
            "metacognitive_selection": compiled.decision.to_dict(),
            "dossier": dossier.to_dict(),
            "failure_mode_findings": [vars(x) for x in faults],
            "epistemic_reconciliation": conflict_report,
            "next_action": ("submit frozen artifact/check bundle to independent G6 promotion door"
                            if passed else "address failed method/acceptance checks; do not promote"),
            "warning": warning,
        }
        if not passed:
            return refusal(ASSURANCE_BLOCKED, "; ".join(why), **fields)
        return settled(None, warning_card=warning_card(NOT_PROMOTED, "the checks this process ran passed. " + warning),
                       **fields)


__all__ = ["CompiledResearch", "CognitiveAssuranceOrchestrator"]
