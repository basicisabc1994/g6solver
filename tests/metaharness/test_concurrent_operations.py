# SPDX-License-Identifier: Apache-2.0
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
from threading import Event
import pytest

from g6.metaharness import adapt, Profile
from g6.metaharness import api

spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(spec.name, importlib.util.module_from_spec(spec))
if not hasattr(rig, "build"):
    spec.loader.exec_module(rig)


@pytest.mark.parametrize("separate_handle", [False, True])
def test_overlapping_evaluations_cannot_both_spend_the_last_holdout_use(tmp_path, monkeypatch, separate_handle):
    workflow, factory = rig.build(7)
    workflow = replace(workflow, policy=replace(workflow.policy, max_evaluations=1))
    mh = adapt(workflow, factory, workspace=tmp_path, clock=rig.Clock())
    mh.confirm("Ada")
    proposal = mh.candidate(Profile(routing={"answer": "local"}, prompt="CAREFUL"), proposer="builder")["proposal"]
    other = adapt(workflow, factory, workspace=tmp_path, clock=rig.Clock()) if separate_handle else mh
    other_proposal, why = other._recorded("proposal", proposal.digest())
    assert not why
    entered, release, second_entered = Event(), Event(), Event()
    original = api.evaluate
    calls = []

    def delayed(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        else:
            second_entered.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "evaluate", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(mh.evaluate, proposal)
        assert entered.wait(5)
        second = pool.submit(other.evaluate, other_proposal)
        overlapping = second_entered.wait(0.2)
        release.set()
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert not overlapping, "both operations entered the evaluator before either recorded its use"
    assert sum("report" in result for result in results) == 1
    assert len(mh.ledger.rows("evaluation")) == 1
