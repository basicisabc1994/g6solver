# SPDX-License-Identifier: Apache-2.0
"""Cost/latency/quality are measured together, with missing receipts visible."""
from g6.metaharness import Objective

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("metaharness_merge_rig", Path(__file__).with_name("_merge_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "harness"):
    _spec.loader.exec_module(rig)
harness = rig.harness


def test_opt_meta_03_d1_a_quality_gain_exposes_its_measured_cost_regression(tmp_path):
    mh, p = harness(tmp_path)
    report = mh.evaluate(p)["report"]
    got = mh.decide(report, p)
    measures = got["decision"].measurements
    assert measures["success"]["candidate_mean"] == 1
    assert measures["cost_usd"]["base_mean"] == 1 and measures["cost_usd"]["candidate_mean"] == 4
    assert "wall_clock_s" in measures and got["decision"].verdict == "refuse"
    # RED: removing the `if cost and not any(o.name == "cost_usd" ...):` gate branch hides the measured regression.


def test_opt_meta_01_a_report_never_activates_its_candidate(tmp_path):
    mh, p = harness(tmp_path, cost=0.5)
    report = mh.evaluate(p)["report"]
    got = mh.decide(report, p)
    assert got["decision"].measurements["cost_usd"]["diff_mean"] == -0.5
    assert got["decision"].verdict == "recommend" and len(mh.store.versions()) == 1
    assert got["completion_state"] == "qualified-draft"
    # RED: removing `checks=tuple(trail), measurements={...}` in gate.out loses the measured benefit.


def test_opt_meta_02_missing_required_cost_fails_closed(tmp_path):
    objectives = (Objective("success", "max"), Objective("cost_usd", "min"))
    mh, p = harness(tmp_path, missing=True, objectives=objectives)
    got = mh.decide(mh.evaluate(p)["report"], p)
    assert got["decision"].verdict == "refuse"
    assert got["decision"].measurements["cost_usd"]["status"] == "unknown"
    # RED: removing `if not est:` lets a missing required objective pass as zero.


def test_unknown_cost_is_reported_without_changing_legacy_objectives(tmp_path):
    mh, p = harness(tmp_path, missing=True)
    got = mh.decide(mh.evaluate(p)["report"], p)
    assert got["decision"].measurements["cost_usd"]["status"] == "unknown"
    assert got["decision"].verdict == "recommend"
    # RED: removing `name: dict(report.estimates.get(name) or {"status": "unknown"})` loses explicit unknown cost.

def test_internal_retry_sums_every_known_attempt_receipt():
    from g6.core.llm import EmptyRetry, FakeLLM
    from g6.metaharness.measurement import TraceCaller
    receipt, replies = {}, iter([("", 0.012, 7, 3), ("answer", 0.023, 11, 5)])
    def respond(system, user):
        text, cost, input_tokens, output_tokens = next(replies)
        receipt.update(cost_usd=cost, input_tokens=input_tokens, output_tokens=output_tokens)
        return text
    caller = TraceCaller(EmptyRetry(FakeLLM(respond)), step="answer", model="local", usage=lambda: dict(receipt))
    assert caller.complete("", "question") == "answer" and caller.calls == 2
    observed, = caller.observations
    assert round(observed.cost_usd, 6) == 0.035
    assert observed.input_tokens == 18 and observed.output_tokens == 8
    # RED: removing `receipts.append(read_receipt())` before the next attempt loses the first charge.


def test_internal_retry_with_any_unknown_charge_reports_unknown():
    from g6.core.llm import EmptyRetry, FakeLLM
    from g6.metaharness.measurement import TraceCaller
    for charges in ((None, 0.023), (0.012, None)):
        receipt, replies = {}, iter(zip(("", "answer"), charges))
        def respond(system, user):
            text, cost = next(replies)
            receipt.clear()
            receipt.update(input_tokens=7, output_tokens=3)
            if cost is not None:
                receipt["cost_usd"] = cost
            return text
        caller = TraceCaller(EmptyRetry(FakeLLM(respond)), step="answer", model="local", usage=lambda: dict(receipt))
        assert caller.complete("", "question") == "answer" and caller.calls == 2
        assert caller.observations[0].cost_usd is None
        assert caller.observations[0].input_tokens == 14
    # RED: removing `all(value is not None for value in values)` treats an incomplete charge as a total.


def test_unobserved_child_attempts_cannot_be_billed_as_the_last_receipt():
    from g6.core.model_calls import note_child_model_calls
    from g6.metaharness.measurement import TraceCaller
    class Child:
        def complete(self, system, user, *, max_tokens):
            note_child_model_calls(scoped=2, process=2)
            return "answer"
    caller = TraceCaller(Child(), step="answer", model="local", usage=lambda: {"cost_usd": 0.023})
    assert caller.complete("", "question") == "answer" and caller.calls == 2
    assert caller.observations[0].cost_usd is None
    # RED: removing `len(receipts) == attempts` reports a final receipt as the cost of unobserved attempts.


def test_attempt_receipt_observer_is_scoped_and_restored():
    from g6.core.model_calls import counting, note_model_call
    seen = []
    with counting(before_call=lambda: seen.append("outer")) as outer:
        note_model_call()
        with counting(before_call=lambda: seen.append("inner")) as inner:
            note_model_call()
        note_model_call()
    note_model_call()
    assert seen == ["outer", "outer", "inner", "outer"]
    assert (outer(), inner()) == (3, 1)
    # RED: removing `scope.before_call()` prevents receipts from being captured at attempt boundaries.

def test_exhausted_empty_retries_keep_every_known_charge():
    import pytest
    from g6.core.llm import EmptyRetry, FakeLLM, LLMError
    from g6.metaharness.measurement import TraceCaller
    caller = TraceCaller(EmptyRetry(FakeLLM(default="")), step="answer", model="local",
                         usage=lambda: {"cost_usd": 0.01})
    with pytest.raises(LLMError, match="empty completion"):
        caller.complete("", "question")
    assert caller.calls == 4 and caller.observations[0].cost_usd == pytest.approx(0.04)
    assert caller.observations[0].error == "G6_E_META_CALL_FAILED"
    # RED: removing the final `receipts.append(read_receipt())` loses the last charged empty attempt.
