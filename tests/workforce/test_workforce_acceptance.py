# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What counts as done, measured (Phase E Part 2, E2c and E7d; Ledger §7, §10, §16; Doctrine §6, §7, §8).

Pinned: a mission's acceptance contract has the Ledger §7 fields and round-trips; its requirements come from the
contract's checkpoints (a closed set of model-free predicates) and any criterion no checkpoint names is one G6 cannot
check; anything written wrong is not checked, never met, never a crash; R0 to R3 are Doctrine §7's tiers, each with its
verification requirement and required state, and a stored contract never asks less than its tier; completeness is the
intent engine's own elicitor's decision; each check's Ledger §10 mechanism stays inspectable under a coarse class;
an unmet or unchecked requirement never closes verified; a result below its required state is blocked unless only an
allowed degradation holds it there; nothing is ever promoted. Offline.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from g6.intent_engine.schema import IntentContract
from g6.workforce import store
from g6.workforce import acceptance as wa
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import economics as we
from g6.workforce import missions as wm
from g6.workforce import plans as wp
from g6.workforce import service as ws

COMPLETE = {"why": "the overdue total decides this month's calls", "success_criteria": ["the total is 6"],
            "failure_criteria": ["a total that does not match the export"], "non_goals": ["chasing anyone"],
            "risk_tolerance": "medium"}
SUM = {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
       "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}}
CHECK = {"key": "check-sum", "kind": "verify", "verifies": "sum", "goal": "The total is the ledger's 6",
         "capabilities": [["fake_solver", "check"]],
         "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6}, "bind": {"total": "sum.total"},
                    "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}}


def _contract(**kw) -> IntentContract:
    return IntentContract(goal="Total the ledger, checked", **kw)


def _compile(risk="R1", overrides=None, **kw) -> wa.AcceptanceContract:
    return wa.compile_contract(objective="Total the ledger, checked", contract=_contract(**kw), risk_tier=risk,
                               budget_cents=50, overrides=overrides)


def _record(result="pass", *, mechanism_kind="formal", independent=True, model_free=True, executed=True,
            check="check-sum", measured=None, authority="") -> dict:
    return wa.mechanism_record(check=check, judges="sum", artifact_id="ev_a", kind=mechanism_kind,
                               op=("fake_solver", "check"), model_free=model_free, executed=executed,
                               independent=independent, result=result, measured=measured, authority=authority)


def _facts(**kw) -> wa.Facts:
    base = {"do_units": ("sum",), "outputs": {"sum": {"total": 6, "claims": [{"text": "a", "source": "export"}]}},
            "checks": {"sum": (_record(),)}, "approvals": {}, "sends": {}, "degradations": {}}
    return wa.Facts(**{**base, **kw})


def _checkpoint(predicate, **params) -> dict:
    return {"name": f"{predicate} holds", "predicate": predicate, "params": params}


# --- the contract (Ledger §7) ----------------------------------------------------------------------------------------


def test_the_contract_has_the_ledger_section_7_fields_and_round_trips():
    found = _compile(checkpoints=[_checkpoint("field_equals", unit="sum", field="total", equals=6)], **COMPLETE)
    assert set(found.as_dict()) == {
        "requirements", "verification_requirements", "risk_tier", "required_completion_state", "max_cost",
        "max_latency_seconds", "deadline", "human_approval_requirements", "objective_verifiers",
        "allowed_degradation", "metadata", "derived_from", "policy_version"}  # E10b: TS §4's sources and version
    assert found.max_cost == we.Money(50, "AUD")
    assert wa.AcceptanceContract.from_dict(found.as_dict()) == found
    assert found.sha256 == wa.AcceptanceContract.from_dict(found.as_dict()).sha256 and len(found.sha256) == 64


@pytest.mark.parametrize(("risk", "tier", "needs", "required"), [
    ("R0", "exploratory", ("output_checked",), wc.QUALIFIED),
    ("R1", "ordinary", ("output_checked",), wc.QUALIFIED),
    ("R2", "consequential", ("independent_check",), wc.QUALIFIED),
    ("R3", "high_consequence", ("objective_check", "person_approved"), wc.VERIFIED),
])
def test_each_risk_tier_asks_for_its_own_verification(risk, tier, needs, required):
    found = _compile(risk)
    assert (found.risk_tier, found.verification_requirements, found.required_completion_state) == (tier, needs, required)
    assert ("a_person_approves" in found.human_approval_requirements) is (risk == "R3")
    assert found.objective_verifiers == (wa.OBJECTIVE_KINDS if risk == "R3" else ())
    assert _compile(risk, risk_tolerance="low").required_completion_state == wc.VERIFIED  # the owner said so


def test_checkpoints_are_measured_and_criteria_no_checkpoint_names_cannot_be_checked():
    found = _compile(checkpoints=[_checkpoint("field_equals", unit="sum", field="total", equals=6)],
                     success_criteria=["field_equals holds", "the books feel right"])
    assert [(r.name, r.predicate, r.problem) for r in found.requirements] == [
        ("field_equals holds", "field_equals", ""), ("the books feel right", "", wa.CANNOT_CHECK)]
    assert [r.name for r in wa.cannot_check(found)] == ["the books feel right"]


def test_the_acceptance_object_sets_what_the_owner_asked_and_nothing_else():
    found = _compile(overrides={"required_completion_state": "verified", "allowed_degradation": ["shadow"],
                                "max_latency_seconds": 600})
    assert (found.required_completion_state, found.allowed_degradation, found.max_latency_seconds) == (
        wc.VERIFIED, ("shadow",), 600)
    for bad, why in (({"colour": "blue"}, "unknown field"), ({"required_completion_state": "done"}, "required"),
                     ({"allowed_degradation": ["anything"]}, "allowed_degradation"),
                     ({"max_latency_seconds": -1}, "max_latency_seconds"),
                     ({"max_latency_seconds": True}, "max_latency_seconds")):
        with pytest.raises(ValueError, match=why):
            _compile(overrides=bad)


def test_a_stored_contract_never_asks_less_than_its_tier():
    weakened = _compile("R1").as_dict() | {"verification_requirements": [], "required_completion_state": wc.QUALIFIED}
    held = wa.floor(wa.AcceptanceContract.from_dict(weakened), "R3")
    assert (held.verification_requirements, held.required_completion_state) == (
        ("objective_check", "person_approved"), wc.VERIFIED)
    lost = wa.unreadable("R1", "Expecting value")
    assert lost.requirements[0].problem.startswith("it could not be read") and lost.verification_requirements


# --- completeness: the intent engine's own elicitor (E7d) ------------------------------------------------------------


def test_completeness_is_the_intent_engines_elicitors_decision():
    complete, asked = wa.spec_complete("Total the ledger, checked", _contract(**COMPLETE))
    assert (complete, asked) == (True, [])
    bare, asked = wa.spec_complete("Total the ledger, checked", _contract())
    assert bare is False and [field for field, _ in asked] == [
        "why", "success_criteria", "failure_criteria", "non_goals", "risk_tolerance"]
    assert asked[0][1] == "Why is this task worth doing now?"
    declined, _ = wa.spec_complete("Total the ledger, checked",
                                   _contract(declined_clarification=True, success_criteria=["the total matches"]))
    assert declined is True  # the owner may decline clarification
    empty, asked = wa.spec_complete("Total the ledger, checked", _contract(declined_clarification=True))
    assert empty is False and "success_criteria" in [field for field, _ in asked]  # IE-001: but not with nothing to check
    by_checkpoint, asked = wa.spec_complete("x", _contract(**{**COMPLETE, "success_criteria": []}, checkpoints=[
        _checkpoint("output_present", unit="sum")]))
    assert by_checkpoint is True  # a checkpoint counts as a success criterion
    assert _compile(**COMPLETE).metadata["spec_complete"] is True
    assert _compile().metadata["missing"][0] == ["why", "Why is this task worth doing now?"]


def test_placeholder_answers_do_not_complete_a_spec():
    """Audit 005 IE-002 (T-09): an all-"x" spec is not complete, declined or not; real answers still are."""
    junk = {"why": "x", "success_criteria": ["x"], "failure_criteria": ["x"], "non_goals": ["x"],
            "risk_tolerance": "medium"}
    complete, asked = wa.spec_complete("Total the ledger", IntentContract(goal="Total the ledger", **junk))
    assert complete is False and [field for field, _ in asked] == [
        "why", "success_criteria", "failure_criteria", "non_goals"]
    declined, asked = wa.spec_complete("Total the ledger, checked",
                                       _contract(declined_clarification=True, success_criteria=["tbd"]))
    assert declined is False and "success_criteria" in [field for field, _ in asked]  # IE-001 holds for a placeholder
    assert _compile(**junk).metadata["spec_complete"] is False
    assert wa.spec_complete("Total the ledger, checked", _contract(**COMPLETE)) == (True, [])  # control


# --- the closed set of predicates (P2.11) ----------------------------------------------------------------------------


@pytest.mark.parametrize(("predicate", "params", "facts", "status"), [
    ("output_present", {"unit": "sum"}, {}, wa.MET),
    ("output_present", {"unit": "sum"}, {"outputs": {}}, wa.UNMET),
    ("fields_present", {"unit": "sum", "fields": ["total", "claims.0.source"]}, {}, wa.MET),
    ("fields_present", {"unit": "sum", "fields": ["currency"]}, {}, wa.UNMET),
    ("field_equals", {"unit": "sum", "field": "total", "equals": 6}, {}, wa.MET),
    ("field_equals", {"unit": "sum", "field": "total", "equals": 7}, {}, wa.UNMET),
    ("each_has", {"unit": "sum", "list": "claims", "field": "source"}, {}, wa.MET),
    ("each_has", {"unit": "sum", "list": "claims", "field": "page"}, {}, wa.UNMET),
    ("each_has", {"unit": "sum", "list": "claims", "field": "source"}, {"outputs": {"sum": {"claims": []}}},
     wa.UNMEASURED),
    ("check_passed", {"unit": "sum"}, {}, wa.MET),
    ("check_passed", {"unit": "sum"}, {"checks": {"sum": (_record("fail"),)}}, wa.UNMET),
    ("check_passed", {"unit": "sum"}, {"checks": {}}, wa.UNMEASURED),
    ("tests_passed", {"unit": "sum"}, {"checks": {"sum": (_record(mechanism_kind="test",
                                                                 measured={"passed": 2, "total": 2}),)}}, wa.MET),
    ("tests_passed", {"unit": "sum"}, {}, wa.UNMEASURED),  # a solver passed it, but no test ran
    ("approved", {"unit": "approve"}, {"approvals": {"approve": "approve"}}, wa.MET),
    ("approved", {"unit": "approve"}, {"approvals": {"approve": "reject"}}, wa.UNMET),
    ("approved", {"unit": "approve"}, {}, wa.UNMEASURED),
    ("receipt", {"unit": "send"}, {"sends": {"send": ("sent", "sent")}}, wa.MET),
    ("receipt", {"unit": "send"}, {"sends": {"send": ("refused",)}}, wa.UNMET),  # shadow: nothing was sent
    ("receipt", {"unit": "send"}, {}, wa.UNMEASURED),
])
def test_each_predicate_measures_what_it_says(predicate, params, facts, status):
    found = wa.measure(wa.requirement("it holds", predicate, params), _facts(**facts))
    assert found.status == status, found.why
    assert found.why


@pytest.mark.parametrize(("predicate", "params", "why"), [
    ("ledger.balanced", {}, "G6 has no check called 'ledger.balanced'"),
    ("field_equals", {"unit": "sum", "field": "total"}, "'equals' is missing"),
    ("field_equals", {"unit": "sum", "field": 3, "equals": 6}, "'field' must be a str"),
    ("output_present", {"unit": "sum", "colour": "blue"}, "'colour' is not a parameter of output_present"),
    ("fields_present", {"unit": "sum", "fields": [1]}, "'fields' must be a list of field paths"),
    ("output_present", {"unit": "nosuchstep"}, "the plan has no step 'nosuchstep'"),
    ("", {}, wa.CANNOT_CHECK),
])
def test_a_requirement_written_wrong_is_not_checked_never_met_never_a_crash(predicate, params, why):
    """Review Focus 4."""
    found = wa.measure(wa.requirement("it holds", predicate, params), _facts())
    assert (found.status, why in found.why) == (wa.UNMEASURED, True), found.why
    words = wa.compile_contract(objective="x", contract=_contract(deadline="next Friday"), risk_tier="R1",
                                budget_cents=0)
    late = wa.evaluate(words, _facts(), wc.Outcome(wc.VERIFIED))[1].limits[0]
    assert (late.status, "not a date and time" in late.why) == (wa.UNMEASURED, True)


def test_an_empty_field_list_checks_nothing_and_true_is_not_one():
    """Fix pass M2: ``fields_present`` naming no field is not checked (never met by default); ``field_equals`` compares
    a true/false value and a number strictly, so true does not match 1."""
    empty = wa.measure(wa.requirement("it holds", "fields_present", {"unit": "sum", "fields": []}), _facts())
    assert (empty.status, empty.why) == (wa.UNMEASURED, "'fields' names no field, so it would check nothing")
    flags = _facts(outputs={"sum": {"total": 1, "done": True}})
    for field, equals, status in (("total", True, wa.UNMET), ("done", 1, wa.UNMET), ("done", True, wa.MET),
                                  ("total", 1, wa.MET), ("total", 1.0, wa.MET), ("done", "true", wa.UNMET)):
        found = wa.measure(wa.requirement("it holds", "field_equals", {"unit": "sum", "field": field,
                                                                       "equals": equals}), flags)
        assert found.status == status, (field, equals, found.why)


def test_the_verification_requirement_says_what_it_measures():
    """Fix pass M3: each output's checks are measured as passed, so that is what the requirement says."""
    _, found = wa.evaluate(_compile("R1"), _facts(), wc.Outcome(wc.VERIFIED))
    assert [m.name for m in found.verification] == ["every result passed a check"]
    _, found = wa.evaluate(_compile("R2"), _facts(), wc.Outcome(wc.VERIFIED))
    assert [m.name for m in found.verification] == ["every result passed an independent check"]


# --- mechanisms and strength (Ledger §10) ----------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "model_free", "op", "mechanism"), [
    ("formal", True, ("fake_solver", "check"), "formal_proof_check"),
    ("test", True, ("workforce", "run_tests"), "test_execution"),
    ("deterministic", True, ("workforce", "check_reminders"), "deterministic_computation"),
    ("oracle", True, ("x", "y"), "deterministic_computation"),
    ("oracle", False, ("x", "y"), "other"),
    ("deterministic", True, ("workforce", "check_receipts"), "receipt_validation"),
])
def test_each_check_kind_is_a_ledger_mechanism(kind, model_free, op, mechanism):
    assert wa.mechanism_of(kind, model_free, op) == mechanism and mechanism in wa.MECHANISMS


def test_a_test_check_keeps_its_tally_and_nothing_else_is_guessed():
    assert wa.tally({"passed": True, "tests": {"t::a": True, "t::b": False}}) == {"passed": 1, "total": 2}
    assert wa.tally({"passed": True}) == {} and wa.tally({"tests": {"a": "yes"}}) == {} and wa.tally(None) == {}


def test_strength_is_the_weakest_outputs_and_objective_needs_an_independent_model_free_pass():
    assert wa.unit_strength([_record()]) == "objective"
    assert wa.unit_strength([_record(model_free=False, mechanism_kind="oracle")]) == "moderate"
    assert wa.unit_strength([_record(model_free=False, mechanism_kind="oracle"),
                             _record(model_free=False, mechanism_kind="oracle", check="check-2")]) == "strong"
    assert wa.unit_strength([_record(independent=False)]) == "weak"
    assert wa.unit_strength([_record("unmeasured", executed=False)]) == "none"
    assert wa.unit_strength([]) == "none"
    assert wa.weakest(["objective", "moderate", "strong"]) == "moderate" and wa.weakest([]) == "none"


# --- evaluating a roll-up (never promotes) ---------------------------------------------------------------------------


def test_an_unmet_or_unchecked_requirement_holds_the_result_at_qualified_draft_and_never_promotes():
    met = _compile(checkpoints=[_checkpoint("field_equals", unit="sum", field="total", equals=6)])
    assert wa.evaluate(met, _facts(), wc.Outcome(wc.VERIFIED))[0].state == wc.VERIFIED
    unmet = _compile(checkpoints=[_checkpoint("field_equals", unit="sum", field="total", equals=7)])
    out, found = wa.evaluate(unmet, _facts(), wc.Outcome(wc.VERIFIED))
    assert out.state == wc.QUALIFIED and found.accepted is True  # qualified is what an R1 contract needs
    assert any("what counts as done is not met: field_equals holds" in r for r in out.reasons)
    prose = _compile(success_criteria=["the books feel right"])
    out, _ = wa.evaluate(prose, _facts(), wc.Outcome(wc.VERIFIED))
    assert out.state == wc.QUALIFIED and any("was not checked: the books feel right" in r for r in out.reasons)
    blocked, _ = wa.evaluate(met, _facts(), wc.Outcome(wc.BLOCKED, reasons=("a step stopped",)))
    assert blocked.state == wc.BLOCKED  # nothing is promoted


def test_a_result_below_its_required_state_is_blocked_unless_only_an_allowed_degradation_holds_it():
    strict = _compile(overrides={"required_completion_state": "verified"}, success_criteria=["the books feel right"])
    out, found = wa.evaluate(strict, _facts(), wc.Outcome(wc.VERIFIED))
    assert (out.state, found.accepted) == (wc.BLOCKED, False)
    assert any("needs a verified result and this one is qualified-draft" in r for r in out.reasons)
    # The check is verifier-grade (A4): the degradation is then the only shortfall (Gate W review I1).
    shadow = _facts(checks={"sum": (_record(authority="A4"),)}, degradations={"sum": ("shadow: nothing was sent",)})
    degraded = wc.Outcome(wc.QUALIFIED, degraded=True, reasons=("unit sum's output was degraded",))
    allowed = _compile(overrides={"required_completion_state": "verified", "allowed_degradation": ["shadow"]})
    out, found = wa.evaluate(allowed, shadow, degraded)
    assert (out.state, found.accepted, found.allowed) == (wc.QUALIFIED, True, ("shadow",))  # kept, never promoted
    out, found = wa.evaluate(_compile(overrides={"required_completion_state": "verified"}), shadow, degraded)
    assert (out.state, found.accepted) == (wc.BLOCKED, False)


def test_a_persons_set_aside_beside_a_shadow_degradation_is_not_an_allowed_degradation():
    """Fix pass M8: the exception holds only when the degradation is the ONLY shortfall. A failed check a person set
    aside also holds the result below verified, so the result is blocked for a person, never accepted."""
    allowed = _compile(overrides={"required_completion_state": "verified", "allowed_degradation": ["shadow"]})
    aside = {**_record("fail", check="check-2"), "set_aside": True}
    person = wa.mechanism_record(check="check-2", judges="sum", artifact_id="ev_a", kind="human", op=(),
                                 model_free=True, executed=True, independent=True, result="set_aside")
    facts = _facts(checks={"sum": (_record(), aside, person)}, degradations={"sum": ("shadow: nothing was sent",)})
    held = wc.Outcome(wc.QUALIFIED, degraded=True, reasons=("unit sum's output was degraded: shadow: nothing was sent",
                                                          "verifier check-2's fail was set aside by a human: fine"))
    out, found = wa.evaluate(allowed, facts, held)
    assert (out.state, found.accepted, found.allowed) == (wc.BLOCKED, False, ())


def test_a_check_below_verifier_grade_is_a_shortfall_no_allowed_degradation_excuses():
    """Gate W review I1: a result's state never exceeds the authority of the evidence that establishes it. A mission
    that needs a verified result and allows the shadow degradation, whose only check is A3 (or carries no class, which
    reads as A0), is held below verified by its check as well as by the degradation: blocked for a person, never
    accepted. The roll-up's own cap is a shortfall too, whatever the record says. The control: the same degradation
    over an A4 check is still accepted."""
    allowed = _compile(overrides={"required_completion_state": "verified", "allowed_degradation": ["shadow"]})
    degraded = wc.Outcome(wc.QUALIFIED, degraded=True,
                          reasons=("unit sum's output was degraded: shadow: nothing was sent",))
    shadow = {"sum": ("shadow: nothing was sent",)}
    for cls in ("A3", ""):
        out, found = wa.evaluate(allowed, _facts(checks={"sum": (_record(authority=cls),)}, degradations=shadow),
                                 degraded)
        assert (out.state, found.accepted, found.allowed) == (wc.BLOCKED, False, ()), cls
    verifier_grade = _facts(checks={"sum": (_record(authority="A4"),)}, degradations=shadow)
    out, found = wa.evaluate(allowed, verifier_grade, degraded)
    assert (out.state, found.accepted, found.allowed) == (wc.QUALIFIED, True, ("shadow",))  # the control
    capped = wc.Outcome(wc.QUALIFIED, degraded=True,
                        reasons=(*degraded.reasons, wc.AUTHORITY_COPY["rollup_capped"].format(unit="sum", reason=wc.AUTHORITY_COPY["verdict_capped"])))
    out, found = wa.evaluate(allowed, verifier_grade, capped)
    assert (out.state, found.accepted, found.allowed) == (wc.BLOCKED, False, ())


def test_each_check_records_its_mechanism_and_the_mission_keeps_them_inspectable():
    _, found = wa.evaluate(_compile(), _facts(), wc.Outcome(wc.VERIFIED))
    assert (found.strength, found.mechanisms, found.unit_strengths) == ("objective", ("formal_proof_check",),
                                                                        (("sum", "objective"),))
    assert found.records[0]["result"] == "pass" and found.verification[0].status == wa.MET
    assert wa.Evaluation.from_dict(found.as_dict()) == found


def test_a_plans_unit_checks_are_requirements_on_their_unit():
    plan = wp.parse_plan({"units": [SUM, {**CHECK, "checks": [_checkpoint("field_equals", field="total", equals=6),
                                                                {"name": "suite", "predicate": "coder.run_tests"}]}],
                          "edges": []}, risk_tier="R1", budget_cents=0)
    found = wa.unit_requirements(plan)
    assert [(r.name, r.predicate, r.params.get("unit")) for r in found] == [
        ("check-sum: field_equals holds", "field_equals", "sum"), ("check-sum: suite", "", None)]


# --- the mission keeps its contract (Task 5) -------------------------------------------------------------------------

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731
CONTRACTS = {
    ("fake_ledger", "total"): wcap.ParamContract.from_dict({
        "component": "fake_ledger", "op": "total", "required": {"numbers": "list"}, "probe_params": {"numbers": [1]},
        "expect_field": "total", "side_effect": "read", "check": "input_model"}),
    ("fake_solver", "check"): wcap.ParamContract.from_dict({
        "component": "fake_solver", "op": "check", "required": {"total": "number", "expected": "number"},
        "probe_params": {"total": 1, "expected": 1}, "expect_field": "satisfiable", "side_effect": "read",
        "check": "input_model", "model_free": True}),
    ("fake_tests", "run"): wcap.ParamContract.from_dict({
        "component": "fake_tests", "op": "run", "required": {"total": "number"}, "probe_params": {"total": 1},
        "expect_field": "passed", "side_effect": "read", "check": "input_model", "model_free": True}),
}


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver", "fake_tests"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


class World:
    """The fake components; ``degraded`` makes the ledger answer as a shadow send does (nothing was sent)."""

    def __init__(self) -> None:
        self.degraded = False

    def __call__(self, component: str, op: str, params: dict):
        if component == "fake_ledger":
            reply = {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
            if self.degraded:
                reply.update(degraded=True, degradation_reason="shadow: nothing was sent")
            return reply
        if component == "fake_tests":
            return {"ok": True, "completion_state": "qualified-draft",
                    "result": {"passed": params["total"] == 6, "tests": {"t::total": params["total"] == 6,
                                                                         "t::shape": True}}}
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"satisfiable": params["total"] != params["expected"]}}


def _rig(tmp_path, owner: str = "alice", authority=SCRIPTED):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture(owner, f"{owner}'s", **_CAPS)["venture_id"]
    world = World()
    env = {ws.ENV_DB_DIR: str(tmp_path), ws.ENV_VENTURE: venture, ws.ENV_OWNER: owner}
    wf = ws.bind(env, registry=FakeRegistry(), invoke=world, contracts=CONTRACTS, authority=authority)
    for pair in CONTRACTS:
        assert ws.probe(wf, *pair)["level"] == "probe_passed"

    def mission(contract: dict | None = None, *, risk: str = "R1", plan: dict | None = None) -> str:
        mid = ws.create(wf, objective="Total the ledger, checked", risk_tier=risk, budget_cents=0)["mission_id"]
        ws.accept(wf, mid, contract)
        if plan is not None:
            ws.plan(wf, mid, plan)
        return mid

    return SimpleNamespace(vstore=vstore, venture=venture, world=world, wf=wf, mission=mission)


def test_accepting_a_mission_keeps_the_contract_its_spec_compiled_into(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"goal": "the total, checked",
                       "checkpoints": [_checkpoint("field_equals", unit="sum", field="total", equals=6)]})
    kept = rig.wf.missions.acceptance(mid)
    assert [(r.predicate, r.params["equals"]) for r in kept.requirements] == [("field_equals", 6)]
    assert rig.wf.missions.get(mid)["status"] == "awaiting_plan"
    moved = [e["detail"] for e in rig.wf.missions.events(mid, kind="mission.status")][-1]
    assert (moved["to"], moved["acceptance_sha256"]) == ("awaiting_plan", kept.sha256)
    assert ws.status(rig.wf, mid)["acceptance"]["sha256"] == kept.sha256


def test_the_contract_may_carry_what_the_owner_asks_of_the_result(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"goal": "the total", "acceptance": {"required_completion_state": "verified",
                                                           "allowed_degradation": ["shadow"]}})
    kept = rig.wf.missions.acceptance(mid)
    assert (kept.required_completion_state, kept.allowed_degradation) == (wc.VERIFIED, ("shadow",))
    other = ws.create(rig.wf, objective="x", risk_tier="R1", budget_cents=0)["mission_id"]
    with pytest.raises(ValueError, match="unknown field"):
        ws.accept(rig.wf, other, {"goal": "x", "acceptance": {"colour": "blue"}})
    assert rig.wf.missions.get(other)["status"] == "eliciting"  # a refusal changes nothing


def test_a_mission_accepted_before_this_part_is_judged_by_the_contract_it_holds(tmp_path):
    """Review Focus 1: a mission moved to awaiting its plan before acceptance contracts were kept has its contract
    compiled from the clarified contract it holds, every time it is read."""
    rig = _rig(tmp_path)
    mid = ws.create(rig.wf, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0)["mission_id"]
    rig.wf.missions.set_contract(mid, {"goal": "x", "checkpoints": [_checkpoint("output_present", unit="sum")]},
                                 actor="chat")
    rig.wf.missions.transition(mid, "awaiting_plan", reason="accepted before Part 2", actor="chat")
    assert rig.wf.missions.get(mid)["acceptance"] is None
    found = rig.wf.missions.acceptance(mid)
    assert found.requirements[0].predicate == "output_present" and "on read" in found.metadata["compiled"]


def _accepted_before_this_part(rig, risk: str = "R1") -> str:
    """A mission moved to awaiting its plan as the code before this part did: a clarified contract, no acceptance
    contract kept."""
    mid = ws.create(rig.wf, objective="Total the ledger, checked", risk_tier=risk, budget_cents=0)["mission_id"]
    rig.wf.missions.set_contract(mid, {"goal": "x"}, actor="chat")
    rig.wf.missions.transition(mid, "awaiting_plan", reason="accepted before Part 2", actor="chat")
    return mid


def as_before_this_part(db: str, mid: str) -> None:
    """The mission's rows rewritten to the shape the code before this part wrote (pre dc8895a108): no Ledger §10
    records on ``run.closed``, no evaluation on ``mission.closed``, no acceptance contract or its digest, no
    intervention rows."""
    conn = sqlite3.connect(db)
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
        conn.execute(f'DROP TRIGGER "{name}"')
    for kind, path in (("run.closed", "$.verification"), ("mission.closed", "$.acceptance"),
                       ("mission.status", "$.acceptance_sha256")):
        conn.execute("UPDATE evidence SET detail_json = json_remove(detail_json, ?) WHERE kind = ? AND ref = ?",
                     (path, kind, mid))
    conn.execute("DELETE FROM evidence WHERE kind = 'intervention' AND ref = ?", (mid,))
    conn.execute("UPDATE missions SET acceptance_json = '' WHERE mission_id = ?", (mid,))
    conn.commit()
    conn.close()




def test_an_r2_mission_accepted_before_this_part_plans_as_before(tmp_path):
    """Fix pass I2 (Review Focus 1, R2): a consequential mission accepted before contracts were kept is not stranded
    waiting for a confirmation nothing can give: it plans as before, and its return says what counts as done was
    compiled from its older record."""
    from g6.workforce import decide as wdecide

    rig = _rig(tmp_path)
    mid = _accepted_before_this_part(rig, "R2")
    waiting = ws.status(rig.wf, mid)
    assert waiting["awaiting_human"] == [] and wdecide._pending(rig.wf) == []
    note = "What counts as done was compiled from this mission's older record"
    assert note in waiting["result"]["markdown"]
    planned = ws.plan(rig.wf, mid, SUM_PLAN)
    assert planned["status"] == "active" and note in planned["result"]["markdown"]
    assert ws.run(rig.wf, mid)["status"] == "done"


def test_a_store_from_before_this_part_gains_the_column_and_keeps_its_missions(tmp_path):
    """Review Focus 1: the missions table as it shipped before Part 2 (no acceptance_json) migrates on open."""
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    vstore.conn.executescript(wm._DDL.replace("    acceptance_json    TEXT NOT NULL DEFAULT '',\n", ""))
    vstore.conn.execute("INSERT INTO missions (mission_id, venture_id, objective, contract_json, risk_tier, created_at,"
                        " updated_at) VALUES ('m_old', ?, 'An old mission', '{\"goal\": \"An old mission\"}', 'R1',"
                        " '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')", (venture,))
    vstore.conn.commit()
    missions = wm.MissionStore(vstore.for_owner("alice"))
    assert missions.get("m_old")["acceptance"] is None
    assert missions.acceptance("m_old").risk_tier == "ordinary"


def test_a_stored_contract_that_cannot_be_read_holds_the_result_back(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"goal": "x"})
    conn = sqlite3.connect(tmp_path / "venture.db")
    conn.execute("DROP TRIGGER IF EXISTS missions_finished_are_final")
    conn.execute("UPDATE missions SET acceptance_json = '{\"requirements\": 5}' WHERE mission_id = ?", (mid,))
    conn.commit()
    conn.close()
    found = rig.wf.missions.acceptance(mid)
    assert found.metadata.get("unreadable") is True and found.requirements[0].problem.startswith("it could not")

# --- the roll-up measures it (Task 6) --------------------------------------------------------------------------------

SUM_PLAN = {"units": [SUM, CHECK], "edges": []}
TESTED = {"units": [SUM, {**CHECK, "key": "test-sum", "capabilities": [["fake_tests", "run"]],
                          "oracle": {"capability": ["fake_tests", "run"], "params": {}, "bind": {"total": "sum.total"},
                                     "expect": {"field": "passed", "equals": True}, "kind": "test"}}], "edges": []}


def _closed(rig, mid: str) -> dict:
    ws.run(rig.wf, mid)
    return [e["detail"] for e in rig.wf.missions.events(mid, kind="mission.closed")][-1]


def test_an_unmet_acceptance_requirement_never_closes_verified(tmp_path):
    rig = _rig(tmp_path)
    met = rig.mission({"goal": "x", "checkpoints": [_checkpoint("field_equals", unit="sum", field="total", equals=6)]},
                      plan=SUM_PLAN)
    assert ws.run(rig.wf, met)["completion_state"] == wc.VERIFIED
    unmet = rig.mission({"goal": "x", "checkpoints": [
        _checkpoint("field_equals", unit="sum", field="total", equals=7)]}, plan=SUM_PLAN)
    final = ws.run(rig.wf, unmet)
    assert final["completion_state"] == wc.QUALIFIED
    assert any("not met: field_equals holds: total is 6, not 7" in r for r in final["outcome"]["reasons"])


def test_a_requirement_g6_cannot_check_holds_the_result_at_use_with_caution(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"goal": "x", "success_criteria": ["the books feel right"]}, plan=SUM_PLAN)
    final = ws.run(rig.wf, mid)
    assert final["completion_state"] == wc.QUALIFIED
    assert any("was not checked: the books feel right" in r for r in final["outcome"]["reasons"])


def test_a_result_below_its_required_state_closes_blocked_for_a_person(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"goal": "x", "success_criteria": ["the books feel right"],
                       "acceptance": {"required_completion_state": "verified"}}, plan=SUM_PLAN)
    final = ws.run(rig.wf, mid)
    assert final["completion_state"] == wc.BLOCKED
    assert _closed(rig, mid)["acceptance"]["accepted"] is False


def test_an_allowed_degradation_keeps_a_result_accepted_without_promoting_it(tmp_path):
    rig = _rig(tmp_path)
    rig.world.degraded = True  # the producer answers as a shadow send does: nothing was sent
    allowed = rig.mission({"goal": "x", "acceptance": {"required_completion_state": "verified",
                                                       "allowed_degradation": ["shadow"]}}, plan=SUM_PLAN)
    assert ws.run(rig.wf, allowed)["completion_state"] == wc.QUALIFIED  # kept, never promoted
    assert (_closed(rig, allowed)["acceptance"]["accepted"], _closed(rig, allowed)["acceptance"]["allowed"]) == (
        True, ["shadow"])
    strict = rig.mission({"goal": "x", "acceptance": {"required_completion_state": "verified"}}, plan=SUM_PLAN)
    assert ws.run(rig.wf, strict)["completion_state"] == wc.BLOCKED


def test_an_allowed_degradation_does_not_excuse_a_check_below_verifier_grade(tmp_path):
    """Gate W review I1 (the review's probe 2, a): the mission needs verified and allows the shadow degradation; its
    check's op is A3. The degradation is not its only shortfall, so it closes blocked for a person, not accepted. The
    control (the A4 check, the same degradation) is the test above."""
    rig = _rig(tmp_path, authority=lambda c, o: "A3" if c == "fake_solver" else "A4")
    rig.world.degraded = True
    mid = rig.mission({"goal": "x", "acceptance": {"required_completion_state": "verified",
                                                   "allowed_degradation": ["shadow"]}}, plan=SUM_PLAN)
    final = ws.run(rig.wf, mid)
    assert final["completion_state"] == wc.BLOCKED, final["outcome"]["reasons"]
    found = _closed(rig, mid)["acceptance"]
    assert (found["accepted"], found["allowed"]) == (False, [])
    assert any("Use with caution: this check has not met G6’s verification requirements." in r for r in final["outcome"]["reasons"])


def test_each_check_records_its_mechanism_and_the_tally_of_a_test_run(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"goal": "x"}, plan=TESTED)
    assert ws.run(rig.wf, mid)["completion_state"] == wc.VERIFIED
    checked = [e["detail"] for e in rig.wf.missions.events(mid, kind="run.closed") if e["detail"]["unit"] == "test-sum"]
    record, = checked[-1]["verification"]
    assert (record["mechanism"], record["result"], record["measured"]) == (
        "test_execution", "pass", {"passed": 2, "total": 2})
    found = _closed(rig, mid)["acceptance"]
    assert (found["strength"], found["mechanisms"], found["accepted"]) == ("objective", ["test_execution"], True)






def test_a_check_whose_finding_was_refused_keeps_no_pass_record(tmp_path, monkeypatch):
    """Fix pass M4: a verdict the ledger refuses (not bound to the output it claims to judge), or a close the ledger
    could not record, keeps no "pass" mechanism record, so no trust panel says a check passed a blocked result."""
    import dataclasses

    from g6.workforce import rungs as wrung

    rig = _rig(tmp_path)
    real = wrung.run_verify

    def unbound(*args, **kwargs):  # the digest of what the check read is not the output's
        found = real(*args, **kwargs)
        return dataclasses.replace(found, verdict=dataclasses.replace(found.verdict, artifact_sha="0" * 64))

    monkeypatch.setattr(wrung, "run_verify", unbound)
    mid = rig.mission({"goal": "x"}, plan=SUM_PLAN)
    final = ws.run(rig.wf, mid)
    monkeypatch.undo()
    assert final["completion_state"] == wc.BLOCKED
    checked = [e["detail"] for e in rig.wf.missions.events(mid, kind="run.closed") if e["detail"]["unit"] == "check-sum"]
    assert [d.get("verification") for d in checked] == [[]]  # recorded: no check was kept on the output
    assert "A solver checked" not in final["result"]["markdown"]
    as_before_this_part(str(tmp_path / "venture.db"), mid)  # the same refused pass, in rows from before this part
    assert "A solver checked" not in ws.status(rig.wf, mid)["result"]["markdown"]
    real_close, left = rig.wf.ledger.close, [1]

    def refused(run_id, outcome, **kwargs):  # the ledger refuses the check's record once: the fallback close
        if kwargs.get("verdict") is not None and left[0]:
            left[0] -= 1
            raise sqlite3.IntegrityError("injected: the ledger refused the record")
        return real_close(run_id, outcome, **kwargs)

    monkeypatch.setattr(rig.wf.ledger, "close", refused)
    other = rig.mission({"goal": "x"}, plan=SUM_PLAN)
    assert ws.run(rig.wf, other)["completion_state"] == wc.BLOCKED
    checked = [e["detail"] for e in rig.wf.missions.events(other, kind="run.closed")
               if e["detail"]["unit"] == "check-sum"]
    assert [r["failure_code"] for r in rig.wf.ledger.runs(other, "check-sum")] == ["unrecorded"]
    assert checked and not [r for d in checked for r in d.get("verification") or () if r["result"] == "pass"]

# --- the pain spec into acceptance: confirmation and unmet demand (Task 7) -------------------------------------------


def test_an_r2_mission_with_an_incomplete_spec_is_not_accepted_and_says_what_to_answer(tmp_path):
    rig = _rig(tmp_path)
    mid = ws.create(rig.wf, objective="Total the ledger, checked", risk_tier="R2", budget_cents=0)["mission_id"]
    with pytest.raises(ValueError) as refused:
        ws.accept(rig.wf, mid, {"goal": "x"})
    text = str(refused.value)
    assert text.startswith("This mission cannot be accepted yet: what counts as done is not complete")
    assert "Why is this task worth doing now?" in text and "(code G6_E_MISSING_PARAM)" in text
    assert rig.wf.missions.get(mid)["status"] == "eliciting"


def test_an_r2_plan_waits_for_the_owners_confirmation_bound_to_the_contract(tmp_path):
    rig = _rig(tmp_path)
    mid = ws.create(rig.wf, objective="Total the ledger, checked", risk_tier="R2", budget_cents=0)["mission_id"]
    ws.accept(rig.wf, mid, {"goal": "x", **COMPLETE})
    assert [q["kind"] for q in ws.status(rig.wf, mid)["awaiting_human"]] == ["acceptance"]
    with pytest.raises(ValueError, match="has not been confirmed by you"):
        ws.plan(rig.wf, mid, SUM_PLAN)
    with pytest.raises(ValueError, match="only a person"):
        rig.wf.runtime.confirm_acceptance(mid, reason="looks right", actor="chat")
    iv = rig.wf.runtime.confirm_acceptance(mid, reason="this is what I need", actor="human",
                                           decided_by="terminal:alice")
    assert (iv.kind, iv.outcome, iv.required_by_policy) == ("clarification", "confirmed", True)
    assert iv.metadata["acceptance_sha256"] == rig.wf.missions.acceptance(mid).sha256
    assert ws.status(rig.wf, mid)["awaiting_human"] == []
    with pytest.raises(ValueError, match="already confirmed"):
        rig.wf.runtime.confirm_acceptance(mid, reason="again", actor="human")
    assert ws.plan(rig.wf, mid, SUM_PLAN)["status"] == "active"
    assert ws.run(rig.wf, mid)["completion_state"] == wc.QUALIFIED  # R2 asks an independent check; prose criteria


def test_a_plan_problem_is_reported_before_the_missing_confirmation(tmp_path):
    rig = _rig(tmp_path)
    mid = ws.create(rig.wf, objective="Total the ledger, checked", risk_tier="R2", budget_cents=0)["mission_id"]
    ws.accept(rig.wf, mid, {"goal": "x", **COMPLETE})
    with pytest.raises(wp.PlanRejected):
        ws.plan(rig.wf, mid, {"units": [SUM], "edges": []})  # no verify unit: the plan's own problem comes first


def test_an_r1_mission_needs_no_confirmation_and_may_have_one(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"goal": "x", **COMPLETE})
    assert ws.status(rig.wf, mid)["awaiting_human"] == []
    iv = rig.wf.runtime.confirm_acceptance(mid, reason="fine", actor="human")
    assert iv.required_by_policy is False
    bare = rig.mission({"goal": "x"})
    with pytest.raises(ValueError, match="cannot be confirmed yet"):
        rig.wf.runtime.confirm_acceptance(bare, reason="fine", actor="human")  # incomplete: nothing to confirm


def test_a_need_g6_cannot_check_or_cannot_meet_is_recorded_as_unmet_demand(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"goal": "x", "success_criteria": ["the books feel right"]})
    plan = {"units": [{**SUM, "capabilities": [["job_accountant", "assess_compliance"]],
                       "call": {"capability": ["job_accountant", "assess_compliance"], "params": {}}}, CHECK],
            "edges": []}
    with pytest.raises(wp.PlanRejected):
        ws.plan(rig.wf, mid, plan)
    found = [(d["kind"], d["subject"]) for d in ws.unmet_demand(rig.wf)]
    assert found == [("cannot_check", "the books feel right"),
                     ("not_available", "job_accountant.assess_compliance")]
    with pytest.raises(wp.PlanRejected):
        ws.plan(rig.wf, mid, plan)
    assert len(ws.unmet_demand(rig.wf)) == 2  # recorded once per mission and need


def test_the_command_line_confirms_what_counts_as_done(tmp_path, monkeypatch, capsys):
    from g6.workforce import decide as wdecide

    rig = _rig(tmp_path)
    mid = ws.create(rig.wf, objective="Total the ledger, checked", risk_tier="R2", budget_cents=0)["mission_id"]
    ws.accept(rig.wf, mid, {"goal": "x", **COMPLETE})
    for name, value in ((ws.ENV_DB_DIR, str(tmp_path)), (ws.ENV_VENTURE, rig.venture), (ws.ENV_OWNER, "alice")):
        monkeypatch.setenv(name, value)
    real = ws.bind
    monkeypatch.setattr(ws, "bind", lambda env=None: real(env, registry=FakeRegistry(), invoke=rig.world,
                                                         contracts=CONTRACTS, authority=SCRIPTED))
    assert wdecide.main(["list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["kind"] == "acceptance"
    monkeypatch.setattr(wdecide, "_interactive", lambda: True)
    assert wdecide.main(["confirm", mid, "--reason", "this is what I need"], ask=lambda prompt: mid) == 0
    assert "Stakes: consequential" in capsys.readouterr().out
    assert rig.wf.missions.confirmed(mid) is True