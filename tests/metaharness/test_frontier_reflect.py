# SPDX-License-Identifier: Apache-2.0
"""The Pareto pool, edits and the reflective proposer (frontier design §4.1): proposals that read the current best's
failures and compound, instead of one fixed option at a time."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import random
import sys
from pathlib import Path

from g6.metaharness import Profile, Suite, adapt
from g6.metaharness.pool import EditSpace, Pool
from g6.metaharness.reflect import EditStrategy, ReflectiveStrategy

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)  # registered first: its dataclasses look their module up while they are built

BASE = Profile(routing={"answer": "local"})
SPACE = EditSpace({"retries": [0, 1], "exemplars": [0, 4]})


class Reflector:
    """A stand-in reflection model: replies in turn, and records every prompt it was shown."""

    def __init__(self, *replies: str) -> None:
        self.replies, self.prompts = list(replies), []

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.prompts.append(user)
        return self.replies.pop(0) if self.replies else "{}"


def _mh(tmp_path, seed=1, **kw):
    wf, stub = rig.build(seed, **kw)
    suite = Suite(dev=wf.suite.dev, held_out=wf.suite.held_out, selection=rig.cases("s", 20, 900),
                  provenance=dict(wf.suite.provenance))
    wf = dataclasses.replace(wf, suite=suite)
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Dana")
    return mh, wf


def test_a_reflected_edit_becomes_a_candidate(tmp_path):
    mh, _ = _mh(tmp_path, base_acc=0.3, careful_acc=0.95)
    strategy = ReflectiveStrategy(Reflector(json.dumps({"prompt": "CAREFUL: add the numbers digit by digit."})), SPACE,
                                  calls=1)
    reply = mh.propose(strategy)
    assert reply["completion_state"] == "qualified-draft"
    assert reply["proposals"][0].candidate.prompt.startswith("CAREFUL")


def test_the_reflector_sees_dev_failures_and_feedback_only(tmp_path):
    seen_cases = []

    def feedback(output: str, case) -> str:
        seen_cases.append(case.case_id)
        return f"FEEDBACK-{case.case_id}: expected {case.expected}"

    mh, wf = _mh(tmp_path, base_acc=0.3, feedback=feedback)
    reflector = Reflector(json.dumps({"retries": 1}))
    mh.propose(ReflectiveStrategy(reflector, SPACE, calls=1))
    prompt = reflector.prompts[0]
    assert "FEEDBACK-d" in prompt and "What is 1" in prompt                 # dev inputs are What is 10+..
    assert all(c.input not in prompt for c in (*wf.suite.held_out, *wf.suite.selection))
    assert seen_cases and all(case_id.startswith("d") for case_id in seen_cases)


def test_unknown_fields_and_non_json_are_dropped(tmp_path):
    mh, _ = _mh(tmp_path)
    strategy = ReflectiveStrategy(Reflector("no json here", json.dumps({"evil": 1, "oracle": "x"})), SPACE, calls=2)
    reply = mh.propose(strategy)
    assert reply["warning_card"]["code"] == "G6_E_META_NO_NEW_CANDIDATE"    # nothing valid and new; nothing raised


def test_the_frontier_keeps_each_case_winner_and_drops_the_dominated():
    a, b, c = (BASE.model_copy(update={"prompt": p}) for p in ("a", "b", "c"))
    pool = Pool()
    pool.add(a, {"1": True, "2": True, "3": False})
    pool.add(b, {"1": False, "2": False, "3": True})
    pool.add(c, {"1": True, "2": False, "3": False})                       # dominated by a
    assert pool.frontier() == [a, b]


def test_parent_sampling_is_seeded():
    pool = Pool()
    for p, wins in (("a", {"1": True, "2": True}), ("b", {"3": True})):
        pool.add(BASE.model_copy(update={"prompt": p}), {"1": False, "2": False, "3": False, **wins})
    draw = lambda seed: [pool.parent(random.Random(seed)).prompt for _ in range(1)]   # noqa: E731
    assert draw(5) == draw(5)
    picks = [pool.parent(random.Random(i)).prompt for i in range(200)]
    assert picks.count("a") > picks.count("b") > 0                         # weighted by the cases each covers


def test_merge_combines_two_changes_and_refuses_a_conflict():
    one = BASE.model_copy(update={"retries": 1})
    two = BASE.model_copy(update={"exemplars": 4})
    merged = SPACE.merge(one, two, BASE)
    assert merged is not None and merged["retries"] == 1 and merged["exemplars"] == 4
    assert SPACE.merge(one, BASE.model_copy(update={"retries": 0, "prompt": "x"}), BASE) is not None
    clash = BASE.model_copy(update={"prompt": "p1"})
    assert SPACE.merge(clash, BASE.model_copy(update={"prompt": "p2"}), BASE) is None


def test_edit_strategy_proposes_a_neighbour(tmp_path):
    mh, _ = _mh(tmp_path, base_acc=0.1, careful_acc=0.95)
    space = EditSpace({"prompt": ["", "CAREFUL: add the numbers digit by digit."]})
    reply = mh.propose(EditStrategy(space, calls=2, seed=3))
    assert reply["completion_state"] == "qualified-draft"
    assert reply["proposals"][0].candidate.prompt.startswith("CAREFUL")


def test_neighbours_change_one_field():
    out = SPACE.neighbours(BASE)
    assert {json.dumps({k: v for k, v in n.items() if k in ("retries", "exemplars")}, sort_keys=True) for n in out} == {
        json.dumps({"exemplars": 0, "retries": 1}, sort_keys=True),
        json.dumps({"exemplars": 4, "retries": 0}, sort_keys=True)}


def test_a_change_that_ties_its_parent_on_dev_is_still_proposed(tmp_path):
    # round 1, MBPP: repair-with-tests tied G6 as built on the 20 dev cases (8 and 8) and so was never raced, though it
    # gained 6 of 100 on dev-eval; a tie on a small dev split is no evidence against a change, so racing decides
    mh, _ = _mh(tmp_path, base_acc=1.0, careful_acc=1.0)
    reply = mh.propose(EditStrategy(EditSpace({"prompt": ["Note."]}), calls=1))
    assert reply["completion_state"] == "qualified-draft"
    assert [p.candidate.prompt for p in reply["proposals"]] == ["Note."]
