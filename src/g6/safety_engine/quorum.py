# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""quorum.py -- validator-vote counting gate.

This is vote *counting* only. It decides whether a set of :class:`ValidatorVote` s clears the policy's quorum
threshold:

1. **Votes are de-duplicated by declared ``fault_domain``.** Clones that declare the same domain count once; clones
   that declare different domains count separately. Nothing here checks a declaration, so this is not a proof of
   independence or of Byzantine fault tolerance (audit 007 SE-009).
2. **The quorum shape is validated, not assumed.** When ``require_quorum`` is on, the policy must satisfy
   ``n == 3f + 1`` and ``required_accepts == 2f + 1`` or the gate fails closed.

The library runs no panel of independent validators; a caller that wants one supplies the votes.
"""
from __future__ import annotations

from collections.abc import Sequence

from g6.safety_engine.schema import RuntimePolicy, ValidatorVote


def bft_shape_ok(policy: RuntimePolicy) -> bool:
    """True iff the policy's quorum parameters satisfy the BFT relations."""
    return (
        policy.quorum_n == 3 * policy.quorum_f + 1
        and policy.quorum_required_accepts == 2 * policy.quorum_f + 1
    )


def count_independent_accepts(votes: Sequence[ValidatorVote]) -> int:
    """Count APPROVE votes, de-duplicated by declared fault domain.

    A fault domain that casts any non-APPROVE vote does not count as an accept
    even if another (clone) vote in the same domain approved — a split domain is
    treated conservatively as not a clean independent accept.
    """
    approve_domains: set[str] = set()
    reject_domains: set[str] = set()
    for v in votes:
        if v.decision == "APPROVE":
            approve_domains.add(v.fault_domain)
        else:  # REJECT or ABSTAIN
            reject_domains.add(v.fault_domain)
    # A domain only counts as an accept if it never dissented.
    return len(approve_domains - reject_domains)


def quorum_satisfied(
    votes: Sequence[ValidatorVote], policy: RuntimePolicy
) -> bool:
    """Return whether *votes* clear *policy*'s quorum requirement.

    With ``require_quorum`` off this is a pass-through (``True``) — the other
    safety layers still apply. With it on, the policy must be BFT-shaped and the
    independent-accept count must reach ``quorum_required_accepts``.
    """
    if not policy.require_quorum:
        return True
    if not bft_shape_ok(policy):
        # Misconfigured BFT quorum: fail closed rather than silently accept.
        return False
    return count_independent_accepts(votes) >= policy.quorum_required_accepts
