# SPDX-License-Identifier: Apache-2.0
"""A code candidate whose run no operating-system boundary could confine, where isolation is required, is refused, and
the refusal is never scored: the executor raises ``confine.IsolationRefused`` instead of answering "", the evaluation
stops unfinished with "refused: no boundary", the gate refuses it with the isolation code, and the report says how the
code ran (``isolation``). Offline."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core import confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.metaharness import adapt, gate, steps
from g6.metaharness.api import judging_sha256
from g6.metaharness.evaluate import REFUSED_NO_BOUNDARY

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

OFF = "unavailable: switched off by this test"
BROKEN = 'import re\ndef solve(task):\n    a, b = map(int, re.search(r"(\\d+)\\+(\\d+)", task).groups())\n' \
         '    return str(a + b) if a % 2 == 0 else str(a + b + 1)\n'
FIXED = 'import re\ndef solve(task):\n    a, b = map(int, re.search(r"(\\d+)\\+(\\d+)", task).groups())\n' \
        '    return str(a + b)\n'


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, OFF))


def _code_harness(tmp_path: Path):
    root = tmp_path / "agent"
    root.mkdir()
    (root / "main.py").write_text(BROKEN, encoding="utf-8")
    wf, _ = rig.build(n_held=30, n_dev=4)
    mh = adapt(replace(wf, code_root=root), workspace=tmp_path / "ws", clock=rig.Clock())
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft", mh.envelope
    return mh


def test_a_code_candidate_refused_for_isolation_is_never_scored(tmp_path, monkeypatch) -> None:
    mh = _code_harness(tmp_path)
    proposal = mh.code_candidate({"main.py": FIXED}, proposer="bo")["proposal"]
    _off(monkeypatch)
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    report = mh.evaluate(proposal)["report"]
    # fails if a refused run reads as an empty answer (a wrong one): the evaluation must stop unfinished, scoring none
    assert report.partial and report.n == 0 and report.cases == []
    assert report.stopped.startswith("refused: no boundary") and ISOLATION_UNAVAILABLE in report.stopped
    assert report.isolation["backend"] is None and report.isolation["why"] == OFF
    # fails if the gate lets an evaluation that scored nothing through
    assert mh.decide(report, proposal)["decision"].verdict != "recommend"


def test_the_gate_names_an_evaluation_stopped_for_isolation_as_such(tmp_path) -> None:
    wf, _ = rig.build(n_held=30, n_dev=4)
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    proposal = mh.candidate({"routing": {"answer": "local"}, "prompt": "CAREFUL"}, proposer="bo")["proposal"]
    report = mh.evaluate(proposal)["report"]
    stopped = replace(report, partial=True, stopped=f"{REFUSED_NO_BOUNDARY}: [{ISOLATION_UNAVAILABLE}] {OFF}")
    decision = gate.decide(stopped, proposal, mh.workflow, confirmed=mh.store.confirmed(),
                           judging_sha256=judging_sha256(), base=mh.store.head()[1])
    # fails if the gate calls an evaluation stopped for want of a boundary a spent budget
    assert decision.verdict == "refuse" and decision.code == ISOLATION_UNAVAILABLE, decision
    assert OFF in decision.reasons[0]


def test_an_evaluation_that_ran_code_says_how_it_ran(tmp_path, monkeypatch) -> None:
    mh = _code_harness(tmp_path)
    proposal = mh.code_candidate({"main.py": FIXED}, proposer="bo")["proposal"]
    _off(monkeypatch)
    report = mh.evaluate(proposal)["report"]
    # fails if the report drops how the code ran
    assert not report.partial and report.n == 30
    assert report.to_dict()["isolation"]["why"] == OFF and report.isolation["warning"] == confine.UNCONFINED_WARNING


def test_a_model_only_evaluation_carries_no_isolation(tmp_path) -> None:
    wf, _ = rig.build(n_held=30, n_dev=4)
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    proposal = mh.candidate({"routing": {"answer": "local"}, "prompt": "CAREFUL"}, proposer="bo")["proposal"]
    report = mh.evaluate(proposal)["report"]
    # fails if a report of model calls alone grows an isolation field (its digest would change for nothing)
    assert "isolation" not in report.to_dict()


def test_the_shown_tests_step_raises_the_refusal_instead_of_handing_it_to_the_model(monkeypatch) -> None:
    _off(monkeypatch)
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    # fails if repair_with_tests reads the refusal as the code's failure and asks the model to fix code that never ran
    with pytest.raises(confine.IsolationRefused) as refused:
        steps.run_shown("def f():\n    return 1\n", [], ["assert f() == 1"], timeout_s=10)
    assert str(refused.value).startswith(f"[{ISOLATION_UNAVAILABLE}]") and refused.value.isolation["why"] == OFF
