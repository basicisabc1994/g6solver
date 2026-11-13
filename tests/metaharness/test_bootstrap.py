# SPDX-License-Identifier: Apache-2.0
"""PR #4's cognitive bootstrap in the meta-harness (P1): the same stages, bounds and parsing, with every prompt the one
catalogue's strict render, filled only with the template's own placeholders."""
from __future__ import annotations

import pytest

from g6.metaharness.bootstrap import BootstrapResult, bootstrap


def _thinker(seen):
    def think(stage, prompt):
        seen.append((stage, prompt))
        return {"constraints": '["c1", "c2"]', "decompose": '["s1", "s2"]'}.get(stage, "text")
    return think


def test_bootstrap_walks_the_stages_in_order_with_strict_prompts():
    seen = []
    result = bootstrap("ship the report", thinker=_thinker(seen), frozen_constraints=("frozen",), max_revisions=2)
    assert isinstance(result, BootstrapResult) and result.status == "qualified-draft"
    assert result.calls == 12 and result.constraints == ("frozen", "c1", "c2") and result.subgoals == ("s1", "s2")
    assert [s for s, _ in seen] == ["constraints", "first_principles", "heuristics", "self_assessment", "decompose",
                                    "execute", "execute", "synthesize", "critique", "revise", "critique", "revise"]
    assert all("{{" not in p for _, p in seen) and "ship the report" in seen[0][1]
    assert "s2" in seen[6][1] and result.critiques == ("text", "text")


def test_a_reply_that_is_not_a_json_list_gives_no_constraint_or_subgoal():
    result = bootstrap("g", thinker=lambda stage, prompt: "not json", max_revisions=0)
    assert (result.constraints, result.subgoals, result.calls) == ((), (), 6)


def test_bootstrap_bounds_are_kept():
    for bad in ({"max_subgoals": 13}, {"max_subgoals": 0}, {"max_revisions": 5}):
        with pytest.raises(ValueError):
            bootstrap("g", thinker=lambda s, p: "[]", **bad)
    with pytest.raises(ValueError):
        bootstrap("  ", thinker=lambda s, p: "[]")
    with pytest.raises(TypeError):
        bootstrap("g", thinker=lambda s, p: 1)
    long = bootstrap("g", thinker=lambda s, p: "x" * 50_000, max_revisions=1)
    assert len(long.candidate) == 40_000
