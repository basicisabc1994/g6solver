# SPDX-License-Identifier: Apache-2.0
"""Warnings from recorded scores, with no second event store."""
import pytest

from g6.metaharness import detect_agreement_collapse, detect_anchor_decoupling
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("metaharness_merge_rig", Path(__file__).with_name("_merge_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "harness"):
    _spec.loader.exec_module(rig)
harness = rig.harness


def test_self_observation_d1_internal_gains_that_the_anchor_does_not_follow():
    assert detect_anchor_decoupling([0.05 * i for i in range(12)], [0.2] * 12).fired
    assert not detect_anchor_decoupling([0.05 * i for i in range(12)], [0.05 * i for i in range(12)]).fired
    assert not detect_anchor_decoupling([0.2] * 12, [0.2] * 12).fired
    assert not detect_anchor_decoupling([0.1, 0.2, 0.3, 0.4], [0.2] * 4).fired
    # RED: removing `fired = internal[-1] > internal[0] and wins > losses and p <= alpha` loses the divergence check.


def test_self_observation_d1_agreement_collapse_is_judge_neutral():
    assert detect_agreement_collapse((40, 60), (98, 2), judges=("review-a", "review-b")).fired
    assert not detect_agreement_collapse((40, 60), (40, 60)).fired
    assert not detect_agreement_collapse((40, 60), (42, 58)).fired
    assert not detect_agreement_collapse((4, 6), (10, 0)).fired
    with pytest.raises(ValueError):
        detect_agreement_collapse((1, -1), (3, 0))
    # RED: removing `fired = recent[1] / n2 < baseline[1] / n1 and p <= alpha` loses the collapse check.


def test_self_observation_01_monitor_writes_only_the_existing_ledger(tmp_path):
    mh, proposal = harness(tmp_path, cost=0.5)
    mh.baseline()
    decision = mh.decide(mh.evaluate(proposal)["report"], proposal)["decision"]
    assert mh.activate(decision, "Ada")["head_version"] == 1
    mh.baseline()
    result = mh.monitor()
    assert any(w["kind"] == "agreement_collapse" for w in result["observations"])
    tickets = [r for r in mh.ledger.rows("ticket") if r.get("kind") == "self_observation"]
    assert tickets and tickets[0]["failure_mode"] == "unseen_early_warning"
    before = len(tickets)
    mh.monitor()
    assert len([r for r in mh.ledger.rows("ticket") if r.get("kind") == "self_observation"]) == before
    assert mh.ledger.verify() == () and (tmp_path / "ledger.jsonl").is_file()
    # RED: removing `self._keep("ticket", digest(observation), ...)` loses the recorded warning.
