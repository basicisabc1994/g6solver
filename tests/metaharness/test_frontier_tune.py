# SPDX-License-Identifier: Apache-2.0
"""``tune``: the frontier loop as library code (frontier design §4): propose, race on the selection split for a few
cycles, then confirm the champion once on the held-out split, decide, and activate only on a person's name."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

from g6.metaharness import EvidencePolicy, Suite, adapt
from g6.metaharness.method import Method, tune
from g6.metaharness.pool import EditSpace

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)  # registered first: its dataclasses look their module up while they are built

SPACE = EditSpace({"retries": [0, 1]})
FAST = Method(cycles=2, propose_budget=60, select_budget=600, race_block=10, reflect_calls=2)


class Reflector:
    def __init__(self, change: dict) -> None:
        self.change, self.calls = change, 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        return json.dumps(self.change)


def _mh(tmp_path, seed=2, **kw):
    wf, _ = rig.build(seed, n_held=60, policy=EvidencePolicy(max_evaluations=1, looks=2), **kw)
    suite = Suite(dev=wf.suite.dev, held_out=wf.suite.held_out, selection=rig.cases("s", 30, 900),
                  provenance=dict(wf.suite.provenance))
    mh = adapt(dataclasses.replace(wf, suite=suite), workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Dana")
    return mh


def test_tune_finds_and_activates_a_planted_gain(tmp_path):
    mh = _mh(tmp_path, base_acc=0.3, careful_acc=0.95)
    run = tune(mh, FAST, reflector=Reflector({"prompt": "CAREFUL: add the numbers digit by digit."}), space=SPACE,
               approve=lambda reply: "Dana")
    assert run.outcome == "activated" and run.activated == 1
    assert mh.store.head()[1].prompt.startswith("CAREFUL")


def test_tune_activates_nothing_when_nothing_helps(tmp_path):
    mh = _mh(tmp_path, base_acc=0.5, careful_acc=0.5)
    run = tune(mh, FAST, reflector=Reflector({"prompt": "Be brief."}), space=SPACE, approve=lambda reply: "Dana")
    assert run.activated is None and run.outcome in ("no_winner", "refused")
    if run.outcome == "refused":
        assert run.recovery                               # every refusal says why and what next
    assert mh.store.head()[0] == 0


def test_without_approval_nothing_is_activated(tmp_path):
    mh = _mh(tmp_path, base_acc=0.3, careful_acc=0.95)
    run = tune(mh, FAST, reflector=Reflector({"prompt": "CAREFUL: add the numbers digit by digit."}), space=SPACE,
               approve=lambda reply: None)
    assert run.outcome == "recommended_not_approved" and run.activated is None and mh.store.head()[0] == 0


def test_tune_confirms_once_per_run(tmp_path):
    mh = _mh(tmp_path, base_acc=0.3, careful_acc=0.95)
    tune(mh, FAST, reflector=Reflector({"prompt": "CAREFUL: add the numbers digit by digit."}), space=SPACE,
         approve=lambda reply: "Dana")
    assert len([r for r in mh.ledger.rows("evaluation") if r["purpose"] == "evaluation"]) == 1


def test_the_run_record_names_the_method_and_every_cycle(tmp_path):
    mh = _mh(tmp_path, base_acc=0.3, careful_acc=0.95)
    run = tune(mh, FAST, reflector=Reflector({"prompt": "CAREFUL: add the numbers digit by digit."}), space=SPACE,
               approve=lambda reply: "Dana")
    row = mh.ledger.rows("tuning_run")[-1]
    assert row["method_digest"] == FAST.digest() == run.method_digest
    assert [c["cycle"] for c in row["cycles"]] == [1, 2]
    assert {"recovery", "reflect"} <= set(row["cycles"][0]["strategies"])


def test_without_a_reflector_the_reflective_proposer_is_skipped_and_said_so(tmp_path):
    mh = _mh(tmp_path)
    run = tune(mh, FAST, space=SPACE, approve=lambda reply: None)
    assert any("reflect" in note for note in run.notes)
