# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The CSIR kernel (Runtime Validator Constructor R1, 2026-10-14): the typing judgement, standard refinement and solvability.

CSIR (``docs/theory/G6_Computational_Structural_Intermediate_Representation.md``) types a mission program with the
judgement
``Gamma; S; Pi; B |- p : A ~> B ! eps |> C_p`` and states serial soundness (C1) only under six obligations A1-A6. The kernel
enforces them, so every validator finding is filed under the obligation it breaks and a judged mission is ``sound`` only when
none is broken. What must hold, each with a control that fails for the stated reason:
- every validator code belongs to exactly one obligation, so a new code cannot escape the judgement;
- a sound mission yields its typed interface (what it consumes, what it yields), its effect bound and its resource bound;
- an unknown cost is never coerced to zero (the resource bound is unknown if any node's cost is);
- two writers that no edge orders are hidden shared state (A6);
- a standard is only ever refined: a re-freeze adds predicates, changes the hash and keeps a verifiable chain; a dropped
  predicate is refused;
- a solvability belief is advisory on three separate axes: it never forbids an attempt, never implies findability, never
  implies optimality.
"""
from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from g6.research.mission_bridge import (
    FLAG,
    MissionIR,
    OpFact,
    ResearchDisabled,
    checks,
)
from g6.research.mission_bridge.csir import judgement as J
from g6.research.mission_bridge.csir import solvability as SV
from g6.research.mission_bridge.csir import standard as SD

SHA = "ab" * 32

CATALOG = {
    "alpha.ask": OpFact(effect="read", authority="A1"),
    "beta.fetch": OpFact(effect="read", authority="A1"),
    "gamma.write": OpFact(effect="local_write", authority="A1"),
    "theta.write": OpFact(effect="local_write", authority="A1"),
    "delta.check": OpFact(effect="read", authority="A4"),
    "eps.send": OpFact(effect="external", authority="A2"),
}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


def node(node_id, op, requires=None, provides=None, **over):
    body = {"id": node_id, "role": over.pop("role", node_id), "source_operation": op,
            "declared_effect": CATALOG[op].effect, "requires": requires or {}, "provides": provides or {},
            "data_class": "public", "egress": "none", "budget_cents": 10}
    body.update(over)
    return body


def mission(**over):
    body = {
        "mission_id": "m-1", "domain": "synthetic", "high_stakes": False, "inputs": {"question": "text"},
        "standard": {"standard_id": "std-1", "standard_sha256": SHA, "predicates": ["p-a", "p-b"]},
        "nodes": [
            node("scope", "alpha.ask", {"question": "text"}, {"scope": "text"}),
            node("draft", "gamma.write", {"scope": "text"}, {"draft": "file"}),
            node("check", "delta.check", {"draft": "file"}, {"verdict": "check_result"}, verifies="draft"),
        ],
        "budget_cents": 100, "charged": False, "approvals": [], "required_roles": ["scope", "check"],
    }
    body.update(over)
    return body


def judged(raw):
    return J.judge(MissionIR.from_dict(raw), CATALOG)


def broken(raw):
    return {o for o, found in judged(raw).violations.items() if found}


# --- the judgement ----------------------------------------------------------------------------------------------------


def test_every_validator_code_belongs_to_exactly_one_obligation():
    codes = {value for name, value in vars(checks).items() if name.isupper() and not name.startswith("_")
             and isinstance(value, str) and value.replace("_", "").isalpha() and value.islower()}
    assert "cycle" in codes and "self_verification" in codes
    assert codes, "found no validator codes to file"
    assert codes <= set(J.OBLIGATION_OF), sorted(codes - set(J.OBLIGATION_OF))
    assert set(J.OBLIGATION_OF.values()) <= set(J.OBLIGATIONS)
    assert J.OBLIGATIONS == ("A1", "A2", "A3", "A4", "A5", "A6")


def test_a_sound_mission_yields_its_typed_interface_effect_and_resources():
    j = judged(mission())
    assert j.sound and all(not found for found in j.violations.values())
    assert j.consumes == (("question", "text"),)
    assert j.yields == (("verdict", "check_result"),)  # every other output is consumed inside the program
    assert j.effect == "local_write"
    assert j.resources.cents == 30


@pytest.mark.parametrize(("change", "obligation"), [
    (lambda m: m["nodes"][1].update(declared_effect="read"), "A1"),                     # declares less than the op does
    (lambda m: m["nodes"][1].update(source_operation="nobody.knows"), "A1"),           # no source authority
    (lambda m: m["nodes"][2].update(requires={"draft": "text"}), "A2"),                 # edge types differ
    (lambda m: m["nodes"][2].update(requires={"missing": "file"}), "A2"),               # nothing provides it
    (lambda m: m["nodes"][0].update(standard={"standard_sha256": SHA, "predicates": ["p-a"]}), "A3"),  # standard lowered
    (lambda m: m.update(budget_cents=5), "A4"),                                          # over budget
    (lambda m: m["nodes"][2].update(source_operation="gamma.write", declared_effect="local_write"), "A5"),  # self-check
    (lambda m: m["nodes"].append(node("again", "alpha.ask", {"question": "text"}, {"scope": "text"})), "A6"),
])
def test_each_broken_obligation_is_filed_under_its_name_and_the_mission_is_not_sound(change, obligation):
    raw = copy.deepcopy(mission())
    change(raw)
    j = judged(raw)
    assert obligation in broken(raw)
    assert not j.sound


def test_two_writers_no_edge_orders_are_hidden_shared_state():
    raw = mission()
    raw["nodes"].append(node("other", "theta.write", {"scope": "text"}, {"other": "file"}))
    j = judged(raw)
    assert [f.code for f in j.violations["A6"]] == [J.PARALLEL_WRITERS]
    ordered = mission()
    ordered["nodes"].append(node("other", "theta.write", {"draft": "file"}, {"other": "file"}))
    assert judged(ordered).sound  # the edge from draft orders the two writes


def test_a_person_s_decision_has_no_cost_so_a_charged_mission_with_a_gate_is_not_over_its_budget_rule():
    raw = mission(charged=True)
    raw["nodes"].append({"id": "approve", "role": "approve", "source_operation": "", "declared_effect": "read",
                         "requires": {"verdict": "check_result"}, "provides": {"approved": "decision"}, "gate": True})
    assert "A4" not in broken(raw)
    raw["nodes"][0]["budget_cents"] = None  # an operation with an unknown cost still breaks A4 on a charged mission
    assert "A4" in broken(raw)


def test_an_unknown_cost_makes_the_resource_bound_unknown_never_zero():
    raw = mission()
    raw["nodes"][1]["budget_cents"] = None
    assert judged(raw).resources.cents is None


@given(st.lists(st.one_of(st.none(), st.integers(min_value=0, max_value=10_000)), max_size=8))
def test_resource_composition_is_a_conservative_sum(costs):
    total = J.Resources.compose(J.Resources(c) for c in costs)
    assert total.cents == (None if any(c is None for c in costs) else sum(costs))


def test_the_effect_bound_is_the_worst_effect_in_the_program():
    raw = mission(approvals=[{"node": "send", "scope": "egress"}])
    raw["nodes"].append(node("send", "eps.send", {"verdict": "check_result"}, {"sent": "receipt"}))
    assert judged(raw).effect == "external"


def test_the_effect_bound_is_the_operation_s_real_effect_when_a_node_declares_less():
    raw = mission(approvals=[{"node": "send", "scope": "egress"}])
    raw["nodes"].append(node("send", "eps.send", {"verdict": "check_result"}, {"sent": "receipt"}, declared_effect="read"))
    j = judged(raw)
    assert j.effect == "external" and "A1" in broken(raw)


def test_the_judgement_needs_the_flag(monkeypatch):
    ir = MissionIR.from_dict(mission())
    monkeypatch.delenv(FLAG)
    with pytest.raises(ResearchDisabled):
        J.judge(ir, CATALOG)


# --- standard refinement (S3) -----------------------------------------------------------------------------------------


def base_standard():
    return SD.FrozenStandard.first("std-1", ("p-a", "p-b"))


def test_a_refreeze_adds_predicates_changes_the_hash_and_chains():
    first = base_standard()
    second = SD.refreeze(first, add=("p-c",), reason="owner answered Q1: retries must be idempotent")
    assert second.version == first.version + 1
    assert set(second.predicates) == {"p-a", "p-b", "p-c"}
    assert second.sha256 != first.sha256 and second.parent == first.sha256
    assert SD.refines(second, first) and not SD.refines(first, second)
    assert SD.chain_problems([first, second]) == []


def test_a_refreeze_without_a_reason_or_without_anything_new_is_refused():
    with pytest.raises(SD.NotARefinement):
        SD.refreeze(base_standard(), add=("p-c",), reason=" ")
    with pytest.raises(SD.NotARefinement):
        SD.refreeze(base_standard(), add=("p-a",), reason="nothing new")


def test_a_hand_made_revision_that_drops_a_predicate_breaks_the_chain():
    first = base_standard()
    forged = SD.FrozenStandard(standard_id="std-1", version=2, predicates=("p-a",), parent=first.sha256,
                               reason="quietly dropped p-b")
    assert not SD.refines(forged, first)
    assert any("drops" in p for p in SD.chain_problems([first, forged]))


def test_a_chain_must_start_at_the_first_revision():
    first = base_standard()
    second = SD.refreeze(first, add=("p-c",), reason="owner answered")
    assert any("first revision" in p for p in SD.chain_problems([second]))
    assert SD.chain_problems([]) != []


def test_a_blank_predicate_name_is_not_a_refinement():
    with pytest.raises(SD.NotARefinement):
        SD.refreeze(base_standard(), add=(" ",), reason="looks like an addition")
    with pytest.raises(SD.NotARefinement):
        SD.FrozenStandard.first("std-1", ("p-a", ""))


def test_a_revision_whose_parent_is_not_the_previous_hash_breaks_the_chain():
    first = base_standard()
    stray = SD.FrozenStandard(standard_id="std-1", version=2, predicates=("p-a", "p-b", "p-c"), parent="00" * 32,
                              reason="from elsewhere")
    assert any("parent" in p for p in SD.chain_problems([first, stray]))


# --- solvability: advisory, three separate axes -----------------------------------------------------------------------


def test_an_open_problem_may_always_be_attempted():
    belief = SV.Solvability(existence="open", basis="no known proof either way")
    assert belief.permits_attempt() is True
    assert SV.Solvability(existence="believed_unsolvable", basis="a reduction from halting").permits_attempt() is True


def test_believing_a_solution_exists_claims_neither_findability_nor_optimality():
    belief = SV.Solvability(existence="believed_solvable", basis="a known algorithm exists")
    assert belief.findability == "unknown" and belief.optimality == "not_claimed"
    assert "does not mean" in belief.describe()


def test_a_belief_states_its_basis_and_optimality_needs_a_proof():
    with pytest.raises(ValueError):
        SV.Solvability(existence="believed_solvable", basis="")
    with pytest.raises(ValueError):
        SV.Solvability(existence="believed_solvable", basis="known", optimality="proved_optimal", optimality_proof="")
    with pytest.raises(ValueError):
        SV.Solvability(existence="maybe", basis="x")


def test_g6_finding_a_solution_contradicts_believing_none_exists():
    with pytest.raises(ValueError):
        SV.Solvability(existence="believed_unsolvable", basis="x", findability="yes", findability_basis="catalogue op")
