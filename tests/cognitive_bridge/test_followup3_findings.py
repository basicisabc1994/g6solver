# SPDX-License-Identifier: Apache-2.0
"""Follow-up review 3's three findings, brought in as the bridge's own tests.

Findings 1 and 2 are guard_covers_less_than_it_guards, met in repair 2: the claim rule (``codes.claims``) opened only the
containers it listed and passed every other object unread (a ``deque``, a plain dataclass), and read a mapping's keys by
name only, so a record used as a key was never read. ``test_claim_kinds.py`` is the inventory of what the rule reads.

Finding 3 is untrusted_input_given_authority and one_fact_two_rules: a compilation's state was its own ``refused`` field,
not its selection's verdict, so a compilation a caller made with a blocked selection and ``refused=None`` executed and
was credited. Its state is now decided in one place, from its selection and its plan, and both ``execute`` and
``assess`` read that place."""
from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    DOCUMENT_FIELDS, ClaimIR, CognitiveAssuranceOrchestrator, CompiledResearch, Criterion, DomainStandard,
    EvidenceArtifact, FrozenVerifier, InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MethodRouter,
    MethodVariant, MixedMethodExecutor, ProblemSignature, codes,
)
from g6.cognitive_bridge.snapshot import record

# --- Findings 1 and 2: the claim rule reads everything, or refuses ---------------------------------------------------


@dataclass(frozen=True)
class ForeignRecord:
    reliability_label: str = "verified"
    verified: bool = True


def _formal_plan():
    return MethodRouter().plan([ClaimIR("c", "check A", ("formal",), {"formal": {"formula": "A"}})])


def _answering(answer):
    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": answer})
    return Registry()


@pytest.mark.parametrize("container", ["sequence", "record"])
def test_finding_1_tool_claim_in_foreign_container_is_not_credited(container):
    payload = deque([{"reliability_label": "verified", "verified": True}]) if container == "sequence" else ForeignRecord()
    out = MixedMethodExecutor(_answering(payload)).execute(_formal_plan(), approved=True)
    if out["completion_state"] == "qualified-draft":
        leaked = out["steps"][0]["result"]["answer"]
        assert (leaked[0]["verified"] if container == "sequence" else leaked.verified) is True
    assert out["completion_state"] == "blocked-escalated", \
        "tool's unread container retained verified=True and was credited as a completed step"
    step = out["steps"][0]
    assert step["code"] == codes.STEP_FAILED and "result" not in step
    # since follow-up 2 of PR #8 a deque is refused as a deque, where it enters, before anything in it is read
    named = "answer: a collections.deque is not JSON data" if container == "sequence" else "ForeignRecord"
    assert named in step["reason"], step["reason"]


@record
class ClaimInKey:
    verified: bool = True


def test_finding_2_record_used_as_mapping_key_cannot_smuggle_a_state():
    out = MixedMethodExecutor(_answering({ClaimInKey(): "unchecked"})).execute(_formal_plan(), approved=True)
    if out["completion_state"] == "qualified-draft":
        assert next(iter(out["steps"][0]["result"]["answer"])).verified is True
    assert out["completion_state"] == "blocked-escalated", \
        "a recognized snapshot record in a mapping key retained verified=True and the step was credited"
    # since follow-up 2 of PR #8 a key that is not text is refused as what it is, where it enters
    assert out["steps"][0]["code"] == codes.STEP_FAILED and "ClaimInKey key is not text" in out["steps"][0]["reason"]


# --- Finding 3: a compilation's state is its selection's verdict -----------------------------------------------------

class _Recorder:
    def __init__(self):
        self.calls = []

    def invoke(self, name, op, params=None, **kwargs):
        self.calls.append((name, op))
        return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})


@pytest.fixture
def research():
    """Follow-up review 3's case: one formal obligation, its variant costing 1, compiled once with a budget of 2
    (qualified) and once with a budget of 0 (the selection is blocked: no affordable variant)."""
    registry = _Recorder()
    standard = DomainStandard("review", "1", "AU", "rubric", "a" * 64,
                              (Criterion("c", "formal check", "checker", "1:1", required_methods=("formal",)),))
    variant = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
    selector = MetacognitiveMethodSelector()
    orch = CognitiveAssuranceOrchestrator(selector, registry=registry)

    def compile_(budget):
        return orch.compile(claim=ClaimIR("claim", "A entails A", ("formal",), {}),
                            context=ProblemSignature("review", "logic", "validity", 1, budget),
                            standard=standard, variants=[variant],
                            variant_models={"v": {"formula": "A", "premises": ["A"]}})

    qualified, blocked = compile_(2), compile_(0)
    assert qualified.completion_state == "qualified-draft" and blocked.completion_state == "blocked-escalated"
    rejected = selector.select(blocked.context, [variant], required_methods=["formal"])
    assert rejected.completion_state == "blocked-escalated" and rejected == blocked.decision

    def assess(compiled, run):
        return orch.assess(
            compiled, standard=standard, run_result=run, evaluator=InProcessAssuranceEvaluator(evaluator_id="outside"),
            artifacts={"c": EvidenceArtifact("e", b"GOOD", "report", "candidate", "source:e", "claim")},
            verifiers={"checker": FrozenVerifier("checker", "outside", "b" * 64, lambda b: b == b"GOOD", (b"GOOD",),
                                                 (b"BAD",))},
            documentation={name: "Explicit scope and limitations" for name in DOCUMENT_FIELDS},
            candidate_actor="candidate")

    return SimpleNamespace(registry=registry, orch=orch, qualified=qualified, blocked=blocked, rejected=rejected,
                           assess=assess)


def _never_runs_nor_credited(r, make):
    """``make()`` builds a compilation whose selection is blocked: either it is refused as it is made, or it runs
    nothing and is not credited (execute and assess are both blocked)."""
    try:
        compiled = make()
    except (TypeError, ValueError):
        assert r.registry.calls == []
        return "refused as made"
    run = r.orch.execute(compiled, approved=True)
    assessment = r.assess(compiled, run)
    assert r.registry.calls == [], f"a blocked selection executed {r.registry.calls}"
    assert run["completion_state"] == "blocked-escalated" and assessment["completion_state"] == "blocked-escalated", (
        f"a blocked selection was credited: run {run['completion_state']}, assessment {assessment['completion_state']}")
    return "blocked"


def test_finding_3_handmade_compilation_cannot_execute_and_credit_a_refused_selection(research):
    r = research
    # No mutation past a frozen view: these are ordinary new public records. The caller's refusal field is the only
    # thing saying this compilation may run.
    _never_runs_nor_credited(r, lambda: replace(r.qualified, context=r.blocked.context, decision=r.rejected,
                                                refused=None))


def test_a_blocked_selection_with_no_refusal_is_refused_as_it_is_made(research):
    r = research
    with pytest.raises(ValueError, match="selection"):
        replace(r.qualified, decision=r.rejected, refused=None)
    with pytest.raises(ValueError, match="selection"):
        replace(r.blocked, refused=None)
    with pytest.raises(ValueError, match="selection"):  # made by hand, not through replace
        CompiledResearch(**{**{f.name: getattr(r.qualified, f.name) for f in dataclasses.fields(CompiledResearch)},
                            "decision": r.rejected, "refused": None})
    assert r.registry.calls == []


FORGED = {
    "another-reason": lambda: codes.refusal(codes.COMPILE_BLOCKED, "nothing is missing"),
    "another-code": lambda: codes.refusal(codes.SELECTION_INCOMPLETE, "no affordable variant covers: formal"),
    "says-qualified": lambda: {"completion_state": "qualified-draft"},
    "unregistered": lambda: {**codes.refusal(codes.COMPILE_BLOCKED, "x"), "code": "G6_E_REVIEW_UNREGISTERED"},
}


@pytest.mark.parametrize("forged", sorted(FORGED))
@pytest.mark.parametrize("selection", ["qualified", "blocked"])
def test_a_forged_refusal_is_refused_whatever_the_selection(research, selection, forged):
    r = research
    with pytest.raises(ValueError):
        replace(getattr(r, selection), refused=FORGED[forged]())
    assert r.registry.calls == []


FIELDS = [f.name for f in dataclasses.fields(CompiledResearch)]


@pytest.mark.parametrize("field", FIELDS)
def test_each_field_a_caller_replaces_cannot_run_or_credit_a_blocked_selection(research, field):
    r = research
    # the blocked compilation, with one field taken from the qualified one
    _never_runs_nor_credited(r, lambda: replace(r.blocked, **{field: getattr(r.qualified, field)}))
    # the qualified compilation, with one field taken from the blocked one: its state is its own selection's and
    # plan's verdict, whatever the field
    try:
        made = replace(r.qualified, **{field: getattr(r.blocked, field)})
    except (TypeError, ValueError):
        return
    expected = ("qualified-draft" if made.decision.completion_state == "qualified-draft" and not made.decision.missing
                and not made.executable_plan.missing else "blocked-escalated")
    assert made.completion_state == expected
    if expected == "blocked-escalated":
        assert r.orch.execute(made, approved=True)["completion_state"] == "blocked-escalated"
        assert r.registry.calls == []


def test_the_genuine_compilations(research):
    r = research
    assert r.blocked.refused["code"] == codes.COMPILE_BLOCKED
    assert _never_runs_nor_credited(r, lambda: r.blocked) == "blocked"
    run = r.orch.execute(r.qualified, approved=True)
    assert r.registry.calls == [("formal", "check_validity")] and run["completion_state"] == "qualified-draft"
    assert r.assess(r.qualified, run)["completion_state"] == "qualified-draft"


def test_execute_and_assess_read_the_state_from_the_selection_not_the_refused_field(research):
    r = research
    # Past the frozen view, the refusal is gone, and the state is still the selection's verdict.
    forged = replace(r.qualified, context=r.blocked.context, decision=r.rejected,
                     refused=codes.refusal(codes.COMPILE_BLOCKED, "no selected variant for formal"))
    object.__setattr__(forged, "refused", None)
    assert forged.completion_state == "blocked-escalated"
    assert forged.to_dict()["completion_state"] == "blocked-escalated"  # what it publishes says the same
    assert _never_runs_nor_credited(r, lambda: forged) == "blocked"
    # and the assessment says why
    assert "no selected variant for formal" in r.assess(forged, r.orch.execute(forged, approved=True))["reason"]


class _SaysQualified:
    """Not a compilation: an object that says it is qualified and answers every question a compilation would."""

    def __init__(self, compiled):
        self._compiled = compiled
        self.completion_state = "qualified-draft"

    def __getattr__(self, name):
        return getattr(self._compiled, name)

    def changed_since_compiled(self, plan=None):
        return ""

    def __call__(self):  # code is kept as it is by a snapshot
        return None


def test_only_a_compilation_is_executed_or_assessed(research):
    r = research
    blocked_selection = replace(r.qualified, context=r.blocked.context, decision=r.rejected,
                                refused=codes.refusal(codes.COMPILE_BLOCKED, "no selected variant for formal"))
    fake = _SaysQualified(blocked_selection)
    run = r.orch.execute(fake, approved=True)
    assert r.registry.calls == [], "an object that says it is qualified executed a blocked selection"
    assert run["completion_state"] == "blocked-escalated"
    genuine = r.orch.execute(r.qualified, approved=True)  # a real run of the same plan, for the same claim
    try:
        out = r.assess(fake, genuine)
    except (TypeError, ValueError):
        return
    assert out["completion_state"] == "blocked-escalated", \
        "an object that says it is qualified was credited for a blocked selection"
