# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Tests for cross-process BlockContract serialization (V2-3).

A contract that crosses an MCP boundary must round-trip through JSON. The
callable fields (preconditions / stop_conditions) reduce to their
``__name__`` on the way out and re-resolve through the local
PRECONDITION_REGISTRY / STOP_CONDITION_REGISTRY on the way in. Unknown
names degrade to safe stand-ins (precondition → fails closed, stop
condition → never fires).
"""
from __future__ import annotations

import json

import pytest

from g6.core.block_contract import (
    BlockContract,
    ComponentExample,
    ComponentTemplate,
    CostEstimate,
    FailureMode,
    SourceToolRequirement,
    PRECONDITION_REGISTRY,
    STOP_CONDITION_REGISTRY,
    contract_from_dict,
    contract_to_dict,
    register_precondition,
    register_stop_condition,
)


@pytest.fixture(autouse=True)
def _isolated_registries():
    """Snapshot+restore the global callable registries around each test."""
    saved_pre = dict(PRECONDITION_REGISTRY)
    saved_stop = dict(STOP_CONDITION_REGISTRY)
    try:
        yield
    finally:
        PRECONDITION_REGISTRY.clear()
        PRECONDITION_REGISTRY.update(saved_pre)
        STOP_CONDITION_REGISTRY.clear()
        STOP_CONDITION_REGISTRY.update(saved_stop)


def test_to_dict_emits_plain_json_serialisable_payload():
    @register_precondition
    def has_brief(ctx):  # noqa: ANN001
        return bool(ctx.get("context"))

    contract = BlockContract(
        name="example",
        purpose="example purpose",
        inputs_required=("x",),
        preconditions=(has_brief,),
        known_failure_modes=(FailureMode("drift", detector="noop", severity="low"),),
        cost_estimate=CostEstimate(tokens_p50=10, tokens_p95=100),
        verification_method="external",
        human_review_required="R2+",
    )
    payload = contract_to_dict(contract)
    # Round-trip through json — proves there are no Python-only objects left.
    blob = json.dumps(payload)
    assert "has_brief" in blob
    assert "drift" in blob


def test_round_trip_preserves_resolved_callables():
    @register_precondition
    def has_brief(ctx):  # noqa: ANN001
        return bool(ctx.get("context"))

    @register_stop_condition
    def contains_drop(output):  # noqa: ANN001
        return "DROP" in str(getattr(output, "result", "")).upper()

    contract = BlockContract(
        name="ex",
        preconditions=(has_brief,),
        stop_conditions=(contains_drop,),
    )
    payload = contract_to_dict(contract)
    rebuilt = contract_from_dict(payload)

    # The rebuilt callables resolve to the registered functions.
    assert rebuilt.preconditions[0] is has_brief
    assert rebuilt.stop_conditions[0] is contains_drop


def test_unresolved_precondition_fails_closed():
    """A precondition name that wasn't registered locally must NOT pass —
    the safe default is to treat the precondition as unmet (so the task
    contract reports it as a missing input).
    """
    payload = {
        "name": "ex",
        "preconditions": ["never_registered_locally"],
    }
    rebuilt = contract_from_dict(payload)
    fn = rebuilt.preconditions[0]
    assert fn({"task": "anything"}) is False
    assert "unresolved" in fn.__name__


def test_unresolved_stop_condition_never_fires():
    """A stop condition name that wasn't registered locally must NOT trigger
    — safer to allow the call than to spuriously block on a check the local
    runtime doesn't recognise.
    """
    payload = {
        "name": "ex",
        "stop_conditions": ["unknown_stop_check"],
    }
    rebuilt = contract_from_dict(payload)
    fn = rebuilt.stop_conditions[0]

    class Out:
        result = "DROP TABLE users;"

    # Even an obviously-bad output doesn't fire the unknown stop condition.
    assert fn(Out()) is False
    assert "unresolved" in fn.__name__


def test_register_precondition_with_explicit_name():
    @register_precondition(name="custom.name.alias")
    def some_check(ctx):  # noqa: ANN001
        return True

    payload = contract_to_dict(BlockContract(name="ex", preconditions=(some_check,)))
    # The serialised name comes from the function's __name__, not the
    # alias — by design. The alias is for resolution; serialisation uses
    # the natural name.
    assert payload["preconditions"] == ["some_check"]
    # But the alias resolution still works:
    rebuilt = contract_from_dict({"name": "ex", "preconditions": ["custom.name.alias"]})
    assert rebuilt.preconditions[0] is some_check


def test_failure_mode_round_trip():
    contract = BlockContract(
        name="ex",
        known_failure_modes=(
            FailureMode("hallucination", detector="d1", severity="high", description="bad"),
            FailureMode("timeout", detector="d2", severity="medium"),
        ),
    )
    rebuilt = contract_from_dict(contract_to_dict(contract))
    assert len(rebuilt.known_failure_modes) == 2
    assert rebuilt.known_failure_modes[0].name == "hallucination"
    assert rebuilt.known_failure_modes[0].severity == "high"


def test_cost_estimate_round_trip():
    contract = BlockContract(
        name="ex",
        cost_estimate=CostEstimate(tokens_p50=100, tokens_p95=500, wall_seconds_p95=3.5, usd_p95=0.05),
    )
    rebuilt = contract_from_dict(contract_to_dict(contract))
    assert rebuilt.cost_estimate.tokens_p50 == 100
    assert rebuilt.cost_estimate.tokens_p95 == 500
    assert rebuilt.cost_estimate.usd_p95 == 0.05


def test_component_card_fields_round_trip():
    contract = BlockContract(
        name="ex",
        purpose="extended card",
        outputs=("memo",),
        domain="security",
        standards=("SOC-2",),
        required_sources_tools=(SourceToolRequirement("logs", purpose="incident evidence"),),
        usage_examples=(ComponentExample("triage", input={"task": "incident"}),),
        prebuilt_templates=(ComponentTemplate("triage_template", payload={"op": "triage"}),),
        review_level="expert_review",
    )
    payload = contract_to_dict(contract)
    assert payload["reliability_card"]["block_name"] == "ex"
    rebuilt = contract_from_dict(payload)
    assert rebuilt.domain == "security"
    assert rebuilt.required_sources_tools[0].name == "logs"
    assert rebuilt.usage_examples[0].title == "triage"
    assert rebuilt.prebuilt_templates[0].name == "triage_template"


def test_blockcontract_is_frozen():
    """Sanity: BlockContract remains immutable (frozen dataclass)."""
    c = BlockContract(name="x")
    with pytest.raises(Exception):
        c.name = "y"  # type: ignore[misc]
