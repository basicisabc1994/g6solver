# SPDX-License-Identifier: Apache-2.0
"""Compose cognitive method learning, G6's typed solver router and assurance.

Planning never runs a tool. Approved execution goes through G6's existing
registry, which retains its own controls. A separately owned evaluator must
then check the produced bytes against frozen professional criteria. Neither
step upgrades the result to G6's `verified` label.

What runs is what was compiled: each selected model is a deep copy pinned by its digest, the plan's steps are pinned
too, and execution refuses when either no longer matches. A compilation's state is its selection's verdict and its
plan's, decided in one place (:func:`compilation_gaps`) that execution and assessment both read; its ``refused`` field
is exactly the refusal that place gives, or the compilation is not made. What an assessment credits is what this orchestrator ran: its
own record of each run, step by step through G6's door, never a caller's run result or label.

The orchestrator honours only records it, its selector, its evaluator and its gate made: ``compile`` records the
digest of each compilation it returns (``snapshot.issue``, over every field, its context included) and takes a
selection only when its selector issued it; ``execute`` and ``assess`` take a compilation only when this orchestrator
issued it, and ``assess`` credits a dossier or a reconciliation only when the evaluator or gate it called issued it
(``snapshot.issued``). Everything a caller hands in is taken as plain data where it enters (``snapshot``).

Credit binds its subject. A dossier is credited only for the evidence submitted to that assessment, and a
reconciliation only for the assertions submitted to it, each by the digest it names; a selection is taken only when
each row is a variant given, eligible under the context given, and the rows together fit its budget. What an evaluator,
a gate or a selector answers carries only its format's fields and claims no state at any depth (``codes.only_fields``
and ``codes.claims``; a nested result is in the makers' format), so what this module republishes from them is what was
checked (``tests/cognitive_bridge/test_credit_inventory.py``, ``test_claim_inventory.py``).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any, Iterable, Mapping, Sequence

from .adaptation import detect_failure_modes
from .assurance import (CandidateDossier, DomainStandard, EvidenceArtifact,
                        FrozenVerifier, InProcessAssuranceEvaluator,
                        ProfessionalAttestation, canonical_json, evidence_sha256, sha256)
from .codes import (ASSURANCE_BLOCKED, COMPILE_BLOCKED, EXECUTION_NOT_APPROVED, NOT_PROMOTED, PLAN_TAMPERED, STATES,
                    affirmed, refusal, settled, unclaimed)
from .epistemic import CrossMethodConflictGate, ScopedAssertion, assertions_sha256, checked_report, findings_state
from .metacognition import (MethodVariant, MetacognitiveMethodSelector,
                            ProblemSignature, SelectionDecision, checked_selection)
from .mixed_methods import (ENGINE_SPECS, ClaimIR, MethodPlan, MethodRouter, MixedMethodExecutor, step_digest)
from .snapshot import fresh, issue, issued, number, plain, record, returned, snapshot, text


def _dossier(dossier: Any, subject: str) -> tuple[CandidateDossier, str]:
    """An evaluator's answer as the assessment uses it (a ``snapshot.returned`` check): an intact CandidateDossier
    (which carries only a dossier's fields, no claim, and the state its blockers give: ``CandidateDossier`` checks
    itself), and why it is not credited (``""`` when it is): it is credited only when it is for the evidence submitted,
    by that evidence's digest (``subject``)."""
    if type(dossier) is not CandidateDossier or not dossier.intact():
        raise ValueError("an evaluator returns an intact CandidateDossier")
    bound = dossier.payload["subject_sha256"] == subject
    return dossier, "" if bound else "the dossier is not for the evidence submitted to this assessment"


def _reconciled(report: Any, subject: str) -> tuple[Mapping[str, Any], str]:
    """A conflict gate's answer as the assessment uses it (a ``snapshot.returned`` check): a report with only a report's
    fields, no claim and one of the bridge's two states (``epistemic.checked_report``), and why it is not credited
    (``""`` when it is): it is credited only when it is for the assertions submitted, by their digest (``subject``), and
    its state is the one its own findings give (``epistemic.findings_state``)."""
    report = checked_report(report)
    if report.get("subject_sha256") != subject:
        return report, "the reconciliation is not for the assertions submitted to this assessment"
    if report["completion_state"] != findings_state(report):
        return report, "the reconciliation's state is not the one its findings give"
    return report, ""


def _plan_digest(plan: MethodPlan) -> str:
    return sha256(canonical_json([asdict(step) for step in plan.steps]))


def compilation_gaps(decision: SelectionDecision, plan: MethodPlan) -> list[str]:
    """Why a compilation of the selection ``decision`` into ``plan`` may not run, or ``[]`` when it may: the one place
    a compilation's state is decided. A selection blocked by its own verdict, or missing a method, blocks it; so does
    an evidence need with no typed model. A compilation's ``refused`` is the refusal these give, and nothing else."""
    blocked = decision.completion_state != STATES[0] or decision.missing
    return ([f"no selected variant for {', '.join(decision.missing) or 'a mandatory method'}"] if blocked else []) + (
        [f"no typed model for {', '.join(m['method'] for m in plan.missing)}"] if plan.missing else [])


def _refusal_for(gaps: Sequence[str]) -> Mapping[str, Any] | None:
    return refusal(COMPILE_BLOCKED, "; ".join(gaps)) if gaps else None


def _claims_digest(claims: Sequence[ClaimIR]) -> str:
    """The identity of the claims a run is for (each one's id, statement, evidence needs, sources and origin)."""
    return sha256(canonical_json([{"id": c.id, "statement": c.statement, "evidence_needs": list(c.evidence_needs),
                                   "source_refs": list(c.source_refs), "origin": c.origin} for c in claims]))


@record
class CompiledResearch:
    original_claim: ClaimIR
    executable_plan: MethodPlan
    decision: SelectionDecision
    standard_digest: str
    context: ProblemSignature
    selected_model_hashes: Mapping[str, str]
    refused: Mapping[str, Any] | None  # the refusal its selection and plan give (compilation_gaps), or None
    plan_digest: str = ""               # the steps as compiled; execution refuses when they no longer match

    def __post_init__(self) -> None:
        # Whoever makes a compilation (compile(), a replace(), a caller), its refusal is the one its selection and plan
        # give, exactly: a blocked selection with no refusal, or a refusal of a qualified one, is not a compilation.
        if type(self.decision) is not SelectionDecision or type(self.executable_plan) is not MethodPlan:
            raise TypeError("a compilation holds a SelectionDecision and a MethodPlan")
        gaps = compilation_gaps(self.decision, self.executable_plan)
        if self.refused != snapshot(_refusal_for(gaps)):
            raise ValueError(f"a compilation is refused exactly as its selection and plan give (compilation_gaps: "
                             f"{'; '.join(gaps) or 'none'}): its refused field is not that refusal")

    @property
    def completion_state(self) -> str:
        """The compilation's state: its selection's verdict and its plan's (:func:`compilation_gaps`), never its own
        ``refused`` field."""
        return STATES[bool(compilation_gaps(self.decision, self.executable_plan))]

    def changed_since_compiled(self, plan: MethodPlan | None = None) -> str:
        """Why ``plan`` (the executable plan, or a frozen copy of it about to run) no longer matches what was compiled
        and pinned (a selected model's digest, the steps' digest), or ``""`` when it still does."""
        plan = snapshot(self.executable_plan if plan is None else plan, where="plan")
        models = {need: model for claim in plan.claims for need, model in claim.models.items()}
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
            steps = _plan_digest(plan)
        except (TypeError, ValueError):
            steps = ""
        return "" if steps and steps == self.plan_digest else "the compiled steps no longer match their pin"

    def to_dict(self) -> dict[str, Any]:
        return settled(_refusal_for(compilation_gaps(self.decision, self.executable_plan)),
                       claim_id=self.original_claim.id,
                       risk_tier=self.context.risk_tier,
                       standard_digest=self.standard_digest,
                       metacognitive_selection=self.decision.to_dict(),
                       executable_plan=self.executable_plan.to_dict(),
                       selected_model_hashes=dict(self.selected_model_hashes),
                       activation="none; planned only")


class CognitiveAssuranceOrchestrator:
    def __init__(self, selector: MetacognitiveMethodSelector, *, registry: Any = None):
        self.selector, self.registry = selector, registry
        #: This orchestrator's own record of each run it executed: run id -> (the plan's pinned digest, the digest of
        #: the claims it ran, the run's bytes). An assessment credits a run only from here, and only for those claims.
        #: It is in memory: after a restart nothing is credited.
        self._runs: dict[str, tuple[str, str, bytes]] = {}

    def compile(self, *, claim: ClaimIR, context: ProblemSignature,
                standard: DomainStandard, variants: Sequence[MethodVariant],
                variant_models: Mapping[str, Mapping[str, Any]],
                max_supplemental: int = 0) -> CompiledResearch:
        claim = snapshot(claim, where="claim")
        context = snapshot(context, where="context")
        standard = snapshot(standard, where="standard")
        variants = snapshot(variants, where="variants")
        variant_models = snapshot(variant_models, where="variant_models")
        max_supplemental = snapshot(max_supplemental, where="max_supplemental")
        number(max_supplemental, where="max_supplemental")
        if context.risk < standard.risk or context.domain != standard.id:
            raise ValueError("the problem context cannot weaken its standard's risk tier or domain")
        mandatory = {m for criterion in standard.criteria for m in criterion.required_methods}
        requested = set(claim.evidence_needs)
        if not mandatory.issubset(requested):
            raise ValueError("the claim omits reasoning methods mandatory under its frozen standard")
        if not requested:
            raise ValueError("claim requires at least one method")
        # Existing G6 method schemas, not a new collection of arbitrary tools.
        selection = returned(self.selector.select(fresh(context), fresh(variants),
                                                  required_methods=fresh(sorted(requested)),
                                                  max_optional=fresh(max_supplemental)), checked_selection, variants,
                             context, sorted(requested))
        if not issued(self.selector, selection):
            raise ValueError("the selection is not one this selector issued: a selection is taken only from the "
                             "selector that made it")
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
            if not isinstance(source, dict):
                continue
            # A method's chosen representation becomes declarative, reviewed
            # input to the canonical router; no arbitrary generated code. It is
            # what the boundary makes of it (a new value), so the caller's
            # dictionaries cannot change what runs.
            try:
                model = plain(source, where=f"the {method} model of {vid!r}")
            except ValueError as exc:
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
        return issue(self, CompiledResearch(claim, plan, selection, standard.digest, context, model_hashes,
                                            _refusal_for(compilation_gaps(selection, plan)), _plan_digest(plan)))

    def execute(self, compiled: CompiledResearch, *, approved: bool = False,
                allow: Iterable[str] = ()) -> dict[str, Any]:
        """Run the approved plan through G6's checked door; ``approved`` is ``True`` and nothing else
        (``codes.affirmed``), and ``allow`` is the person's allow for a sensitive op. Only a compilation this
        orchestrator issued runs (``snapshot.issued``: not one a caller built, edited or another instance made), and
        only when its state (its selection's verdict, :func:`compilation_gaps`) is ``qualified-draft``."""
        try:
            compiled = snapshot(compiled, where="compiled")  # made again: what is checked is what runs
            allow = snapshot(allow, where="allow")
        except ValueError as exc:
            return refusal(EXECUTION_NOT_APPROVED, f"the compilation cannot be taken: {exc}", steps=[],
                           method_execution_coverage=0.0)
        if (not affirmed(approved) or self.registry is None or type(compiled) is not CompiledResearch
                or compiled.completion_state != "qualified-draft"):
            return refusal(EXECUTION_NOT_APPROVED, "approved, fully specified plan and connected G6 registry required")
        plan = compiled.executable_plan
        changed = compiled.changed_since_compiled(plan)
        if changed:
            return refusal(PLAN_TAMPERED, changed, steps=[], method_execution_coverage=0.0)
        if not issued(self, compiled):
            return refusal(EXECUTION_NOT_APPROVED, "the compilation is not one this orchestrator compiled: built by "
                                                   "hand, edited, or made by another instance", steps=[],
                           method_execution_coverage=0.0)
        out = MixedMethodExecutor(self.registry, allow=allow).execute(plan, approved=True)
        # a result built from the executor's, with the compilation's pins beside it: what the boundary made of it
        result = plain(unclaimed({**out, "selection_policy_digest": compiled.decision.policy_digest,
                                  "standard_digest": compiled.standard_digest,
                                  "claim_id": ",".join(c.id for c in plan.claims)}, where="a run"), where="a run")
        if isinstance(result.get("run_id"), str):
            self._runs[result["run_id"]] = (compiled.plan_digest, _claims_digest(plan.claims), canonical_json(result))
        return result

    def _executed(self, compiled: CompiledResearch, run_result: Mapping[str, Any]) -> dict[str, Any] | None:
        """This orchestrator's own record of the run ``run_result`` names, when it ran this compiled plan for the
        claim the compiled research names, and the caller's copy is that record unchanged; else None."""
        run_id = run_result.get("run_id") if isinstance(run_result, dict) else None
        record = self._runs.get(run_id) if type(run_id) is str else None  # a run's id is text; nothing is made text
        if record is None or record[0] != compiled.plan_digest or compiled.changed_since_compiled():
            return None
        try:
            if record[1] != _claims_digest([compiled.original_claim]) or canonical_json(dict(run_result)) != record[2]:
                return None
        except (TypeError, ValueError):
            return None
        return json.loads(record[2])

    @staticmethod
    def _every_step_ran(compiled: CompiledResearch, executed: Mapping[str, Any]) -> bool:
        """Each compiled step ran through G6's door as compiled (the same step digest) and came back qualified."""
        ran = {step.get("id"): step for step in executed.get("steps", []) if isinstance(step, dict)}
        return (executed.get("completion_state") == "qualified-draft" and bool(compiled.executable_plan.steps)
                and all(ran.get(step.id, {}).get("completion_state") == "qualified-draft"
                        and ran[step.id].get("step_digest") == step_digest(step)
                        for step in compiled.executable_plan.steps))

    @staticmethod
    def execution_artifact(run_result: Mapping[str, Any], *, actor: str,
                           claim_id: str) -> EvidenceArtifact:
        run_result = plain(run_result, where="run_result")
        actor = text(actor, where="actor, the producer")
        claim_id = text(claim_id, where="claim_id")
        if not actor or not claim_id:
            raise ValueError("actor and claim must be supplied")
        payload = canonical_json(run_result)
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
        # Everything the assessment checks and then uses is taken where it enters, before any check: plain data,
        # records made again, the producer as text. A compilation that cannot be taken is not one this orchestrator
        # issued, and is refused as one.
        try:
            compiled = snapshot(compiled, where="compiled")
        except ValueError as exc:
            return refusal(ASSURANCE_BLOCKED, f"the compilation cannot be taken: {exc}", evidence=[],
                           request_id=uuid.uuid4().hex[:12], run_id="", method_results={})
        standard = snapshot(standard, where="standard")
        run_result = snapshot(run_result, where="run_result")
        verifiers = snapshot(verifiers, where="verifiers")
        artifacts = snapshot(artifacts, where="artifacts")
        documentation = snapshot(documentation, where="documentation")
        candidate_actor = text(candidate_actor, where="candidate_actor, the producer")
        attestation = snapshot(attestation, where="attestation")
        scoped_assertions = snapshot(scoped_assertions, where="scoped_assertions")
        if type(compiled) is not CompiledResearch:
            raise ValueError("an assessment is of a CompiledResearch: its state is its selection's verdict")
        if not issued(self, compiled):
            return refusal(ASSURANCE_BLOCKED, "the compilation is not one this orchestrator compiled: built by hand, "
                                              "edited, or made by another instance",
                           evidence=[], request_id=uuid.uuid4().hex[:12], run_id="", method_results={})
        gate = epistemic_gate or CrossMethodConflictGate()
        # The compilation's state, read where it is decided (compilation_gaps), as execute reads it.
        blocked_by = compilation_gaps(compiled.decision, compiled.executable_plan)
        if standard.digest != compiled.standard_digest:
            raise ValueError("standard changed since the plan was compiled")
        # The compiled plan is the caller's object: what it covers is read again here, not taken from compile().
        uncovered = sorted({m for criterion in standard.criteria for m in criterion.required_methods}
                           - {step.method for step in compiled.executable_plan.steps})
        for item in artifacts.values():
            if item.claim_id != compiled.original_claim.id:
                raise ValueError("evidence belongs to a different claim")
        # Credit binds its subject: the dossier is credited only for the evidence submitted here, by its digest.
        evidence = evidence_sha256(standard=standard, artifacts=artifacts, verifiers=verifiers,
                                   documentation=documentation, candidate_actor=candidate_actor)
        dossier, dossier_uncredited = returned(
            evaluator.evaluate(standard=fresh(standard), artifacts=fresh(artifacts), verifiers=fresh(verifiers),
                               documentation=fresh(documentation), candidate_actor=fresh(candidate_actor),
                               attestation=fresh(attestation)), _dossier, evidence)
        if not dossier_uncredited and not issued(evaluator, dossier):
            dossier_uncredited = "the dossier is not one this evaluator issued"
        # Whether the compiled steps ran is read from this orchestrator's own record, never from the caller's label.
        executed = self._executed(compiled, run_result)
        backend_ok = executed is not None and self._every_step_ran(compiled, executed)
        # A cross-engine disagreement is an adjudication obligation, not a
        # scalar to average away. Without an outside admission gate, submitted
        # assertions block rather than lending support to the dossier. A report is credited only for the assertions
        # submitted here, by their digest, and only in the state its own findings give.
        conflict_report, report_uncredited = None, ""
        if scoped_assertions:
            conflict_report, report_uncredited = returned(gate.reconcile(fresh(scoped_assertions)), _reconciled,
                                                          assertions_sha256(scoped_assertions))
            if not report_uncredited and not issued(gate, conflict_report):
                report_uncredited = "the reconciliation is not one this gate issued"
        epistemic_ok = conflict_report is None or (not report_uncredited
                                                   and conflict_report["completion_state"] == "qualified-draft")
        faults = detect_failure_modes(dossier)
        # The decision rests on the standard's digest, so it is read again after use: the standard the evaluator
        # evaluated, and the standard now, are the one compiled.
        same_standard = (dossier.payload.get("standard_digest") == compiled.standard_digest
                         and standard.digest == compiled.standard_digest)
        passed = (not blocked_by and backend_ok and not uncovered and epistemic_ok and same_standard
                  and not dossier_uncredited and dossier.payload["completion_state"] == "qualified-draft")
        warning = "Independent external custody and G6 door replay are NOT established here."
        # The state is completion_state alone (never a second state field); the card says why, with a stable code.
        why =(([f"the compilation is blocked: {'; '.join(blocked_by)}"] if blocked_by else [])
               + ([f"the compiled plan has no step for the standard's mandatory {', '.join(uncovered)}"] if uncovered
                  else [])
               + ([] if same_standard else ["the standard evaluated is not the standard compiled"])
               + ([dossier_uncredited] if dossier_uncredited else [])
               + (["no run of this compiled plan by this orchestrator matches the run result"] if executed is None
                  else ["a compiled step did not run through G6's door as compiled"] if not backend_ok else [])
               + ([report_uncredited] if report_uncredited
                  else ["the cross-method assertions were not reconciled"] if not epistemic_ok else [])
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
            "failure_mode_findings": [asdict(x) for x in faults],
            "epistemic_reconciliation": conflict_report,
            "next_action": ("submit frozen artifact/check bundle to independent G6 promotion door"
                            if passed else "address failed method/acceptance checks; do not promote"),
            "warning": warning,
        }
        if not passed:
            return refusal(ASSURANCE_BLOCKED, "; ".join(why), **fields)
        return settled(None, draft_card=(NOT_PROMOTED, "the checks this process ran passed. " + warning), **fields)


__all__ = ["CompiledResearch", "CognitiveAssuranceOrchestrator"]
