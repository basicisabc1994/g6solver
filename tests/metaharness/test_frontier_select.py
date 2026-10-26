# SPDX-License-Identifier: Apache-2.0
"""The selection split and racing (frontier design §4.2): candidates are ranked on their own split, so the held-out
split is spent once, on the winner."""
from __future__ import annotations

import dataclasses
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from g6.metaharness import Profile, Suite, adapt
from g6.metaharness.executor import model_call_factory
from g6.metaharness.schema import WORKFLOW_INVALID
from g6.metaharness.select import race

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)  # registered first: its dataclasses look their module up while they are built
Clock, build, cases = rig.Clock, rig.build, rig.cases

BASE = Profile(routing={"answer": "local"})
CAREFUL = BASE.model_copy(update={"prompt": "CAREFUL: check the sum."})


def _with_selection(n: int = 40, **kw):
    wf, stub = build(**kw)
    suite = Suite(dev=wf.suite.dev, held_out=wf.suite.held_out, selection=cases("s", n, 900),
                  provenance=dict(wf.suite.provenance))
    return dataclasses.replace(wf, suite=suite), stub


def _race(wf, candidates, **kw):
    return race(wf, model_call_factory(), BASE, candidates, budget=kw.pop("budget", 10_000), clock=Clock(), **kw)


def test_racing_finds_the_better_candidate():
    wf, _ = _with_selection(seed=3)
    neutral = [BASE.model_copy(update={"prompt": f"Note {i}."}) for i in range(2)]
    result = _race(wf, [*neutral, CAREFUL])
    assert result.winner == CAREFUL
    assert result.ranking[0]["candidate_digest"] == CAREFUL.digest()


def test_racing_reads_only_the_selection_split():
    wf, stub = _with_selection(seed=4)
    _race(wf, [CAREFUL, BASE.model_copy(update={"prompt": "Note."})])
    firsts = {int(m.group(1)) for _, user in stub.log for m in [re.search(r"What is (\d+)\+", user)] if m}
    assert firsts and min(firsts) >= 910                   # selection cases start at 910; dev at 10, held-out at 510


def test_no_winner_when_nothing_beats_the_base():
    wf, _ = _with_selection(seed=5, base_acc=0.6, careful_acc=0.6)
    result = _race(wf, [BASE.model_copy(update={"prompt": "Note."})])
    assert result.winner is None or result.ranking[0]["wins"] > result.ranking[0]["losses"]
    flat = _race(wf, [BASE])                               # the base itself can never win
    assert flat.winner is None


def test_racing_stops_at_its_budget():
    wf, _ = _with_selection(seed=6)
    result = _race(wf, [CAREFUL, BASE.model_copy(update={"prompt": "Note."})], budget=3, block=10)
    assert result.winner is None and "budget" in result.stopped


def test_a_selection_split_sharing_a_held_out_case_is_a_problem():
    wf, _ = build()
    suite = Suite(dev=wf.suite.dev, held_out=wf.suite.held_out, selection=wf.suite.held_out[:2],
                  provenance=dict(wf.suite.provenance))
    assert any("selection" in why for _, why in suite.problems())


def test_an_empty_selection_split_keeps_the_workflow_digest():
    wf, _ = build()
    same = dataclasses.replace(wf, suite=Suite(dev=wf.suite.dev, held_out=wf.suite.held_out, selection=(),
                                               provenance=dict(wf.suite.provenance)))
    assert same.digest() == wf.digest()


def test_select_refuses_without_a_selection_split(tmp_path):
    wf, _ = build()
    mh = adapt(wf, workspace=tmp_path, clock=Clock())
    mh.confirm("Dana")
    made = mh.candidate(CAREFUL, proposer="test")
    reply = mh.select([made["proposal"]])
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_META_NO_SELECTION_SPLIT"


def test_select_ranks_proposals_and_records_it(tmp_path):
    wf, _ = _with_selection(seed=7)
    mh = adapt(wf, workspace=tmp_path, clock=Clock())
    mh.confirm("Dana")
    proposals = [mh.candidate(p, proposer="test")["proposal"]
                 for p in (CAREFUL, BASE.model_copy(update={"prompt": "Note."}))]
    reply = mh.select(proposals)
    assert reply["completion_state"] == "qualified-draft" and reply["winner"] is proposals[0]
    assert mh.ledger.rows("selection")[-1]["winner"] == proposals[0].candidate_digest


@pytest.mark.parametrize("bad", [{"block": 0}, {"keep": 0.0}, {"keep": 1.5}])
def test_race_refuses_bad_settings(bad):
    wf, _ = _with_selection()
    with pytest.raises(ValueError):
        _race(wf, [CAREFUL], **bad)


def test_workflow_invalid_code_is_used_for_selection_ids():
    wf, _ = build()
    bad = Suite(dev=wf.suite.dev, held_out=wf.suite.held_out, selection=(cases("s", 1, 900)[0],) * 2,
                provenance=dict(wf.suite.provenance))
    assert (WORKFLOW_INVALID, "selection: every case needs its own non-empty id") in bad.problems()


def test_a_lone_candidate_is_raced_over_the_whole_selection_split():
    # round 1, MASSIVE: the 8-examples change (+11 on dev-eval) was the only candidate left after one block, so it ran
    # 10 of the 60 selection cases, saw 0 wins and 0 losses, and was never confirmed
    wf, _ = _with_selection(n=40, seed=7)
    result = _race(wf, [CAREFUL], block=10)
    assert result.ranking[0]["n"] == 40 and result.cases_used == 40
