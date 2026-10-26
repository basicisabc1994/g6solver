# SPDX-License-Identifier: Apache-2.0

"""Action-gating schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.evidence import EvidenceStance

CompletionState = Literal["verified", "qualified-draft", "blocked-escalated"]
Reversibility = Literal[
    "fully_reversible",
    "reversible_with_cost",
    "partially_reversible",
    "compensatable_only",
    "irreversible",
    "unknown",
]
RiskLevel = Literal["low", "medium", "high", "critical", "unknown"]
ImpactLevel = Literal["none", "low", "medium", "high", "unknown"]
GateDecisionName = Literal[
    "allow",
    "allow_with_notice",
    "require_confirmation",
    "require_additional_information",
    "require_mitigation",
    "require_human_review",
    "block",
]


class FactualEvidenceRef(BaseModel):
    """Evidence supporting a factual precondition; maps to ``core.evidence.EvidenceRecord``.

    Only the fields the deterministic epistemic grader reads are carried here (source
    independence, type, stance, and authority); the full EvidenceRecord is reconstructed
    at grading time. Free-form ``str`` mirrors EvidenceRecord so callers may pass e.g.
    ``model_assertion`` / ``primary`` / ``canonical`` source types. An unknown key, or a stance outside the vocabulary,
    is refused (review B1-1: a misspelt ``stance`` was dropped and the record read as support); a record that gives no
    stance supports the premise it is declared for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_uri_or_artifact_id: str = "unspecified"
    source_type: str = "secondary"
    supports_or_opposes: EvidenceStance = "supports"
    independence_group: str = ""
    authority: str = "unknown"


class FactualPrecondition(BaseModel):
    """A factual claim an action's safety or correctness depends on (Epistemic Integrity EI-090..094).

    Before an external, irreversible, or real-world action executes, each precondition is
    graded by the deterministic epistemic floor. A precondition whose evidence is inadequate
    for the action's risk tier — or that is credibly contested — must not let the action
    proceed (a raw LLM would assert the premise true and act ballistically). This is the
    real raw-LLM delta the action gate enforces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")  # a misspelt ``evidence`` is refused, never dropped

    statement: str
    evidence: list[FactualEvidenceRef] = Field(default_factory=list)


class VerificationContractModel(BaseModel):
    """The closed-loop verification plan a *material* action must carry (G6_Action_Logic.md G6-INV-05).

    Mirrors ``mvp.action_logic.VerificationContract``; the deterministic Dörner floor reads
    it at gate time. A material action whose contract is missing or incomplete is "ballistic"
    (fire-and-forget) and is blocked — the raw-LLM delta (a raw model emits the action with no
    plan to observe, stop, or roll back its effects).
    """

    model_config = ConfigDict(frozen=True)

    expected_result: str = ""
    observation_window: str = ""
    leading_indicators: list[str] = Field(default_factory=list)
    lagging_indicators: list[str] = Field(default_factory=list)
    monitor: str = ""
    verification_time: str = ""
    stop_threshold: str = ""
    rollback_procedure: str = ""
    model_update_rule: str = ""


class ActionIntentModel(BaseModel):
    """Declared facts about an intervention, for action-collision detection (G6_Action_Logic.md G6-INV-08).

    Mirrors ``g6.action_logic.ActionIntent``; the deterministic Dörner coordination floor reads it
    at gate time. ``window`` is an ISO-8601 interval ``start/end`` or a bucket token; anything that
    cannot be proven separate is treated as overlapping (fail-safe; see ``g6.action_logic``).
    """

    model_config = ConfigDict(frozen=True)

    action_id: str
    target_resource: str = ""
    direction: str = ""  # increase | decrease | set | "" — objective direction on the target
    shared_resources: list[str] = Field(default_factory=list)
    costs: list[tuple[str, float]] = Field(default_factory=list)  # per-resource draw, for thresholds
    effect_signature: str = ""
    window: str = ""
    relies_on_assumptions: list[str] = Field(default_factory=list)
    invalidates_assumptions: list[str] = Field(default_factory=list)
    idempotency_key: str = ""  # shared non-empty key = safe to repeat (see g6.action_logic)


class CoordinationContextModel(BaseModel):
    """The live/scheduled interventions a proposed action must be coordinated against (G6-INV-08).

    Opt-in (mirroring ``verification_contract``): when an action declares this context, the Dörner
    coordination floor checks the proposed intent for collisions (contradictory objectives, duplicate
    effects, shared-resource contention, combined-threshold breach, invalidated assumption) against
    the active set. An unresolved collision on a *material* action blocks it — the raw-LLM delta (a
    raw model treats the system as a single snapshot and ignores other actions already in flight).
    """

    model_config = ConfigDict(frozen=True)

    proposed: ActionIntentModel
    active: list[ActionIntentModel] = Field(default_factory=list)
    budgets: dict[str, float] = Field(default_factory=dict)


class ControlVariableModel(BaseModel):
    """A monitored system variable as a (value, threshold, trend) triple (G6_Action_Logic.md G6-INV-03)."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: float | None = None
    threshold: float | None = None
    trend: str = "unknown"  # rising | falling | flat | unknown
    is_resource: bool = False  # a finite supporting resource that depletes downward toward a floor
    acknowledged: bool = False  # the emerging risk has been represented / explicitly accepted


class WorldStateModel(BaseModel):
    """A snapshot of monitored variables plus which future horizons were evaluated (G6_Action_Logic.md §9).

    Opt-in (mirroring ``verification_contract``): when an action declares world state, the Dörner
    weak-signal floor blocks a *material* action that evaluates no future horizon or that ignores an
    unacknowledged deteriorating signal. Defeats Dörner's reactive-repair trap (fix the visible
    fault, ignore the trend) — the raw-LLM delta.
    """

    model_config = ConfigDict(frozen=True)

    variables: list[ControlVariableModel] = Field(default_factory=list)
    horizons_evaluated: list[str] = Field(default_factory=list)


class HypothesisModel(BaseModel):
    """An operational hypothesis (G6-INV-04) as a wire model, mirroring the deterministic
    ``mvp.action_logic.schema.Hypothesis`` dataclass. Well-formed requires a non-empty statement
    and falsification_condition."""

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str
    statement: str = ""
    confidence: float = 0.5
    supporting_evidence: list[str] = Field(default_factory=list)
    disconfirming_evidence: list[str] = Field(default_factory=list)
    falsification_condition: str = ""
    status: Literal["active", "supported", "weakened", "falsified"] = "active"


class HypothesisContextModel(BaseModel):
    """Hypothesis-discipline context for the AT-08 / AT-12 reflection gate (G6-INV-04/09, §11).

    Opt-in (mirroring ``world_state``): when an action declares its hypothesis context AND the
    default-OFF ``G6_ACTION_LOGIC_HYPOTHESIS_GATE`` flag is set, the reflection floor pauses an
    action whose in-use hypothesis carries unresolved disconfirming evidence (AT-08 -> review)
    and blocks an action relying on a FALSIFIED hypothesis without a valid, evidence-backed
    replacement (AT-12 -> block). Defeats Dörner's "guard the competence" trap (ignore
    disconfirming evidence; repeat a failed strategy) — the raw-LLM delta.
    """

    model_config = ConfigDict(frozen=True)

    active_hypotheses: list[HypothesisModel] = Field(default_factory=list)
    relied_hypothesis: HypothesisModel | None = None
    replacement_hypothesis: HypothesisModel | None = None


class ActionCapsule(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: str
    requested_by: str | None = None
    actor_type: Literal["user", "agent", "system", "service"] = "agent"
    action_type: str
    target_backend: str = "unknown"
    target_resource: str = "unknown"
    description: str
    user_visible_summary: str
    proposed_inputs: dict = Field(default_factory=dict)
    normalized_inputs: dict = Field(default_factory=dict)
    real_world_effect: bool = False
    external_side_effect: bool = False
    irreversible_or_costly_to_reverse: bool = False
    reversibility: Literal["reversible", "partially_reversible", "irreversible", "unknown"] = "unknown"
    blast_radius: Literal[
        "none", "local", "account", "organization", "external_party", "public", "unknown"
    ] = "unknown"
    affected_entities: list[str] = Field(default_factory=list)
    affected_systems: list[str] = Field(default_factory=list)
    required_authority: list[str] = Field(default_factory=list)
    observed_authority: list[str] = Field(default_factory=list)
    required_dependencies: list[str] = Field(default_factory=list)
    available_dependencies: list[str] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)
    safety_domains: list[str] = Field(default_factory=list)
    factual_preconditions: list[FactualPrecondition] = Field(default_factory=list)
    verification_contract: VerificationContractModel | None = None
    coordination_context: CoordinationContextModel | None = None
    world_state: WorldStateModel | None = None
    hypothesis_context: HypothesisContextModel | None = None
    created_at: str


class ImpactAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    financial_impact: ImpactLevel = "none"
    privacy_impact: ImpactLevel = "none"
    security_impact: ImpactLevel = "none"
    legal_impact: ImpactLevel = "none"
    reputational_impact: ImpactLevel = "none"
    data_integrity_impact: ImpactLevel = "none"
    operational_impact: ImpactLevel = "none"
    aggregate_risk: RiskLevel = "low"
    confidence: float | None = None  # deterministic rules provide no confidence estimate
    rationale: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str
    likely_consequences: list[str] = Field(default_factory=list)
    affected_entities: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"
    likelihood: Literal["unlikely", "possible", "likely", "unknown"] = "unknown"
    detectability: Literal["easy", "moderate", "hard", "unknown"] = "unknown"
    recoverability: Literal["easy", "moderate", "hard", "impossible", "unknown"] = "unknown"
    assumptions: list[str] = Field(default_factory=list)


class ActionScenarioModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    best_case: Scenario
    expected_case: Scenario
    failure_case: Scenario
    partial_failure_case: Scenario
    incorrect_assumption_case: Scenario
    abuse_or_misuse_case: Scenario
    rollback_case: Scenario | None = None


class MitigationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    preventive_controls: list[str] = Field(default_factory=list)
    confirmation_requirements: list[str] = Field(default_factory=list)
    pre_execution_checks: list[str] = Field(default_factory=list)
    rollback_steps: list[str] = Field(default_factory=list)
    compensation_steps: list[str] = Field(default_factory=list)
    monitoring_steps: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    escalation_paths: list[str] = Field(default_factory=list)
    residual_risk: RiskLevel = "unknown"
    residual_risk_rationale: list[str] = Field(default_factory=list)


class RumsfeldMatrix(BaseModel):
    model_config = ConfigDict(frozen=True)

    known_knowns: list[str] = Field(default_factory=list)
    known_unknowns: list[str] = Field(default_factory=list)
    unknown_knowns: list[str] = Field(default_factory=list)
    unknown_unknowns: list[str] = Field(default_factory=list)
    assumptions_to_validate: list[str] = Field(default_factory=list)
    hidden_knowledge_prompts: list[str] = Field(default_factory=list)
    monitoring_tripwires: list[str] = Field(default_factory=list)
    contingency_plans: list[str] = Field(default_factory=list)


class UserMentalModelEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    likely_user_goal: str | None = None
    likely_user_assumptions: list[str] = Field(default_factory=list)
    possible_user_misunderstandings: list[str] = Field(default_factory=list)
    missing_user_acknowledgements: list[str] = Field(default_factory=list)
    user_capability_signals: list[str] = Field(default_factory=list)
    user_capability_uncertainties: list[str] = Field(default_factory=list)
    communication_needs: list[str] = Field(default_factory=list)
    consent_quality: Literal[
        "not_required", "insufficient", "basic", "informed", "explicit_high_risk", "unknown"
    ] = "unknown"
    confidence: float | None = None  # no estimator exists; the template reports none (AG-011)
    caveats: list[str] = Field(default_factory=list)


class SafetyQAAssurance(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_check_passed: bool = True
    authority_check_passed: bool = True
    consent_check_passed: bool = True
    reversibility_check_passed: bool = True
    mitigation_check_passed: bool = True
    degradation_check_passed: bool = True
    explanation_check_passed: bool = True
    detected_issues: list[str] = Field(default_factory=list)
    required_fixes_before_execution: list[str] = Field(default_factory=list)
    confidence: float | None = None  # rule checks provide no confidence estimate


class DegradationNotice(BaseModel):
    model_config = ConfigDict(frozen=True)

    degraded: bool = False
    severity: Literal["none", "info", "warning", "unsafe", "blocked"] = "none"
    missing_dependencies: list[str] = Field(default_factory=list)
    unavailable_capabilities: list[str] = Field(default_factory=list)
    affected_features: list[str] = Field(default_factory=list)
    impact_on_assurance: list[str] = Field(default_factory=list)
    fallback_used: str | None = None
    user_visible_message: str = ""
    blocks_execution: bool = False


class ActionGatingCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: Literal["action_gating"] = "action_gating"
    version: str = "1.0"
    capabilities: dict[str, bool] = Field(default_factory=dict)
    supported_action_types: list[str] = Field(default_factory=list)
    supported_backends: list[str] = Field(default_factory=list)
    degradation: list[DegradationNotice] = Field(default_factory=list)
    safety_guards: dict[str, bool] = Field(default_factory=dict)


class BackendNativeOpDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    backend_id: str
    op_name: str
    description: str
    side_effect_type: Literal[
        "none", "read_only", "local_write", "external_write", "real_world_action", "unknown"
    ] = "unknown"
    reversibility: Reversibility = "unknown"
    requires_confirmation: bool = True
    supports_dry_run: bool = False
    supports_rollback: bool = False
    required_permissions: list[str] = Field(default_factory=list)
    required_dependencies: list[str] = Field(default_factory=list)
    risk_domains: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)


class GateDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: GateDecisionName
    reason_codes: list[str] = Field(default_factory=list)
    risk: ImpactAssessment
    degradation: list[DegradationNotice] = Field(default_factory=list)
    scenario_model: ActionScenarioModel | None = None
    mitigation_plan: MitigationPlan | None = None
    rumsfeld_matrix: RumsfeldMatrix | None = None
    user_mental_model: UserMentalModelEstimate | None = None
    safety_qa: SafetyQAAssurance | None = None
    required_user_message: str
    required_confirmation_phrase: str | None = None
    gate_token: str | None = None
    expires_at: str | None = None
    audit_id: str
    action_id: str = ""
    completion_state: CompletionState = "qualified-draft"
    warning_card: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)


class ActionGateAuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    audit_id: str
    action_id: str
    timestamp: str
    action_capsule: ActionCapsule
    capabilities_snapshot: ActionGatingCapabilities
    degradation: list[DegradationNotice] = Field(default_factory=list)
    risk_assessment: ImpactAssessment
    scenario_model: ActionScenarioModel | None = None
    rumsfeld_matrix: RumsfeldMatrix | None = None
    mitigation_plan: MitigationPlan | None = None
    user_mental_model: UserMentalModelEstimate | None = None
    safety_qa: SafetyQAAssurance | None = None
    decision: GateDecisionName
    reason_codes: list[str] = Field(default_factory=list)
    user_message: str | None = None
    confirmation_required: bool = False
    confirmation_phrase: str | None = None
    confirmation_received: bool = False
    gate_token_issued: bool = False
    gate_token_id: str | None = None
    executed: bool = False
    execution_result_ref: str | None = None
    # PA-15 (2026-06-25): the evidence-provenance class of this audit entry —
    # "model" | "tool" | "formal" | "human_approval" — so the inspectable trail
    # DISTINGUISHES what backed the decision (ISO/IEC 42001 Clause 9.2). Classified
    # at write time (see mvp.action_gating.provenance). Default "" → additive, so
    # older persisted records and field-pinning tests are unaffected.
    provenance_class: str = ""
    evidence: list[dict] = Field(default_factory=list)


class ActionGatingEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_capsule: ActionCapsule
    capabilities: ActionGatingCapabilities
    degradation: list[DegradationNotice]
    gate_decision: GateDecision
    audit_record: ActionGateAuditRecord | None = None
    request_id: str
    task_id: str
    run_id: str
    completion_state: CompletionState
    reliability_label: CompletionState
    warning_card: dict
    evidence: list[dict]

    def to_response_dict(self) -> dict:
        return self.model_dump(mode="json")


__all__ = [  # pragma: no cover - re-export
    "ActionCapsule",
    "ActionIntentModel",
    "CoordinationContextModel",
    "ControlVariableModel",
    "WorldStateModel",
    "ActionGatingCapabilities",
    "ActionGatingEvaluation",
    "ActionGateAuditRecord",
    "ActionScenarioModel",
    "BackendNativeOpDescriptor",
    "DegradationNotice",
    "FactualEvidenceRef",
    "FactualPrecondition",
    "GateDecision",
    "ImpactAssessment",
    "MitigationPlan",
    "RumsfeldMatrix",
    "SafetyQAAssurance",
    "Scenario",
    "UserMentalModelEstimate",
    "VerificationContractModel",
]
