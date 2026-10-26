# SPDX-License-Identifier: Apache-2.0
"""g6.invariants: each rule an envelope can break is named, and a pass from no blocking check is no pass. Offline."""
from __future__ import annotations

import pytest

from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.invariants import RELIABILITY_LABELS, assert_envelope, check_envelope, nonvacuous_pass

CARD = warning_card("G6_E_CHECK_NOT_RUN", "the check did not run")


def _rules(payload) -> list[str]:
    return [v.rule for v in check_envelope(payload)]


def test_the_labels_are_the_cores() -> None:
    assert RELIABILITY_LABELS == {"verified", "qualified-draft", "blocked-escalated"}


def test_well_formed_envelopes_pass() -> None:
    draft = envelope_fields(reliability_label="qualified-draft", warning_card=CARD, request_id="q", run_id="r")
    verified = {"completion_state": "verified", "reliability_label": "verified", "degraded": False,
                "evidence": [{"kind": "test", "ref": "t1"}], "request_id": "q", "run_id": "r"}
    assert check_envelope(draft) == [] and check_envelope(verified) == []
    assert_envelope(draft)


def test_a_verified_the_core_holds_is_still_a_whole_envelope() -> None:
    """The promotion law holds a stated verified that G6 did not re-check (plan A1); the held envelope says why."""
    held = envelope_fields(reliability_label="verified", evidence=[{"kind": "test", "ref": "t1"}],
                           request_id="q", run_id="r")
    assert held["completion_state"] == "qualified-draft" and check_envelope(held) == []
    assert held["warning_card"]["code"] == "G6_E_PROMOTION_LAW" and held["claimed_completion_state"] == "verified"


def test_a_result_that_is_not_verified_needs_a_card_with_a_stable_code() -> None:
    assert _rules(envelope_fields(reliability_label="qualified-draft", request_id="q", run_id="r")) == [
        "missing_warning_card"]
    bad = envelope_fields(reliability_label="blocked-escalated", warning_card=warning_card("oops", "m"),
                          request_id="q", run_id="r")
    assert _rules(bad) == ["unstable_code"]


def test_verified_needs_evidence() -> None:
    assert _rules({"completion_state": "verified", "evidence": [], "request_id": "q", "run_id": "r"}) == [
        "verified_without_evidence"]


@pytest.mark.parametrize(("payload", "rule"), [
    ({"completion_state": "done", "request_id": "", "run_id": ""}, "unknown_state"),
    ({"completion_state": "verified", "reliability_label": "qualified-draft", "evidence": [1], "request_id": "",
      "run_id": ""}, "label_mismatch"),
    ({"completion_state": "verified", "evidence": [1], "run_id": ""}, "missing_id"),
    ({"completion_state": "verified", "evidence": [1], "request_id": ""}, "missing_id"),
    ({"completion_state": "verified", "evidence": [1], "degraded": True, "request_id": "", "run_id": ""},
     "degraded_mismatch"),
    ({"completion_state": "qualified-draft", "warning_card": dict(CARD), "degraded": False, "request_id": "",
      "run_id": ""}, "degraded_mismatch"),
    (["not", "a", "mapping"], "not_an_envelope"),
])
def test_each_broken_rule_is_named(payload, rule) -> None:
    assert rule in _rules(payload)


def test_assert_envelope_lists_every_violation() -> None:
    with pytest.raises(AssertionError, match="unknown_state.*missing_id.*missing_id"):
        assert_envelope({"completion_state": None})


@pytest.mark.parametrize(("results", "passed"), [
    ([], False),                                   # nothing ran
    ([(True, False)], False),                      # only advisory checks ran
    ([(True, True)], True),
    ([(True, True), (False, True)], False),
    ([(True, True), (False, False)], True),        # an advisory failure does not block
])
def test_nonvacuous_pass(results, passed) -> None:
    assert nonvacuous_pass(results) is passed
