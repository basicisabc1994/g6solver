# SPDX-License-Identifier: Apache-2.0
"""Catalog bridge refuses unreviewed metadata and preserves resource gating."""
from g6.algorithm_design.ir import DesignProblem
from g6.algorithm_design.knowledge_bridge import catalog_plan
from g6.algorithm_design.architecture import PRINCIPLES
from g6.algorithm_design.catalog import primitives
from g6.algorithm_design.search import search_algorithms
from g6.algorithm_design.resources import DeviceBudget, rank_options


def test_catalog_exposes_agent_ops_but_does_not_auto_certify():
    p = DesignProblem(("intervals",), "maximum_compatible_intervals",
                      ("unit_reward", "intervals"))
    plan = catalog_plan(p, problem_id="interval_scheduling")
    assert "proof" in plan.agent_operations
    assert any(x.algorithm_id == "interval_earliest_finish"
               and x.status == "eligible_as_proposal" for x in plan.insights)
    result = search_algorithms(p, catalog=(*primitives(), *plan.typed_primitives))
    assert any(x.steps == ("interval_earliest_finish",) for x in result.candidates)
    assert all(x.status == "candidate_only" for x in result.candidates)


def test_missing_preconditions_are_not_assumed():
    p = DesignProblem(("intervals",), "maximum_compatible_intervals")
    plan = catalog_plan(p, problem_id="interval_scheduling")
    assert not plan.typed_primitives
    assert plan.insights[0].status == "needs_preconditions"


def test_unmodeled_resources_fail_closed():
    p = DesignProblem(("intervals",), "maximum_compatible_intervals",
                      ("unit_reward", "intervals"))
    plan = catalog_plan(p, problem_id="interval_scheduling")
    candidate = search_algorithms(p, catalog=(*primitives(), *plan.typed_primitives)).candidates[0]
    costs = rank_options(candidate.steps, {"n": 10}, DeviceBudget(10**6, 0, 10**6))
    assert not all(c.eligible for c in costs)


def test_architecture_principles_are_present():
    assert len(PRINCIPLES) >= 8
    assert any("mutable state" in s for s in PRINCIPLES)
