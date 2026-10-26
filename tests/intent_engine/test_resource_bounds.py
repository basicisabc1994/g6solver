# SPDX-License-Identifier: Apache-2.0
"""IE-RB (found by the goal_engine port, 2026-10-18): declared resource limits are validated, not crashed on or let
through. The ported ``ResourceBoundsSchema`` compared ``v < 0`` before any type coercion, so a NaN limit was accepted
(it compares false with every usage figure and can never trip), infinity was accepted, and a string raised a raw
``TypeError`` instead of a validation error."""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from g6.intent_engine.schema import GoalInput, ResourceBoundsSchema


@pytest.mark.parametrize("field", ["max_execution_seconds", "max_tokens_per_hour"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -1, "not a number"])
def test_ie_rb_a_limit_that_cannot_bound_anything_is_a_validation_error(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ResourceBoundsSchema(**{field: value})


def test_ie_rb_survival_ordinary_limits_still_load() -> None:
    bounds = ResourceBoundsSchema(max_execution_seconds=60, max_disk_bytes=0, max_tokens_per_hour=500_000)
    assert (bounds.max_execution_seconds, bounds.max_disk_bytes, bounds.max_tokens_per_hour) == (60, 0, 500_000)
    goal = GoalInput.model_validate({"goal": "g", "resource_bounds": {"max_tokens_per_day": 10}})
    assert goal.resource_bounds is not None and goal.resource_bounds.max_tokens_per_day == 10
