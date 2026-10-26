# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The standard the task demands (Phase E Part 10, E10b; TS §4-§10, §31, §34-§38, §45-§47; S2, S9, S12, S15).

Pinned: a mission's tier is the highest of the tier declared, the signals in the task's own words and what its plan's
steps do, and nothing lowers it; the contract records what it was derived from and under which policy, and a contract
kept before this part reads back with its digest; the chat may raise the result a mission needs, never lower it; a
raised standard is the next revision, logged, which a consequential mission's owner confirms again, and a lowering is
refused (STD-005); each step takes the stakes of what it does; TS §46's table is a regression suite; a verify_task
record is kept at the tier its task demands. Offline.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest
from g6.workforce import result_view as rv
from g6.intent_engine.schema import IntentContract
from g6.workforce import store
from g6.workforce import acceptance as wa
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import missions as wm
from g6.workforce import plans as wp
from g6.workforce import service as ws

COMPLETE = {"why": "the overdue total decides this month's calls", "success_criteria": ["the total is 6"],
            "failure_criteria": ["a total that does not match the export"], "non_goals": ["chasing anyone"],
            "risk_tolerance": "medium"}
#: A complete spec whose success criterion is a checkpoint G6 measures, so a verified result is reachable.
CHECKED = {**COMPLETE, "success_criteria": [], "checkpoints": [
    {"name": "the total is 6", "predicate": "field_equals", "params": {"unit": "sum", "field": "total", "equals": 6}}]}
#: A contract as Part 2 stored it (2026-10-04): no derived_from, policy_version or revision.
PART_2 = {"requirements": [], "verification_requirements": ["independent_check"], "risk_tier": "consequential",
          "required_completion_state": "qualified-draft", "max_cost": {"amount_minor_units": 0, "currency": "AUD"},
          "max_latency_seconds": None, "deadline": "", "human_approval_requirements": ["external_ops_behind_a_gate"],
          "objective_verifiers": [], "allowed_degradation": [], "metadata": {"plan_risk": "R2", "spec_complete": True}}


def _compile(objective="Total the ledger, checked", risk="R1", overrides=None, floors=(), **kw):
    return wa.compile_contract(objective=objective, contract=IntentContract(goal=objective, **kw), risk_tier=risk,
                               budget_cents=50, overrides=overrides, floors=floors)


# --- the contract records the standard (Task 2) ----------------------------------------------------------------------


@pytest.mark.parametrize(("objective", "declared", "tier", "sources"), [
    ("Total the ledger, checked", "R1", "R1", ["declared"]),  # Review Focus 4: no signal, the declared tier stands
    ("Patch the production login code", "R0", "R2", ["declared", "objective"]),  # a floor the chat cannot go under
    ("Patch the production login code", "R3", "R3", ["declared", "objective"]),  # Review Focus 3: a higher tier stays
    ("Brainstorm ten names for a coffee brand", "R2", "R2", ["declared", "objective"]),  # a low signal never lowers
    ("Delete production data older than a year", "R1", "R3", ["declared", "objective"]),
])
def test_the_tier_is_the_highest_of_what_was_declared_and_what_the_task_says(objective, declared, tier, sources):
    found = _compile(objective, declared)
    assert (wa.risk_code(found), [d["source"] for d in found.derived_from]) == (tier, sources)
    assert (found.policy_version, found.revision) == (wa.STANDARD_POLICY, 1)
    assert found.risk_tier == wa.TIERS[tier] and found.metadata["plan_risk"] == declared  # what was declared, kept


def test_what_the_task_says_is_cited_in_plain_words():
    found = _compile("Patch the production login code", "R0")
    assert found.derived_from[1] == {"source": "objective", "tier": "R2", "why": "the task mentions 'production'"}
    assert wa.standard_words(found) == "consequential (the task mentions 'production')"
    assert wa.standard_words(_compile()) == "ordinary (as set for this mission)"
    assert (wa.base_tier(found), wa.code_floor(found), wa.code_floor(_compile())) == ("R2", "R2", "R0")


def test_a_contract_kept_before_the_standard_reads_back_with_the_same_digest():
    """Review Focus 1: a Part 2 contract read back through the new rules keeps its digest, so a person's confirmation
    bound to it still holds, and it keeps the standard it was given; nothing is invented for it."""
    stored = json.dumps(PART_2, sort_keys=True, separators=(",", ":"), default=str)
    back = wa.floor(wa.AcceptanceContract.from_dict(json.loads(stored)), "R2")
    assert back.sha256 == hashlib.sha256(stored.encode("utf-8")).hexdigest()
    assert (back.derived_from, back.policy_version, back.revision) == ((), "", 1)
    assert wa.standard_words(back) == ""


def test_a_mission_accepted_before_contracts_were_kept_keeps_the_standard_it_was_given():
    found = wa.compile_contract(objective="Patch the production login code", contract=IntentContract(goal="x"),
                                risk_tier="R0", budget_cents=0, compiled_on_read=True)
    assert (wa.risk_code(found), found.derived_from, found.policy_version) == ("R0", (), "")


def test_a_template_that_derives_its_own_standard_keeps_it_as_given():
    """Ruling E10b-R1 (E-1): A3's objective entry (a code change, a JSON format) derives its standard with its own
    code (``coding._freeze``'s feasibility floor; ``formatting.start``'s clear-only gate), so the contract keeps it as
    given, exactly as before E10b: the same body, no second derivation, no policy version."""
    kept = wa.compile_contract(objective="Fix the drop table helper", contract=IntentContract(goal="x"),
                               risk_tier="R2", budget_cents=0, template_derived=True)
    before = wa.compile_contract(objective="Fix the drop table helper", contract=IntentContract(goal="x"),
                                 risk_tier="R2", budget_cents=0, compiled_on_read=True)
    assert (wa.risk_code(kept), kept.derived_from, kept.policy_version) == ("R2", (), "")
    assert "compiled" not in kept.metadata and kept.as_dict() == {**before.as_dict(), "metadata": {
        k: v for k, v in before.metadata.items() if k != "compiled"}}
    assert wa.risk_code(wa.compile_contract(objective="Fix the drop table helper", contract=IntentContract(goal="x"),
                                            risk_tier="R2", budget_cents=0)) == "R3"  # control: derived, it rises


@pytest.mark.parametrize(("objective", "risk", "kw"), [
    ("Total the ledger, checked", "R3", {}),
    ("Total the ledger, checked", "R1", {"risk_tolerance": "low"}),  # the owner said so: the hole this closes
    ("Delete production data older than a year", "R1", {}),          # the task's words make it high consequence
])
def test_the_chat_may_raise_the_result_a_mission_needs_never_lower_it(objective, risk, kw):
    with pytest.raises(ValueError, match="never lower it.*G6_E_POLICY_BLOCKED"):
        _compile(objective, risk, overrides={"required_completion_state": "qualified-draft"}, **kw)
    assert _compile(overrides={"required_completion_state": "verified"}).required_completion_state == wc.VERIFIED
    assert _compile(overrides={"required_completion_state": "qualified-draft"}
                    ).required_completion_state == wc.QUALIFIED  # asking what it already needs changes nothing


def test_a_stored_standard_above_its_missions_tier_keeps_its_own_tier():
    assert wa.risk_code(wa.floor(_compile("Patch the production login code", "R0"), "R0")) == "R2"
    high = wa.floor(_compile(), "R3")
    assert "a_person_approves" in high.human_approval_requirements and high.objective_verifiers == wa.OBJECTIVE_KINDS
    assert high.required_completion_state == wc.VERIFIED


@pytest.mark.parametrize("bad", [{"derived_from": [{"source": "chat", "tier": "R0", "why": "x"}]},
                                 {"derived_from": [{"source": "objective", "tier": "R9", "why": "x"}]},
                                 {"revision": 0}, {"revision": True}])
def test_a_stored_standard_written_wrong_is_refused_so_the_mission_fails_closed(bad):
    with pytest.raises(ValueError):
        wa.AcceptanceContract.from_dict({**PART_2, **bad})


# --- one door raises a standard (Task 3) -----------------------------------------------------------------------------

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
RAISED = {"source": "runtime", "tier": "R2", "why": "the ledger holds personal data"}


def _store(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    return wm.MissionStore(vstore.for_owner("alice")), venture


def _accept(missions, venture, objective, risk, spec=None, **create):
    mid = missions.create(venture, objective=objective, risk_tier=risk, budget_cents=0, actor="chat", **create)
    contract = IntentContract(goal=objective, **(spec or {}))
    missions.accept(mid, wa.compile_contract(objective=objective, contract=contract, risk_tier=risk, budget_cents=0),
                    actor="chat", contract=contract)
    return mid


def test_accepting_raises_the_missions_tier_to_its_standard_and_logs_it(tmp_path):
    missions, venture = _store(tmp_path)
    mid = _accept(missions, venture, "Patch the production login code", "R0", COMPLETE)
    assert missions.get(mid)["risk_tier"] == "R2"
    (logged,) = missions.events(mid, kind="standard")
    assert {k: logged["detail"][k] for k in ("revision", "from", "to")} == {"revision": 1, "from": "R0", "to": "R2"}
    assert logged["detail"]["acceptance_sha256"] == missions.acceptance(mid).sha256
    assert missions.events(mid, kind="mission.created")[0]["detail"]["risk_tier"] == "R0"  # what was declared stays


def test_work_the_task_makes_consequential_needs_the_owners_full_spec(tmp_path):
    missions, venture = _store(tmp_path)
    with pytest.raises(ValueError, match="what counts as done is not complete"):
        _accept(missions, venture, "Patch the production login code", "R0")
    (mission,) = missions.list(venture)
    assert (mission["status"], mission["risk_tier"]) == ("eliciting", "R0")  # nothing was recorded


def test_a_record_of_a_check_already_given_keeps_its_tier_with_no_confirmation(tmp_path):
    """Ruling E10.12: a verify_task record runs nothing on the owner's behalf; its tier is kept, E7d does not apply."""
    missions, venture = _store(tmp_path)
    kept = _accept(missions, venture, "Fix the production login bug", "R0", template="verify_task",
                   task_sha256="a" * 64)
    assert missions.get(kept)["risk_tier"] == "R2" and missions.records_only(kept)
    with pytest.raises(ValueError, match="not complete"):  # control: an ad hoc mission at those stakes still asks
        _accept(missions, venture, "Fix the production login bug", "R0")


def test_a_raised_standard_is_the_next_revision_and_a_lowering_is_refused(tmp_path):
    """TS §34-§35, S15, STD-005: a standard rises by a new revision, logged; one that would ask less in any way is
    refused, whoever offers it."""
    missions, venture = _store(tmp_path)
    mid = _accept(missions, venture, "Total the ledger, checked", "R1", COMPLETE)
    before = missions.acceptance(mid)
    assert wa.strengthen(before, tier="R0") is before and wa.strengthen(before, tier="R1") is before
    stronger = wa.strengthen(before, tier="R2", derived_from=(RAISED,))
    assert (stronger.revision, wa.risk_code(stronger), stronger.derived_from[-1]) == (2, "R2", RAISED)
    missions.strengthen(mid, stronger, actor="code", reason="the ledger holds personal data")
    assert (missions.get(mid)["risk_tier"], missions.acceptance(mid).revision) == ("R2", 2)
    assert [e["detail"]["to"] for e in missions.events(mid, kind="standard")] == ["R1", "R2"]
    for weaker in (dataclasses.replace(stronger, revision=3, verification_requirements=()),
                   dataclasses.replace(stronger, revision=3, risk_tier="ordinary"),
                   dataclasses.replace(stronger, revision=3, allowed_degradation=("shadow",)),
                   stronger):  # the same revision again
        with pytest.raises(ValueError, match="G6_E_POLICY_BLOCKED"):
            missions.strengthen(mid, weaker, actor="chat", reason="a worker asks for fewer checks")
    assert missions.acceptance(mid).revision == 2


CONTRACTS = {
    ("fake_ledger", "total"): wcap.ParamContract.from_dict({
        "component": "fake_ledger", "op": "total", "required": {"numbers": "list"}, "probe_params": {"numbers": [1]},
        "expect_field": "total", "side_effect": "read", "check": "input_model", "model_free": True}),
    ("fake_solver", "check"): wcap.ParamContract.from_dict({
        "component": "fake_solver", "op": "check", "required": {"total": "number", "expected": "number"},
        "probe_params": {"total": 1, "expected": 1}, "expect_field": "satisfiable", "side_effect": "read",
        "check": "input_model", "model_free": True}),
    ("fake_mail", "send"): wcap.ParamContract.from_dict({
        "component": "fake_mail", "op": "send", "required": {"total": "number"}, "probe_params": {"total": 1},
        "expect_field": "sent", "side_effect": "external", "check": "input_model"}),
    ("fake_db", "delete"): wcap.ParamContract.from_dict({
        "component": "fake_db", "op": "delete", "required": {"total": "number"}, "probe_params": {"total": 1},
        "expect_field": "deleted", "side_effect": "irreversible", "check": "input_model"}),
}
def SCRIPTED(component, op):  # a scripted oracle stands in for a verifier-grade one (Gate W)
    return "A4"


class FakeRegistry:
    def list_all(self):
        return sorted({component for component, _ in CONTRACTS})

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


def _world(component: str, op: str, params: dict):
    if component == "fake_ledger":
        return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
    if component == "fake_solver":
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"satisfiable": params["total"] != params["expected"]}}
    raise AssertionError(f"unexpected call {component}.{op}")  # nothing here sends or deletes


@pytest.fixture
def wf(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    env = {ws.ENV_DB_DIR: str(tmp_path), ws.ENV_VENTURE: venture, ws.ENV_OWNER: "alice"}
    bound = ws.bind(env, registry=FakeRegistry(), invoke=_world, contracts=CONTRACTS, authority=SCRIPTED)
    for pair in (("fake_ledger", "total"), ("fake_solver", "check")):
        assert ws.probe(bound, *pair)["level"] == "probe_passed"
    return bound


def _mission(wf, objective: str, risk: str, spec: dict | None) -> str:
    mid = ws.create(wf, objective=objective, risk_tier=risk, budget_cents=0)["mission_id"]
    ws.accept(wf, mid, {"goal": objective, **(spec or {})})
    return mid


SUM = {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
       "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}}
CHECK = {"key": "check-sum", "kind": "verify", "verifies": "sum", "goal": "The total is the ledger's 6",
         "capabilities": [["fake_solver", "check"]],
         "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6}, "bind": {"total": "sum.total"},
                    "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}}




# --- the plan's steps set their own floor (Task 4) -------------------------------------------------------------------

EFFECTS = {("fake_ledger", "total"): "read", ("fake_solver", "check"): "read", ("fake_mail", "send"): "external",
           ("fake_db", "delete"): "irreversible"}
GATE = {"key": "approve", "kind": "gate", "goal": "A person approves the total going out", "inputs": ["sum"]}
SEND = {"key": "send", "kind": "do", "goal": "Send the total", "inputs": ["approve", "sum"],
        "capabilities": [["fake_mail", "send"]],
        "call": {"capability": ["fake_mail", "send"], "params": {}, "bind": {"total": "sum.total"}}}
CHECK_SEND = {**CHECK, "key": "check-send", "verifies": "send", "goal": "The sent total is 6",
              "oracle": {**CHECK["oracle"], "bind": {"total": "send.total"}}}
DROP = {**SEND, "key": "drop", "goal": "Delete the old rows", "capabilities": [["fake_db", "delete"]],
        "call": {"capability": ["fake_db", "delete"], "params": {}, "bind": {"total": "sum.total"}}}
CHECK_DROP = {**CHECK_SEND, "key": "check-drop", "verifies": "drop", "goal": "The rows are gone",
              "oracle": {**CHECK["oracle"], "bind": {"total": "drop.total"}}}
SEND_PLAN = {"units": [SUM, CHECK, GATE, SEND, CHECK_SEND], "edges": []}
DROP_PLAN = {"units": [SUM, CHECK, GATE, DROP, CHECK_DROP], "edges": []}


def _side(component: str, op: str):
    return EFFECTS.get((component, op))


def _parsed(data: dict, inherit: str, floor: str = "R0") -> wp.Plan:
    drafted = wp.unit_floors(data, effects=_side, inherit=inherit, floor=floor)
    return wp.parse_plan(drafted, risk_tier=wp.higher(inherit, wp.highest_risk(drafted)), budget_cents=0, effects=_side)


def test_a_step_takes_the_stakes_of_what_its_tools_do_and_an_unrated_one_the_missions():
    """Review Focus 2: a step the plan did not rate is never R0 by default."""
    drafted = wp.unit_floors(SEND_PLAN, effects=_side, inherit="R1")
    assert {u["key"]: u["risk"] for u in drafted["units"]} == {
        "sum": "R1", "check-sum": "R1", "approve": "R1", "send": "R2", "check-send": "R1"}
    assert "risk" not in SEND_PLAN["units"][0]  # the draft itself is not changed


def test_a_step_rated_lower_keeps_its_rating_but_never_below_the_tasks_words_or_its_tools():
    """S9 and ruling E10.9: the chat may rate a step below its mission; never below what the task's own words or the
    step's tools demand."""
    rated = {"units": [{**SUM, "risk": "R0"}, {**SEND, "risk": "R0"}], "edges": []}
    assert [u["risk"] for u in wp.unit_floors(rated, effects=_side, inherit="R3")["units"]] == ["R0", "R2"]
    assert [u["risk"] for u in wp.unit_floors(rated, effects=_side, inherit="R3", floor="R2")["units"]] == ["R2", "R2"]


def test_a_delegated_child_takes_its_parents_stakes():
    drafted = wp.unit_floors({"units": [{**SUM, "risk": "R1"}, {"key": "part", "kind": "do", "goal": "x",
                                                                 "parent": "sum"}]}, effects=_side, inherit="R3")
    assert drafted["units"][1]["risk"] == "R1"


def test_an_op_with_no_contract_counts_as_acting_and_what_is_malformed_is_left_for_the_plan_check():
    assert wp.effect_of([("nobody", "knows")], lambda c, o: None) == "external"
    assert wp.effect_of([], _side) == "read"
    assert wp.unit_floors("not json", effects=_side, inherit="R1") == "not json"
    assert wp.unit_floors({"units": [{"key": "x", "risk": "R9"}]}, effects=_side, inherit="R1")["units"][0]["risk"] == "R9"
    assert wp.highest_risk({"units": [{"risk": "R2"}, {"risk": "R9"}, "x"]}) == "R2"


def test_the_plans_steps_raise_the_standard_and_say_why():
    contract = _compile("Total the ledger and share it", "R1", **COMPLETE)
    raised = wa.for_plan(contract, _parsed(SEND_PLAN, "R1"), _side)
    assert (wa.risk_code(raised), raised.revision) == ("R2", 2)
    assert raised.derived_from[-1] == {"source": "step", "tier": "R2", "why": "step 'send' reaches someone outside",
                                       "unit": "send", "reversibility": "partially_reversible"}
    reads = _parsed({"units": [SUM, CHECK], "edges": []}, "R1")
    assert wa.for_plan(contract, reads, _side) is contract  # nothing rises: no revision, no new confirmation


def test_a_step_that_cannot_be_undone_needs_its_outside_state_confirmed():
    raised = wa.for_plan(_compile(risk="R1", **COMPLETE), _parsed(DROP_PLAN, "R1"), _side)
    assert wa.risk_code(raised) == "R3" and raised.required_completion_state == wc.VERIFIED
    assert "a_person_approves" in raised.human_approval_requirements
    assert "person_approved" in raised.verification_requirements
    (state,) = [r for r in raised.requirements if r.predicate == "state_confirmed"]
    assert (state.params, state.problem) == ({"unit": "drop"}, "")


def _check_on_drop(kind: str, op: tuple[str, str], result: str) -> dict:
    return wa.mechanism_record(check="check-drop", judges="drop", artifact_id="ev_d", kind=kind, op=op,
                               model_free=True, executed=True, independent=True, result=result)


@pytest.mark.parametrize(("records", "status"), [
    ((_check_on_drop("deterministic", ("workforce", "check_receipts"), "pass"),), wa.MET),
    ((_check_on_drop("deterministic", ("workforce", "check_receipts"), "fail"),), wa.UNMET),
    ((_check_on_drop("formal", ("fake_solver", "check"), "pass"),), wa.UNMEASURED),  # a solver confirms no outside state
    ((), wa.UNMEASURED),
])
def test_a_state_confirmation_is_met_only_by_a_receipt_or_an_outside_state_check(records, status):
    facts = wa.Facts(do_units=("drop",), outputs={"drop": {"deleted": 3}}, checks={"drop": records}, approvals={},
                     sends={}, degradations={})
    found = wa.measure(wa.requirement("drop: what it changed was confirmed", "state_confirmed", {"unit": "drop"}),
                       facts)
    assert found.status == status, found.why


# --- storing a plan applies the floor (Task 5) -----------------------------------------------------------------------


def _plan(wf, mid: str, plan: dict) -> int:
    """A plan stored as ``mission_plan`` stores it, with the side effects the broker declares (readiness aside: a send
    and a delete cannot be probed here)."""
    return wf.missions.add_plan(mid, plan, actor="chat", ready=lambda c, o: None, effects=wf.caps.side_effect)


def test_a_plan_that_sends_raises_the_mission_and_waits_for_the_owners_confirmation(wf):
    mid = _mission(wf, "Total the ledger and share it", "R1", COMPLETE)
    with pytest.raises(ValueError, match=r"raised what counts as done for this mission to R2 \(step 'send' reaches "
                                         r"someone outside\).*G6_E_PENDING_APPROVAL"):
        _plan(wf, mid, SEND_PLAN)
    mission = wf.missions.get(mid)
    assert (mission["risk_tier"], mission["plan_revision"], wf.missions.acceptance(mid).revision) == ("R2", 0, 2)
    assert [q["kind"] for q in ws.status(wf, mid)["awaiting_human"]] == ["acceptance"]
    wf.runtime.confirm_acceptance(mid, reason="yes, it sends the total out", actor="human")
    assert _plan(wf, mid, SEND_PLAN) == 1  # the same plan, now stored; the standard did not rise again
    _, stored = wf.missions.plan(mid)
    assert {u.key: u.risk for u in stored.units} == {
        "sum": "R1", "check-sum": "R1", "approve": "R1", "send": "R2", "check-send": "R1"}
    assert wf.missions.acceptance(mid).revision == 2


def test_a_plan_that_only_reads_changes_nothing_and_an_unrated_step_takes_the_missions_stakes(wf):
    """Review Focus 2 and 3."""
    mid = _mission(wf, "Total the ledger, checked", "R2", CHECKED)
    wf.runtime.confirm_acceptance(mid, reason="this is what I need", actor="human")
    ws.plan(wf, mid, {"units": [SUM, CHECK], "edges": []})
    assert (wf.missions.get(mid)["risk_tier"], wf.missions.acceptance(mid).revision) == ("R2", 1)
    _, stored = wf.missions.plan(mid)
    assert {u.risk for u in stored.units} == {"R2"}


@pytest.mark.parametrize(("objective", "declared", "kept"), [
    ("Total the ledger, checked", "R2", "R0"),             # the chat's own rating stands below its own declaration
    ("Total the production ledger, checked", "R0", "R2"),  # never below what the task's words demand
])
def test_a_step_rated_below_the_mission_keeps_its_rating_unless_the_task_itself_says_more(wf, objective, declared,
                                                                                            kept):
    mid = _mission(wf, objective, declared, CHECKED)
    wf.runtime.confirm_acceptance(mid, reason="this is what I need", actor="human")
    ws.plan(wf, mid, {"units": [{**SUM, "risk": "R0"}, CHECK], "edges": []})
    _, stored = wf.missions.plan(mid)
    assert stored.unit("sum").risk == kept


def test_a_step_that_cannot_be_undone_raises_the_mission_to_high_consequence(wf):
    mid = _mission(wf, "Clear out the old rows", "R1", COMPLETE)
    with pytest.raises(ValueError, match="G6_E_PENDING_APPROVAL"):
        _plan(wf, mid, DROP_PLAN)
    contract = wf.missions.acceptance(mid)
    assert (wa.risk_code(contract), contract.required_completion_state) == ("R3", wc.VERIFIED)
    assert [r.params for r in contract.requirements if r.predicate == "state_confirmed"] == [{"unit": "drop"}]


def test_when_a_plan_raises_an_incomplete_mission_the_chat_accepts_it_again_with_the_owners_answers(wf):
    mid = _mission(wf, "Total the ledger and share it", "R1", None)  # incomplete: fine at R1
    with pytest.raises(ValueError, match="accept the mission again with the owner's answers"):
        _plan(wf, mid, SEND_PLAN)
    with pytest.raises(ValueError, match="cannot be confirmed yet"):
        wf.runtime.confirm_acceptance(mid, reason="ok", actor="human")
    ws.accept(wf, mid, {"goal": "the total, shared", **COMPLETE})  # again, before any plan
    assert (wf.missions.get(mid)["risk_tier"], wf.missions.acceptance(mid).revision) == ("R2", 3)
    wf.runtime.confirm_acceptance(mid, reason="yes, it sends the total out", actor="human")
    assert _plan(wf, mid, SEND_PLAN) == 1
    with pytest.raises(ValueError, match="before any plan"):
        ws.accept(wf, mid, {"goal": "x", **COMPLETE})  # once a plan is stored, what counts as done is fixed


def test_a_mission_whose_template_derived_its_standard_plans_as_before(wf):
    """Ruling E10b-R1: a contract kept with no policy version (Part 2's, or A3's templates') is not re-derived from
    its plan: a step above the mission is still refused, and nothing is raised."""
    mid = ws.create(wf, objective="Total the ledger and share it", risk_tier="R1", budget_cents=0)["mission_id"]
    wf.missions.accept(mid, wa.compile_contract(objective="Total the ledger and share it",
                                                contract=IntentContract(goal="x", **COMPLETE), risk_tier="R1",
                                                budget_cents=0, template_derived=True), actor="code")
    assert wf.missions.normalized(mid, SEND_PLAN, wf.caps.side_effect) is SEND_PLAN
    with pytest.raises(wp.PlanRejected, match="risk R2 exceeds the mission's R1"):
        _plan(wf, mid, {"units": [SUM, CHECK, GATE, {**SEND, "risk": "R2"}, CHECK_SEND], "edges": []})
    assert (wf.missions.get(mid)["risk_tier"], wf.missions.acceptance(mid).revision) == ("R1", 1)


def test_render_the_e10b_review_samples(wf):
    """The Design §34 review's texts: the refusal a raised plan returns, and what the owner reads before confirming;
    written to G6_DESIGN_REVIEW_DIR when it is set, free of the avoid list."""
    mid = _mission(wf, "Total the ledger and share it", "R1", COMPLETE)
    with pytest.raises(ValueError) as refused:
        _plan(wf, mid, SEND_PLAN)
    answer = ws.status(wf, mid)["result"]["markdown"]
    assert "Why these stakes: step 'send' reaches someone outside." in answer
    for text in (str(refused.value), answer):
        assert not rv.AVOID.search(text), text
    out = os.environ.get("G6_DESIGN_REVIEW_DIR")
    if out:
        Path(out, "e10b-raised-plan.md").write_text(f"{refused.value}\n\n---\n\n{answer}\n", encoding="utf-8")


# --- a standard raised while the mission runs (Task 6) ---------------------------------------------------------------

PULLED = {"key": "sum", "kind": "do", "goal": "Total the ledger by hand", "output_schema": "total"}


@pytest.mark.parametrize(("confirmed", "state"), [(False, wc.QUALIFIED), (True, wc.VERIFIED)])
def test_a_standard_raised_while_the_mission_runs_holds_its_result_until_the_owner_confirms_it(wf, confirmed, state):
    """S15: the raised standard is the next revision; until the owner confirms it, the result is at most Use with
    caution, and the reason says what resolves it."""
    mid = _mission(wf, "Total the ledger, checked", "R1", CHECKED)
    ws.plan(wf, mid, {"units": [PULLED, CHECK], "edges": []})
    assert ws.run(wf, mid)["status"] == "waiting"  # for the chat's total
    wf.missions.strengthen(mid, wa.strengthen(wf.missions.acceptance(mid), tier="R2", derived_from=(RAISED,)),
                           actor="code", reason="the ledger holds personal data")
    asked = ws.status(wf, mid)["awaiting_human"]
    assert [q["kind"] for q in asked] == ["acceptance"] and "raised while the mission ran" in asked[0]["question"]
    if confirmed:
        wf.runtime.confirm_acceptance(mid, reason="yes, treat it as consequential", actor="human")
    ws.submit(wf, mid, "sum", {"total": 6})
    done = ws.run(wf, mid)
    assert done["completion_state"] == state, done["outcome"]
    assert any("you have not confirmed" in r for r in done["outcome"]["reasons"]) is (not confirmed)




def test_a_running_mission_takes_a_confirmation_only_for_a_standard_raised_while_it_ran(wf):
    mid = _mission(wf, "Total the ledger, checked", "R2", CHECKED)
    wf.runtime.confirm_acceptance(mid, reason="this is what I need", actor="human")
    ws.plan(wf, mid, {"units": [PULLED, CHECK], "edges": []})
    ws.run(wf, mid)
    with pytest.raises(ValueError, match="before its plan"):
        wf.runtime.confirm_acceptance(mid, reason="again", actor="human")
    assert ws.status(wf, mid)["awaiting_human"] == []


# --- TS §46's assurance regression suite (Task 7) --------------------------------------------------------------------

#: TS §46's fixtures, plus TS §45's control against over-classifying harmless work. Each row: a task, the steps its plan
#: names (component, op, declared side effect), and the standard G6 must derive: its tier, whether a person must approve
#: before it acts, and whether its outside state must be confirmed. A changed row is a reviewed decision, never drift.
ASSURANCE = [
    ("brainstorming", "Brainstorm ten names for a coffee brand", "R0", [], "R0", False, False),
    ("internal summary", "Summarise this internal meeting", "R0", [], "R1", False, False),
    ("production code", "Patch the production authentication code", "R0", [("fake_tests", "run", "read")], "R2", False,
     False),
    ("send marketing email", "Send the marketing email to the list", "R0", [("fake_mail", "send", "external")], "R2",
     True, False),
    ("delete production data", "Delete production data older than a year", "R0",
     [("fake_db", "delete", "irreversible")], "R3", True, True),
]


def _act(objective: str, steps: list, *, gated: bool = True) -> dict:
    act = {"key": "act", "kind": "do", "goal": objective, "inputs": ["approve", "sum"] if gated else ["sum"],
           "capabilities": [[c, o] for c, o, _ in steps],
           "call": {"capability": [steps[0][0], steps[0][1]], "params": {}, "bind": {"total": "sum.total"}}}
    check = {**CHECK, "key": "check-act", "verifies": "act", "goal": "It holds",
             "oracle": {**CHECK["oracle"], "bind": {"total": "act.total"}}}
    return {"units": [SUM, CHECK, *([GATE] if gated else []), act, check], "edges": []}


def _standard(objective: str, declared: str, steps: list) -> wa.AcceptanceContract:
    contract = wa.compile_contract(objective=objective, contract=IntentContract(goal=objective), risk_tier=declared,
                                   budget_cents=0)
    if not steps:
        return contract
    side = {(c, o): e for c, o, e in steps}.get
    drafted = wp.unit_floors(_act(objective, steps), effects=lambda c, o: side((c, o), "read"),
                             inherit=wa.base_tier(contract), floor=wa.code_floor(contract))
    plan = wp.parse_plan(drafted, risk_tier=wp.higher(wa.risk_code(contract), wp.highest_risk(drafted)), budget_cents=0,
                         effects=lambda c, o: side((c, o), "read"))
    return wa.for_plan(contract, plan, lambda c, o: side((c, o), "read"))


@pytest.mark.parametrize(("name", "objective", "declared", "steps", "tier", "approval", "state"), ASSURANCE,
                         ids=[row[0] for row in ASSURANCE])
def test_ts_46s_assurance_regression_suite(name, objective, declared, steps, tier, approval, state):
    found = _standard(objective, declared, steps)
    assert wa.risk_code(found) == tier, name
    acts = any(effect in ("external", "irreversible") for *_, effect in steps)
    assert ("a_person_approves" in found.human_approval_requirements or acts) is approval, name
    assert any(r.predicate == "state_confirmed" for r in found.requirements) is state, name
    if acts:  # the approval is structural: a step that acts outside never runs without a person's gate (TS §46)
        side = {(c, o): e for c, o, e in steps}.get
        with pytest.raises(wp.PlanRejected, match="must run behind a gate a person approves"):
            wp.parse_plan(_act(objective, steps, gated=False), risk_tier="R3", budget_cents=0,
                          effects=lambda c, o: side((c, o), "read"))


def test_control_the_suite_fails_when_a_rule_weakens(monkeypatch):
    """Survival control: with 'send' gone from the words and a send no longer external, the send row derives R0, so
    the suite above would fail (CI detects an unexpected standard change, TS §46)."""
    from g6.core import risk_signals as rs

    monkeypatch.setattr(rs, "TIER_TERMS", tuple((t, tuple(w for w in words if w != "send"), r)
                                                for t, words, r in rs.TIER_TERMS))
    monkeypatch.setitem(wp.EFFECT_TIERS, "external", "R0")
    row = next(r for r in ASSURANCE if r[0] == "send marketing email")
    assert wa.risk_code(_standard(*row[1:4])) == "R0" != row[4]


