# SPDX-License-Identifier: Apache-2.0
"""Meta-harness Phase 7: the coding specialisation and Level 2 (strategy choice credited by approvals only)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from g6.metaharness import Profile, adapt
from g6.metaharness import coding as C
from g6.metaharness import level2

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                                       encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit",
                                                "-qm", "base"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return repo


def test_the_coding_oracle_passes_only_on_a_passed_verdict(tmp_path) -> None:
    seen = []

    def run(repo, tests, *, timeout_s, mutant=None):
        seen.append((repo, tuple(tests)))
        return {"verdict": "passed" if "ok" in tests[0] else "failed"}

    oracle = C.coding_oracle(run=run)
    assert oracle(str(tmp_path), {"tests": ["test_ok.py"]}) is True
    assert oracle(str(tmp_path), {"tests": ["test_bad.py"]}) is False
    assert oracle(str(tmp_path), {"tests": []}) is False and oracle("not a dir", {"tests": ["x"]}) is False
    assert len(seen) == 2


def test_mutation_testing_runs_inside_the_oracle_and_a_surviving_mutant_fails_the_case(tmp_path) -> None:
    repo = _repo(tmp_path)

    def killing(repo_, tests, *, timeout_s, mutant=None):
        return {"verdict": "failed" if mutant else "passed"}     # every mutant is caught by the tests

    def blind(repo_, tests, *, timeout_s, mutant=None):
        return {"verdict": "passed"}                              # the tests pass whatever the code does

    assert C.coding_oracle(mutation=True, run=killing)(str(repo), {"tests": ["test_calc.py"]}) is True
    assert C.coding_oracle(mutation=True, run=blind)(str(repo), {"tests": ["test_calc.py"]}) is False


def test_the_shortcut_check_fails_a_change_that_deletes_an_assertion(tmp_path) -> None:
    repo = _repo(tmp_path)
    assert C.NO_SHORTCUT.run(str(repo)) is True
    (repo / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    pass\n", encoding="utf-8")
    assert C.NO_SHORTCUT.run(str(repo)) is False


def test_a_live_coding_run_never_runs_the_oracle(tmp_path) -> None:
    calls = []
    wf = C.coding_workflow("fix", dev=rig.cases("d", 4, 0), held_out=rig.cases("h", 30, 500),
                           models={"local": rig.Model(rig.Fixed(""))},
                           oracle_run=lambda *a, **k: calls.append(1) or {"verdict": "passed"})
    mh = adapt(wf, C.agent_factory(lambda task, llm: str(tmp_path)), workspace=tmp_path / "ws", clock=rig.Clock())
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft"
    assert mh.run("fix the add function")["completion_state"] != "blocked-escalated" and calls == []


@dataclass
class Fixed:
    profile: dict
    name: str

    def propose(self, view, runner):
        return [self.profile]


def test_level2_credits_a_strategy_only_for_changes_a_person_activated(tmp_path) -> None:
    wf, _ = rig.build(7)
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    careful = Fixed({"routing": {"answer": "local"}, "prompt": "CAREFUL: add the two numbers digit by digit."}, "careful")
    brief = Fixed({"routing": {"answer": "local"}, "prompt": "Be brief."}, "brief")
    assert level2.choose([careful, brief], mh) is careful            # untried first
    for strategy in (careful, brief):
        proposal = mh.propose(strategy)["proposals"][0]
        mh.decide(mh.evaluate(proposal)["report"], proposal)
    table = level2.credit(mh)
    assert table == {"careful": {"proposed": 1, "approved": 0}, "brief": {"proposed": 1, "approved": 0}}
    careful_decision = next(d for d in mh._decisions.values() if d.verdict == "recommend")
    mh.activate(careful_decision, accepted_by="Ada")
    assert level2.credit(mh)["careful"]["approved"] == 1 and level2.credit(mh)["brief"]["approved"] == 0
    assert level2.choose([careful, brief], mh) is careful


def test_a_demoted_activation_earns_no_credit(tmp_path) -> None:
    wf, _ = rig.build(7)
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    proposal = mh.propose(Fixed({"routing": {"answer": "local"}, "prompt": "CAREFUL: check."}, "careful"))["proposals"][0]
    decision = mh.decide(mh.evaluate(proposal)["report"], proposal)["decision"]
    mh.activate(decision, accepted_by="Ada")
    mh.store.append_version("demote", Profile(routing={"answer": "local"}), at="t", accepted_by="monitor",
                            target_version=0)
    assert level2.credit(mh)["careful"]["approved"] == 0
    assert replace(decision) == decision
