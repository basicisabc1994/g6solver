# SPDX-License-Identifier: Apache-2.0
"""Review B2-I1: approving a decision another process recorded.

(a) the verdict is decided again from the recorded report and proposal, so a ``recommend`` row over a report that shows
a loss is refused; (b) a proposal rebuilt from the run ledger is validated as a new one is, so an invalid profile is
never evaluated; (c) approving by digest from Python shows the person the decision's card first and needs the
candidate digest it shows, so with several recommendations the agent cannot pick one for them. Offline; the rig's stub.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from g6.invariants import check_envelope
from g6.metaharness import adapt
from g6.metaharness import schema as S
from g6.metaharness.api import _proposal_body
from g6.metaharness.gate import GateDecision
from g6.metaharness.schema import Profile, Proposal
from g6.metaharness.surface import Surface

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

CAREFUL = {"routing": {"answer": "local"}, "prompt": "CAREFUL: add the two numbers digit by digit."}
TWICE = {"routing": {"answer": "local"}, "prompt": "CAREFUL: check each digit twice."}


def _proposer(tmp_path, *, careful_acc: float = 0.85):
    wf, _ = rig.build(7, careful_acc=careful_acc)
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    assert mh.confirm("Ada")["code"] == "G6_E_META_CONFIRMED"
    return mh, wf


def _decided(mh, profile: dict):
    proposal = mh.candidate(profile, proposer="bo")["proposal"]
    report = mh.evaluate(proposal)["report"]
    return mh.decide(report, proposal)["decision"]


def _elsewhere(wf, tmp_path):
    """A fresh process: nothing in memory, the same workspace."""
    return adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())


def _second_surface(wf, tmp_path) -> Surface:
    return Surface({"arith": {"workflow": wf, "workspace": tmp_path / "ws", "clock": rig.Clock()}})


# --- (a) the verdict is decided again ------------------------------------------------------------------------------

def _forged_recommendation(mh) -> str:
    """A ``recommend`` row written into the ledger over a real report that shows a loss."""
    refused = _decided(mh, CAREFUL)   # careful_acc 0.1: the candidate loses
    assert refused.verdict == "refuse", refused.reasons
    body = {k: v for k, v in refused.to_dict().items() if k != "completion_state"}
    forged = GateDecision(**{**body, "verdict": "recommend", "code": S.RECOMMENDED,
                             "reasons": ("the candidate beat the base on the held-out split under every rule",),
                             "checks": tuple(body["checks"])})
    mh.ledger.append("decision", forged.digest(), forged.to_dict())
    return forged.digest()


def test_a_recommend_row_over_a_losing_report_is_refused_from_another_process(tmp_path) -> None:
    mh, wf = _proposer(tmp_path, careful_acc=0.1)
    forged = _forged_recommendation(mh)
    found, why = _elsewhere(wf, tmp_path)._recorded("decision", forged)
    assert found is None and "does not follow" in why, why
    approved = _second_surface(wf, tmp_path).approve("arith", forged, session="reviewer", answer="Ada")
    assert approved["completion_state"] == "blocked-escalated", approved
    assert approved["warning_card"]["code"] != "G6_E_META_ACTIVATED" and check_envelope(approved) == []
    assert len(_elsewhere(wf, tmp_path).store.versions()) == 1   # nothing was activated


def test_survival_a_real_recommendation_is_decided_again_and_found(tmp_path) -> None:
    mh, wf = _proposer(tmp_path)
    decision = _decided(mh, CAREFUL)
    assert decision.verdict == "recommend", decision.reasons
    found, why = _elsewhere(wf, tmp_path)._recorded("decision", decision.digest())
    assert found is not None and found.digest() == decision.digest(), why


# --- (b) a rebuilt proposal is validated -----------------------------------------------------------------------------

def test_an_invalid_proposal_written_into_the_ledger_is_never_evaluated(tmp_path) -> None:
    mh, wf = _proposer(tmp_path)
    _, base = mh._head()
    bad = Profile.model_validate({"routing": {"answer": "local"}, "extra_checks": ["no-such-check"],
                                  "distillates": {"answer": "0" * 64}})
    proposal = Proposal(base.digest(), bad, "bo", "hand-written", {}, (), {})
    mh.ledger.append("candidate", proposal.digest(), _proposal_body(proposal))
    found, why = _elsewhere(wf, tmp_path)._recorded("proposal", proposal.digest())
    assert found is None and "no-such-check" in why and "distillate" in why, why
    evaluations = len(mh.ledger.rows("evaluation"))
    reply = _second_surface(wf, tmp_path).evaluate("arith", proposal.digest())
    assert reply["warning_card"]["code"] == S.SCHEMA and reply["completion_state"] == "blocked-escalated", reply
    assert len(_elsewhere(wf, tmp_path).ledger.rows("evaluation")) == evaluations   # nothing ran


# --- (c) approving by digest shows the card and needs the candidate it shows ----------------------------------------

def test_approving_by_digest_shows_the_card_first_and_needs_its_candidate(tmp_path) -> None:
    mh, wf = _proposer(tmp_path)
    careful, twice = _decided(mh, CAREFUL), _decided(mh, TWICE)
    assert careful.verdict == twice.verdict == "recommend", (careful.reasons, twice.reasons)
    elsewhere = _elsewhere(wf, tmp_path)

    shown = elsewhere.activate(careful.digest(), accepted_by="Ada")
    assert shown["warning_card"]["code"] == S.APPROVAL_REQUIRED and check_envelope(shown) == []
    card = shown["card"]
    assert card["candidate_digest"] == careful.candidate_digest and card["held_out_cases"] == 40
    assert "CAREFUL: add the two numbers digit by digit." in str(card["diff"]) and "success" in card
    assert "candidate_digest=" in shown["next_action"]
    # the agent hands over one decision's digest but the person read the other's card: refused
    mixed = elsewhere.activate(careful.digest(), accepted_by="Ada", candidate_digest=twice.candidate_digest)
    assert mixed["warning_card"]["code"] == S.APPROVAL_REQUIRED and mixed["card"]["candidate_digest"] != \
        twice.candidate_digest
    assert len(elsewhere.store.versions()) == 1   # nothing was activated yet

    done = elsewhere.activate(careful.digest(), accepted_by="Ada", candidate_digest=card["candidate_digest"])
    assert (done["warning_card"]["code"], done["head_version"]) == ("G6_E_META_ACTIVATED", 1), done["message"]
    assert elsewhere.store.versions()[-1]["decision_digest"] == careful.digest()


def test_the_owner_rules_still_hold_by_digest(tmp_path) -> None:
    mh, wf = _proposer(tmp_path)
    decision = _decided(mh, CAREFUL)
    elsewhere = _elsewhere(wf, tmp_path)
    for who, code in (("bo", S.APPROVER_IS_PROPOSER), ("auto:ci", S.APPROVAL_REQUIRED), ("", S.APPROVAL_REQUIRED)):
        got = elsewhere.activate(decision.digest(), accepted_by=who, candidate_digest=decision.candidate_digest)
        assert got["warning_card"]["code"] == code, who
    assert len(elsewhere.store.versions()) == 1


def test_the_question_put_to_the_person_over_mcp_shows_the_change(tmp_path) -> None:
    mh, wf = _proposer(tmp_path)
    decision = _decided(mh, CAREFUL)
    refused, question = _second_surface(wf, tmp_path).approval_check("arith", decision.digest(), session="reviewer")
    assert refused is None
    assert decision.candidate_digest in question and "CAREFUL: add the two numbers digit by digit." in question
    assert "held-out" in question


@pytest.mark.parametrize("careful_acc", [0.1, 0.85])
@pytest.mark.parametrize("cached", [False, True])
def test_upgrade_refuses_approval_before_consent_with_judging_hashes(tmp_path, monkeypatch, careful_acc, cached):
    from g6.metaharness import api

    mh, wf = _proposer(tmp_path, careful_acc=careful_acc)
    decision = _decided(mh, CAREFUL)
    surface = _second_surface(wf, tmp_path)
    if cached:
        surface.harness("arith")._recorded("decision", decision.digest())
    confirmed_hash = mh.store.confirmed()["judging_sha256"]
    before = {name: (tmp_path / "ws" / name).read_bytes() for name in ("store.jsonl", "ledger.jsonl")}
    monkeypatch.setattr(api, "judging_sha256", lambda: "f" * 64)

    refused, question = surface.approval_check("arith", decision.digest(), session="reviewer")
    assert question == ""
    assert refused["code"] == S.PROTECTED
    assert refused["confirmed_judging_sha256"] == confirmed_hash
    assert refused["current_judging_sha256"] == "f" * 64
    assert "judging code changed" in refused["message"]
    assert "reconfirm" in refused["next_action"].lower()
    assert check_envelope(refused) == []
    # A caller supplying an answer directly cannot bypass the same preflight.
    reply = surface.approve("arith", decision.digest(), session="reviewer", answer="Ada")
    assert reply["code"] == S.PROTECTED
    assert before == {name: (tmp_path / "ws" / name).read_bytes() for name in before}
