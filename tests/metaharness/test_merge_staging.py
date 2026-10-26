# SPDX-License-Identifier: Apache-2.0
"""Staging only rejects early; the unchanged meta-gate still decides admission."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from g6.metaharness import EvidencePolicy, Profile, adapt
from g6.metaharness.pool import Pool
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("metaharness_merge_rig", Path(__file__).with_name("_merge_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "harness"):
    _spec.loader.exec_module(rig)
harness = rig.harness


def staged(tmp_path, *, prompt="CAREFUL"):
    old, _ = harness(tmp_path / "source", missing=True)
    wf = replace(old.workflow, policy=EvidencePolicy(staged_evaluation=True))
    mh = adapt(wf, workspace=tmp_path / "staged", clock=lambda: 0.0)
    mh.confirm("Ada")
    p = mh.candidate(Profile(routing={"answer":"local"}, prompt=prompt), proposer="Bo")["proposal"]
    return mh, p


def test_dgm_01_d1_staged_failure_is_never_a_proof(tmp_path):
    mh, p = staged(tmp_path, prompt="unchanged behaviour")
    report = mh.evaluate(p)["report"]
    assert report.n == 5 and report.partial and report.stages[0]["passed"] is False
    got = mh.decide(report, p)
    assert got["decision"].code == "G6_E_META_STAGE_FAILED" and got["completion_state"] == "blocked-escalated"
    # RED: removing `if staged and len(rows) in (5, 20):` loses first-stage rejection.


def test_dgm_04_surviving_stages_still_needs_all_held_out_cases(tmp_path):
    mh, p = staged(tmp_path)
    report = mh.evaluate(p)["report"]
    assert report.n == 40 and [s["tasks"] for s in report.stages] == [5, 20, 40]
    assert mh.decide(report, p)["decision"].verdict == "recommend"
    assert len(mh.store.versions()) == 1
    # RED: removing `stages.append({"tasks": len(rows), "pass_rate": rate, ...})` loses checkpoint evidence.


def test_dgm_02_staged_code_uses_the_existing_sandbox(tmp_path, monkeypatch):
    mh, _ = staged(tmp_path)
    root = tmp_path / "program"
    root.mkdir()
    (root / "main.py").write_text("def solve(task): return 'right'\n", encoding="utf-8")
    wf = replace(mh.workflow, code_root=root)
    from g6.core import sandbox
    calls = []
    def run_script(source, files, **kw):
        calls.append((source, files, kw))
        return SimpleNamespace(status="ok", stdout='{"output":"wrong"}', stderr="")
    monkeypatch.setattr(sandbox, "run_script", run_script)
    code = adapt(wf, workspace=tmp_path / "code", clock=lambda: 0.0)
    code.confirm("Ada")
    p = code.candidate(Profile(routing={"answer":"local"}, prompt="changed"), proposer="Bo")["proposal"]
    report = code.evaluate(p)["report"]
    assert calls and report.n == 5 and all("main.py" in files for _, files, _ in calls)
    assert code.decide(report, p)["decision"].verdict != "recommend"
    # RED: removing code_factory's `run_script` call loses sandbox receipts; no engine executor exists.


def test_dgm_03_proposal_lineage_is_persisted(tmp_path):
    mh, p = staged(tmp_path)
    assert p.parent_digests == (p.base_digest,)
    reopened = adapt(mh.workflow, workspace=mh.workspace, clock=lambda: 0.0)
    saved, why = reopened._recorded("proposal", p.digest())
    assert not why and saved.parent_digests == p.parent_digests and saved.candidate != mh.store.head()[1]
    # RED: removing `**({"parent_digests": list(proposal.parent_digests)} if proposal.parent_digests else {})` loses lineage.


def test_parent_novelty_uses_aligned_behaviour_not_claimed_fitness():
    base = Profile(routing={"answer":"local"})
    pool = Pool(novelty_weight=4.0)
    for prompt, results in [("a", {"1":True,"2":True,"3":False}),
                            ("b", {"1":False,"2":False,"3":True}),
                            ("c", {"1":True,"2":False,"3":False})]:
        pool.add(base.model_copy(update={"prompt":prompt}), results)
    class Draw:
        def choices(self, front, *, weights, k):
            assert weights == pytest.approx([2 + 4 * 2/3, 1 + 4 * 5/6])
            return [front[0]]
    assert pool.parent(Draw()).prompt == "a"
    # RED: removing `self.novelty_weight * self.novelty(p)` leaves weights [2, 1].


def test_second_stage_rejects_a_candidate_that_only_solves_first_five(tmp_path):
    from g6.core.llm import FakeLLM
    from g6.metaharness import Model
    old, _ = staged(tmp_path / "old")
    def respond(system, user):
        i = int(user.split()[-1])
        return "right" if (i < 5 if "NARROW" in system else i % 2 == 0) else "wrong"
    wf = replace(old.workflow, models={"local": Model(FakeLLM(respond))})
    mh = adapt(wf, workspace=tmp_path / "second", clock=lambda: 0.0)
    mh.confirm("Ada")
    p = mh.candidate(Profile(routing={"answer":"local"}, prompt="NARROW"), proposer="Bo")["proposal"]
    report = mh.evaluate(p)["report"]
    assert report.n == 20 and report.partial
    assert report.stages[0]["passed"] and not report.stages[1]["passed"]
    assert mh.decide(report, p)["decision"].code == "G6_E_META_STAGE_FAILED"
    # RED: removing `if rate < required:` at the staged checkpoint loses rejection at 20 tasks.


def test_pooled_strategy_keeps_its_actual_parent_on_the_proposal(tmp_path):
    from g6.metaharness.pool import EditSpace
    from g6.metaharness.reflect import EditStrategy, Pooled
    mh, _ = harness(tmp_path, missing=True)
    parent = Profile(routing={"answer":"local"}, prompt="CAREFUL parent")
    pool = Pool()
    pool.add(parent, {c.case_id: True for c in mh.workflow.suite.dev})
    inner = EditStrategy(EditSpace({"prompt": ["CAREFUL child"]}), pool=pool, calls=1)
    proposal = mh.propose(Pooled(inner, pool))["proposals"][0]
    assert proposal.parent_digests == (parent.digest(),)
    # RED: removing `return getattr(self.inner, "lineage", {})` from Pooled.lineage loses the actual parent.
