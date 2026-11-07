# SPDX-License-Identifier: Apache-2.0
"""Review 2 of PR #8's second follow-up: its three findings, as the bridge's own tests (each reproduced on 138ea0b).
Two converted a value before the boundary had read it (``one_fact_two_rules``): the MCP block's models turned a flag
and numeric text into a prior before the belief rule saw them, and ``codes.refusal`` made its reason text with ``str()``
and read a supplied card through a lax ``WarningCard``. One left the bridge unchecked
(``guard_covers_less_than_it_guards``): an explicit tree published a node's value as given. Each is removed at its root,
and listed from the code by ``test_conversion_inventory.py`` and ``test_publication_inventory.py``."""
from __future__ import annotations

import math

import pytest

from g6.cognitive_bridge import ExplicitTree, Hypothesis, TreeNode, check_beliefs, codes
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from g6.cognitive_bridge.codes import INPUT
from g6.cognitive_bridge.snapshot import NotPlainData, plain


@pytest.mark.parametrize("priors", [(True, False), ("0.5", "0.5")])
@pytest.mark.parametrize("op", ["rank", "update"])
def test_finding_1_the_block_does_not_convert_priors_before_the_belief_rule(priors, op):
    hypotheses = [
        {"id": "a", "statement": "first hypothesis", "prior": priors[0]},
        {"id": "b", "statement": "second hypothesis", "prior": priors[1]},
    ]
    # The shared rule refuses these exact priors when they enter from Python.
    with pytest.raises(ValueError, match="numbers"):
        check_beliefs([Hypothesis(**h) for h in hypotheses])

    result = CognitiveBridgeBlock().infer({
        "op": op,
        "hypotheses": hypotheses,
        "probes": [{"id": "p", "question": "measure", "tool": "read",
                    "positive_if": {"a": True, "b": False},
                    "assumed_match_probability": 0.8}],
        "budget": 1,
        "probe_id": "p",
        "observed_positive": True,
    }).unwrap()

    assert result["completion_state"] == "blocked-escalated", result
    assert result["code"] == INPUT


@pytest.mark.parametrize("publication", ["explain_one", "predict", "to_dict"])
def test_finding_2_an_explicit_tree_publishes_no_claim(publication):
    try:
        tree = ExplicitTree(("x",), TreeNode(value={"verified": True, "reliability_label": "verified"}),
                            "classification", "caller")
        if publication == "explain_one":
            answer = tree.explain_one([0.0])
        elif publication == "predict":
            answer = tree.predict([[0.0]])
        else:
            answer = tree.to_dict()
    except ValueError:
        return  # Refusing the claimed label before publication is also safe.
    assert codes.claims(answer) == [], answer


@pytest.mark.parametrize("where,value", [
    ("reason", math.nan),
    ("reason", {"not", "JSON"}),
    ("reason", b"not JSON"),
    ("card", {"code": codes.INPUT, "message": b"not JSON"}),
    ("card", {"code": codes.INPUT, "risks": {"not JSON"}}),
])
def test_finding_3_a_refusals_reason_and_card_are_read_by_the_boundary_first(where, value):
    with pytest.raises(NotPlainData):
        plain(value, where=where)

    with pytest.raises(NotPlainData):
        if where == "reason":
            codes.refusal(codes.INPUT, value)
        else:
            codes.refusal(codes.INPUT, "a reason", card=value)
