# SPDX-License-Identifier: Apache-2.0
"""Adoption with no person, in shadow only: the loop's own head moves and a person's record never does."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from g6.core import stop
from g6.core.ledger import LedgerError
from g6.metaharness import Profile, adapt
from g6.metaharness import monitor as drift
from g6.metaharness.shadow import ADOPTER, SHADOW_ONLY, adapt_shadow

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

CAREFUL = Profile(routing={"answer": "local"}, prompt="CAREFUL: add the two numbers digit by digit.")
AGAIN = Profile(routing={"answer": "local"}, prompt="CAREFUL: add the two numbers digit by digit. Twice.")


@dataclass
class Collapsing(rig.Stub):
    collapsed: bool = False
    marker: str = "CAREFUL"

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        if self.collapsed and self.marker in system:
            self.calls += 1
            return "drifted"
        return super().complete(system, user, max_tokens=max_tokens)


def _shadow(tmp_path, *, seed=7, stub=None):
    wf, stub = rig.build(seed, stub=stub or Collapsing(seed, 0.5, 0.85))
    mh = adapt_shadow(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft"
    return mh, wf, stub


def _recommended(mh, profile=CAREFUL):
    proposal = mh.candidate(profile, proposer="auto:test")["proposal"]
    decision = mh.decide(mh.evaluate(proposal)["report"], proposal)["decision"]
    assert decision.verdict == "recommend", decision.reasons
    return decision


def _adopted(tmp_path, **kw):
    mh, wf, stub = _shadow(tmp_path, **kw)
    assert mh.adopt(_recommended(mh))["head_version"] == 1
    return mh, wf, stub


def test_an_adopted_candidate_is_the_head_every_later_step_runs_on(tmp_path):
    mh, _, _ = _adopted(tmp_path)
    assert mh._head() == (1, CAREFUL)
    later = mh.candidate(AGAIN, proposer="auto:test")["proposal"]
    assert later.base_digest == CAREFUL.digest()
    assert mh.evaluate(later)["report"].base_digest == CAREFUL.digest()


def test_a_persons_record_never_moves(tmp_path):
    mh, wf, _ = _adopted(tmp_path)
    assert [v["kind"] for v in mh.store.versions()] == ["initial"]
    assert mh.store.versions()[0]["accepted_by"] == "Ada"
    person = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())   # the ordinary handle on the same workspace
    assert person.store.head()[0] == 0
    assert person.store.head()[1].digest() != CAREFUL.digest()


def test_the_adoption_says_whose_it_was_and_where_it_holds(tmp_path):
    mh, _, _ = _adopted(tmp_path)
    row = mh.adoption_rows()[-1]
    assert row["by"] == ADOPTER and ADOPTER.startswith("auto:")
    assert row["scope"] == "shadow" and row["decision_digest"] and row["kind"] == "adopt"


@pytest.mark.parametrize("name", ["Ada", "", "the owner"])
def test_no_adoption_row_can_carry_a_persons_name(tmp_path, name):
    mh, _, _ = _adopted(tmp_path)
    row = dict(mh.adoption_rows()[-1], version=2, by=name)
    with pytest.raises(LedgerError, match="the loop's own"):
        mh.adoptions.append("adopt", "2", row)


def test_a_new_process_finds_the_adoption(tmp_path):
    mh, wf, _ = _adopted(tmp_path)
    again = adapt_shadow(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    assert again._head() == (1, CAREFUL)


def test_only_a_recommendation_of_this_harness_can_be_adopted(tmp_path):
    mh, _, _ = _shadow(tmp_path)
    proposal = mh.candidate(Profile(routing={"answer": "local"}, prompt="Be brief."), proposer="auto:test")["proposal"]
    refused = mh.decide(mh.evaluate(proposal)["report"], proposal)["decision"]
    assert refused.verdict != "recommend"
    assert mh.adopt(refused)["completion_state"] == "blocked-escalated"
    other, _, _ = _shadow(tmp_path / "other")
    assert mh.adopt(_recommended(other))["completion_state"] == "blocked-escalated"
    assert mh._head()[0] == 0


def test_a_decision_on_an_old_head_cannot_be_adopted(tmp_path):
    mh, _, _ = _shadow(tmp_path)
    first, second = _recommended(mh), _recommended(mh, AGAIN)
    assert mh.adopt(first)["completion_state"] == "qualified-draft"
    assert mh.adopt(second)["completion_state"] == "blocked-escalated"
    assert mh._head() == (1, CAREFUL)


def test_the_stop_refuses_an_adoption(tmp_path):
    mh, _, _ = _shadow(tmp_path)
    decision = _recommended(mh)
    stop.engage("test")
    try:
        assert mh.adopt(decision)["warning_card"]["code"] == stop.STOPPED
    finally:
        stop.release()
    assert mh._head()[0] == 0


@pytest.mark.parametrize("call", [lambda mh, d: mh.activate(d, accepted_by="Ada"),
                                  lambda mh, d: mh.rollback(0, accepted_by="Ada"),
                                  lambda mh, d: mh.run("What is 2+2?")])
def test_a_shadow_harness_activates_rolls_back_and_runs_nothing_for_real(tmp_path, call):
    mh, _, _ = _shadow(tmp_path)
    got = call(mh, _recommended(mh))
    assert got["warning_card"]["code"] == SHADOW_ONLY and got["completion_state"] == "blocked-escalated"
    assert len(mh.store.versions()) == 1


def test_held_out_uses_carry_across_adoptions(tmp_path):
    mh, wf, _ = _shadow(tmp_path)
    assert mh.held_out_left() == wf.policy.max_evaluations
    mh.adopt(_recommended(mh))
    assert mh.held_out_left() == wf.policy.max_evaluations - 1
    again = adapt_shadow(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    assert again.held_out_left() == wf.policy.max_evaluations - 1


# The monitor --------------------------------------------------------------------------------------------------------

def test_with_nothing_adopted_there_is_nothing_to_audit(tmp_path):
    mh, wf, stub = _shadow(tmp_path)
    before = stub.calls
    got = mh.audit(rig.cases("m", 10, 900), count=10)
    assert got["audited"] == 0 and not got["rolled_back"] and stub.calls == before


def test_an_adoption_that_got_worse_is_rolled_back_with_no_one_asked(tmp_path):
    mh, _, stub = _adopted(tmp_path)
    stub.collapsed = True
    got = mh.audit(rig.cases("m", 60, 900), count=60)
    assert got["rolled_back"], got
    assert mh._head() == (2, mh.store.head()[1])                # back on the version before it
    assert mh.adoption_rows()[-1]["kind"] == "rollback"
    assert mh.adoption_rows()[-1]["by"] == ADOPTER
    assert mh.ledger.rows("ticket")[-1]["kind"] == "re-evaluate"


def test_a_healthy_adoption_is_kept(tmp_path):
    mh, _, _ = _adopted(tmp_path)
    got = mh.audit(rig.cases("m", 60, 900), count=60)
    assert got["audited"] == 60 and not got["rolled_back"] and mh._head() == (1, CAREFUL)


def test_each_monitor_case_is_used_once_per_adoption(tmp_path):
    mh, _, stub = _adopted(tmp_path)
    cases = rig.cases("m", 10, 900)
    assert mh.audit(cases, count=6)["audited"] == 6
    assert mh.audit(cases, count=6)["audited"] == 4
    before = stub.calls
    assert mh.audit(cases, count=6)["audited"] == 0 and stub.calls == before


def test_rolling_back_stops_at_the_version_before_never_further(tmp_path):
    mh, _, stub = _adopted(tmp_path)
    first = mh.adoption_rows()[-1]
    mh.adoptions.append("adopt", "2", {**first, "version": 2, "base_version": 1,
                                       "profile": AGAIN.model_dump(mode="json")})
    stub.collapsed, stub.marker = True, "Twice"
    assert mh.audit(rig.cases("m", 60, 900), count=60)["rolled_back"]
    assert mh._head() == (3, CAREFUL)                           # one step back, not to the initial version


def test_no_rollback_row_can_skip_a_version(tmp_path):
    mh, _, _ = _adopted(tmp_path)
    with pytest.raises(LedgerError, match="the version before"):
        mh.adoptions.append("rollback", "2", {"kind": "rollback", "version": 2, "target_version": 5, "by": ADOPTER,
                                              "scope": "shadow", "profile": CAREFUL.model_dump(mode="json")})


def test_the_rollback_uses_the_monitors_own_bound(tmp_path):
    mh, wf, stub = _adopted(tmp_path)
    stub.collapsed = True
    got = mh.audit(rig.cases("m", 60, 900), count=60)
    assert got["pairs"] >= drift.MIN_PAIRS and got["mean_difference"] > got["threshold"]


def test_while_stopped_the_audit_runs_nothing(tmp_path):
    mh, _, stub = _adopted(tmp_path)
    before = stub.calls
    stop.engage("test")
    try:
        got = mh.audit(rig.cases("m", 10, 900), count=10)
    finally:
        stop.release()
    assert got["audited"] == 0 and stub.calls == before
