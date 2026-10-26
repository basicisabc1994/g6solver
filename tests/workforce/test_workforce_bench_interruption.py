# SPDX-License-Identifier: Apache-2.0
"""Fault injection at the native bench boundary, supplemented by real MCP timeout controls."""
from __future__ import annotations

import pytest
from g6.core import component_process
from g6.workforce import truth
from g6.workforce import bench

NODES = ["tests/test_calc.py::test_one", "tests/test_calc.py::test_two"]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path))
    repo = tmp_path / "repos" / "owner" / "calc"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text("x = 0\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(
        "def test_one():\n    assert True\ndef test_two():\n    assert True\n", encoding="utf-8")
    return repo


def invoke():
    return bench.run("run_tests", {"repo": "calc", "tests": NODES, "files": {"calc.py": "x = 1\n"}},
                     venture="owner")


def timed_out(*args):
    raise component_process.ExecutionInterrupted("The worker runtime allowance expired during execution")


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("phase", ["named_tests", "suite"])
def test_partial_observations_never_certify_an_incomplete_check(setup, monkeypatch, failed, phase):
    calls = []

    def named(node, repo):
        calls.append(node)
        if phase == "named_tests" and node == NODES[1]:
            timed_out()
        return not failed

    monkeypatch.setattr(truth, "run_isolated", named)
    monkeypatch.setattr(bench, "_suite", timed_out)
    out = invoke()
    assert out["ok"] and out["degraded"]
    result = out["result"]
    assert result["tests"] == {n: not failed for n in NODES[:1 if phase == "named_tests" else 2]}
    assert result["not_run"] == (NODES[1:] if phase == "named_tests" else [])
    assert result["interruption"]["phase"] == phase
    assert result["suite"]["status"] == "not_run"
    if failed:
        assert result["passed"] is False
    else:
        assert "passed" not in result, "A missing check is neither a pass nor an observed failure"


@pytest.mark.parametrize("kind", ["stop", "cleanup"])
def test_stop_and_unconfirmed_cleanup_do_not_become_partial_answers(setup, monkeypatch, kind):
    monkeypatch.setattr(truth, "run_isolated", lambda *args: False)
    error = (component_process.ExecutionInterrupted("operator stop", stopped=True) if kind == "stop"
             else component_process.ContainmentFailure("cleanup unconfirmed"))

    def interrupted(*args):
        raise error

    monkeypatch.setattr(bench, "_suite", interrupted)
    with pytest.raises(type(error)):
        invoke()


@pytest.mark.parametrize("target", ["source", "test_copy", "deleted_test", "added_test"])
def test_integrity_failure_discards_partial_findings(setup, monkeypatch, target):
    monkeypatch.setattr(truth, "run_isolated", lambda *args: False)

    def altered(source, copy):
        path = source / "calc.py" if target == "source" else copy / "tests" / "test_calc.py"
        if target == "deleted_test":
            path.unlink()
        else:
            if target == "added_test":
                path = copy / "tests" / "test_added.py"
            path.write_text("# tampered\n", encoding="utf-8")
        timed_out()

    monkeypatch.setattr(bench, "_suite", altered)
    out = invoke()
    assert out["ok"] is False and "result" not in out


def test_no_completed_observations_preserves_timeout_control(setup, monkeypatch):
    monkeypatch.setattr(truth, "run_isolated", timed_out)
    with pytest.raises(component_process.ExecutionInterrupted):
        invoke()


def test_long_failure_lists_cannot_hide_missing_work_from_repair_message():
    from types import SimpleNamespace

    from g6.workforce import calls, contracts, plans, rungs

    partial = {"passed": False, "tests": {f"tests/test_{i}_{'long' * 40}.py::test_one": False for i in range(20)},
               "suite": {"status": "not_run"}, "not_run": [],
               "interruption": {"phase": "suite", "reason": "Remaining checks were not completed."}}
    partial["causes"] = {node: "the test process exited before reporting" for node in partial["tests"]}
    reply = {"ok": True, "result": partial, "degraded": True}
    door = SimpleNamespace(worker=SimpleNamespace(read_only=True), call=lambda c, o, p: calls.CallResult(
        c, o, contracts.Outcome.from_envelope(reply, source=f"{c}.{o}"), reply))
    spec = plans.UnitSpec(key="check", kind="verify", goal="check", verifies="patch",
                          capabilities=(("workforce", "run_tests"),), oracle={
                              "capability": ["workforce", "run_tests"], "bind": {"files": "patch.files"},
                              "expect": {"field": "passed", "equals": True}, "kind": "deterministic"})
    found = rungs.run_verify(spec, door, instance_id="checker", produced={
        "artifact_id": "patch-1", "producer": "worker", "sha256": "0" * 64, "content": {"files": {}},
        "provenance": {"call": None, "inputs": {}}}, inputs={}, contract=None, test_provenance="caller")
    message = "; ".join(found.outcome.reasons)[:2000]  # existing plain repair limit
    assert partial["interruption"]["reason"] in message
    assert found.verdict.verdict.result == "fail"
