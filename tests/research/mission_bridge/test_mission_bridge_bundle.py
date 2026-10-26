# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The claim and evidence bundle (integrated audit ENG-04, 2026-10-11). Research only, default OFF; synthetic evidence.

A mission's answer is a set of claims, each bound to the frozen standard and each derived from recorded evidence by the ECHA
kernel's own claim rule. What must hold, each with a positive control so the guard is shown able to say yes:
- process evidence (a step ran) is not truth evidence, and supports nothing;
- a formal check counts only with a certificate from the formal operation the graph names, never as a self-declared tag;
- correlated model opinions, however many, are one voice and never support a claim;
- a check with no negative control is a structural protection, not an active check;
- a claim with no evidence, a predicate with no claim, a missing standard or clinical review, and a decision nobody made are each
  an explicit unknown, and the mission is then ``qualified-draft``, never ``verified``;
- an independent check that fails refutes its claim and the mission is ``blocked-escalated``;
- every claim traces to its standard hash and its evidence.
"""
from __future__ import annotations

import pytest

from g6.research.echa import FLAG as KERNEL_FLAG
from g6.research.echa import ResearchDisabled as KernelDisabled
from g6.research.echa import StandardRef, trace_claim
from g6.research.mission_bridge import FLAG, MissionIR, ResearchDisabled
from g6.research.mission_bridge import fixtures as fx
from g6.research.mission_bridge.bundle import (
    FORMAL_CHECKERS,
    BundleRefused,
    ClaimSpec,
    Observation,
    build_bundle,
    standard_ref,
)

SEALED = "ef" * 32
CERT = "cd" * 32
CALLER_DECLARED = "evidence_caller_declared"   # bundle.CALLER_DECLARED, the gap every mission names (RESEARCH-01)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(KERNEL_FLAG, "1")


def ir_of(name="medico_legal", *, complete=True) -> MissionIR:
    raw = fx.load(name)
    if name == "engineering" and complete:
        raw["inputs"]["design_load"] = "load"
    return MissionIR.from_dict(raw)


def standard_of(ir: MissionIR) -> StandardRef:
    return standard_ref("medico_legal" if ir.mission_id.startswith("synthetic-medico") else
                        "engineering" if ir.mission_id.startswith("synthetic-eng") else "coding")


def obs(obs_id, node, predicate, kind="executable", result="pass", **over):
    op = next(n.source_operation for n in over.pop("ir", ir_of()).nodes if n.id == node)
    body = {"obs_id": obs_id, "node": node, "predicate": predicate, "kind": kind, "result": result,
            "scope": f"{predicate} on the synthetic case", "source_operation": op, "oracle_version": f"{op}@1",
            "group": f"group-{obs_id}"}
    if kind == "executable" and over.pop("sealed", True):
        body.update(sealed_fixture=SEALED, negative_control=f"ctl-{obs_id}")
    body.update(over)
    return Observation(**body)


def control_for(obs_id, node, predicate, **over):
    return obs(f"ctl-{obs_id}", node, predicate, result="fail", purpose="negative_control", negative_control="", **over)


def claim(claim_id, predicate, observation_ids, **over):
    return ClaimSpec(claim_id=claim_id, predicate=predicate, statement=f"{predicate} holds", scope=f"{predicate}, synthetic case",
                     observation_ids=tuple(observation_ids), **over)


def bundle(claims, observations, *, decisions=(), ir=None):
    ir = ir or ir_of()
    return build_bundle(ir, standard_of(ir), claims, observations, decisions=set(decisions))


def state(b, claim_id):
    return next(o for o in b.outcomes if o.claim_id == claim_id)


P_SOURCES = "material_claims_source_linked"
P_CONTRARY = "contrary_evidence_disclosed"
P_IMAGE = "image_interpretation_independently_reviewed_if_used_for_conclusion"
P_PRIVACY = "privacy_approved"
P_SIGNOFF = "human_expert_signoff_if_required"


def full_scenario():
    """The positive control: every predicate backed by an active, independent check with its negative control, and the people
    who must decide have decided. It exercises the machinery; it is not a domain result."""
    pairs = {P_SOURCES: "context_check", P_CONTRARY: "challenge", P_IMAGE: "image_check", P_PRIVACY: "scope_check",
             P_SIGNOFF: "scope_check"}
    observations, claims = [], []
    for index, (predicate, node) in enumerate(pairs.items()):
        oid = f"o{index}"
        observations += [obs(oid, node, predicate), control_for(oid, node, predicate)]
        claims.append(claim(f"c{index}", predicate, [oid],
                            needs_gate="expert_signoff" if predicate == P_SIGNOFF else
                            "image_permission" if predicate == P_PRIVACY else ""))
    return claims, observations, ("expert_signoff", "image_permission")


# --- the positive control ---------------------------------------------------------------------------------------------


def test_caller_declared_evidence_for_every_predicate_supports_every_claim_but_caps_the_mission_at_a_draft():
    # RESEARCH-01: the claim rule still says yes (the positive control), but every observation, sealed fixture and
    # certificate was handed in by the caller, so the mission is a qualified-draft with that gap named
    # (fails while _bundle returns "verified" when every material claim is supported)
    claims, observations, decisions = full_scenario()
    b = bundle(claims, observations, decisions=decisions)
    assert {o.state for o in b.outcomes} == {"supported"}
    assert b.mission_state == "qualified-draft" and b.unknowns == (("mission", CALLER_DECLARED),)


def test_a_formal_certificate_the_caller_hands_in_is_a_claim_and_never_makes_the_mission_verified():
    # RESEARCH-01: a certificate checked for its shape only, and a vault entry the bundle writes for any 64-hex
    # digest, stand behind nothing G6 observed (fails while _bundle returns "verified")
    ir, f = formal()
    nodes = {"applicable_standard_identified": "standards_check", "design_load_declared": "scope_check",
             "geometry_status_disclosed": "geometry_check", "human_engineer_review_if_required": "scope_check",
             "units_consistent": "scope_check"}
    observations, claims = [f], [claim("c-calc", "calculation_reproduced", ["f1"])]
    for index, (predicate, node) in enumerate(nodes.items()):
        observations += [obs(f"e{index}", node, predicate, ir=ir), control_for(f"e{index}", node, predicate, ir=ir)]
        claims.append(claim(f"c{index}", predicate, [f"e{index}"]))
    b = bundle(claims, observations, ir=ir)
    assert {o.state for o in b.outcomes} == {"supported"}
    assert b.mission_state == "qualified-draft" and b.unknowns == (("mission", CALLER_DECLARED),)


def test_every_claim_traces_to_the_frozen_standard_and_its_evidence():
    claims, observations, decisions = full_scenario()
    b = bundle(claims, observations, decisions=decisions)
    trace = trace_claim(b.ledger, "c0")
    assert trace["ledger_ok"] is True
    assert b.standard.standard_sha256 == ir_of().standard.sha256


# --- the audit's exit checks ------------------------------------------------------------------------------------------


def test_a_missing_human_signoff_leaves_the_mission_a_qualified_draft_with_the_gap_named():
    claims, observations, _ = full_scenario()
    b = bundle(claims, observations, decisions=("image_permission",))  # nobody decided expert_signoff
    assert b.mission_state == "qualified-draft"
    assert state(b, "c4").state == "unknown" and "human_decision_missing" in state(b, "c4").reasons
    assert any("human_decision_missing" in why for _subject, why in b.unknowns)


def test_a_missing_clinical_review_or_observation_is_an_explicit_unknown_not_a_pass():
    claims, observations, decisions = full_scenario()
    claims = [c for c in claims if c.predicate != P_IMAGE] + [claim("c-image", P_IMAGE, [])]
    b = bundle(claims, [o for o in observations if o.predicate != P_IMAGE], decisions=decisions)
    assert b.mission_state == "qualified-draft"
    assert state(b, "c-image").state == "unknown" and state(b, "c-image").reasons == ("no_evidence_recorded",)


def test_a_predicate_nobody_made_a_claim_about_is_an_unknown_and_blocks_verification():
    claims, observations, decisions = full_scenario()
    b = bundle([c for c in claims if c.predicate != P_PRIVACY], observations, decisions=decisions)
    assert b.mission_state == "qualified-draft"
    assert (f"predicate:{P_PRIVACY}", "predicate_has_no_claim") in b.unknowns


def test_a_missing_standard_is_a_refusal_not_a_guess():
    ir = ir_of()
    other = StandardRef.from_text("std-other", "a different standard", "elsewhere", tuple(ir.standard.predicates))
    with pytest.raises(BundleRefused, match="standard"):
        build_bundle(ir, other, [], [], decisions=set())
    narrower = StandardRef.from_text(ir.standard.standard_id, "medico-legal frozen standard v1", "x", (P_SOURCES,))
    with pytest.raises(BundleRefused, match="predicate"):
        build_bundle(ir, narrower, [], [], decisions=set())


def test_correlated_model_opinions_are_one_voice_and_never_support_a_claim_however_many():
    opinions = [obs(f"m{i}", "challenge", P_CONTRARY, kind="model_opinion", group="model-x") for i in range(10)]
    b = bundle([claim("c-opinions", P_CONTRARY, [o.obs_id for o in opinions])], opinions)
    assert state(b, "c-opinions").state == "unknown"
    assert "only_heuristic_evidence" in state(b, "c-opinions").reasons


def test_process_evidence_that_a_step_ran_is_not_truth_evidence():
    ran = [obs("p1", "context", P_SOURCES, kind="process", result="pass")]
    b = bundle([claim("c-ran", P_SOURCES, ["p1"])], ran)
    assert state(b, "c-ran").state == "unknown" and "no_active_evidence" in state(b, "c-ran").reasons
    assert [r["oracle_category"] for r in b.evidence_categories()] == ["NO_DISCRIMINATING_EVIDENCE"]


def test_a_check_with_no_negative_control_is_a_structural_protection_and_supports_nothing():
    lone = [obs("o1", "context_check", P_SOURCES, sealed=True, negative_control="")]
    b = bundle([claim("c-lone", P_SOURCES, ["o1"])], lone)
    assert state(b, "c-lone").state == "unknown" and "not_an_active_check" in state(b, "c-lone").reasons


@pytest.mark.parametrize("breadth", ["covered_cases", "general"])
def test_an_author_controlled_check_is_partial_evidence_and_never_supports_a_claim(breadth):
    partial = [obs("o1", "context_check", P_SOURCES, sealed=False, negative_control="ctl-o1"),
               control_for("o1", "context_check", P_SOURCES, sealed=False)]
    b = bundle([claim("c-partial", P_SOURCES, ["o1"], breadth=breadth)], partial)
    assert state(b, "c-partial").state == "unknown"
    assert "independence_not_met" in state(b, "c-partial").reasons
    assert ("partial_evidence_cannot_support_a_general_claim" in state(b, "c-partial").reasons) == (breadth == "general")


def test_an_independent_check_that_fails_refutes_the_claim_and_blocks_the_mission():
    claims, observations, decisions = full_scenario()
    observations = [obs("o0", "context_check", P_SOURCES, result="fail") if o.obs_id == "o0" else o for o in observations]
    b = bundle(claims, observations, decisions=decisions)
    assert state(b, "c0").state == "unsupported" and b.mission_state == "blocked-escalated"


# --- a formal check is a certificate, not a tag -----------------------------------------------------------------------


def formal(**over):
    ir = ir_of("engineering")
    body = {"obs_id": "f1", "node": "calculation_check", "predicate": "calculation_reproduced", "kind": "formal",
            "result": "pass", "scope": "the stated constraints are satisfiable", "source_operation": "formal_methods.solve",
            "oracle_version": "formal_methods.solve@1", "group": "z3",
            "certificate": {"checker": "formal_methods.solve", "version": "z3-4.12", "digest": CERT}}
    body.update(over)
    return ir, Observation(**body)


def test_a_formal_check_with_a_certificate_from_the_named_formal_operation_is_accepted():
    ir, f = formal()
    b = bundle([claim("c-calc", "calculation_reproduced", ["f1"])], [f], ir=ir)
    assert state(b, "c-calc").state == "supported" and b.rejected == ()
    assert "formal_methods.solve" in FORMAL_CHECKERS


@pytest.mark.parametrize(("label", "change"), [
    ("no certificate", {"certificate": None}),
    ("checker is not a formal operation", {"certificate": {"checker": "debate.debate", "version": "1", "digest": CERT}}),
    ("no checker version", {"certificate": {"checker": "formal_methods.solve", "version": "", "digest": CERT}}),
    ("digest is not a digest", {"certificate": {"checker": "formal_methods.solve", "version": "1", "digest": "trust me"}}),
    ("certificate from another operation than the node's", {"source_operation": "align_evals.evaluate"}),
])
def test_a_formal_claim_without_a_valid_certificate_is_never_recorded_as_a_formal_check(label, change):
    ir, f = formal(**change)
    if "source_operation" in change:
        with pytest.raises(BundleRefused, match="operation"):
            bundle([claim("c-calc", "calculation_reproduced", ["f1"])], [f], ir=ir)
        return
    b = bundle([claim("c-calc", "calculation_reproduced", ["f1"])], [f], ir=ir)
    assert state(b, "c-calc").state == "unknown", label
    assert [why for _oid, why in b.rejected] == ["formal_check_without_a_valid_certificate"], label
    assert all(rec["oracle_category"] != "FORMAL_CHECK" for rec in b.evidence_categories()), label


# --- where an observation may come from -------------------------------------------------------------------------------


def test_a_certificate_from_the_formal_operation_does_not_vouch_for_a_node_that_is_another_operation():
    ir = ir_of("engineering")
    borrowed = Observation(obs_id="f1", node="scope_check", predicate="calculation_reproduced", kind="formal", result="pass",
                           scope="s", source_operation="align_evals.evaluate", group="x",
                           certificate={"checker": "formal_methods.solve", "version": "1", "digest": CERT})
    b = bundle([claim("c", "calculation_reproduced", ["f1"])], [borrowed], ir=ir)
    assert state(b, "c").state == "unknown" and [w for _o, w in b.rejected] == ["formal_check_without_a_valid_certificate"]


def test_an_observation_must_come_from_the_operation_its_node_names():
    bad = obs("o1", "context_check", P_SOURCES, source_operation="debate.debate")
    with pytest.raises(BundleRefused, match="operation"):
        bundle([claim("c1", P_SOURCES, ["o1"])], [bad])


def test_an_observation_from_a_node_the_graph_does_not_have_is_refused():
    ir = ir_of()
    ghost = Observation(obs_id="g1", node="ghost", predicate=P_SOURCES, kind="process", result="pass", scope="s",
                        source_operation="ctx_rag.rag_evaluate_retrieval")
    with pytest.raises(BundleRefused, match="node"):
        bundle([claim("c1", P_SOURCES, ["g1"])], [ghost], ir=ir)


def test_a_claim_that_cites_an_observation_nobody_recorded_is_refused():
    with pytest.raises(BundleRefused, match="observation"):
        bundle([claim("c1", P_SOURCES, ["nowhere"])], [])


def test_an_observation_from_a_gate_is_refused_because_a_person_s_decision_is_not_an_operation_s_output():
    ghost = Observation(obs_id="g1", node="search_approval", predicate=P_SOURCES, kind="process", result="pass", scope="s",
                        source_operation="ctx_rag.rag_evaluate_retrieval")
    with pytest.raises(BundleRefused, match="does not have as an operation"):
        bundle([], [ghost])


def test_an_observation_about_a_predicate_outside_the_standard_is_refused():
    stray = obs("o1", "context_check", "a_predicate_the_standard_lacks")
    with pytest.raises(BundleRefused, match="predicate"):
        bundle([], [stray, control_for("o1", "context_check", "a_predicate_the_standard_lacks")])


def test_a_negative_control_must_be_recorded_as_one():
    check = obs("o1", "context_check", P_SOURCES, negative_control="o2")
    not_a_control = obs("o2", "context_check", P_SOURCES, negative_control="")
    with pytest.raises(BundleRefused, match="negative control"):
        bundle([], [check, not_a_control])


def test_independent_evidence_must_name_who_is_independent_of_whom():
    nameless = obs("o1", "context_check", P_SOURCES, group="")
    with pytest.raises(BundleRefused, match="independence_group"):
        bundle([], [nameless, control_for("o1", "context_check", P_SOURCES, group="g")])


def test_a_claim_that_needs_a_gate_the_graph_does_not_have_is_refused():
    with pytest.raises(BundleRefused, match="gate"):
        bundle([claim("c1", P_SOURCES, [], needs_gate="ghost")], [])


def test_a_claim_about_a_predicate_outside_the_standard_is_refused():
    with pytest.raises(BundleRefused, match="predicate"):
        bundle([claim("c1", "a_predicate_the_standard_lacks", [])], [])


def test_duplicate_ids_are_refused():
    one = obs("o1", "context_check", P_SOURCES)
    with pytest.raises(BundleRefused, match="duplicate"):
        bundle([claim("c1", P_SOURCES, ["o1"])], [one, one])
    with pytest.raises(BundleRefused, match="duplicate"):
        bundle([claim("c1", P_SOURCES, ["o1"]), claim("c1", P_PRIVACY, [])], [one])


def test_a_decision_for_a_gate_the_graph_does_not_have_is_refused():
    with pytest.raises(BundleRefused, match="gate"):
        bundle([], [], decisions=("context",))


# --- what the audit expects of the realistic medico-legal case --------------------------------------------------------


def test_the_realistic_medico_legal_evidence_ends_as_a_qualified_draft_with_explicit_unknowns_not_a_certification():
    sources = [obs("o1", "context_check", P_SOURCES), control_for("o1", "context_check", P_SOURCES)]
    opinions = [obs("m1", "challenge", P_CONTRARY, kind="model_opinion", group="model-x"),
                obs("m2", "image_check", P_IMAGE, kind="model_opinion", group="model-x")]
    claims = [claim("c-sources", P_SOURCES, ["o1"]), claim("c-contrary", P_CONTRARY, ["m1"]),
              claim("c-image", P_IMAGE, ["m2"]), claim("c-privacy", P_PRIVACY, [], needs_gate="image_permission")]
    b = bundle(claims, [*sources, *opinions], decisions=("image_permission",))
    assert b.mission_state == "qualified-draft"
    assert state(b, "c-sources").state == "supported"
    assert {o.claim_id for o in b.outcomes if o.state == "unknown"} == {"c-contrary", "c-image", "c-privacy"}


# --- flags ------------------------------------------------------------------------------------------------------------


def test_the_bundle_needs_both_research_flags(monkeypatch):
    claims, observations, decisions = full_scenario()
    ir = ir_of()  # built while the flag is on, so only the bundle itself can refuse below
    monkeypatch.delenv(FLAG)
    with pytest.raises(ResearchDisabled):
        build_bundle(ir, standard_of(ir), claims, observations, decisions=set(decisions))
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.delenv(KERNEL_FLAG)
    with pytest.raises(KernelDisabled):
        build_bundle(ir, standard_of(ir), claims, observations, decisions=set(decisions))
