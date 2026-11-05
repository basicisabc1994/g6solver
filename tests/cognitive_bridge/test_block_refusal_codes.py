# SPDX-License-Identifier: Apache-2.0
"""The bridge's stable codes: each refusal carries its own G6_E_ code, in ``code`` and in its warning card, the door
shows the block's code, the skill names every code, and the list of codes is pinned (conformance kit, D4 and D6)."""
from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from returns.result import Success

from g6.cognitive_bridge import ClaimIR, CognitiveAssuranceOrchestrator, MethodRouter, MixedMethodExecutor
from g6.cognitive_bridge.metacognition import MetacognitiveMethodSelector
from g6.core import skills
from g6.mcp import invoke_reply

PINNED = ("G6_E_COGNITIVE_BRIDGE_INPUT", "G6_E_COGNITIVE_APPROVAL_REQUIRED", "G6_E_COGNITIVE_PLAN_TAMPERED",
          "G6_E_COGNITIVE_EXECUTION_NOT_APPROVED", "G6_E_COGNITIVE_ASSURANCE_BLOCKED", "G6_E_COGNITIVE_NOT_PROMOTED")


def _reply(op: str, params: dict) -> dict:
    return invoke_reply("cognitive_bridge", op, json.dumps(params))


def test_the_codes_are_pinned_and_well_formed() -> None:
    from g6.cognitive_bridge import codes

    assert codes.CODES == PINNED
    assert all(re.fullmatch(r"G6_E_[A-Z0-9_]+", code) for code in codes.CODES)


def test_the_skill_names_every_code() -> None:
    text = skills.skill_text("cognitive_bridge") or ""
    assert [code for code in PINNED if code not in text] == []


def test_an_unknown_field_is_refused_not_dropped() -> None:
    reply = _reply("rank", {"budget": 1, "hypothesis": []})
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_INPUT_VALIDATION]"), reply["error"]


@pytest.mark.parametrize(("op", "params"), [
    ("rank", {}),
    ("update", {}),
    ("plan_methods", {}),
    ("rank_methods", {}),
    ("rank", {"hypotheses": [{"id": "A", "statement": "x", "prior": 0.2}, {"id": "B", "statement": "y", "prior": 0.2}]}),
])
def test_each_block_refusal_is_coded_at_the_door(op: str, params: dict) -> None:
    reply = _reply(op, params)
    assert reply["ok"] is False and reply["result"]["completion_state"] == "blocked-escalated"
    assert reply["result"]["code"] == "G6_E_COGNITIVE_BRIDGE_INPUT"
    assert reply["result"]["warning_card"]["code"] == reply["warning_card"]["code"] == "G6_E_COGNITIVE_BRIDGE_INPUT"


def _plan():
    return MethodRouter().plan([ClaimIR("q", "Check it", ("formal",), {"formal": {"formula": "True"}})])


class _Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def invoke(self, name, op, params=None, *, block_sensitive=False, llm=None):
        self.calls.append((name, op))
        return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})


def test_running_without_approval_is_coded_and_runs_nothing() -> None:
    registry = _Registry()
    out = MixedMethodExecutor(registry).execute(_plan())
    assert out["code"] == out["warning_card"]["code"] == "G6_E_COGNITIVE_APPROVAL_REQUIRED"
    assert registry.calls == []


def test_an_edited_plan_is_coded_and_runs_nothing() -> None:
    plan = _plan()
    registry = _Registry()
    out = MixedMethodExecutor(registry).execute(replace(plan, steps=(replace(plan.steps[0], op="check_sat"),)),
                                                approved=True)
    assert out["code"] == out["warning_card"]["code"] == "G6_E_COGNITIVE_PLAN_TAMPERED"
    assert registry.calls == []


def test_the_orchestrator_refusal_is_coded() -> None:
    from g6.cognitive_bridge import Criterion, DomainStandard, MethodVariant, ProblemSignature

    standard = DomainStandard("software_engineering", "v1", "AU", "rubric", "a" * 64,
                              (Criterion("c1", "derivation", "checker1", "v1:1", required_methods=("formal",)),))
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=_Registry())
    compiled = orchestrator.compile(
        claim=ClaimIR("claim-1", "entailment", ("formal",), {}),
        context=ProblemSignature("software_engineering", "formal_specification", "invariant", 1, 5),
        standard=standard, variants=[MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2)],
        variant_models={"smt": {"formula": "B", "premises": ["(=> A B)", "A"], "sorts": {"A": "Bool", "B": "Bool"}}})
    out = orchestrator.execute(compiled)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == out["warning_card"]["code"] == "G6_E_COGNITIVE_EXECUTION_NOT_APPROVED"
