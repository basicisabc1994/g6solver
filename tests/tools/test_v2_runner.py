# SPDX-License-Identifier: Apache-2.0
"""The V2 study runner on its smoke fixture (made-up calls in AgentDojo's shape, not evaluation data): labels come
from the call's kind and the frozen mapping, an arm's error never helps it, an unknown tool stops the run, and the
pre-registered verdict rules apply in order."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "validation" / "v2_action_gating"
_spec = importlib.util.spec_from_file_location("v2_run", HERE / "run.py")
v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v2)
FIXTURE = HERE / "SMOKE_FIXTURE_NOT_EVALUATION_DATA.jsonl"


def test_labels_follow_the_kind_and_the_mapping():
    calls = v2.load(FIXTURE)
    assert [c["label"] for c in calls] == ["benign", "benign", "injection_read", "harmful"]


def test_the_runner_runs_end_to_end(tmp_path):
    assert v2.main([str(FIXTURE), "--out", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "v2_report.json").read_text(encoding="utf-8"))
    assert (report["harmful"], report["benign"], report["injection_read_steps"]) == (1, 2, 1)
    allow = report["arms"]["allow_list"]
    assert allow["missed_harm"]["k"] == 0 and allow["false_escalation"]["k"] == 1   # the rent payment asks
    assert report["verdict"] in ("insufficient_failure_opportunities", "unsafe_or_regressive")


def test_an_arm_error_never_helps_the_arm():
    calls = v2.load(FIXTURE)
    report = v2.evaluate(calls, {"broken": lambda call: None})
    arm = report["arms"]["broken"]
    assert arm["errors"] == 4 and arm["missed_harm"]["k"] == 1 and arm["false_escalation"]["k"] == 2


def test_a_tool_the_mapping_does_not_know_stops_the_run(tmp_path):
    row = json.loads(FIXTURE.read_text(encoding="utf-8").splitlines()[0])
    unknown = tmp_path / "calls.jsonl"
    unknown.write_text(json.dumps({**row, "function": "transfer_everything"}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="not in the frozen mapping"):
        v2.load(unknown)


@pytest.mark.parametrize(("gate_misses", "allow_misses", "verdict"), [
    (1, 0, "unsafe_or_regressive"), (0, 0, "insufficient_failure_opportunities"), (0, 1, "inconclusive")])
def test_the_verdict_rules_apply_in_order(gate_misses, allow_misses, verdict):
    calls = [c for c in v2.load(FIXTURE) if c["label"] == "harmful"]
    report = v2.evaluate(calls, {"gate_declared": lambda call: bool(gate_misses),
                                 "allow_list": lambda call: bool(allow_misses)})
    assert report["verdict"] == verdict   # no judge arm: a positive verdict needs it, so (0, 1) is inconclusive
