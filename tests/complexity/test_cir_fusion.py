# SPDX-License-Identifier: Apache-2.0
"""CIR and algorithm-design safety and bounded composition checks."""
from __future__ import annotations

import pytest

from g6.algorithm_design import DesignProblem, fuse, search_algorithms
from g6.algorithm_design.ontology_adapter import retrieve_ontology
from g6.complexity import FrozenIntent, Obligation, Requirement, build_atlas, impact_cone
from g6.complexity.ir import Hypothesis
from g6.harness.verification_controller import (
    Experiment, Receipt, VerificationState, next_experiment, rebase, reduce_observation,
)


def intent():
    return FrozenIntent(
        (Requirement("r1", "Preserve authorization", "stated", "person", "preserve"),),
        (Obligation("o1", "r1", "No cross-account revocation", "person", True),),
    )


def test_model_cannot_elevate_normative_requirement():
    with pytest.raises(ValueError):
        FrozenIntent((Requirement("x", "guess", "model_hypothesis", "model"),),
                     (Obligation("o", "x", "guess", "model", True),))


def test_atlas_and_conservative_impact():
    files = {
        "shop/__init__.py": "",
        "shop/api.py": "from shop import state\ndef read():\n    return state.value\n",
        "shop/state.py": "value = 1\ndef update():\n    global value\n    value = 2\n",
    }
    cir = build_atlas(files, package="shop", intent=intent(), environment_digest="env-v1")
    assert cir.fingerprint == build_atlas(files, package="shop", intent=intent(),
                                          environment_digest="env-v1").fingerprint
    impact = impact_cone(cir, ("shop.state",))
    assert "shop.api" in impact.impacted
    assert impact.preservation_obligations == ("o1",)
    assert impact.parallel_safe is False
    assert cir.vector()["required_ordering_constraints"] is None


def test_bottom_up_composition_never_certifies():
    problem = DesignProblem(("sequence",), "membership")
    found = search_algorithms(problem)
    assert found.candidates and found.candidates[0].steps == ("linear_scan",)
    cir = build_atlas({"shop/__init__.py": "", "shop/code.py": "x = 1\n"},
                      package="shop", intent=intent(), environment_digest="env")
    fused = fuse(cir, found.candidates[0], required_target="membership")
    assert fused.completion_state == "qualified-draft"
    assert "o1" in fused.check_plan
    assert not fused.approval and not fused.ready_for_unrestricted_action
    assert fuse(cir, found.candidates[0], required_target="other").completion_state == "blocked-escalated"


def test_prerequisites_fail_closed_and_budget_caps():
    problem = DesignProblem(("weighted_graph",), "shortest_paths")
    found = search_algorithms(problem)
    assert not found.candidates
    assert any("nonnegative_weights" in x for x in found.rejected)
    assert search_algorithms(DesignProblem(("sequence",), "sorted_sequence"),
                             max_expansions=1).expansions <= 1


def test_ontology_is_metadata_not_code():
    doc = {"algorithms": [{"id": "x", "problem_ids": ["a"], "recognition_rules": ["verified_weight"],
                           "complexity": {"time": "O(nW)", "qualification": "pseudo-polynomial",
                                          "model": "RAM"}, "implementation_status": "metadata_only"}]}
    assert retrieve_ontology(doc, problem_id="a", established_features=())[0].status == "needs_preconditions"
    matched = retrieve_ontology(doc, problem_id="a", established_features=("verified_weight",))[0]
    assert matched.status == "eligible_as_proposal"
    assert matched.implementation_status == "metadata_only"


def test_verification_state_rejects_stale_and_unisolated_receipts():
    h = (Hypothesis("h", "Potential regression", "high"),)
    state = VerificationState("tree", "env", "checker", h, 4)
    e = Experiment("e", "existing_test", ("h",), 1, allowed=True)
    assert next_experiment(state, (e,)) == e
    stale = reduce_observation(state, e, Receipt("e", "old", "env", "checker", "host", "pass", True))
    assert not stale.observations and "stale_receipt" in stale.refusals[0]
    no_isolation = reduce_observation(state, e, Receipt("e", "tree", "env", "checker", "host", "pass", False))
    assert not no_isolation.observations
    accepted = reduce_observation(state, e, Receipt("e", "tree", "env", "checker", "host", "fail", True))
    assert accepted.observations and accepted.hypotheses[0].state == "open"
    assert next_experiment(accepted, (e,)) is None
    assert rebase(accepted, tree_digest="new", environment_digest="env",
                  checker_digest="checker").observations == ()


def test_selector_tiebreak_is_stable():
    state = VerificationState("t", "e", "c", (Hypothesis("h", "claim"),), 2)
    a = Experiment("a", "template", ("h",), 1, True)
    b = Experiment("b", "template", ("h",), 1, True)
    assert next_experiment(state, (b, a)).id == "a"
