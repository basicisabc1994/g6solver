# SPDX-License-Identifier: Apache-2.0
"""Trusted host composition: freeze the existing policy and complete protected union.

Only the host bootstrap calls these factories. The lower merge authority accepts
these pinned data inputs without reaching upwards into metaharness. Missions
cannot replace the policy, manifest, verifier or host service.
"""
from g6.distill.meta.policy_gate import (FROZEN_MERGE_POLICY as FROZEN_POLICY,
    FROZEN_MERGE_POLICY_SHA256 as FROZEN_POLICY_SHA256, merge_judging_patterns)
from g6.self_training.merge_gate import MergeGate, ProtectedSet
from g6.distill.judging import JUDGING_PATHS

__all__ = ['FROZEN_POLICY', 'FROZEN_POLICY_SHA256', 'protected_set', 'host_merge_gate']


def protected_set():
    # The acceptance gate's judging list is protected from the list its digest reads, so the two cannot drift.
    return ProtectedSet.load(judging_patterns=(*merge_judging_patterns(), 'distill/judging.py',
                                               *(path.removeprefix('src/') for path in JUDGING_PATHS)))


def host_merge_gate(**kwargs):
    """Owner host service factory; no activation, credential discovery or effects."""
    return MergeGate(policy=FROZEN_POLICY, manifest=protected_set(), **kwargs)
