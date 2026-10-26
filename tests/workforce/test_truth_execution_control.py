# SPDX-License-Identifier: Apache-2.0
"""Actual isolated pytest processes obey the enclosing operator's control window."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from g6.core import component_process
from g6.workforce import truth


def _repo(tmp_path, body):
    repo = tmp_path / "repo"
    tests = repo / "tests"
    tests.mkdir(parents=True)
    (tests / "test_control.py").write_text(
        "import time, sys, subprocess\nfrom pathlib import Path\n\ndef test_control():\n"
        + "\n".join("    " + line for line in body.splitlines()) + "\n", encoding="utf-8")
    return repo


def _run(kind, repo, timeout=6):
    if kind == "suite":
        return truth.run_suite(repo, timeout=timeout)["ran"]
    return truth.run_isolated("tests/test_control.py::test_control", repo, timeout=timeout)


@pytest.mark.parametrize("kind", ["isolated", "suite"])
def test_enclosing_deadline_bounds_the_actual_test_process(tmp_path, kind):
    entered = tmp_path / "entered"
    repo = _repo(tmp_path, f"Path({str(entered)!r}).touch()\ntime.sleep(2.5)")
    started = time.monotonic()
    with component_process.window(1), pytest.raises(TimeoutError):
        _run(kind, repo)
    assert entered.exists(), "The control must exercise a running test"
    assert time.monotonic() - started < 2.5


@pytest.mark.parametrize("kind", ["isolated", "suite"])
def test_operator_stop_interrupts_the_actual_test_process(tmp_path, kind):
    entered = tmp_path / "entered"
    repo = _repo(tmp_path, f"Path({str(entered)!r}).touch()\ntime.sleep(2.5)")
    started = time.monotonic()
    with component_process.window(5, stop_requested=entered.exists), pytest.raises(TimeoutError):
        _run(kind, repo)
    assert time.monotonic() - started < 2.5


@pytest.mark.parametrize("kind", ["isolated", "suite"])
@pytest.mark.parametrize("timeout", [False, True])
def test_owned_descendants_are_gone_after_success_or_timeout(tmp_path, kind, timeout):
    marker = tmp_path / "descendant"
    code = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).touch()"
    repo = _repo(tmp_path,
                 f"subprocess.Popen([sys.executable, '-c', {code!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                 + ("time.sleep(3)" if timeout else "assert True"))
    assert _run(kind, repo, timeout=1 if timeout else 5) is (not timeout)
    time.sleep(2.2)
    assert not marker.exists(), "An owned descendant outlived the completed test runner"


def test_expired_outer_window_cannot_start_a_test(tmp_path):
    marker = tmp_path / "entered"
    repo = _repo(tmp_path, f"Path({str(marker)!r}).touch()")
    with component_process.window(0), pytest.raises(TimeoutError):
        _run("isolated", repo)
    assert not marker.exists()
    assert truth.LAST_RUN["started"] is False


def test_stop_receipt_does_not_leak_into_the_next_completed_run(tmp_path):
    repo = _repo(tmp_path, "assert True")
    with component_process.window(5, lambda: True), pytest.raises(TimeoutError):
        _run("isolated", repo)
    assert truth.LAST_RUN["interrupted"] is True
    assert _run("isolated", repo)
    assert not truth.LAST_RUN.get("interrupted", False)


def test_native_door_reports_its_allowance_as_blocked_not_as_a_failed_test(monkeypatch):
    from g6.safety_engine.schema import RuntimePolicy
    from g6.workforce import bench, calls
    from g6.workforce.capabilities import ParamContract
    from g6.workforce.workers import WorkerSpec

    pair = ("workforce", "run_tests")
    contract = ParamContract.from_dict({"component": pair[0], "op": pair[1], "required": {"repo": "str", "tests": "list"},
                                        "probe_params": {"repo": bench.PROBE, "tests": [bench.PROBE_TEST]},
                                        "expect_field": "passed", "check": "input_model", "side_effect": "read", "model_free": True})
    worker = WorkerSpec(template="doer", purpose="bounded native call", rung="deterministic", allowed=(pair,), read_only=True,
                        policy=RuntimePolicy(max_runtime_seconds=1, max_cost_cents=0, max_shell_commands=0,
                                             max_files_modified=0, max_lines_changed=0, allowed_tools=frozenset({"workforce.run_tests"})))
    monkeypatch.setattr(bench, "_PROBE_FILES", {"tests/test_probe.py": "import time\ndef test_probe():\n    time.sleep(2.5)\n"})
    result = calls.Door(worker, contracts=lambda *args: contract, run_id="native-timeout").call(*pair, contract.probe_params)
    assert result.raised == "timeout"
    assert result.outcome.state == "blocked-escalated"
    assert result.reply is None


def test_nested_process_cleanup_is_recorded_on_the_calling_window(tmp_path):
    repo = _repo(tmp_path, "assert True")
    with component_process.window(5) as outer:
        assert _run("isolated", repo)
        assert _run("suite", repo)
    assert outer.execution["process_count"] == 2
    assert outer.execution["cleanup_confirmed"] is True
    assert outer.execution["child_exited"] is True


def test_unconfirmed_cleanup_cannot_be_returned_as_a_completed_test(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "assert True")
    real_tree = component_process._Tree
    def tree(*args, **kwargs):
        instance = real_tree(*args, **kwargs)
        kill = instance.kill
        def unconfirmed(**options):
            kill(**options)
            raise OSError("injected confirmation failure after actual cleanup")
        instance.kill = unconfirmed
        return instance
    monkeypatch.setattr(component_process, "_Tree", tree)
    with component_process.window(5) as outer, pytest.raises(component_process.ContainmentFailure):
        _run("isolated", repo)
    assert outer.execution["cleanup_confirmed"] is False


@pytest.mark.parametrize("kind", ["isolated", "suite"])
@pytest.mark.parametrize("signal", ["stop", "deadline"])
def test_completion_and_cleanup_cannot_hide_a_late_control_signal(tmp_path, monkeypatch, kind, signal):
    repo = _repo(tmp_path, "assert True")
    stopped = []
    real_tree = component_process._Tree
    with component_process.window(5, lambda: bool(stopped)) as outer:
        def tree(*args, **kwargs):
            instance = real_tree(*args, **kwargs)
            wait, kill = instance.proc.wait, instance.kill
            def waited(*args, **kwargs):
                result = wait(*args, **kwargs)
                if signal == "stop":
                    stopped.append(True)
                return result
            def cleaned(**kwargs):
                kill(**kwargs)
                if signal == "deadline":
                    outer.deadline = time.monotonic() - 1
                    component_process._WINDOW.get().deadline = outer.deadline
            instance.proc.wait, instance.kill = waited, cleaned
            return instance
        monkeypatch.setattr(component_process, "_Tree", tree)
        with pytest.raises(component_process.ExecutionInterrupted) as caught:
            _run(kind, repo)
    assert caught.value.stopped is (signal == "stop")
    assert outer.execution["cleanup_confirmed"] is True


@pytest.mark.parametrize("kind", ["regression"])  # "learned": deferred with coding_learning
@pytest.mark.parametrize("failure", ["timeout", "uncertain"])
def test_probe_door_preserves_execution_control_failures(tmp_path, monkeypatch, kind, failure):
    from g6.safety_engine.schema import RuntimePolicy
    from g6.workforce import calls
    from g6.workforce.capabilities import ParamContract
    from g6.workforce.workers import WorkerSpec

    source = tmp_path / "repos" / "diagnostic" / "probe"
    source.mkdir(parents=True)
    source.joinpath("logic.py").write_text(
        "def compute(x):\n" + ("    import time\n    time.sleep(2.5)\n" if failure == "timeout" else "")
        + "    return x\n", encoding="utf-8")
    monkeypatch.setenv("G6_WORKFORCE_DB_DIR", str(tmp_path))
    if failure == "uncertain":
        real_tree = component_process._Tree
        def tree(*args, **kwargs):
            instance = real_tree(*args, **kwargs)
            kill = instance.kill
            def unconfirmed(**options):
                kill(**options)
                raise OSError("injected confirmation failure after actual cleanup")
            instance.kill = unconfirmed
            return instance
        monkeypatch.setattr(component_process, "_Tree", tree)
    regression = {"replay": [{"module": "logic.py", "function": "compute", "inputs": [1]}]}
    params = {"repo": "probe", "tests": [], "files": {"logic.py": "def compute(x):\n    return x + 1\n"},
              "regression": regression}
    contract = ParamContract.from_dict(json.loads(Path(calls.__file__).with_name("params").joinpath(
        "workforce.run_tests.json").read_text()))
    worker = WorkerSpec(template="doer", purpose="Probe execution control", rung="deterministic",
                        allowed=(("workforce", "run_tests"),), read_only=True,
                        policy=RuntimePolicy(max_runtime_seconds=1 if failure == "timeout" else 5,
                                             max_cost_cents=0, max_shell_commands=0, max_files_modified=0,
                                             max_lines_changed=0, allowed_tools=frozenset({"workforce.run_tests"})))
    result = calls.Door(worker, contracts=lambda *args: contract, run_id="probe-control",
                        invoke=calls.venture_invoke("diagnostic")).call("workforce", "run_tests", params)
    assert result.raised == (calls.UNCONFIRMED if failure == "uncertain" else "timeout")
    assert result.outcome.state == "blocked-escalated" and result.reply is None
    assert result.cost.metadata["execution"]["cleanup_confirmed"] is (failure != "uncertain")
