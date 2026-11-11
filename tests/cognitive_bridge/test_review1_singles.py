# SPDX-License-Identifier: Apache-2.0
"""Review 1, single findings 1, 17, 18 and 22: a probe runs as selected and within budget, no component result carries a
bare ``verified`` field, refutation-only evidence is decided by ``g6.epistemic_integrity``'s own rule, and one variant's
trials on one case count once whatever the oracle's revision. Walking the class list added one more: a door reply that did
not pass is never read as a run."""
from __future__ import annotations

from dataclasses import replace

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAgent, CrossMethodConflictGate, EvaluatedTrial, Hypothesis, MetacognitiveMethodSelector,
    MethodRouter, MethodVariant, MixedMethodExecutor, Observation, Probe, ProblemSignature, ScopedAssertion,
    StaticProposer, ToolSpec,
)
from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity.adjudicator import adjudicate_claim


def _bare_verified(value, under_card: bool = False) -> list[str]:
    """Each path to a ``verified`` key outside G6's warning cards."""
    if isinstance(value, dict):
        found = [] if under_card or "verified" not in value else ["verified"]
        for key, item in value.items():
            found += [f"{key}.{p}" for p in _bare_verified(item, under_card or key == "warning_card")]
        return found
    if isinstance(value, list):
        return [f"[{i}].{p}" for i, item in enumerate(value) for p in _bare_verified(item, under_card)]
    return []


def test_finding_1_duplicate_probe_ids_cannot_execute_the_unaffordable_probe():
    calls = []

    def observed(name):
        def call(question, arguments):
            calls.append(name)
            return Observation(True, "fixture:measurement")
        return call
    tools = {"expensive": ToolSpec("expensive", observed("expensive"), .8, 10),
             "cheap": ToolSpec("cheap", observed("cheap"), .8, 1)}
    probes = [Probe("same", "expensive question", "expensive", {"A": True, "B": False}, .8, 10),
              Probe("same", "cheap question", "cheap", {"A": True, "B": False}, .8, 1)]
    try:
        result = CognitiveAgent(StaticProposer(probes), tools).run(
            "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    except ValueError:
        assert calls == []
        return
    assert "expensive" not in calls, f'unaffordable probe ran: {calls}; remaining_budget={result["remaining_budget"]}'


def test_finding_17_the_executor_carries_no_bare_verified_field_outside_warning_cards():
    class Registry:
        def invoke(self, *args, **kwargs):
            return Success({"completion_state": "verified", "verified": True,
                            "answer": {"valid": True, "verified": True}})
    plan = MethodRouter().plan([ClaimIR("c", "claim", ("formal",), {"formal": {"formula": "P"}})])
    out = MixedMethodExecutor(Registry()).execute(plan, approved=True)
    # Follow-up review 2's ruling: the flag, even the false one G6's door left, is neither republished outside the
    # door's card nor dropped unsaid. The step fails with its code, names each place, keeps the door's card whole.
    step = out["steps"][0]
    assert step["completion_state"] == "blocked-escalated" and step["code"] == "G6_E_COGNITIVE_STEP_FAILED"
    assert step["door"]["warning_card"]["code"] == "G6_E_PROMOTION_LAW"
    assert "result" not in step, f'the method result was kept: {step.get("result")}'
    assert "verified" in step["reason"] and "answer.verified" in step["reason"], step["reason"]
    assert _bare_verified(out) == []


@pytest.mark.parametrize("reply", ["door_blocked", "never_seen"])
def test_class_walk_a_door_reply_that_did_not_pass_is_never_a_run(monkeypatch, reply):
    # failure_read_as_success, found walking the class: a step counts as run only when G6's door says it passed; a
    # reply the executor does not recognise as a pass (here a state and verdict the door never gives) is a refusal
    class Registry:
        def invoke(self, *args, **kwargs):
            state = "blocked-escalated" if reply == "door_blocked" else "qualified-draft"
            return Success({"completion_state": state, "answer": {"valid": True}})
    executor = MixedMethodExecutor(Registry())
    if reply == "never_seen":
        real = executor._invoke

        def unseen(*args):
            return {**real(*args), "completion_state": "pending", "ok": None}
        monkeypatch.setattr(executor, "_invoke", unseen)
    plan = MethodRouter().plan([ClaimIR("c", "claim", ("formal",), {"formal": {"formula": "P"}})])
    out = executor.execute(plan, approved=True)
    step = out["steps"][0]
    assert step["completion_state"] == "blocked-escalated", f"a {reply} reply was read as a run"
    assert out["completion_state"] == "blocked-escalated"
    assert str(step.get("code", "")).startswith("G6_E_"), step


def test_finding_18_refuted_only_assertions_block_by_the_existing_epistemic_rule():
    canonical = adjudicate_claim("The switch is on", evidence_records=(
        EvidenceRecord("measurement:off", supports_or_opposes="opposes", independence_group="measurement:off",
                       claim_id="c"),))
    assert canonical.claim.disposition == "BLOCK"
    assertion = ScopedAssertion("c", "switch-on", "a" * 64, "refutes", "formal", "measurement:off", "receipt")
    out = CrossMethodConflictGate(admit_assertion=lambda a: a == assertion).reconcile([assertion])
    assert out["completion_state"] == "blocked-escalated", (
        f'refutation labeled {out["scoped_findings"][0]["status"]} and accepted')
    assert out["scoped_findings"][0]["status"] == "refuted"


def test_finding_22_one_case_is_not_several_independent_cases_after_an_oracle_revision():
    variant = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
    bound = ({"variant_digest": variant.definition_digest}
             if "variant_digest" in EvaluatedTrial.__dataclass_fields__ else {})
    original = EvaluatedTrial("first", "same-case", "same-cohort", "d", "u", "k", "v", True, 1, "receipt:first",
                              "a" * 64, **bound)
    replay = replace(original, trial_id="rerun", evaluator_ref="receipt:rerun", oracle_digest="b" * 64)
    admitted = (original, replay)
    selector = MetacognitiveMethodSelector(admitted, admit_trial=lambda t: t in admitted)
    row = selector.select(ProblemSignature("d", "u", "k", 2, 2), [variant],
                          required_methods=["formal"]).ordered_variants[0]
    assert row["training_cases"] == 1, f'one variant on one case counted {row["training_cases"]} times'
