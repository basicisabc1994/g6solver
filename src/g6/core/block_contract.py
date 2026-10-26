# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""BlockContract — mvp.core.block_contract

A block's self-declared promises about what it can do, what it costs,
how it fails, and how a caller should verify it. Used by the reliability
orchestrator to gate output instead of post-hoc enveloping.

Worked example
--------------
```
@block_contract(
    name="example_harness",
    purpose="Checks work before it is accepted as complete.",
    outputs=("evidence", "decision"),
    known_failure_modes=(FailureMode(name="weak_test_evidence"),),
    mitigates_failure_modes=("hallucinated_success",),
    state_surface=StateSurface(persistence="run", queryable_via="ledger", retention="30d"),
    extension_points=ExtensionPoints(
        training_signal_inputs=("contract_warning",),
        validation_gate="unit_tests_only",
        allowed_mutation_surfaces=("mcp:tools",),
    ),
    verification_method="unit_tests_only",
)
class ExampleHarness(AIBlock[dict, dict, None]):
    ...
```

Design notes
------------
* Frozen dataclasses, no runtime mutation.
* Callable fields (``preconditions``, ``stop_conditions``) hold function
  references — they live in code, not in cross-process serialization.
  For wire transport, names are resolved via a ``PRECONDITION_REGISTRY``
  (deferred to v2).
* ``human_review_required`` is either a ``bool`` or a string predicate name
  (``"R2+"`` / ``"R3"``). The orchestrator resolves the predicate against
  the active risk tier.
* The permissive default (``_PERMISSIVE_DEFAULT``) lets unmigrated components
  flow through the orchestrator unchanged.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Callable, ClassVar

from g6.core.env import is_production_env
from g6.core.extension_points import ExtensionPoints
from g6.core.failure_modes import FailureModeRegistry
from g6.core.protocols import SourceOpsDescriptor
from g6.core.state_surface import StateSurface


class FailureType(StrEnum):
    INVALID_INPUT = "invalid_input"
    MISSING_DEPENDENCY = "missing_dependency"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    EXTERNAL_PROVIDER_FAILURE = "external_provider_failure"
    PERSISTENCE_FAILURE = "persistence_failure"
    UNSAFE_ACTION = "unsafe_action"
    CONTRACT_VIOLATION = "contract_violation"
    DEGRADED_MODE = "degraded_mode"
    UNVERIFIED_OUTPUT = "unverified_output"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    UNSUPPORTED_OPERATION = "unsupported_operation"


ALLOWED_FAILURE_TYPES = frozenset(item.value for item in FailureType)
ALLOWED_REVIEW_LEVELS = frozenset({"none", "user_review", "human_review", "expert_review"})
_CONTRACT_LOGGER = logging.getLogger("g6_contract")


def _contract_enforce_mode() -> str:
    raw = os.getenv("G6_CONTRACT_ENFORCE")
    if raw is None:
        # Default: fail-CLOSED in production (an unmigrated / invalid contract is a
        # real defect that must not ship), 'warn' in dev/test so contract authoring
        # and the test suite are not blocked by in-progress contracts (audit
        # 2026-06-21). An explicit G6_CONTRACT_ENFORCE always overrides.
        return "raise" if is_production_env() else "warn"
    mode = raw.strip().lower()
    if mode in {"off", "warn", "raise"}:
        return mode
    _CONTRACT_LOGGER.warning(
        "contract_warning: invalid G6_CONTRACT_ENFORCE=%r; using warn",
        mode,
        extra={"event_type": "contract_warning"},
    )
    return "warn"


def _handle_contract_violation(error_code: str, detail: str) -> None:
    mode = _contract_enforce_mode()
    if mode == "off":
        return
    message = f"{error_code}: {detail}"
    if mode == "raise":
        raise ValueError(message)
    _CONTRACT_LOGGER.warning(
        "contract_warning: %s",
        message,
        extra={"event_type": "contract_warning", "error_code": error_code, "detail": detail},
    )


@dataclass(frozen=True)
class FailureMode:
    """A known way this block can go wrong.

    Important — ``detector`` strings ARE runtime dispatch. The
    orchestrator's ``verify_output`` looks each declared ``detector`` up
    in ``DETECTOR_REGISTRY`` and invokes it against the block's output; a
    True return flags the failure mode as fired (``domain_standard_passed``
    → False, surfaced in ``checks_failed``). A declared-but-unregistered
    detector fails closed (recorded as an unresolved check, never silently
    ignored), and the fleet-drift gate
    (``tests/mvp/reliability/test_fleet_drift.py``) asserts every declared
    detector either resolves in the registry or is the empty string
    (explicit documentation-only). Register implementations with
    ``@register_detector``; ``detector=""`` marks a documented failure mode
    with deliberately no runtime check. ``known_failure_modes`` also
    populates the epistemic contract's ``forbidden_claims`` list and drift
    reports. (This docstring previously described detectors as pure
    documentation — stale since the verify_output dispatch loop landed.)
    """

    name: str                 # short identifier, e.g. "hallucinated_citation"
    detector: str = ""        # DETECTOR_REGISTRY key dispatched by verify_output ("" = doc-only)
    severity: str = "medium"  # "low" | "medium" | "high"
    description: str = ""
    failure_type: str = FailureType.UNVERIFIED_OUTPUT.value


@dataclass(frozen=True)
class ComponentExample:
    title: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class ComponentTemplate:
    name: str
    description: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceToolRequirement:
    name: str
    required: bool = True
    purpose: str = ""


@dataclass(frozen=True)
class ComponentReliabilityCard:
    version: int
    block_name: str
    purpose: str
    inputs_required: tuple[str, ...] = ()
    inputs_optional: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    strengths: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    known_failure_modes: tuple[FailureMode, ...] = ()
    cost_estimate: CostEstimate = field(default_factory=lambda: CostEstimate())
    verification_method: str = "permissive_default"
    human_review_policy: str = "none"
    stop_conditions: tuple[str, ...] = ()
    fallback_workaround: str = ""
    domain: str = "general"
    standards: tuple[str, ...] = ()
    regulatory_ethical_constraints: tuple[str, ...] = ()
    evidence_standard: str = ""
    common_ai_failure_modes: tuple[str, ...] = ()
    required_sources_tools: tuple[SourceToolRequirement, ...] = ()
    source_ops: SourceOpsDescriptor = field(default_factory=SourceOpsDescriptor)
    review_level: str = "user_review"
    forbidden_claims: tuple[str, ...] = ()
    escalation_triggers: tuple[str, ...] = ()
    good_examples: tuple[ComponentExample, ...] = ()
    unsafe_examples: tuple[ComponentExample, ...] = ()
    usage_examples: tuple[ComponentExample, ...] = ()
    prebuilt_templates: tuple[ComponentTemplate, ...] = ()

    def validate(self, *, production: bool = False) -> list[str]:
        errors: list[str] = []
        if not self.block_name.strip():
            errors.append("block_name is required")
        if not self.purpose.strip():
            errors.append("purpose is required")
        if not self.outputs:
            errors.append("outputs must not be empty")
        if self.review_level not in ALLOWED_REVIEW_LEVELS:
            errors.append(f"review_level must be one of {sorted(ALLOWED_REVIEW_LEVELS)}")
        for fm in self.known_failure_modes:
            if fm.failure_type not in ALLOWED_FAILURE_TYPES:
                errors.append(f"unknown failure_type for {fm.name}: {fm.failure_type}")
        if production:
            if not self.known_failure_modes:
                errors.append("production cards require at least one typed failure mode")
            if self.verification_method in {"", "permissive_default", "tier1_review_pending"}:
                errors.append("production cards require a concrete verification_method")
            if not (self.good_examples or self.usage_examples):
                errors.append("production cards require examples")
            if not self.prebuilt_templates:
                errors.append("production cards require prebuilt templates")
        return errors


@dataclass(frozen=True)
class CostEstimate:
    """Self-declared cost envelope. The truthfulness harness asserts
    actual usage stays within ``tokens_p95 * 1.5`` of declared.
    """

    tokens_p50: int = 0
    tokens_p95: int = 0
    wall_seconds_p95: float = 0.0
    usd_p95: float = 0.0


#: Preconditions receive a context dict with these keys (populated by
#: ``JobReliabilityOrchestrator.run_preflight``):
#:
#:   ``task``        — the raw task string
#:   ``context``     — the caller-supplied context (often a dataclass instance
#:                     or a dict; preconditions must handle ``None`` and
#:                     accept-anything semantics)
#:   ``constraints`` — caller-supplied constraints
#:   ``job_name``    — the orchestrator's (stripped) job name
#:   ``domain``      — the resolved domain (legal/medical/.../general)
PreconditionContext = dict[str, Any]


@dataclass(frozen=True)
class BlockContract:
    """What a block promises and how to check it.

    Two kinds of field on this dataclass:

      *Executable* — drive the orchestrator's runtime behaviour:
        - ``preconditions``    → callables run during ``run_preflight``;
                                 failures land in ``task_contract.missing_inputs``.
        - ``stop_conditions``  → callables run during ``verify_output``;
                                 any True flips ``domain_standard_passed=False``
                                 and routes the envelope to BLOCKED_ESCALATED.
        - ``human_review_required``    → predicate consulted by ``verify_output``.
        - ``verification_method``      → appended to the epistemic verification
                                         strategy; surfaced in warning cards.
        - ``fallback_block``   → surfaced in the warning card's recommended
                                 next step on any non-green status.

      *Input contract* — drives the registry invoker's pre-validation:
        - ``inputs_required_per_op`` → maps each op name to the tuple of
          fields that op rejects when empty. The registry invoker checks
          these before Pydantic ``model_validate`` and emits the canonical
          ``[<NAME>_ERROR] <op>: <field> is required`` envelope. Empty dict
          (the default) means no per-op enforcement — unmigrated behaviour.

      *Documentation* — surface in envelopes / drift reports / human review
      but do NOT drive dispatch:
        - ``purpose``, ``inputs_required``, ``inputs_optional``, ``outputs``,
          ``good_at``, ``bad_at``  — descriptive only.
        - ``known_failure_modes``  — the list populates ``forbidden_claims``
          on the epistemic contract (informational markers) and feeds the
          drift detector's declared-vs-observed comparison. The
          ``FailureMode.detector`` string is purely documentary; turn a
          declared mode into an enforced gate by adding a matching
          ``stop_conditions`` callable.
        - ``cost_estimate``        — surfaces in drift's cost_drift finding
          when actual p95 > declared p95 * 1.5; does not gate anything by
          itself.
    """

    __contract_schema_version__: ClassVar[str] = "2.0"

    name: str
    purpose: str = ""
    inputs_required: tuple[str, ...] = ()
    inputs_optional: tuple[str, ...] = ()
    #: Per-op required fields — {op_name: (field, ...)}. Enforced by the
    #: registry invoker before Pydantic validation. Empty = no enforcement.
    inputs_required_per_op: dict[str, tuple[str, ...]] = field(default_factory=dict)
    preconditions: tuple[Callable[[PreconditionContext], bool], ...] = ()
    outputs: tuple[str, ...] = ()
    good_at: tuple[str, ...] = ()
    bad_at: tuple[str, ...] = ()
    known_failure_modes: tuple[FailureMode, ...] = ()
    mitigates_failure_modes: tuple[str, ...] = ()
    state_surface: StateSurface = field(default_factory=StateSurface)
    extension_points: ExtensionPoints | None = None
    cost_estimate: CostEstimate = field(default_factory=CostEstimate)
    verification_method: str = "permissive_default"
    # bool, or one of: "R1+", "R2+", "R3"
    human_review_required: bool | str = False
    #: Stop conditions receive the block's output (whatever object the
    #: orchestrator's ``verify_output`` was given). Any callable that returns
    #: True forces ``domain_standard_passed=False`` and routes the envelope
    #: to BLOCKED_ESCALATED via ``completion_from_report``.
    stop_conditions: tuple[Callable[[Any], bool], ...] = ()
    fallback_block: str | None = None
    domain: str = "general"
    standards: tuple[str, ...] = ()
    regulatory_ethical_constraints: tuple[str, ...] = ()
    evidence_standard: str = ""
    common_ai_failure_modes: tuple[str, ...] = ()
    required_sources_tools: tuple[SourceToolRequirement, ...] = ()
    source_ops: SourceOpsDescriptor = field(default_factory=SourceOpsDescriptor)
    review_level: str = "user_review"
    forbidden_claims: tuple[str, ...] = ()
    escalation_triggers: tuple[str, ...] = ()
    good_examples: tuple[ComponentExample, ...] = ()
    unsafe_examples: tuple[ComponentExample, ...] = ()
    usage_examples: tuple[ComponentExample, ...] = ()
    prebuilt_templates: tuple[ComponentTemplate, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        normalised = tuple(str(code) for code in self.mitigates_failure_modes)
        object.__setattr__(self, "mitigates_failure_modes", normalised)
        if isinstance(self.source_ops, dict):
            object.__setattr__(self, "source_ops", SourceOpsDescriptor.model_validate(self.source_ops))
        for code in normalised:   # a placeholder such as "TBD" is an unknown code like any other (FW-03)
            if not FailureModeRegistry.is_registered(code):
                _handle_contract_violation("G6_E_CONTRACT_UNKNOWN_FAILURE_MODE", code)

    def is_permissive_default(self) -> bool:
        """True when this contract is the no-op default (unmigrated block)."""
        return (
            self.verification_method == "permissive_default"
            and not self.known_failure_modes
            and not self.mitigates_failure_modes
            and not self.preconditions
            and not self.stop_conditions
            and self.fallback_block is None
        )

    def requires_human_review_for_tier(self, risk_tier: str) -> bool:
        """Resolve the ``human_review_required`` predicate against a tier."""
        spec = self.human_review_required
        if isinstance(spec, bool):
            return spec
        if not isinstance(spec, str):
            return False
        spec = spec.strip().upper()
        tier = (risk_tier or "").strip().upper()
        if spec == "R3":
            return tier == "R3"
        if spec == "R2+":
            return tier in {"R2", "R3"}
        if spec == "R1+":
            return tier in {"R1", "R2", "R3"}
        return False

    def reliability_card(self, *, domain: str | None = None) -> ComponentReliabilityCard:
        """Return the versioned operator-facing reliability card for this block."""
        return ComponentReliabilityCard(
            version=max(2, int(self.version or 1)),
            block_name=self.name,
            purpose=self.purpose,
            inputs_required=self.inputs_required,
            inputs_optional=self.inputs_optional,
            preconditions=tuple(getattr(p, "__name__", str(p)) for p in self.preconditions),
            outputs=self.outputs,
            strengths=self.good_at,
            limitations=self.bad_at,
            known_failure_modes=self.known_failure_modes,
            cost_estimate=self.cost_estimate,
            verification_method=self.verification_method,
            human_review_policy=str(self.human_review_required),
            stop_conditions=tuple(getattr(s, "__name__", str(s)) for s in self.stop_conditions),
            fallback_workaround=self.fallback_block or "",
            domain=domain or self.domain,
            standards=self.standards,
            regulatory_ethical_constraints=self.regulatory_ethical_constraints,
            evidence_standard=self.evidence_standard,
            common_ai_failure_modes=self.common_ai_failure_modes,
            required_sources_tools=self.required_sources_tools,
            source_ops=self.source_ops,
            review_level=self.review_level,
            forbidden_claims=self.forbidden_claims,
            escalation_triggers=self.escalation_triggers,
            good_examples=self.good_examples,
            unsafe_examples=self.unsafe_examples,
            usage_examples=self.usage_examples,
            prebuilt_templates=self.prebuilt_templates,
        )


def permissive_default(name: str) -> BlockContract:
    """Construct the no-op default contract for a block by name."""
    return BlockContract(name=name)


# ─────────────────────────────────────────────────────────────────────────────
# Cross-process serialization (V2-3)
#
# A BlockContract is a Python dataclass with callable fields (preconditions,
# stop_conditions) — not naively JSON-serialisable. For MCP transport, we
# serialise callables by *name* (their ``__name__`` attribute) and expect the
# receiving end to resolve the name against a process-local registry. Names
# must be stable identifiers, ideally fully-qualified, declared via the
# ``@register_precondition`` / ``@register_stop_condition`` decorators.
#
# Round-trip guarantee: ``BlockContract.from_dict(c.to_dict())`` is *not*
# bit-exact — the callable fields are looked up through the registry, so any
# precondition/stop_condition that wasn't registered on the receiver becomes
# ``_unresolved_callable_factory(name)`` which returns False (the safest
# default — an unresolved precondition is a "missing input"). This is the
# right semantic: a remote contract claims X is required, the local runtime
# doesn't know how to check X, so X is treated as not satisfied.
# ─────────────────────────────────────────────────────────────────────────────


PRECONDITION_REGISTRY: dict[str, Callable[[PreconditionContext], bool]] = {}
STOP_CONDITION_REGISTRY: dict[str, Callable[[Any], bool]] = {}
#: Registered detectors for ``FailureMode.detector`` strings. The orchestrator
#: looks each declared detector up by name during ``verify_output`` and
#: invokes it against the block's output; a True return value flags the
#: matching failure mode as fired.
#:
#: Unlike preconditions (which receive a context dict) and stop conditions
#: (which receive only the output), detectors receive the output object —
#: the same input as stop conditions — but participate in the contract's
#: ``known_failure_modes`` declaration rather than the orthogonal
#: ``stop_conditions`` field. Use detectors for documented failure modes
#: that need a runtime check; use stop conditions for hard-stops outside
#: the failure-mode taxonomy.
DETECTOR_REGISTRY: dict[str, Callable[[Any], bool]] = {}


def register_precondition(
    fn: Callable[[PreconditionContext], bool] | None = None,
    *,
    name: str | None = None,
) -> Any:
    """Register a precondition by name (default: ``fn.__name__``).

    Usable as ``@register_precondition`` or ``@register_precondition(name="…")``.
    """
    def _wrap(target: Callable[[PreconditionContext], bool]) -> Callable[[PreconditionContext], bool]:
        key = name or getattr(target, "__name__", None) or repr(target)
        PRECONDITION_REGISTRY[key] = target
        return target
    if fn is not None:
        return _wrap(fn)
    return _wrap


def register_stop_condition(
    fn: Callable[[Any], bool] | None = None,
    *,
    name: str | None = None,
) -> Any:
    """Register a stop condition by name (default: ``fn.__name__``)."""
    def _wrap(target: Callable[[Any], bool]) -> Callable[[Any], bool]:
        key = name or getattr(target, "__name__", None) or repr(target)
        STOP_CONDITION_REGISTRY[key] = target
        return target
    if fn is not None:
        return _wrap(fn)
    return _wrap


def register_detector(
    fn: Callable[[Any], bool] | None = None,
    *,
    name: str | None = None,
) -> Any:
    """Register a failure-mode detector by name (default: ``fn.__name__``).

    Mirrors ``register_precondition`` / ``register_stop_condition``. A
    ``FailureMode("hallucination", detector="semantic_drift_detector", ...)``
    on a contract becomes enforced when ``semantic_drift_detector`` is in
    DETECTOR_REGISTRY — the orchestrator's ``verify_output`` calls it
    against the block's output, and a True return value surfaces in the
    envelope's ``checks_failed`` list with the failure-mode name and flips
    ``domain_standard_passed`` to False.

    The fleet-drift acceptance gate
    (``tests/mvp/reliability/test_fleet_drift.py``) asserts every declared
    detector either resolves here or is the empty string (explicit
    documentation-only). At runtime, a declared-but-unresolved detector
    FAILS CLOSED: ``job_reliability_orchestrator.verify_output`` records
    ``"detector unresolved: <name>"`` in the verification report's
    ``unverified_items`` (and ``checks_failed``), forces
    ``domain_standard_passed`` to False, and routes the envelope to
    BLOCKED_ESCALATED — it never fails open or silently passes.
    """
    def _wrap(target: Callable[[Any], bool]) -> Callable[[Any], bool]:
        key = name or getattr(target, "__name__", None) or repr(target)
        DETECTOR_REGISTRY[key] = target
        return target
    if fn is not None:
        return _wrap(fn)
    return _wrap


def _unresolved_precondition_factory(name: str) -> Callable[[PreconditionContext], bool]:
    """Return a precondition stand-in that FAILS CLOSED for an unknown name.

    Returns False — i.e. the precondition is reported as unsatisfied so the
    orchestrator's preflight routes the failure into ``task_contract.missing_inputs``
    rather than silently treating an unknown remote precondition as satisfied.
    """
    def _unresolved(_ctx: PreconditionContext) -> bool:
        return False
    _unresolved.__name__ = f"unresolved_precondition:{name}"
    return _unresolved


def _unresolved_stop_condition_factory(name: str) -> Callable[[Any], bool]:
    """Return a stop-condition stand-in that NEVER FIRES for an unknown name.

    Returns False — i.e. the stop condition is reported as "did not trigger"
    so the orchestrator does not spuriously route to BLOCKED on a check the
    local runtime doesn't recognise. Asymmetric with the precondition
    factory by design: a missing precondition should fail safe (treat as
    unmet), a missing stop check should fail open (allow the call).
    """
    def _unresolved(_output: Any) -> bool:
        return False
    _unresolved.__name__ = f"unresolved_stop_condition:{name}"
    return _unresolved


def _unresolved_detector_factory(name: str) -> Callable[[Any], bool]:
    """Return a detector stand-in that NEVER FIRES for an unknown name.

    Named and shaped like ``_unresolved_precondition_factory`` /
    ``_unresolved_stop_condition_factory``, but its "never fires" fail-open
    semantic applies ONLY to a stand-in callable produced during
    cross-process ``contract_from_dict`` rehydration — it is not the live
    runtime path. ``FailureMode.detector`` stays a plain string on the
    contract; the single-process orchestrator resolves that string
    directly against ``DETECTOR_REGISTRY`` in
    ``job_reliability_orchestrator.verify_output``, and FAILS CLOSED when
    the name is unresolved (``domain_standard_passed`` forced False,
    ``"detector unresolved: <name>"`` recorded in
    ``VerificationReport.unverified_items``, envelope routed to
    BLOCKED_ESCALATED). The fleet-drift acceptance gate
    (``tests/mvp/reliability/test_fleet_drift.py``) is the CI-side
    fail-loud companion to that runtime fail-closed behaviour.
    """
    def _unresolved(_output: Any) -> bool:
        return False
    _unresolved.__name__ = f"unresolved_detector:{name}"
    return _unresolved


def contract_to_dict(contract: BlockContract) -> dict[str, Any]:
    """Serialise a BlockContract for cross-process transport.

    Callables are reduced to their ``__name__``. Receivers should resolve
    them against the local registries via ``contract_from_dict``.
    """
    reliability_card = asdict(contract.reliability_card())
    if isinstance(reliability_card.get("source_ops"), SourceOpsDescriptor):
        reliability_card["source_ops"] = reliability_card["source_ops"].to_public_dict()

    out: dict[str, Any] = {
        "name": contract.name,
        "purpose": contract.purpose,
        "inputs_required": list(contract.inputs_required),
        "inputs_optional": list(contract.inputs_optional),
        "inputs_required_per_op": {
            op: list(fields) for op, fields in contract.inputs_required_per_op.items()
        },
        "preconditions": [getattr(p, "__name__", str(p)) for p in contract.preconditions],
        "outputs": list(contract.outputs),
        "good_at": list(contract.good_at),
        "bad_at": list(contract.bad_at),
        "known_failure_modes": [asdict(fm) for fm in contract.known_failure_modes],
        "mitigates_failure_modes": list(contract.mitigates_failure_modes),
        "state_surface": asdict(contract.state_surface),
        "extension_points": asdict(contract.extension_points) if contract.extension_points else None,
        "cost_estimate": asdict(contract.cost_estimate),
        "verification_method": contract.verification_method,
        "human_review_required": contract.human_review_required,
        "stop_conditions": [getattr(s, "__name__", str(s)) for s in contract.stop_conditions],
        "fallback_block": contract.fallback_block,
        "domain": contract.domain,
        "standards": list(contract.standards),
        "regulatory_ethical_constraints": list(contract.regulatory_ethical_constraints),
        "evidence_standard": contract.evidence_standard,
        "common_ai_failure_modes": list(contract.common_ai_failure_modes),
        "required_sources_tools": [asdict(item) for item in contract.required_sources_tools],
        "source_ops": contract.source_ops.to_public_dict(),
        "review_level": contract.review_level,
        "forbidden_claims": list(contract.forbidden_claims),
        "escalation_triggers": list(contract.escalation_triggers),
        "good_examples": [asdict(item) for item in contract.good_examples],
        "unsafe_examples": [asdict(item) for item in contract.unsafe_examples],
        "usage_examples": [asdict(item) for item in contract.usage_examples],
        "prebuilt_templates": [asdict(item) for item in contract.prebuilt_templates],
        "reliability_card": reliability_card,
        "version": contract.version,
    }
    return out


def contract_from_dict(payload: dict[str, Any]) -> BlockContract:
    """Reconstruct a BlockContract from a ``contract_to_dict`` payload.

    Callable name references are looked up in PRECONDITION_REGISTRY /
    STOP_CONDITION_REGISTRY. Unknown names degrade to safe stand-ins
    (precondition → fails closed; stop condition → never fires).

    Forward-compatible: extra keys on incoming FailureMode / CostEstimate
    payloads are silently dropped rather than raising TypeError, so a
    newer sender that adds a field is tolerated by an older receiver.
    Missing required keys (e.g. FailureMode.name) still raise — those
    can't be silently defaulted without misrepresenting the sender.
    """
    def _resolve_pre(name: str) -> Callable[[PreconditionContext], bool]:
        return PRECONDITION_REGISTRY.get(name) or _unresolved_precondition_factory(name)

    def _resolve_stop(name: str) -> Callable[[Any], bool]:
        return STOP_CONDITION_REGISTRY.get(name) or _unresolved_stop_condition_factory(name)

    def _filter(cls: Any, raw: dict[str, Any]) -> dict[str, Any]:
        from dataclasses import fields as _dc_fields
        allowed = {f.name for f in _dc_fields(cls)}
        return {k: v for k, v in raw.items() if k in allowed}

    failure_modes = tuple(
        FailureMode(**_filter(FailureMode, fm)) for fm in payload.get("known_failure_modes", [])
    )
    cost = CostEstimate(**_filter(CostEstimate, payload.get("cost_estimate", {})))
    state_surface = StateSurface(**_filter(StateSurface, payload.get("state_surface", {}) or {}))
    extension_points = (
        ExtensionPoints(**_filter(ExtensionPoints, payload.get("extension_points", {}) or {}))
        if payload.get("extension_points") is not None
        else None
    )
    preconditions = tuple(_resolve_pre(n) for n in payload.get("preconditions", []))
    stop_conditions = tuple(_resolve_stop(n) for n in payload.get("stop_conditions", []))
    source_tools = tuple(
        SourceToolRequirement(**_filter(SourceToolRequirement, item))
        for item in payload.get("required_sources_tools", [])
    )
    source_ops = SourceOpsDescriptor.model_validate(payload.get("source_ops", {}) or {})
    good_examples = tuple(
        ComponentExample(**_filter(ComponentExample, item))
        for item in payload.get("good_examples", [])
    )
    unsafe_examples = tuple(
        ComponentExample(**_filter(ComponentExample, item))
        for item in payload.get("unsafe_examples", [])
    )
    usage_examples = tuple(
        ComponentExample(**_filter(ComponentExample, item))
        for item in payload.get("usage_examples", [])
    )
    templates = tuple(
        ComponentTemplate(**_filter(ComponentTemplate, item))
        for item in payload.get("prebuilt_templates", [])
    )

    return BlockContract(
        name=str(payload.get("name", "")),
        purpose=str(payload.get("purpose", "")),
        inputs_required=tuple(payload.get("inputs_required", ())),
        inputs_optional=tuple(payload.get("inputs_optional", ())),
        inputs_required_per_op={
            op: tuple(fields)
            for op, fields in (payload.get("inputs_required_per_op", {}) or {}).items()
        },
        preconditions=preconditions,
        outputs=tuple(payload.get("outputs", ())),
        good_at=tuple(payload.get("good_at", ())),
        bad_at=tuple(payload.get("bad_at", ())),
        known_failure_modes=failure_modes,
        mitigates_failure_modes=tuple(payload.get("mitigates_failure_modes", ())),
        state_surface=state_surface,
        extension_points=extension_points,
        cost_estimate=cost,
        verification_method=str(payload.get("verification_method", "permissive_default")),
        human_review_required=payload.get("human_review_required", False),
        stop_conditions=stop_conditions,
        fallback_block=payload.get("fallback_block"),
        domain=str(payload.get("domain", "general")),
        standards=tuple(payload.get("standards", ())),
        regulatory_ethical_constraints=tuple(payload.get("regulatory_ethical_constraints", ())),
        evidence_standard=str(payload.get("evidence_standard", "")),
        common_ai_failure_modes=tuple(payload.get("common_ai_failure_modes", ())),
        required_sources_tools=source_tools,
        source_ops=source_ops,
        review_level=str(payload.get("review_level", "user_review")),
        forbidden_claims=tuple(payload.get("forbidden_claims", ())),
        escalation_triggers=tuple(payload.get("escalation_triggers", ())),
        good_examples=good_examples,
        unsafe_examples=unsafe_examples,
        usage_examples=usage_examples,
        prebuilt_templates=templates,
        version=int(payload.get("version", 1)),
    )


# Exported for type-checking / downstream re-use.
__all__ = [
    "BlockContract",
    "ALLOWED_FAILURE_TYPES",
    "ALLOWED_REVIEW_LEVELS",
    "ComponentExample",
    "ComponentReliabilityCard",
    "ComponentTemplate",
    "CostEstimate",
    "FailureMode",
    "FailureType",
    "ExtensionPoints",
    "PreconditionContext",
    "SourceToolRequirement",
    "DETECTOR_REGISTRY",
    "PRECONDITION_REGISTRY",
    "STOP_CONDITION_REGISTRY",
    "contract_from_dict",
    "contract_to_dict",
    "permissive_default",
    "register_detector",
    "register_precondition",
    "register_stop_condition",
]
