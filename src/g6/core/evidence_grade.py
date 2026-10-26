# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""
evidence_grade — mvp.core.evidence_grade

The one canonical evidence-grade the D1 unification lifts across every gate, plus
the crosswalk to the vocabularies that already exist. Today the trust law is
correct in exactly one place (``benchmarking/claim_ledger.py`` — the hard-oracle
gate) and is absent or degraded in the others (the product envelope uses a
method-NAME denylist; ``risk_policy`` takes its E-grade as a caller-supplied input
it never derives; the invoke gate is a static name denylist). This module is the
shared typed substrate those gates would import so they stop re-encoding trust
three different ways. See
``docs/superpowers/specs/2026-07-12-d1-evidence-grade-unification-implementation.md``.

STANDALONE / ADDITIVE. Nothing consumes this yet — landing it changes no behavior.
It ranks nothing and gates nothing; it makes the design's crosswalk concrete and
testable. Wiring it into the owned gates (job_reliability_orchestrator,
risk_policy) is founder-sign-off + S-P0-1-owner work, not done here.

The three grades are the DEFINE/TRANSFER/REFLECT lattice (design §4a):

* ``kernel``     — an executed *independent* check (machine proof / executed test /
  external attestation) that names a **resolvable artifact**. The only grade that
  warrants ``verified``. ≅ ledger ``HARD_ORACLES`` ≅ E3.
* ``instrument`` — a separated judge (not the generator, not a hard oracle).
  ≅ ledger ``separated_judgment`` ≅ E2. Warrants at most ``qualified-draft``.
* ``model``      — model self-report / unverified. ≅ ledger ``llm_self`` ≅ E0.
  Warrants nothing on its own.

Anti-gaming (design §4a rider 2 — a certificate grades trust only if *inhabited*):
grade is assigned by what a certificate *asserts*, never by a method's *name*. A
method called "formal" that merely checks ``output is not None`` earns ``model``.
``kernel`` requires BOTH an independent checker AND a resolvable artifact a third
party can re-check; without the artifact even a kernel-class method is demoted.
"""
from __future__ import annotations

from typing import Literal

EvidenceGrade = Literal["kernel", "instrument", "model"]

# Mirrors mvp.core.reliability_envelope.ReliabilityLabel (kept local to stay
# standalone). The hyphen spelling is the canonical envelope form; the underscore
# CompletionState enum (verified_ready/…) is normalized to it by
# normalize_completion_label.
CompletionLabel = Literal["verified", "qualified-draft", "blocked-escalated"]

_GRADE_RANK: dict[EvidenceGrade, int] = {"model": 0, "instrument": 1, "kernel": 2}

# Ledger oracle-kind → canonical grade (ORACLE_RANK / HARD_ORACLES, claim_ledger.py
# 45-52). Unknown oracle kinds fail closed to model.
_ORACLE_GRADE: dict[str, EvidenceGrade] = {
    "formal": "kernel",
    "executable": "kernel",
    "external_attestation": "kernel",
    "separated_judgment": "instrument",
    "llm_self": "model",
}

# risk_policy E-level ↔ grade (risk_policy.py _GRADE_ORDER). E1 is treated as
# model (below the instrument bar); E2 = instrument; E3 = kernel.
_E_GRADE: dict[str, EvidenceGrade] = {"E0": "model", "E1": "model", "E2": "instrument", "E3": "kernel"}
_GRADE_E: dict[EvidenceGrade, str] = {"model": "E0", "instrument": "E2", "kernel": "E3"}

# Methods whose grade is a genuine JUDGMENT CALL — graded model here (fail-closed
# on ambiguity), but plausibly higher, pending founder/owner calibration. Surfaced
# so a reviewer can find and re-grade them. Each carries a note in the comment
# beside its _METHOD_CEILING entry. An entry may be either still-pending OR
# reviewed-and-confirmed-model (a founder ruling that kept it model); the comment
# says which. Re-graded methods are REMOVED from this set: invariant_tier_t3 →
# kernel (A17, 2026-07-14); and the A16 batch (2026-07-15) —
# git_op_audited_and_state_consistent, deploy_run_confirmation_probe,
# live_rq_operation_or_typed_failure_envelope,
# server_reachable_version_matched_workflow_checkpoint_present → kernel, and
# real_port_release_gate → instrument — all no longer flagged.
FLAGGED_METHODS: frozenset[str] = frozenset(
    {
        # PRODUCTION — REVIEWED 2026-07-14 (A17): soft similarity proxy, no floor
        # actually enforced, output wrapped untrusted → accept-demote, stays model.
        "top_k_results_meet_relevance_floor",
        # REVIEWED 2026-07-15 (A16) — evidence-grounded, confirmed model (all prod=0):
        "codex_review_verified",  # A16: one-time cross-vendor scaffold disclosure, no per-run attestation; owners still require a per-run reviewer_signature → disclosure-only
        "legal_reviewed_rule_run_or_validated_evidence",  # A16: OR escape hatch; block never rises above qualified-draft (legal_reviewed=False)
        "posterior_diagnostics_pass_or_marked_not_required",  # escape hatch ("or marked not required")
        "static_structural_checks",  # A16: same AST machinery as invariant_tier_t3, but the t3→kernel promotion was kept deliberately narrow; the broad structural-not-semantic method stays model
        "deterministic",  # too vague to grade
        "external_geo_api_response_validated",  # A16: "validation" is a shallow `if "error" in raw` presence check, not an independent oracle
        "paid_call_refusal_or_credentialed_provider_smoke_passes",  # A16: refusal disclosure + env-var-presence "smoke", no executed check
        # PRODUCTION — REVIEWED 2026-07-14 (A17): capacity>=guessed-difficulty
        # heuristic, self-check (grades its own capacity), no output-soundness
        # certificate → accept-demote, stays model.
        "solvability_gate_passes",
        "render:dimensions_match;import_export:format_roundtrip;session:no_orphan_processes;"
        "script_exec:sandboxed_no_traversal;persistence:audit_trail",  # multi-gate
    }
)

# verification_method → the grade CEILING the method can reach given what it
# asserts. The shallow/self-grading methods cap at model even when their name
# suggests otherwise; only genuinely executed independent oracles have a kernel
# ceiling. Unknown methods fail closed to model. The non-built-in entries below are
# a 2026-07-12 snapshot classification of the live component fleet (~55 declared
# methods), conservative + fail-closed; FLAGGED_METHODS marks the judgment calls.
_METHOD_CEILING: dict[str, EvidenceGrade] = {
    # -- placeholders / self-report / disclosure -> model --
    "": "model",
    "permissive_default": "model",
    "tier1_review_pending": "model",  # literally "review pending"
    "acknowledgment_only": "model",
    "qualified-draft": "model",
    "qualified_draft": "model",
    "qualified_draft_completion_envelope": "model",
    "qualified_draft_mock_envelope": "model",
    "self_check_of_envelope_integrity": "model",
    "reliability_pipeline_self_check": "model",  # re-reads a self-reported report
    "vlm_output_is_unverified_proxy_review_required": "model",  # explicitly "unverified proxy"
    "renderer_capability_declared_or_text_fallback_disclosed": "model",  # declared/disclosed only
    "autosklearn_estimator_or_explicit_fallback_disclosure": "model",  # disclosure, no check
    # built-in shallow checks (core/verification/methods.py 13-66)
    "acceptance_criteria_check": "model",  # output is not None
    "evidence_coverage_check": "model",  # a field is present
    "csf": "model",
    "csf_verification": "model",  # a flag is absent
    "formal_methods": "model",  # asserts only output is not None — NOT kernel by name
    "formal_methods_verification": "model",
    # -- separated human expert review -> instrument --
    "separated_judgment": "instrument",
    "domain_expert_review": "instrument",
    "external_evidence_or_human_review": "instrument",
    "lawyer_review": "instrument",
    "clinician_review": "instrument",
    "operator_review": "instrument",
    # A16 (2026-07-15): executed ship-gate over REAL backends naming a gate-result
    # artifact, but the real-lane evidence rests partly on LLM judges (separated
    # judgment) → capped at instrument, not kernel. prod=0.
    "real_port_release_gate": "instrument",
    # -- clearly executed independent oracles -> kernel ceiling --
    # (still earn kernel only WITH a resolvable artifact at runtime)
    "executed_test": "kernel",
    "machine_proof": "kernel",
    "external_attestation": "kernel",
    "unit_tests_only": "kernel",
    "direct_tests": "kernel",
    "subprocess_test_verification": "kernel",
    "deterministic_executed": "kernel",
    "deterministic_unit_and_contract_tests": "kernel",
    "held_out_accuracy_above_baseline": "kernel",  # frozen-anchor holdout eval
    "holdout_evaluation_passes_metric_target_and_backend_disclosed": "kernel",
    "returned_solution_satisfies_constraints": "kernel",
    "output_image_dimensions_match_request": "kernel",
    "audio_output_matches_format_and_duration_within_tolerance": "kernel",
    "generated_project_passes_manage_check_and_makemigrations_dry_run": "kernel",
    "unit_plus_injection_scan": "kernel",
    "component_pytest_and_honest_state_gate": "kernel",
    "component_pytest_and_live_run_confirmation": "kernel",
    "contract_and_component_tests": "kernel",
    "focused_contract_tests_and_solver_evidence_gate": "kernel",
    "component_tests_and_runner_gate_summary": "kernel",
    "task_tracker_lifecycle_and_persistence_tests": "kernel",
    "domain_testing": "kernel",
    "all_safety_layers_pass_with_evidence_for_mutating_ops": "kernel",
    "validated_proof_artifact_or_degraded_envelope": "kernel",  # proof artifact + honest fallback
    "source_sla_and_faithfulness_tests": "kernel",
    "security_gateway_pipeline_stage_tests": "kernel",  # PRODUCTION
    # PRODUCTION — founder-ruled 2026-07-14 (A17): substantiated by the independent
    # mvp.invariants AST/static verifiers (verifiers.py; SecurityVerifier.verify does
    # ast.parse/walk for eval/exec/subprocess/SQL), which emit a VerificationResult
    # carrying a resolvable details artifact; backed by tests/mvp/autofix/test_invariants.py.
    # T3 requires every blocking category to verify. An independent executed check
    # naming a resolvable artifact = kernel by the module's own law. The content-cost
    # rule still self-demotes any call lacking independence/artifact at runtime, so
    # this ceiling is safe. (NB: _make_result treats an empty error-severity detail
    # set as vacuously passed, so the kernel warrant rests on the verifiers actually
    # emitting details — which the real T3 verifiers do — not on a non-vacuity guard.)
    "invariant_tier_t3": "kernel",  # PRODUCTION
    "executed_llm_synthesis_nontemplate_syntax_valid_else_degraded": "kernel",
    "schema_check_passes": "kernel",
    # Operational probes — founder-ruled 2026-07-15 (A16): each runs an INDEPENDENT
    # executed check naming a resolvable runtime artifact and fail-closes honestly.
    # Kernel CEILING only; the content-cost rule still self-demotes any call lacking
    # independence/artifact at runtime (all prod=0 today, so zero production impact).
    "git_op_audited_and_state_consistent": "kernel",  # re-audits real git HEAD/SHA (durable, third-party re-checkable ref)
    "deploy_run_confirmation_probe": "kernel",  # `podman inspect` running+healthy on the started container
    "live_rq_operation_or_typed_failure_envelope": "kernel",  # live Redis enqueue + status readback; honest typed-failure disjunction (cf. validated_proof_artifact_or_degraded_envelope)
    "server_reachable_version_matched_workflow_checkpoint_present": "kernel",  # live HTTP readiness probe → server_info artifact
    # -- judgment calls: fail-closed to model, see FLAGGED_METHODS --
    "top_k_results_meet_relevance_floor": "model",  # PRODUCTION — A17 accept-demote (stays model)
    "codex_review_verified": "model",  # A16 reviewed — disclosure-only, stays model
    "legal_reviewed_rule_run_or_validated_evidence": "model",
    "posterior_diagnostics_pass_or_marked_not_required": "model",
    "static_structural_checks": "model",  # A16 reviewed — t3 promotion kept narrow, stays model
    "deterministic": "model",
    "external_geo_api_response_validated": "model",
    "paid_call_refusal_or_credentialed_provider_smoke_passes": "model",
    "solvability_gate_passes": "model",  # PRODUCTION — A17 accept-demote (stays model)
    # A16 (2026-07-15): the 4 operational probes formerly here are founder-ruled
    # kernel and real_port_release_gate → instrument — moved to the blocks above.
    # (invariant_tier_t3 similarly kernel per A17.)
    "render:dimensions_match;import_export:format_roundtrip;session:no_orphan_processes;"
    "script_exec:sandboxed_no_traversal;persistence:audit_trail": "model",
}


def grade_rank(grade: EvidenceGrade) -> int:
    """Ordinal for comparison: ``model`` 0 < ``instrument`` 1 < ``kernel`` 2."""
    return _GRADE_RANK[grade]


def warrants_verified(grade: EvidenceGrade) -> bool:
    """Only ``kernel`` warrants the ``verified`` completion state."""
    return grade == "kernel"


def grade_from_oracle_kind(oracle_kind: str) -> EvidenceGrade:
    """Map a ledger ``oracle_kind`` to the canonical grade (unknown → model)."""
    return _ORACLE_GRADE.get(str(oracle_kind or "").strip(), "model")


def grade_from_e_level(e_level: str) -> EvidenceGrade:
    """Map a ``risk_policy`` E-level (E0–E3) to the canonical grade (unknown → model)."""
    return _E_GRADE.get(str(e_level or "").strip().upper(), "model")


def e_level_from_grade(grade: EvidenceGrade) -> str:
    """Map the canonical grade to the E-level ``gate_decision`` expects."""
    return _GRADE_E[grade]


def grade_from_verification_method(
    method: str,
    *,
    independent: bool = False,
    has_resolvable_artifact: bool = False,
) -> EvidenceGrade:
    """Grade a ``verification_method`` by what it asserts, applying the content-cost rule.

    The method's registered ceiling (``_METHOD_CEILING``; unknown → ``model``,
    fail-closed) bounds the grade. A ``kernel``-ceiling method realizes ``kernel``
    ONLY when the check was independent AND names a resolvable artifact; with
    independence but no artifact it is demoted to ``instrument``; with neither, to
    ``model``. Non-kernel ceilings are returned as-is (their trust does not depend
    on an artifact they never produce).
    """
    ceiling = _METHOD_CEILING.get(str(method or "").strip(), "model")
    if ceiling == "kernel":
        if independent and has_resolvable_artifact:
            return "kernel"
        if independent:
            return "instrument"
        return "model"
    return ceiling


def max_completion_label(grade: EvidenceGrade) -> CompletionLabel:
    """The strongest completion state this grade warrants.

    ``kernel`` → ``verified``; ``instrument`` / ``model`` → ``qualified-draft``.
    (A grade never *forces* a state — failures/blocks still demote below this
    ceiling; this is the ceiling the grade permits.)
    """
    return "verified" if grade == "kernel" else "qualified-draft"


def normalize_completion_label(state: str) -> str:
    """Normalize the underscore ``CompletionState`` enum to the hyphen envelope form."""
    return {
        "verified_ready": "verified",
        "qualified_draft": "qualified-draft",
        "blocked_escalated": "blocked-escalated",
    }.get(str(state or "").strip(), str(state or "").strip())


__all__ = [
    "EvidenceGrade",
    "CompletionLabel",
    "FLAGGED_METHODS",
    "grade_rank",
    "warrants_verified",
    "grade_from_oracle_kind",
    "grade_from_e_level",
    "e_level_from_grade",
    "grade_from_verification_method",
    "max_completion_label",
    "normalize_completion_label",
]
