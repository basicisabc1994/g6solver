# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""K-P0-2 door port — the fit-for-purpose floor at the promotion-law door.

A surface that emits ``verified`` at the promotion-law / orchestrator door (a
``ReliabilityEnvelope``) is now ALSO held to its declared critical success criteria,
via the SAME ``G6_TASK_CONTRACT_ENFORCE`` staging as the reliability-pipeline seam.
DEMOTE-only, dark by default (an envelope that declares no criteria is byte-identical
even under enforce). ``G6_KERNEL_PROMOTION_LAW`` is kept off so these isolate the
fit-for-purpose floor from the witness law.
"""
from __future__ import annotations

import pytest
from g6.core.reliability_envelope import ReliabilityEnvelope

_CRIT_NO_VERIFIER = {"text": "must be proven", "critical": True, "verifier": ""}
_CRIT_HUMAN = {"text": "expert review", "critical": True, "verifier": "human:expert_review"}
_NONCRIT_NO_VERIFIER = {"text": "nice to have", "critical": False, "verifier": ""}


@pytest.fixture(autouse=True)
def _law_off(monkeypatch):
    # isolate the fit-for-purpose floor from the witness promotion law
    monkeypatch.delenv("G6_KERNEL_PROMOTION_LAW", raising=False)
    # an ambient per-surface scope degrades this identity-less door to shadow
    # (twin of the G6_PROMOTION_ENFORCE_SURFACES scrub in the promotion suites)
    monkeypatch.delenv("G6_TASK_CONTRACT_ENFORCE_SURFACES", raising=False)


def _state(criteria=None, *, mode=None) -> str:
    kwargs: dict = {"reliability_label": "verified", "completion_state": "verified"}
    if criteria is not None:
        kwargs["task_success_criteria"] = criteria
    if mode is not None:
        kwargs["task_mode"] = mode
    return ReliabilityEnvelope(**kwargs).completion_state


def test_off_by_default_is_inert(monkeypatch):
    monkeypatch.delenv("G6_TASK_CONTRACT_ENFORCE", raising=False)
    # even a critical criterion with no available verifier does NOT demote when off
    assert _state([_CRIT_NO_VERIFIER]) == "verified"


def test_legacy_envelope_no_criteria_stays_verified_under_enforce(monkeypatch):
    monkeypatch.setenv("G6_TASK_CONTRACT_ENFORCE", "enforce")
    assert _state() == "verified"  # no declared criteria → byte-identical
    assert _state([]) == "verified"


def test_critical_without_verifier_caps_under_enforce(monkeypatch):
    monkeypatch.setenv("G6_TASK_CONTRACT_ENFORCE", "enforce")
    assert _state([_CRIT_NO_VERIFIER]) == "qualified-draft"


def test_non_critical_without_verifier_stays_verified_under_enforce(monkeypatch):
    monkeypatch.setenv("G6_TASK_CONTRACT_ENFORCE", "enforce")
    assert _state([_NONCRIT_NO_VERIFIER]) == "verified"


def test_critical_with_human_verifier_stays_verified_under_enforce(monkeypatch):
    monkeypatch.setenv("G6_TASK_CONTRACT_ENFORCE", "enforce")
    assert _state([_CRIT_HUMAN]) == "verified"


def test_critical_with_registered_oracle_stays_verified_under_enforce(monkeypatch):
    from g6.core import promotion_evaluator

    promotion_evaluator.register_oracle("test_door_oracle_kp02", lambda _w: None)
    monkeypatch.setenv("G6_TASK_CONTRACT_ENFORCE", "enforce")
    assert _state([
        {"text": "reproduced by oracle", "critical": True, "verifier": "test_door_oracle_kp02"}
    ]) == "verified"


def test_shadow_does_not_apply(monkeypatch):
    monkeypatch.setenv("G6_TASK_CONTRACT_ENFORCE", "shadow")
    assert _state([_CRIT_NO_VERIFIER]) == "verified"


def test_exploratory_mode_caps_under_enforce(monkeypatch):
    monkeypatch.setenv("G6_TASK_CONTRACT_ENFORCE", "enforce")
    assert _state(mode="exploratory") == "qualified-draft"


def test_malformed_criteria_never_demote_under_enforce(monkeypatch):
    monkeypatch.setenv("G6_TASK_CONTRACT_ENFORCE", "enforce")
    # a non-iterable / junk criteria field must never force a demote (fail-open to
    # verified w.r.t. the cap — a broken contract withholds the cap, never applies it)
    assert _state(criteria=5) == "verified"  # type: ignore[arg-type]
    assert _state([None, "junk", 7]) == "verified"
