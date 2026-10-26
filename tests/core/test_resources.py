"""g6.core.resources: declared limits and the usage a component counts itself, checked before each unit of work."""
from __future__ import annotations

import dataclasses

import pytest

from g6.core.resources import RESOURCE_LIMIT, ResourceBounds, ResourceUsage, check
from g6.goal_engine.schema import ResourceBoundsSpec


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _usage() -> tuple[ResourceUsage, Clock]:
    clock = Clock()
    return ResourceUsage(clock=clock), clock


def test_the_code_is_the_shared_one() -> None:
    assert RESOURCE_LIMIT == "G6_E_RESOURCE_LIMIT"


def test_goal_engine_limits_map_onto_the_shared_bounds() -> None:
    spec_fields = set(ResourceBoundsSpec.model_fields)
    assert spec_fields <= {f.name for f in dataclasses.fields(ResourceBounds)}
    spec = ResourceBoundsSpec(max_tokens_per_hour=200_000, max_execution_seconds=30)
    assert ResourceBounds(**spec.model_dump()).max_tokens_per_hour == 200_000


@pytest.mark.parametrize("value", [-1, True, "10", float("nan"), float("inf")])
def test_a_limit_must_be_a_finite_non_negative_number(value: object) -> None:
    with pytest.raises(ValueError, match="max_disk_bytes"):
        ResourceBounds(max_disk_bytes=value)  # type: ignore[arg-type]


def test_no_limits_never_refuse() -> None:
    usage, _ = _usage()
    usage.record_tokens(10**9)
    usage.record_cost(10**6)
    assert check(ResourceBounds(), usage) is None


def test_a_token_limit_refuses_once_reached() -> None:
    usage, _ = _usage()
    usage.record_tokens(99)
    bounds = ResourceBounds(max_tokens_per_minute=100)
    assert check(bounds, usage) is None
    usage.record_tokens(1)
    assert check(bounds, usage) == "100 tokens in the last minute reach the limit of 100"


def test_tokens_leave_the_window_as_time_passes() -> None:
    usage, clock = _usage()
    usage.record_tokens(100)
    clock.now += 61
    assert usage.tokens_in_window(60) == 0
    assert usage.tokens_in_window(3600) == 100
    assert check(ResourceBounds(max_tokens_per_minute=100), usage) is None
    assert check(ResourceBounds(max_tokens_per_hour=100), usage) == (
        "100 tokens in the last hour reach the limit of 100")


def test_cost_counts_only_the_last_day() -> None:
    usage, clock = _usage()
    usage.record_cost(0.75)
    clock.now += 86_401
    usage.record_cost(0.5)
    bounds = ResourceBounds(max_cost_per_day=1.0)
    assert check(bounds, usage) is None
    usage.record_cost(0.5)
    assert check(bounds, usage) == "US$1.0000 in the last day reaches the limit of US$1.0000"
    assert check(ResourceBounds(max_cost_per_month=1.5), usage) == (
        "US$1.7500 in the last 30 days reaches the limit of US$1.5000")


def test_time_and_disk_refuse_once_reached() -> None:
    usage, clock = _usage()
    clock.now += 9.5
    assert check(ResourceBounds(max_execution_seconds=10), usage) is None
    clock.now += 0.5
    assert check(ResourceBounds(max_execution_seconds=10), usage) == "10.0 seconds reach the limit of 10 seconds"
    usage.record_disk(2048)
    assert check(ResourceBounds(max_disk_bytes=2048), usage) == "2048 bytes on disk reach the limit of 2048 bytes"


def test_time_is_checked_first() -> None:
    usage, clock = _usage()
    usage.record_tokens(500)
    clock.now += 20
    message = check(ResourceBounds(max_execution_seconds=10, max_tokens_per_minute=100), usage)
    assert message is not None and message.startswith("20.0 seconds")


@pytest.mark.parametrize("record", ["record_tokens", "record_cost", "record_disk"])
def test_a_negative_amount_is_refused(record: str) -> None:
    usage, _ = _usage()
    with pytest.raises(ValueError):
        getattr(usage, record)(-1)


def test_totals_are_kept() -> None:
    usage, _ = _usage()
    usage.record_tokens(3)
    usage.record_tokens(4)
    usage.record_cost(0.25)
    usage.record_disk(10)
    assert (usage.tokens_used, usage.cost_used, usage.disk_bytes_used) == (7, 0.25, 10)
