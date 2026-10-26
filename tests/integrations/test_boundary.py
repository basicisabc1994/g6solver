# SPDX-License-Identifier: Apache-2.0
"""Caller JSON budgets must hold before an encoder sees the value."""
import json

import pytest
from pydantic import ValidationError

from g6.integrations import _boundary
from g6.integrations.adapt_rq import RQInput
from g6.integrations.hat_orchestrator import HATOrchestratorInput


@pytest.mark.parametrize("value", ["x" * 65_537, ["x" * 22_000] * 3,
                                   {"x" * 33_000: 0, "y" * 33_000: 0},
                                   [[None] * 100] * 100, "\U0001f600" * 6_000, 1 << 300_000],
                         ids=["one-string", "cumulative-strings", "keys", "nodes", "escaped-unicode", "integer"])
def test_cumulative_json_budgets_refuse_before_encoding(monkeypatch, value):
    monkeypatch.setattr(json, "dumps", lambda *a, **kw: pytest.fail("oversize input encoded"))
    with pytest.raises(_boundary.Refusal) as error:
        _boundary.json_value(value)
    assert error.value.code == "G6_E_RESOURCE_LIMIT"
    # Removal of `visit(value)` before returning the JSON value makes this test fail.


@pytest.mark.parametrize("kind", ["rq", "hat"])
def test_typed_inputs_apply_json_budget_before_encoding(monkeypatch, kind):
    monkeypatch.setattr(json, "dumps", lambda *a, **kw: pytest.fail("oversize input encoded"))
    with pytest.raises(ValidationError):
        if kind == "rq":
            RQInput(op="enqueue", function="size", args=[["x" * 22_000] * 3])
        else:
            HATOrchestratorInput(op="complete_task", task_id="t", agent_id="a", claim=["x" * 6_000] * 3)
    # Removal of `visit(value)` in the shared input validator makes this test fail.


def test_json_budget_accepts_small_values_and_refuses_non_json():
    value = {"answer": [None, True, 17, -3.5, "\U0001f600"]}
    assert _boundary.json_value(value) is value
    for bad in (float("nan"), float("inf"), {1: "bad"}, (1, 2), object()):
        with pytest.raises(ValueError):
            _boundary.json_value(bad)
    # Removal of the finite-number/type checks in `visit` makes this test fail.
