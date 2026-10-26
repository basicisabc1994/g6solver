# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Server-side approval authority — the fix for caller-asserted approval.

Three code paths previously let the party being approved supply its own verdict:
``_preflight_hitl`` (a caller boolean), ``_caller_verified_is_earned`` (a caller
dict), and ``validation_gates["human_approval"]`` (a no-op returning True). These
tests attack the replacement: an approval must be a record the CONTROL PLANE
issued, and a caller may only present its identifier.
"""
from __future__ import annotations

import time

import pytest
from g6.core.approval_authority import (
    ApprovalAuthority,
    ApprovalError,
    approval_scope_for_operation,
    check_operation_approval,
    get_approval_authority,
    require_approval_record,
)

SCOPE = "job:job_lawyer:file_motion"
OTHER_SCOPE = "job:job_lawyer:read_docket"


@pytest.fixture
def authority():
    a = ApprovalAuthority()
    yield a
    a.clear()


@pytest.fixture(autouse=True)
def _clean_global():
    get_approval_authority().clear()
    yield
    get_approval_authority().clear()


# ---------------------------------------------------------------------------
# The attack: fabricate an approval
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "forged",
    [
        None, "", "approved", "true", "True", "1", "yes",
        "approval", "approval_id", "*", "%", "admin",
        0, 1, True, {"decision": "approved"}, ["approved"],
        "a" * 43,  # right shape, wrong value
    ],
)
def test_fabricated_approval_never_verifies(authority, forged):
    """No caller-supplied value authorizes anything without an issued record.
    This is the whole point: the old checks accepted a truthy dict or boolean."""
    assert authority.check(forged, scope=SCOPE) is False


def test_issued_approval_verifies_once_then_is_consumed(authority):
    record = authority.issue(reviewer_id="reviewer@example.com", scope=SCOPE)
    assert authority.check(record.approval_id, scope=SCOPE, consume=True) is True
    # Single-use by default: a replay of the same id must not authorize again.
    assert authority.check(record.approval_id, scope=SCOPE) is False


def test_approval_is_scope_bound(authority):
    """An approval for one operation cannot authorize a different one."""
    record = authority.issue(reviewer_id="r", scope=SCOPE)
    assert authority.check(record.approval_id, scope=OTHER_SCOPE) is False
    assert authority.check(record.approval_id, scope=SCOPE) is True


def test_approval_is_run_bound_when_pinned(authority):
    record = authority.issue(reviewer_id="r", scope=SCOPE, run_id="run-1")
    assert authority.check(record.approval_id, scope=SCOPE, run_id="run-2") is False
    assert authority.check(record.approval_id, scope=SCOPE, run_id="run-1") is True


def test_rejected_decision_never_authorizes(authority):
    record = authority.issue(reviewer_id="r", scope=SCOPE, decision="rejected")
    assert authority.check(record.approval_id, scope=SCOPE) is False


def test_expired_approval_does_not_authorize(authority):
    record = authority.issue(reviewer_id="r", scope=SCOPE, ttl_seconds=0.01)
    time.sleep(0.05)
    assert authority.check(record.approval_id, scope=SCOPE) is False


def test_revoked_approval_does_not_authorize(authority):
    record = authority.issue(reviewer_id="r", scope=SCOPE)
    assert authority.revoke(record.approval_id) is True
    assert authority.check(record.approval_id, scope=SCOPE) is False


def test_multi_use_approval_survives_reuse_but_still_scope_bound(authority):
    record = authority.issue(reviewer_id="r", scope=SCOPE, single_use=False)
    assert authority.check(record.approval_id, scope=SCOPE, consume=True) is True
    assert authority.check(record.approval_id, scope=SCOPE, consume=True) is True
    assert authority.check(record.approval_id, scope=OTHER_SCOPE) is False


def test_approval_ids_are_unguessable_and_unique(authority):
    ids = {authority.issue(reviewer_id="r", scope=SCOPE).approval_id for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) >= 32 for i in ids)


def test_issue_requires_reviewer_and_scope(authority):
    """An approval that cannot say WHO approved WHAT is not an approval."""
    with pytest.raises(ApprovalError):
        authority.issue(reviewer_id="", scope=SCOPE)
    with pytest.raises(ApprovalError):
        authority.issue(reviewer_id="r", scope="")
    with pytest.raises(ApprovalError):
        authority.issue(reviewer_id="r", scope=SCOPE, decision="maybe")


def test_check_never_raises(authority):
    """Verification must not raise: a caller must not be able to turn a failed
    check into an exception-handling bypass upstream."""
    for weird in (object(), b"bytes", 3.14, (), set()):
        assert authority.check(weird, scope=SCOPE) is False


def test_redacted_view_omits_the_bearer_credential(authority):
    record = authority.issue(reviewer_id="r", scope=SCOPE, note="ok")
    view = record.redacted()
    assert "approval_id" not in view
    assert record.approval_id not in str(view)
    assert view["reviewer_id"] == "r" and view["scope"] == SCOPE


# ---------------------------------------------------------------------------
# The named-gate registry (D7): human_approval was a no-op returning True
# ---------------------------------------------------------------------------

def test_human_approval_gate_is_no_longer_a_noop():
    from g6.core.validation_gates import get

    gate = get("human_approval")
    # No approval at all -> denied. Previously this returned True unconditionally.
    assert gate(approval_id=None, scope=SCOPE) is False
    assert gate(approval_id="fabricated", scope=SCOPE) is False

    record = get_approval_authority().issue(reviewer_id="r", scope=SCOPE)
    assert gate(approval_id=record.approval_id, scope=SCOPE) is True


def test_human_approval_gate_requires_a_scope():
    """A human approval that cannot say WHAT was approved authorizes nothing."""
    from g6.core.validation_gates import get

    record = get_approval_authority().issue(reviewer_id="r", scope=SCOPE)
    assert get("human_approval")(approval_id=record.approval_id, scope="") is False


def test_registry_still_exposes_the_other_names():
    from g6.core.validation_gates import list_names

    for name in ("meta_eval_suite", "unit_tests_only", "unit_plus_codex_review",
                 "human_approval", "none"):
        assert name in list_names()


# ---------------------------------------------------------------------------
# Operation-scoped helper used by the pre-execution HITL gate
# ---------------------------------------------------------------------------

def test_operation_approval_round_trip():
    scope = approval_scope_for_operation("job_lawyer", "file_motion")
    record = get_approval_authority().issue(reviewer_id="r", scope=scope)
    assert check_operation_approval(
        record.approval_id, agent_name="job_lawyer", operation="file_motion",
    ) is True


def test_operation_approval_rejects_other_operation():
    scope = approval_scope_for_operation("job_lawyer", "read_docket")
    record = get_approval_authority().issue(reviewer_id="r", scope=scope)
    assert check_operation_approval(
        record.approval_id, agent_name="job_lawyer", operation="file_motion",
    ) is False


def test_require_approval_record_defaults_on(monkeypatch):
    """Fail-closed default: a caller-asserted boolean is refused unless an operator
    explicitly opts into the legacy migration mode."""
    monkeypatch.delenv("G6_HITL_REQUIRE_RECORD", raising=False)
    assert require_approval_record() is True
    for val in ("1", "true", "yes", "", "00", "off"):
        monkeypatch.setenv("G6_HITL_REQUIRE_RECORD", val)
        assert require_approval_record() is True, f"{val!r} must not disable the record requirement"
    monkeypatch.setenv("G6_HITL_REQUIRE_RECORD", "0")
    assert require_approval_record() is False  # only the explicit opt-out
