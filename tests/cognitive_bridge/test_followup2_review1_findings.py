# SPDX-License-Identifier: Apache-2.0
"""Review 1 of PR #8's second follow-up: its three findings, as the bridge's own tests (each reproduced on 82a3356).
Each was a second copy of a rule the boundary already has: the depth bound restarted where one walk handed a subtree
to another (``boundary_case``); the claim rule kept its own test of what is JSON data and the result makers never took
their fields through the boundary (``one_fact_two_rules``); and the Bayesian update used beliefs without the belief
rule priors follow (``one_fact_two_rules``). Each is removed at its root, and listed from the code by
``test_depth_inventory.py``, ``test_json_rule_inventory.py`` and ``test_belief_inventory.py``."""
from __future__ import annotations

import math

import pytest

from g6.cognitive_bridge import BayesianUpdater, Hypothesis, Probe, check_beliefs, codes
from g6.cognitive_bridge.codes import DEPTH
from g6.cognitive_bridge.snapshot import NotPlainData, plain, snapshot, view


def test_finding_1_a_record_beside_the_path_does_not_reset_the_depth_bound():
    at_limit = "leaf"
    for _ in range(DEPTH):
        at_limit = [at_limit]
    assert plain(at_limit, where="input") == at_limit

    # The outer array puts the leaf at depth 65. A record in the other branch must not make that same data
    # acceptable to snapshot().
    value = [Hypothesis("A", "hypothesis A", .5), at_limit]
    with pytest.raises(NotPlainData, match="deeper than 64"):
        plain(view(value), where="input")
    with pytest.raises(NotPlainData, match="deeper than 64"):
        snapshot(value, where="input")


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("maker", ["settled", "refusal"])
def test_finding_2_the_result_makers_refuse_a_number_that_is_not_finite(maker, value):
    extra = {"measurement": {"score": value}}
    with pytest.raises(NotPlainData):
        plain(extra, where="extra")
    with pytest.raises(NotPlainData):
        if maker == "settled":
            codes.settled(None, **extra)
        else:
            codes.refusal(codes.INPUT, "invalid measurement", **extra)


def test_finding_3_the_public_update_takes_beliefs_by_the_rule_priors_follow():
    beliefs = {"A": 1.5, "B": -.5}
    probe = Probe("p", "measure", "read", {"A": True, "B": False}, .8)
    with pytest.raises(ValueError, match="in \\[0,1\\]"):
        check_beliefs([Hypothesis("A", "A", beliefs["A"]), Hypothesis("B", "B", beliefs["B"])])
    # Refusing the same prior is what the rule asks; returning a posterior outside [0, 1] is not.
    try:
        posterior = BayesianUpdater().update(beliefs, probe, True)
    except ValueError:
        return
    assert all(0 <= probability <= 1 for probability in posterior.values()), posterior
