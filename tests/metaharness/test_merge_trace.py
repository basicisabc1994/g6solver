# SPDX-License-Identifier: Apache-2.0
"""Measured trace contract: no unchecked success, no ambiguous graph."""
import pytest
from pydantic import ValidationError

from g6.metaharness import MeasuredStep, MeasuredTrace, validate_dag


def test_opt_shared_02_unchecked_is_not_passed():
    step = MeasuredStep(name="answer", latency_s=0.3)
    assert step.passed is None and step.checked_by == () and step.cost_usd is None
    with pytest.raises(ValidationError):
        MeasuredStep(name="answer", latency_s=0.3, passed=True)
    # RED: removing `if self.passed is not None and not self.checked_by:` admits an unnamed check.


def test_opt_shared_01_a_valid_graph_is_only_a_draft():
    trace = MeasuredTrace(case_id="dev-1", steps=(MeasuredStep(name="answer", latency_s=0.3),))
    assert trace.completion_state == "qualified-draft"
    with pytest.raises(ValidationError):
        MeasuredTrace(case_id="dev-1", steps=trace.steps, completion_state="verified")
    # RED: removing `completion_state: Literal["qualified-draft"]` admits an unearned label.


@pytest.mark.parametrize("edges", [
    {"a": ("b",), "b": ("c",), "c": ("a",)},
    {"a": ("missing",)}, {"a": ("a",)},
])
def test_opt_shared_d1_invalid_dags_never_reach_a_proposer(edges):
    steps = tuple(MeasuredStep(name=n, depends_on=d, latency_s=0.1) for n, d in edges.items())
    assert validate_dag(steps)
    with pytest.raises(ValidationError, match="G6_E_META_TRACE"):
        MeasuredTrace(case_id="dev-x", steps=steps)
    # RED: removing `raise ValueError("G6_E_META_TRACE: " + "; ".join(problems))` admits the graph.


def test_trace_refuses_duplicate_steps_and_unknown_fields():
    step = MeasuredStep(name="a", latency_s=0.1)
    with pytest.raises(ValidationError):
        MeasuredTrace(case_id="d", steps=(step, step))
    with pytest.raises(ValidationError):
        MeasuredStep(name="a", latency_s=0.1, confidence=1.0)
    # RED: removing the duplicate-name check or `extra="forbid"` admits ambiguous observations.
