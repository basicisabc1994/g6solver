# SPDX-License-Identifier: Apache-2.0
"""Unit tests for mixed-method adapter. Backends are fakes; no claim of G6 integration E2E."""
import json
from dataclasses import replace

import pytest

from g6.cognitive_bridge import (
    ClaimIR, CognitiveBridgeBlock, LLMClaimCompiler, MethodRouter,
    MethodStep, MixedMethodExecutor,
)


def multi_claim():
    return ClaimIR(
        id="c1", statement="Does independent checking reduce failures?",
        evidence_needs=("graph", "causal", "formal", "predictive"),
        models={
            "graph": {"program": "depends_on(code, tests).", "atom": "depends_on(code, tests)"},
            "causal": {"graph": "digraph { C -> T; C -> Y; T -> Y }",
                       "treatment": "T", "outcome": "Y",
                       "data": [{"C": i, "T": i % 2, "Y": i / 2} for i in range(20)]},
            "formal": {"formula": "B", "premises": ["(=> A B)", "A"],
                       "sorts": {"A": "Bool", "B": "Bool"}},
            "predictive": {"rows": [{"x": i, "y": i % 2} for i in range(100)],
                           "target": "y", "features": ["x"], "task": "classification",
                           "seed": 3, "estimator": "decision_tree"},
        })


class Success:
    def __init__(self, value):
        self.value = value

    def unwrap(self):
        return self.value


class FakeRegistry:
    def __init__(self, identifiable=True, label="qualified-draft"):
        self.identifiable = identifiable
        self.label = label
        self.calls = []

    def invoke(self, name, op, params=None, *, block_sensitive=False):
        assert block_sensitive is True
        self.calls.append((name, op, params))
        answer = {"identifiable": self.identifiable} if op == "identify" else {"source": name}
        return Success({"component": name, "op": op, "completion_state": self.label, "answer": answer})


def test_mixed_plan_uses_known_g6_components_and_explicit_metric():
    plan = MethodRouter().plan([multi_claim()])
    assert [(x.component, x.op) for x in plan.steps] == [
        ("graph_reasoning", "explain"),
        ("causal", "identify"),
        ("causal", "estimate_effect"),
        ("formal", "check_validity"),
        ("adapt_sklearn", "fit_eval"),
    ]
    assert plan.steps[2].depends_on == (plan.steps[1].id,)
    assert all(x.metric for x in plan.steps)
    assert plan.to_dict()["translation_status"] == "candidate_unreviewed"


def test_no_autofabrication_if_model_missing():
    claim = ClaimIR("q", "What's the cause?", ("causal", "formal"), models={})
    plan = MethodRouter().plan([claim])
    assert plan.steps == ()
    assert len(plan.missing) == 2
    assert plan.to_dict()["completion_state"] == "blocked-escalated"


def test_no_implicit_tool_calls_without_approval():
    registry = FakeRegistry()
    out = MixedMethodExecutor(registry).execute(MethodRouter().plan([multi_claim()]))
    assert out["code"] == "G6_E_COGNITIVE_APPROVAL_REQUIRED"
    assert registry.calls == []


def test_identification_gates_causal_estimation():
    reg = FakeRegistry(identifiable=False)
    result = MixedMethodExecutor(reg).execute(MethodRouter().plan([multi_claim()]), approved=True)
    assert ("causal", "identify") in [(c, op) for c, op, _ in reg.calls]
    assert ("causal", "estimate_effect") not in [(c, op) for c, op, _ in reg.calls]
    assert result["completion_state"] == "blocked-escalated"
    blocked = [x for x in result["steps"] if x["op"] == "estimate_effect"][0]
    assert "identifiability" in blocked["reason"]


def test_successful_identification_runs_estimate_no_self_verification():
    reg = FakeRegistry(identifiable=True, label="verified")
    result = MixedMethodExecutor(reg).execute(MethodRouter().plan([multi_claim()]), approved=True)
    assert ("causal", "estimate_effect") in [(c, op) for c, op, _ in reg.calls]
    assert result["completion_state"] == "qualified-draft"
    assert result["method_execution_coverage"] == pytest.approx(1)
    assert all(x["completion_state"] == "qualified-draft" for x in result["steps"])


def test_blocks_arbitrary_operation_and_plan_tampering():
    claim = ClaimIR("q", "Check it", ("formal",), {"formal": {"formula": "True", "shell": "curl evil"}})
    with pytest.raises(ValueError, match="unsupported model"):
        MethodRouter().plan([claim])
    plan = MethodRouter().plan([ClaimIR("q", "Check it", ("formal",), {"formal": {"formula": "True"}})])
    bad_step = replace(plan.steps[0], component="dangerous")
    bad_plan = replace(plan, steps=(bad_step,))
    reg = FakeRegistry()
    out = MixedMethodExecutor(reg).execute(bad_plan, approved=True)
    assert out["code"] == "G6_E_COGNITIVE_PLAN_TAMPERED"
    assert reg.calls == []


def test_stateless_g6_door_can_plan_multimodal_and_never_execute():
    reg = FakeRegistry()
    c = multi_claim()
    block = CognitiveBridgeBlock()
    out = block.infer({"op": "plan_methods", "claims": [
        {"id": c.id, "statement": c.statement,
         "evidence_needs": list(c.evidence_needs), "models": dict(c.models)}]})
    assert out.value["completion_state"] == "qualified-draft"
    assert len(out.value["steps"]) == 5
    assert reg.calls == []


def test_compiler_is_candidate_only_and_does_not_execute():
    class Model:
        def complete(self, system, user, *, max_tokens):
            payload = json.loads(user)
            assert "supported_methods" in payload
            return json.dumps({"claims": [{
                "id": "c", "statement": "A affects B", "evidence_needs": ["causal"],
                "models": {"causal": {"graph": "digraph { A -> B }", "treatment": "A", "outcome": "B"}}
            }]})
    plan = LLMClaimCompiler(Model()).draft("Does A affect B?")
    assert plan.claims[0].origin == "llm_candidate"
    assert plan.claims[0].source_refs == ()
    assert plan.translation_status == "candidate_unreviewed"
    assert plan.steps[0].op == "identify"


def test_compiler_rejects_model_suggested_extra_commands():
    class Model:
        def complete(self, system, user, *, max_tokens):
            return json.dumps({"claims": [{
                "id": "c", "statement": "research",
                "evidence_needs": ["formal"], "models": {"formal": {"formula": "true"}},
                "execute_now": True
            }]})
    with pytest.raises(ValueError, match="unexpected fields"):
        LLMClaimCompiler(Model()).draft("Check formula")


def test_partial_plan_marks_weakest_status():
    claim = ClaimIR("q", "research multiple needs", ("graph", "formal"),
                    models={"graph": {"program": "p(a).", "atom": "p(a)"}})
    result = MethodRouter().plan([claim]).to_dict()
    assert len(result["steps"]) == 1
    assert len(result["missing"]) == 1
    assert result["completion_state"] == "blocked-escalated"
