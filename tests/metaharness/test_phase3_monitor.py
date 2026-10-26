# SPDX-License-Identifier: Apache-2.0
"""Meta-harness Phase 3: the monitor, the audit floor, demotion and opt-in online tuning (T3-1..T3-7)."""
from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from g6.metaharness import Profile, Suite, SuiteCase, adapt
from g6.metaharness import monitor as M
from g6.metaharness import schema as S

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

CAREFUL = Profile(routing={"answer": "local"}, prompt="CAREFUL: add the two numbers digit by digit.")


@dataclass
class DriftStub(rig.Stub):
    drift: bool = False

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        if self.drift and "CAREFUL" in system:
            self.calls += 1
            return "drifted"            # not a number: the standard's check fails it
        return super().complete(system, user, max_tokens=max_tokens)


def _activated(tmp_path, stub=None, seed=7):
    wf, stub = rig.build(seed, stub=stub or DriftStub(seed, 0.5, 0.85))
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    proposal = mh.candidate(CAREFUL, proposer="bo")["proposal"]
    decision = mh.decide(mh.evaluate(proposal)["report"], proposal)["decision"]
    assert decision.verdict == "recommend"
    assert mh.activate(decision, accepted_by="Ada")["head_version"] == 1
    return mh, wf, stub


def _upper_99(n: int, p: float) -> int:
    acc = 0.0
    for k in range(n + 1):
        acc += math.comb(n, k) * p ** k * (1 - p) ** (n - k)
        if acc >= 0.99:
            return k
    return n


# T3-1 ---------------------------------------------------------------------------------------------------------------

def test_t3_1_injected_drift_is_demoted_within_the_bound(tmp_path) -> None:
    mh, _, stub = _activated(tmp_path)
    assert mh.set_audit_rate(1.0, accepted_by="Ada")["completion_state"] == "qualified-draft"
    stub.drift = True
    bound = M.pairs_to_detect(1.0, mh.workflow.policy.demote_alpha)
    demoted = None
    for i in range(bound + 5):
        mh.run(f"What is {i}+1?")
        got = mh.monitor()
        if got["warning_card"]["code"] == S.DRIFT_DEMOTED:
            demoted = got
            break
    assert demoted is not None and demoted["audit"]["pairs"] <= bound
    assert mh.store.versions()[-1]["kind"] == "demote" and mh.store.current_approved() == 0
    assert mh.run("What is 2+2?")["profile_digest"] == Profile(routing={"answer": "local"}).digest()
    assert mh.ledger.rows("ticket")[-1]["kind"] == "re-evaluate"


# T3-2 ---------------------------------------------------------------------------------------------------------------

def test_t3_2_no_drift_over_500_seeds_rarely_demotes_however_often_it_is_consulted() -> None:
    seeds, pairs, alpha, false = 500, 200, 0.05, 0
    for seed in range(seeds):
        r = random.Random(seed)
        diffs: list[float] = []
        for _ in range(pairs):
            diffs.append(float(r.random() < 0.6) - float(r.random() < 0.6))   # same version, two draws
            if M.assess(diffs, alpha).demote:
                false += 1
                break
    assert false <= _upper_99(seeds, alpha), false


def test_t3_2_without_drift_the_monitor_keeps_the_version(tmp_path) -> None:
    mh, _, _ = _activated(tmp_path)
    mh.set_audit_rate(1.0, accepted_by="Ada")
    for i in range(30):
        mh.run(f"What is {i}+2?")
        assert mh.monitor()["warning_card"]["code"] == "G6_E_META_MONITOR"
    assert mh.store.current_approved() == 1


# T3-3 ---------------------------------------------------------------------------------------------------------------

def test_t3_3_the_audit_floor_can_be_raised_but_not_lowered(tmp_path) -> None:
    mh, wf, _ = _activated(tmp_path)
    assert mh.set_audit_rate(wf.audit_floor / 2, accepted_by="Ada")["warning_card"]["code"] == S.AUDIT_FLOOR
    assert mh.set_audit_rate(0.5, accepted_by="auto:x")["warning_card"]["code"] == S.APPROVAL_REQUIRED
    assert mh.set_audit_rate(0.5, accepted_by="Ada")["audit_rate"] == 0.5
    audited = sum(1 for i in range(1, 11) if "audit" in mh.run(f"What is {i}+3?"))
    assert audited == 5   # every second run at 0.5


def test_t3_3_an_audit_never_returns_the_previous_versions_output(tmp_path) -> None:
    mh, _, _ = _activated(tmp_path)
    mh.set_audit_rate(1.0, accepted_by="Ada")
    got = mh.run("What is 40+2?")
    assert "audit" in got and "output" not in got["audit"] and got["audit"]["model_calls"] == 1


# T3-4 ---------------------------------------------------------------------------------------------------------------

def test_t3_4_while_stopped_the_monitor_runs_nothing_and_a_rollback_works(tmp_path, monkeypatch) -> None:
    mh, _, _ = _activated(tmp_path)
    before = (tmp_path / "ws" / "ledger.jsonl").read_bytes()
    monkeypatch.setenv("G6_STOP", "1")
    assert mh.monitor()["warning_card"]["code"] == "G6_E_STOPPED"
    assert (tmp_path / "ws" / "ledger.jsonl").read_bytes() == before
    assert mh.rollback(0, accepted_by="Ada")["completion_state"] == "qualified-draft"


# T3-5, T3-7: online tuning is opt-in and yields candidates only ----------------------------------------------------------

@dataclass
class Prompted:
    name: str = "prompted"

    def propose(self, view, runner):
        assert not view.dev and view.failures   # live failures, no dev answers in this view
        return [{**view.base, "prompt": f"Avoid these: {len(view.failures)} failures"}]


def test_t3_7_online_tuning_is_off_by_default(tmp_path) -> None:
    wf, _ = rig.build(stub=rig.Fixed(""))
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    assert mh.tune_online(Prompted())["warning_card"]["code"] == S.ONLINE_OFF
    assert any(c["claim"] == "online tuning is off" for c in mh.report()["checked"])
    mh.run("What is 1+1?")
    assert "failure" not in mh.ledger.rows("run")[-1]          # nothing about a live task is kept unless opted in


def test_t3_5_an_online_candidate_never_runs_without_a_person(tmp_path) -> None:
    wf, _ = rig.build(stub=rig.Fixed(""))
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    assert mh.enable_online_tuning(Prompted(), accepted_by="auto:x")["warning_card"]["code"] == S.APPROVAL_REQUIRED
    mh.enable_online_tuning(Prompted(), accepted_by="Ada", every=3)
    head = mh.store.head()[1].digest()
    outs = [mh.run(f"What is {i}+5?") for i in range(3)]
    tuned = outs[-1]["online_tuning"]
    assert tuned["code"] == "G6_E_META_PROPOSED" and tuned["candidates"]
    assert all(o["profile_digest"] == head for o in outs) and len(mh.store.versions()) == 1
    assert mh.run("What is 9+9?")["profile_digest"] == head
    claims = json.dumps(mh.report()["checked"])
    assert "online tuning is ON" in claims and "Ada" in claims


# T3-6: a live episode can never be held-out evidence --------------------------------------------------------------

def test_t3_6_a_live_input_planted_in_a_held_out_split_is_refused(tmp_path) -> None:
    wf, _ = rig.build()
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    mh.run("What is 777+1?")
    planted = Suite(wf.suite.dev, (*wf.suite.held_out, SuiteCase("from-live", "What is 777+1?", "778")),
                    wf.suite.provenance)
    again = adapt(replace(wf, suite=planted, version=2), workspace=tmp_path / "ws")
    assert again.envelope["warning_card"]["code"] == S.NOT_HELD_OUT
    clean = Suite(wf.suite.dev, (*wf.suite.held_out, SuiteCase("new", "What is 778+1?", "779")), wf.suite.provenance)
    assert adapt(replace(wf, suite=clean, version=2), workspace=tmp_path / "ws").envelope["completion_state"] \
        == "qualified-draft"   # survival


def test_the_threshold_is_the_union_bound_it_claims() -> None:
    alpha = 0.05
    assert sum(alpha / (n * (n + 1)) for n in range(1, 100_000)) < alpha
    for n in (5, 20, 100):
        assert math.isclose(math.exp(-n * M.threshold(n, alpha) ** 2 / 2), alpha / (n * (n + 1)))
