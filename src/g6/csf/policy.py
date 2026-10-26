# SPDX-License-Identifier: Apache-2.0
"""A person's approved epsilon cap; it cannot choose or weaken the verifier."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from g6.core.approval_authority import ApprovalAuthority, get_approval_authority
from g6.csf.schema import CSFInputError, check_epsilon


@dataclass(frozen=True)
class EpsilonPolicy:
    name: str
    max_epsilon: float

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise CSFInputError("G6_E_CSF_POLICY_INPUT", "a policy needs a non-empty name of at most 200 characters")
        object.__setattr__(self, "max_epsilon", check_epsilon(self.max_epsilon, "max_epsilon"))

    @property
    def approval_scope(self) -> str:
        content = json.dumps([self.name, self.max_epsilon], separators=(",", ":"))
        return "csf:epsilon_policy:" + hashlib.sha256(content.encode()).hexdigest()


def tighten(epsilon: float, policy: EpsilonPolicy, *, approval_id: str = "",
            authority: ApprovalAuthority | None = None, run_id: str | None = None) -> float:
    """Apply only a live human approval for this exact policy; otherwise keep epsilon.

    Approval issuance belongs to the trusted control plane. Names and booleans
    supplied by a caller are not approvals. The method and model never change.
    Authority exceptions refuse; an absent/rejected/revoked record is ignored.
    """
    eps = check_epsilon(epsilon)
    if not isinstance(policy, EpsilonPolicy):
        raise CSFInputError("G6_E_CSF_POLICY_INPUT", "policy must be an EpsilonPolicy")
    cap = check_epsilon(policy.max_epsilon, "max_epsilon")
    if not approval_id:
        return eps
    try:
        verifier = authority if authority is not None else get_approval_authority()
        if not verifier.check(approval_id, scope=policy.approval_scope, require_human=True,
                              run_id=run_id, consume=False):
            return eps
    except Exception as exc:  # noqa: BLE001 -- authority failure is not approval
        raise CSFInputError("G6_E_CSF_POLICY_AUTHORITY", f"approval could not be checked: {type(exc).__name__}") from exc
    return min(eps, cap)
