# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The workforce's completion contract (workforce OS plan, P0 T1): what a component reply,
an attempt and a verdict mean, read once, worst state wins.

Pinned here: the single reader sees through every place the outer envelope hides the
component's own state (the outer envelope stamps qualified-draft and degraded=False whatever
the payload says, base_helpers/__init__.py:677-685); a component can never certify itself
verified; and only an executed, independent, model-free verdict promotes. Offline: the
registry answer is faked under the real ``invoke_component`` envelope builder.
"""
from __future__ import annotations

import pytest
from g6.workforce.chain import StepResult
from g6.workforce import contracts as wc


BLOCKED, QUALIFIED, VERIFIED = "blocked-escalated", "qualified-draft", "verified"




# --- the single reader sees through the envelope ---












def test_job_metadata_nested_three_levels_down_is_read():
    reply = {"ok": True, "completion_state": QUALIFIED,
             "result": {"result": {"metadata": {"answered_on_defaults": True}}}}
    assert wc.Outcome.from_envelope(reply).state == BLOCKED








def test_a_degraded_computed_answer_with_nothing_else_is_not_measured():
    """The computed answer's satisfiable is not an answer either: a degraded result that carries nothing
    else measured nothing (chain.verdict's rule), as before the answer kind existed."""
    from g6.workforce.chain import verdict

    reply = {"ok": True, "result": {"satisfiable": None, "answer_kind": "computed", "degraded": True,
                                    "degradation_reason": "z3 missing"}}
    assert verdict(reply)[:2] == ("not_measured", False)






@pytest.mark.parametrize("reply", [None, "ok", ["ok"], 7])
def test_a_reply_that_is_not_an_envelope_is_blocked(reply):
    assert wc.Outcome.from_envelope(reply).state == BLOCKED


# Codex review of T1 (2026-10-01): each of these escaped as qualified-draft because the deeper levels
# were read for reasons only, never for the state.
@pytest.mark.parametrize("reply", [
    {"ok": True, "result": {"result": {"completion_state": BLOCKED}}},
    {"ok": True, "result": {"result": '{"completion_state": "blocked-escalated"}'}},
    {"ok": True, "result": {"result": {"ok": False}}},
    {"ok": True, "result": {"result": {"error": "refused"}}},
    {"ok": True, "result": {"result": {"satisfiable": None}}},
    {"ok": True, "result": {"result": {"status": "unknown"}}},
    {"ok": True, "result": {"success": False}},
    {"ok": True, "result": {"answer": 1, "reliability_envelope": {"completion_state": BLOCKED}}},
], ids=["nested-block", "json-string-block", "nested-ok-false", "nested-error", "nested-unknown-sat",
        "nested-unknown-status", "inner-success-false", "envelope-block"])
def test_a_block_or_failure_at_any_nesting_level_blocks(reply):
    assert wc.Outcome.from_envelope(reply).state == BLOCKED


def test_degradation_inside_a_reliability_envelope_is_seen():
    reply = {"ok": True, "result": {"answer": 1, "reliability_envelope": {"degraded": True,
                                                                         "degradation_reason": "no corpus"}}}
    out = wc.Outcome.from_envelope(reply)
    assert out.degraded is True and any("no corpus" in r for r in out.reasons), out




def test_nesting_deeper_than_the_reader_follows_fails_closed():
    reply: dict = {"answer": 1}
    for _ in range(wc.MAX_DEPTH + 2):
        reply = {"ok": True, "result": reply}
    out = wc.Outcome.from_envelope(reply)
    assert out.state == BLOCKED and any("deeper" in r for r in out.reasons), out


# --- the algebra ---


def test_the_state_order_is_the_products_own_precedence():
    product = [s for s in wc._usage_PRECEDENCE if s in wc.STATES]  # the vendored middleware.usage order
    assert sorted(wc.STATES, key=wc.rank) == product


def test_combine_takes_the_worst_state_any_degradation_and_every_reason():
    a = wc.Outcome(VERIFIED, reasons=("a",), evidence=("e1",))
    b = wc.Outcome(QUALIFIED, degraded=True, reasons=("b", "a"), evidence=("e2",))
    c = wc.Outcome(BLOCKED, reasons=("c",))
    for order in ((a, b, c), (c, b, a), (b, c, a)):
        out = wc.combine(*order)
        assert out.state == BLOCKED and out.degraded is True, out
        assert set(out.reasons) == {"a", "b", "c"} and len(out.reasons) == 3, out.reasons
    assert wc.combine(a, b).state == QUALIFIED
    assert set(wc.combine(a, b).evidence) == {"e1", "e2"}


def test_combining_nothing_is_refused_not_verified():
    with pytest.raises(ValueError):
        wc.combine()


def test_demote_never_promotes():
    blocked = wc.Outcome(BLOCKED, reasons=("x",))
    assert blocked.demote(VERIFIED, "no").state == BLOCKED
    assert wc.Outcome(VERIFIED).demote(QUALIFIED, "unmeasured check").state == QUALIFIED


def test_states_are_canonical_and_the_underscore_vocabulary_is_normalised():
    assert wc.normalize_state("qualified_draft") == QUALIFIED
    assert wc.normalize_state("blocked_escalated") == BLOCKED
    with pytest.raises(ValueError):
        wc.Outcome("qualified_draft")
    with pytest.raises(ValueError):
        wc.normalize_state("done")


# --- only an executed, independent, model-free verdict promotes ---


def _verdict(**kw) -> wc.Verdict:
    base = {"verifier_kind": "oracle", "result": "pass", "executed": True, "independent": True, "model_calls": 0}
    return wc.Verdict(**{**base, **kw})


def test_an_executed_independent_model_free_pass_is_verified():
    assert _verdict().outcome().state == VERIFIED


@pytest.mark.parametrize("change, why", [
    ({"executed": False}, "not executed"),
    ({"independent": False}, "not independent"),
    ({"model_calls": 1}, "model"),
    ({"verifier_kind": "model_critic"}, "model critic"),
])
def test_any_missing_condition_keeps_a_pass_at_qualified_draft(change, why):
    out = _verdict(**change).outcome()
    assert out.state == QUALIFIED, out
    assert any(why in r for r in out.reasons), out.reasons


def test_a_failed_verdict_blocks_and_an_unmeasured_one_does_not_verify():
    assert _verdict(result="fail", reason="2 tests failed").outcome().state == BLOCKED
    assert _verdict(result="unmeasured", executed=False).outcome().state == QUALIFIED


def test_a_verdict_is_read_from_a_chain_step_result():
    ran_ok = wc.Verdict.from_step(StepResult("z3", "ran", True), verifier_kind="formal", independent=True)
    ran_no = wc.Verdict.from_step(StepResult("z3", "ran", False, "sums differ"), verifier_kind="formal",
                                  independent=True)
    unmeasured = wc.Verdict.from_step(StepResult("z3", "not_measured", False, "solver answered unknown"),
                                      verifier_kind="formal", independent=True)
    assert (ran_ok.result, ran_ok.executed) == ("pass", True)
    assert (ran_no.result, ran_no.reason) == ("fail", "sums differ")
    assert (unmeasured.result, unmeasured.executed) == ("unmeasured", False)


def test_unknown_verifier_kinds_and_results_are_refused():
    with pytest.raises(ValueError):
        _verdict(verifier_kind="vibes")
    with pytest.raises(ValueError):
        _verdict(result="maybe")


@pytest.mark.parametrize("change", [
    {"op": "other.run_tests"}, {"check": "another-check"}, {"judges": "another-unit"},
    {"artifact_id": "superseded-output"},
])
def test_a_suite_receipt_cannot_qualify_a_different_operation_check_or_output(change):
    receipt = {"op": "workforce.run_tests", "check": "test", "judges": "patch", "artifact_id": "current-output",
               "suite": {"status": "not_run"}}
    closed = [{"run_id": "producer", "unit": "patch", "state": "closed", "artifact_id": "current-output"},
              {"run_id": "verifier", "unit": "test", "state": "closed", "artifact_id": "",
               "verification": [{**receipt, **change}]}]
    assert wc.current_suite_limits(closed) == ()


def test_an_absent_receipt_list_is_not_treated_as_an_explicit_suite_limitation():
    assert wc.suite_receipt_limits([{"run_id": "legacy", "verification": None}]) == {}
