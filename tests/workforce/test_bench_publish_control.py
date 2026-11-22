# SPDX-License-Identifier: Apache-2.0
"""Pending owner controls survive every exit from in-place answer construction."""
import time

import pytest

from g6.core import component_process
from g6.safety_engine.schema import RuntimePolicy
from g6.workforce import bench, calls, capabilities, repo_tests
from g6.workforce.workers import WorkerSpec


@pytest.mark.parametrize("signal", ["stop", "deadline"])
@pytest.mark.parametrize("answer", ["report", "refusal", "raised"])
def test_pending_owner_control_survives_answer_or_construction_error(tmp_path, monkeypatch, signal, answer):
    stopped = []
    monkeypatch.setattr(bench, "_repo", lambda *args: tmp_path)

    def finish(*args, **kwargs):
        if signal == "stop":
            stopped.append(True)
        else:
            component_process._WINDOW.get().deadline = time.monotonic() - 1
        if answer == "refusal":
            raise repo_tests.RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "no backend", isolation={"backend": None})
        if answer == "raised":
            raise ValueError("answer construction failed")
        # A genuine diagnostic report has no execution counters. The bench's
        # result projection may raise before reaching its normal return.
        return repo_tests._not_run_report("no test ran")

    monkeypatch.setattr(repo_tests, "run", finish)
    pair = (bench.COMPONENT, bench.IN_PLACE)
    contract = capabilities.load_contracts()[pair]
    worker = WorkerSpec(template="doer", purpose="Control publication", rung="deterministic",
                        allowed=(pair,), read_only=True,
                        policy=RuntimePolicy(max_runtime_seconds=10, max_cost_cents=0, max_shell_commands=0,
                                             max_files_modified=0, max_lines_changed=0,
                                             allowed_tools=frozenset({".".join(pair)})))
    result = calls.Door(worker, contracts=lambda *args: contract, run_id="publish-control",
                        invoke=calls.venture_invoke("v1"), stop_requested=lambda: bool(stopped)).call(
        *pair, {"repo": "fixture", "tests": ["test_fixture.py::test_example"]})
    assert result.raised == "timeout"
    assert result.reply is None and result.outcome.state == "blocked-escalated"
    assert ("operator requested stop" if signal == "stop" else "runtime allowance expired") in result.outcome.reasons[0]


def test_an_answer_construction_error_without_owner_control_keeps_its_class(monkeypatch):
    def broken(*args):
        raise ValueError("answer construction failed")

    monkeypatch.setattr(bench, "_in_place", broken)
    with component_process.window(10):
        with pytest.raises(ValueError, match="answer construction failed"):
            bench.run(bench.IN_PLACE, {})


def test_unconfirmed_cleanup_keeps_priority_over_pending_stop(monkeypatch):
    def broken(*args):
        raise ValueError("answer construction failed")

    monkeypatch.setattr(bench, "_in_place", broken)
    with component_process.window(10, stop_requested=lambda: True) as window:
        window.containment_failure = component_process.ContainmentFailure("cleanup unconfirmed")
        with pytest.raises(component_process.ContainmentFailure, match="cleanup unconfirmed"):
            bench.run(bench.IN_PLACE, {})
