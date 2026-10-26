# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""@block_contract — mvp.core.contract_decorator

Class decorator that attaches a :class:`BlockContract` to a component
class. Mirrors the existing ``@reliability_tracked`` / ``@component_maturity``
pattern: the decorator sets a class attribute at import time, with no
runtime side effects or registry mutation. The reliability orchestrator
picks it up lazily via :func:`get_block_contract_registry`.

Usage::

    from g6.core.contract_decorator import block_contract
    from g6.core.block_contract import CostEstimate, FailureMode

    @block_contract(
        purpose="LLM call wrapper",
        inputs_required=("prompt",),
        cost_estimate=CostEstimate(tokens_p50=2000, tokens_p95=8000),
        known_failure_modes=(
            FailureMode("hallucination", "semantic_drift_detector", "high"),
        ),
    )
    class LLMBlock(AIBlock[str, str, None]):
        ...
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from g6.core.block_contract import (
    BlockContract,
    ComponentExample,
    ComponentTemplate,
    CostEstimate,
    FailureMode,
    PreconditionContext,
    SourceToolRequirement,
)
from g6.core.extension_points import ExtensionPoints
from g6.core.protocols import SourceOpsDescriptor
from g6.core.state_surface import StateSurface

T = TypeVar("T", bound=type)


def block_contract(
    *,
    purpose: str = "",
    inputs_required: tuple[str, ...] = (),
    inputs_optional: tuple[str, ...] = (),
    inputs_required_per_op: dict[str, tuple[str, ...]] | None = None,
    preconditions: tuple[Callable[[PreconditionContext], bool], ...] = (),
    outputs: tuple[str, ...] = (),
    good_at: tuple[str, ...] = (),
    bad_at: tuple[str, ...] = (),
    known_failure_modes: tuple[FailureMode, ...] = (),
    mitigates_failure_modes: tuple[str, ...] = (),
    state_surface: StateSurface | None = None,
    extension_points: ExtensionPoints | None = None,
    cost_estimate: CostEstimate | None = None,
    verification_method: str = "permissive_default",
    human_review_required: bool | str = False,
    stop_conditions: tuple[Callable[[Any], bool], ...] = (),
    fallback_block: str | None = None,
    domain: str = "general",
    standards: tuple[str, ...] = (),
    regulatory_ethical_constraints: tuple[str, ...] = (),
    evidence_standard: str = "",
    common_ai_failure_modes: tuple[str, ...] = (),
    required_sources_tools: tuple[SourceToolRequirement, ...] = (),
    source_ops: SourceOpsDescriptor | dict[str, Any] | None = None,
    review_level: str = "user_review",
    forbidden_claims: tuple[str, ...] = (),
    escalation_triggers: tuple[str, ...] = (),
    good_examples: tuple[ComponentExample, ...] = (),
    unsafe_examples: tuple[ComponentExample, ...] = (),
    usage_examples: tuple[ComponentExample, ...] = (),
    prebuilt_templates: tuple[ComponentTemplate, ...] = (),
    version: int = 1,
):
    """Decorator: attach a BlockContract to a component class.

    The contract's ``name`` is resolved at registry-lookup time from the
    component's registered name (so the same decorator can be applied to a
    class whether or not the class declares ``name`` at the class level).
    """
    payload: dict[str, Any] = {
        "purpose": purpose,
        "inputs_required": tuple(inputs_required),
        "inputs_optional": tuple(inputs_optional),
        "inputs_required_per_op": dict(inputs_required_per_op or {}),
        "preconditions": tuple(preconditions),
        "outputs": tuple(outputs),
        "good_at": tuple(good_at),
        "bad_at": tuple(bad_at),
        "known_failure_modes": tuple(known_failure_modes),
        "mitigates_failure_modes": tuple(mitigates_failure_modes),
        "state_surface": state_surface if state_surface is not None else StateSurface(),
        "extension_points": extension_points,
        "cost_estimate": cost_estimate if cost_estimate is not None else CostEstimate(),
        "verification_method": verification_method,
        "human_review_required": human_review_required,
        "stop_conditions": tuple(stop_conditions),
        "fallback_block": fallback_block,
        "domain": domain,
        "standards": tuple(standards),
        "regulatory_ethical_constraints": tuple(regulatory_ethical_constraints),
        "evidence_standard": evidence_standard,
        "common_ai_failure_modes": tuple(common_ai_failure_modes),
        "required_sources_tools": tuple(required_sources_tools),
        "source_ops": (
            source_ops
            if isinstance(source_ops, SourceOpsDescriptor)
            else SourceOpsDescriptor.model_validate(source_ops or {})
        ),
        "review_level": review_level,
        "forbidden_claims": tuple(forbidden_claims),
        "escalation_triggers": tuple(escalation_triggers),
        "good_examples": tuple(good_examples),
        "unsafe_examples": tuple(unsafe_examples),
        "usage_examples": tuple(usage_examples),
        "prebuilt_templates": tuple(prebuilt_templates),
        "version": version,
    }

    def _wrap(cls: T) -> T:
        # Store as a dict so the registry can materialise a BlockContract
        # with the right ``name`` at lookup time — class-level decorators
        # don't know the registered component name in advance.
        setattr(cls, "__g6_block_contract__", payload)
        return cls

    return _wrap


def get_block_contract_attr(cls: type) -> BlockContract | None:
    """Resolve the decorator payload to a concrete BlockContract.

    The component's name is taken from the ``name`` class attribute when
    present (most AIBlock subclasses set this), otherwise the class name
    in lowercase.
    """
    attr = getattr(cls, "__g6_block_contract__", None)
    if attr is None:
        return None
    if isinstance(attr, BlockContract):
        return attr
    if not isinstance(attr, dict):
        return None
    payload = dict(attr)
    if "name" not in payload:
        payload["name"] = getattr(cls, "name", None) or cls.__name__.lower()
    try:
        return BlockContract(**payload)
    except TypeError:
        return None


__all__ = ["block_contract", "get_block_contract_attr"]
