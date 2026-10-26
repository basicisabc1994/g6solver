# SPDX-License-Identifier: Apache-2.0
import pytest

from g6.core.approval_authority import ApprovalAuthority
from g6.csf import CSFInputError, EpsilonPolicy, tighten


def test_approved_policy_can_only_tighten():
    # Removal: policy.py `return min(eps, cap)` permits an approved cap to loosen epsilon (D1/capability).
    authority = ApprovalAuthority()
    policy = EpsilonPolicy("bounded actions", 0.12)
    record = authority.issue(reviewer_id="person", scope=policy.approval_scope)
    assert tighten(0.3, policy, approval_id=record.approval_id, authority=authority) == 0.12
    assert tighten(0.04, policy, approval_id=record.approval_id, authority=authority) == 0.04


def test_unapproved_policy_is_ignored_and_has_no_method_switch():
    # Removal: policy.py `if not ...check(...): return eps` admits an unapproved policy (CSF_STRATEGY-02).
    policy = EpsilonPolicy("actions", 0.01)
    assert tighten(0.2, policy, approval_id="invented", authority=ApprovalAuthority()) == 0.2
    with pytest.raises(TypeError):
        EpsilonPolicy("actions", 0.01, verification_method="worst_case")


def test_approval_is_human_record_bound_to_exact_policy():
    # Removal: policy.py `scope=policy.approval_scope, require_human=True` accepts forged/repurposed approval (CSF_STRATEGY-04).
    authority = ApprovalAuthority()
    policy = EpsilonPolicy("actions", 0.1)
    old = EpsilonPolicy("actions", 0.05)
    for record in [authority.issue(reviewer_id="Ada", scope=old.approval_scope),
                   authority.issue(reviewer_id="oracle", scope=policy.approval_scope,
                                   issuer_kind="policy_oracle", policy_version="1")]:
        assert tighten(0.3, policy, approval_id=record.approval_id, authority=authority) == 0.3
    record = authority.issue(reviewer_id="Ada", scope=policy.approval_scope)
    authority.revoke(record.approval_id)
    assert tighten(0.3, policy, approval_id=record.approval_id, authority=authority) == 0.3


def test_authority_failure_does_not_claim_an_applied_policy(monkeypatch):
    # Removal: policy.py `except Exception as exc:` loses the named fail-closed authority refusal.
    authority = ApprovalAuthority()
    def broken(*a, **kw):
        raise OSError("authority down")
    monkeypatch.setattr(authority, "check", broken)
    with pytest.raises(CSFInputError, match="G6_E_CSF_POLICY_AUTHORITY"):
        tighten(0.4, EpsilonPolicy("actions", 0.2), approval_id="x", authority=authority)


@pytest.mark.parametrize("value", [True, float("nan"), -0.1, 1.1])
def test_both_epsilons_are_validated_even_without_approval(value):
    # Removal: policy.py `check_epsilon` on either input would accept an invalid cap or bound.
    with pytest.raises(CSFInputError):
        EpsilonPolicy("actions", value)
    with pytest.raises(CSFInputError):
        tighten(value, EpsilonPolicy("actions", 0.2))


def test_authority_loading_failure_refuses(monkeypatch):
    # Removal: policy.py `except Exception as exc:` would leak an authority-store failure.
    from g6.csf import policy
    def broken():
        raise OSError("approval store unavailable")
    monkeypatch.setattr(policy, "get_approval_authority", broken)
    with pytest.raises(CSFInputError, match="G6_E_CSF_POLICY_AUTHORITY"):
        tighten(0.4, EpsilonPolicy("actions", 0.2), approval_id="x")
