# SPDX-License-Identifier: Apache-2.0
"""Review 2's walk of the class list over the bridge, which passed and must keep passing: identity from bytes, a
foreign format read by its producer, no credit across a restart, the router's and the budget's edges, and the public
registration with the skill's Python example."""
from __future__ import annotations

from dataclasses import replace

import pytest

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, EvidenceArtifact, MethodRouter, MethodVariant, MetacognitiveMethodSelector,
    MixedMethodExecutor, ProblemSignature,
)


def test_identity_uses_bytes_even_when_lossy_renderings_match():
    a, b = b"\xff", b"\xfe"
    assert a.decode("utf-8", "replace") == b.decode("utf-8", "replace")
    assert EvidenceArtifact("a", a, "report", "p", "source", "c").digest != EvidenceArtifact(
        "a", b, "report", "p", "source", "c").digest
    variant = MethodVariant("v", "formal", "Å", "formal", "check_validity", 1)
    assert variant.definition_digest != replace(variant, representation="Å").definition_digest


def test_foreign_formula_is_read_by_its_actual_producer():
    from g6.core.registry import get_registry
    from g6.formal import check_propositional
    formula = "a AND NOT a"
    expected = check_propositional(formula)
    plan = MethodRouter().plan([ClaimIR("c", "unsatisfiability", ("formal",),
                                       {"formal": {"variant_op": "check_propositional", "formula": formula}})])
    run = MixedMethodExecutor(get_registry()).execute(plan, approved=True)
    assert run["completion_state"] == "qualified-draft"
    assert run["steps"][0]["result"]["satisfiable"] is expected.satisfiable is False


def test_restart_does_not_credit_a_previous_process_record(executed_case):
    c = executed_case
    run = c["orchestrator"].execute(c["compiled"], approved=True)
    fresh = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=c["registry"])
    out = fresh.assess(c["compiled"], standard=c["standard"], run_result=run, evaluator=c["evaluator"],
                       artifacts=c["artifacts"], verifiers=c["verifiers"], documentation=c["documentation"],
                       candidate_actor=c["candidate_actor"])
    assert out["completion_state"] == "blocked-escalated" and out["method_results"] == {}


def test_router_claim_count_edges():
    for n in (0, 1, 20, 21):
        claims = [ClaimIR(str(i), "claim", ("formal",), {"formal": {"formula": "True"}}) for i in range(n)]
        if n in (0, 21):
            with pytest.raises(ValueError):
                MethodRouter().plan(claims)
        else:
            assert len(MethodRouter().plan(claims).steps) == n


def test_zero_and_exact_cost_budget_edges():
    variant = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
    for budget, state in [(0, "blocked-escalated"), (1, "qualified-draft")]:
        decision = MetacognitiveMethodSelector().select(ProblemSignature("d", "u", "k", 1, budget),
                                                        [variant], required_methods=["formal"])
        assert decision.completion_state == state


def test_public_registration_and_python_skill_example(monkeypatch, tmp_path):
    from g6.core import discovery, skills
    from g6.core.registry import get_registry
    from g6.mcp import describe_reply
    registry = get_registry()
    assert "cognitive_bridge" in registry.names()
    assert set(discovery.ops("cognitive_bridge")) == {
        "rank", "update", "plan_methods", "rank_methods", "assurance_schema", "rank_strategies"}
    assert describe_reply("cognitive_bridge")["name"] == "cognitive_bridge"
    assert registry.invoke("cognitive_bridge", "help").unwrap()["help"]
    examples = skills.skill_python_examples(skills.skill_text("cognitive_bridge"))
    assert examples
    monkeypatch.chdir(tmp_path)  # the review ran it from its own scratch folder
    for example in examples:
        exec(compile(example, "<trusted repository skill example>", "exec"), {})
