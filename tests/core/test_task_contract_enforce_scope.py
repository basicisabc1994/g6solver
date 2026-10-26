# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Per-surface staging scope for the K-P0-2 fit-for-purpose floor.

``G6_TASK_CONTRACT_ENFORCE_SURFACES`` is the twin of the kernel promotion
law's ``G6_PROMOTION_ENFORCE_SURFACES`` / ``promotion_enforce_scope`` (see
``tests/mvp/core/test_kernel_promotion_law.py``): it narrows the floor's
``enforce`` disposition to a named surface cohort so it can be enabled one
cohort at a time, with instant rollback (unset the var). Scoping can only
NARROW enforce, never widen it; off/shadow stay global and unaffected.
"""
from __future__ import annotations

import json
import logging

import pytest
from g6.core.reliability_envelope import ReliabilityEnvelope
from g6.core.task_contract_verifier import (
    effective_task_contract_enforce_mode,
    task_contract_enforce_mode,
    task_contract_enforce_scope,
)

FLAG = "G6_TASK_CONTRACT_ENFORCE"
SCOPE = "G6_TASK_CONTRACT_ENFORCE_SURFACES"

_CRIT_NO_VERIFIER = {"text": "must be proven", "critical": True, "verifier": ""}


@pytest.fixture(autouse=True)
def _law_off(monkeypatch):
    # isolate the fit-for-purpose floor from the witness promotion law
    monkeypatch.delenv("G6_KERNEL_PROMOTION_LAW", raising=False)


# --------------------------------------------------------------------- #
# Flag parsing
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("*", None),
        ("all", None),
        ("ALL", None),
        ("__all__", None),
        ("document_stc", frozenset({"document_stc"})),
        ("Document_STC, coder ", frozenset({"document_stc", "coder"})),
        (",,", None),
    ],
)
def test_task_contract_enforce_scope_parsing(monkeypatch, raw, expected) -> None:
    if raw is None:
        monkeypatch.delenv(SCOPE, raising=False)
    else:
        monkeypatch.setenv(SCOPE, raw)
    assert task_contract_enforce_scope() == expected


def test_effective_mode_off_and_shadow_ignore_scope(monkeypatch) -> None:
    """Scoping only touches enforce; off/shadow are global and unchanged."""
    monkeypatch.setenv(SCOPE, "document_stc")
    monkeypatch.setenv(FLAG, "off")
    assert effective_task_contract_enforce_mode("anything") == "off"
    monkeypatch.setenv(FLAG, "shadow")
    assert effective_task_contract_enforce_mode("anything") == "shadow"
    assert effective_task_contract_enforce_mode(None) == "shadow"


def test_effective_mode_enforce_unscoped_is_global(monkeypatch) -> None:
    monkeypatch.delenv(SCOPE, raising=False)
    monkeypatch.setenv(FLAG, "enforce")
    assert effective_task_contract_enforce_mode("document_stc") == "enforce"
    assert effective_task_contract_enforce_mode(None) == "enforce"  # core/pipeline seams too


def test_effective_mode_enforce_scoped_narrows(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "document_stc")
    assert effective_task_contract_enforce_mode("document_stc") == "enforce"  # in cohort
    assert effective_task_contract_enforce_mode("DOCUMENT_STC") == "enforce"  # case-insensitive
    assert effective_task_contract_enforce_mode("coder") == "shadow"          # out of cohort
    assert effective_task_contract_enforce_mode(None) == "shadow"             # no identity
    assert effective_task_contract_enforce_mode("") == "shadow"               # empty name


def test_effective_mode_wildcard_enforces_globally(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "*")
    assert effective_task_contract_enforce_mode("coder") == "enforce"
    assert effective_task_contract_enforce_mode(None) == "enforce"


def test_scope_disable_word_is_a_nonmatching_cohort_fail_safe(monkeypatch) -> None:
    """A value like 'off' is NOT a wildcard: it becomes a cohort name that matches
    no surface, so enforce degrades to shadow (fail-safe) rather than turning the
    floor off. Nothing but ``G6_TASK_CONTRACT_ENFORCE`` itself can disable it."""
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "off")
    assert task_contract_enforce_scope() == frozenset({"off"})
    assert effective_task_contract_enforce_mode("document_stc") == "shadow"
    assert effective_task_contract_enforce_mode(None) == "shadow"
    assert task_contract_enforce_mode() == "enforce"  # the floor itself is still ON


# --------------------------------------------------------------------- #
# Core door (ReliabilityEnvelope._apply_task_contract_floor)
# --------------------------------------------------------------------- #
def _state(criteria=None, *, mode=None) -> str:
    kwargs: dict = {"reliability_label": "verified", "completion_state": "verified"}
    if criteria is not None:
        kwargs["task_success_criteria"] = criteria
    if mode is not None:
        kwargs["task_mode"] = mode
    return ReliabilityEnvelope(**kwargs).completion_state


def test_core_door_scoped_out_stays_verified_and_logs(monkeypatch, caplog) -> None:
    """Under a scoped enforce the identity-less core door resolves to shadow: a
    declared-unfit verified stays verified but the would-cap is logged with the
    enforce_scoped_out marker."""
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "document_stc")  # core door has no surface identity
    with caplog.at_level(logging.WARNING, logger="g6.core.reliability_envelope"):
        assert _state([_CRIT_NO_VERIFIER]) == "verified"  # NOT demoted (scoped out)
    rec = next(r for r in caplog.records if "would-cap" in r.getMessage())
    payload = json.loads(rec.getMessage().split(": ", 1)[1])
    assert payload["enforce_scoped_out"] is True
    assert payload["would_demote_to"] == "qualified-draft"


def test_core_door_unscoped_enforce_still_demotes(monkeypatch) -> None:
    """Darkness: with the scope var unset the floor demotes exactly as pre-scope
    global enforce (regression)."""
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.delenv(SCOPE, raising=False)
    assert _state([_CRIT_NO_VERIFIER]) == "qualified-draft"


def test_core_door_wildcard_scope_demotes(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "*")
    assert _state([_CRIT_NO_VERIFIER]) == "qualified-draft"
