# SPDX-License-Identifier: Apache-2.0
"""Meta-harness recovery and root cause (DECISIONS #27): a failed attempt says why and what can be done next, from the
gate's aggregates and the dev split only."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

from g6.core.registry import get_registry
from g6.metaharness import Profile, Suite, SuiteCase, adapt
from g6.metaharness import recovery as R
from g6.metaharness import schema as S
from g6.metaharness.evaluate import power
from g6.metaharness.surface import Surface

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

BRIEF = Profile(routing={"answer": "local"}, prompt="Be brief.")
RISKY_TASK = "Check the card payment fee. {input} Reply with the number only."


def _mh(tmp_path, wf, **kw):
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock(), **kw)
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft", mh.envelope
    return mh


def _held_out_text(wf) -> list[str]:
    return [t for c in wf.suite.held_out for t in (c.case_id, c.input)]


# --- from the gate's aggregates ------------------------------------------------------------------------------------

def test_an_underpowered_win_is_not_read_as_no_effect() -> None:
    wf, _ = rig.build()
    decision = SimpleNamespace(code=S.NO_GAIN, reasons=("wins 9, losses 3",))
    report = SimpleNamespace(n=40, success={"wins": 9, "losses": 3})
    plan = R.for_decision(decision, report, wf)
    assert [c.code for c in plan.causes] == [R.UNDERPOWERED]
    first = plan.remedies[0]
    assert first.who == "person" and "not read this result as 'no effect'" in first.action
    need = R.cases_needed(40, 9, 3, wf.policy.threshold)
    assert need is not None and need > 40 and f"about {need}" in first.action
    # the estimate is about right: at that size the gate's own power formula is near the 0.8 target
    assert 0.6 <= power(need, 12 / 40, 6 / 40, wf.policy.threshold, wf.policy.min_discordant) <= 0.95


def test_no_sign_of_a_gain_sends_you_to_diagnose() -> None:
    wf, _ = rig.build()
    plan = R.for_decision(SimpleNamespace(code=S.NO_GAIN, reasons=()), SimpleNamespace(n=40, success={"wins": 2,
                          "losses": 5}), wf)
    assert plan.causes[0].code == R.NO_SIGN_OF_GAIN and "mh.diagnose()" in plan.next_action
    assert R.cases_needed(40, 2, 5, wf.policy.threshold) is None


def test_each_refusal_says_who_acts() -> None:
    wf, _ = rig.build()
    for code, cause, who in ((S.NOT_COMPUTE_MATCHED, R.MORE_COMPUTE, "candidate"), (S.HOLDOUT_REUSED, R.SPLIT_SPENT,
                             "person"), (S.SUITE_CANNOT_DISCRIMINATE, R.SUITE, "person"),
                             (S.STANDARD_CHANGED, R.NEEDS_A_PERSON, "person")):
        plan = R.for_decision(SimpleNamespace(code=code, reasons=("x",)), SimpleNamespace(n=0, success={}), wf)
        assert plan.causes[0].code == cause and plan.remedies[0].who == who, code


def test_a_refused_decision_carries_its_recovery_and_no_held_out_case(tmp_path) -> None:
    wf, _ = rig.build()
    mh = _mh(tmp_path, wf)
    proposal = mh.candidate(BRIEF, proposer="bo")["proposal"]
    got = mh.decide(mh.evaluate(proposal)["report"], proposal)
    assert got["completion_state"] == "blocked-escalated" and got["recovery"]["causes"]
    assert got["next_action"].startswith("Nothing changes. ") and got["recovery"]["next_action"] in got["next_action"]
    shown = json.dumps(got["recovery"])
    assert not any(t in shown for t in _held_out_text(wf))


# --- from the proposer ---------------------------------------------------------------------------------------------

@dataclass
class Same:
    profile: dict
    name: str = "same"

    def propose(self, view, runner):
        return [self.profile]


@dataclass
class Broken:
    name: str = "broken"

    def propose(self, view, runner):
        raise RuntimeError("no model")


def test_a_proposer_with_nothing_new_or_that_fails_carries_recovery(tmp_path) -> None:
    wf, _ = rig.build()
    mh = _mh(tmp_path, wf)
    same = Same({"routing": {"answer": "local"}, "prompt": "Be brief."})
    mh.propose(same)
    again = mh.propose(same)
    assert again["recovery"]["causes"][0]["code"] == R.SEARCH_EXHAUSTED and "mh.diagnose()" in again["next_action"]
    failed = mh.propose(Broken())
    assert failed["recovery"]["causes"][0]["code"] == R.PROPOSER_FAILED and "no model" in failed["message"]


# --- from the dev split ----------------------------------------------------------------------------------------------

def test_diagnose_names_an_intake_hold_and_that_only_a_person_can_lift_it(tmp_path) -> None:
    wf, _ = rig.build()
    risky = replace(wf, task=RISKY_TASK)
    got = _mh(tmp_path, risky).diagnose()
    assert got["completion_state"] == "qualified-draft" and got["warning_card"]["code"] == "G6_E_META_DIAGNOSED"
    cause = got["recovery"]["causes"][0]
    assert cause["code"] == R.INTAKE_BLOCKED and cause["cases"] == len(wf.suite.dev)
    first = got["recovery"]["remedies"][0]
    assert first["who"] == "person" and "accept_risky" in first["action"] and "accept_risky" in got["next_action"]
    accepted = _mh(tmp_path / "b", replace(risky, accept_risky="Ada")).diagnose()
    assert R.INTAKE_BLOCKED not in [c["code"] for c in accepted["recovery"]["causes"]]


def test_diagnose_names_the_check_that_rejects_answers(tmp_path) -> None:
    wf, _ = rig.build(stub=rig.Fixed("twelve"))
    got = _mh(tmp_path, wf).diagnose()
    cause = next(c for c in got["recovery"]["causes"] if c["code"] == R.CHECK_REJECTED)
    assert cause["cases"] == len(wf.suite.dev) and "the answer is a whole number" in cause["what"]
    assert any(r["who"] == "person" and "never changes a check" in r["action"] for r in got["recovery"]["remedies"])


def test_diagnose_maps_wrong_answers_to_g6_components_that_exist(tmp_path) -> None:
    wf, _ = rig.build(base_acc=0.3)
    got = _mh(tmp_path, wf).diagnose()
    cause = next(c for c in got["recovery"]["causes"] if c["code"] == R.WRONG_ANSWER)
    assert "number" in cause["what"] and cause["cases"] > 0
    assert {r["component"] for r in got["recovery"]["remedies"]} >= {"reasoning"}
    for remedies in R.WRONG_ANSWER_REMEDIES.values():
        for remedy in remedies:
            if remedy.component:
                assert remedy.component in get_registry().names()   # every component named is a real one


def test_diagnose_reads_no_held_out_case(tmp_path) -> None:
    wf, stub = rig.build(base_acc=0.3)
    held = tuple(replace(c, expected=f"CANARY7f3a-{i}") for i, c in enumerate(wf.suite.held_out))
    wf = replace(wf, suite=Suite(wf.suite.dev, held, wf.suite.provenance))
    mh = _mh(tmp_path, wf)
    stub.log.clear()
    got = mh.diagnose()
    shown = json.dumps(got, default=str) + "\n".join(u for _, u in stub.log)
    assert "CANARY7f3a" not in shown and not any(c.input in shown for c in held)
    assert got["dev_cases"] == len(wf.suite.dev)


def test_answer_kinds_come_from_dev_answers_only() -> None:
    wf, _ = rig.build()

    def kind(*expected):
        dev = tuple(SuiteCase(f"d{i}", "x", e) for i, e in enumerate(expected))
        return R.answer_kind(replace(wf, suite=Suite(dev, wf.suite.held_out, wf.suite.provenance)))

    assert kind("12", "3.5") == "number"
    assert kind("alarm_set", "SUPPORT") == "label"
    assert kind({"tests": ["t1"]}) == "code"
    assert kind("Refunds take 14 days.") == "text"


def test_baseline_counts_held_runs_and_names_no_case(tmp_path) -> None:
    wf, _ = rig.build()
    risky = replace(wf, task=RISKY_TASK)
    got = _mh(tmp_path, risky).baseline()
    assert got["baseline"]["held_out"]["held"] == {R.RISKY: len(wf.suite.held_out)}
    assert "mh.diagnose()" in got["next_action"] and "recovery" in got
    assert not any(t in json.dumps(got["recovery"]) for t in _held_out_text(wf))


def test_the_mcp_surface_returns_the_diagnosis(tmp_path) -> None:
    wf, _ = rig.build()
    risky = replace(wf, task=RISKY_TASK)
    surface = Surface({"arith": {"workflow": risky, "workspace": tmp_path / "ws", "clock": rig.Clock()}})
    surface.harness("arith").confirm("Ada")
    got = surface.diagnose("arith")
    json.dumps(got)                                                   # plain JSON for the MCP client
    assert got["recovery"]["causes"][0]["code"] == R.INTAKE_BLOCKED
