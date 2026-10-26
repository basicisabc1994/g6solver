# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The census op of verify_task's repository mode, ``workforce.run_tests_in_place`` (plan
2026-10-05-verify-task-real-repos, Task 7; Gate W F-GW2).

Pinned: a declared, model-free, read-only op with exactly the keys the bench reads; its probe passes through the
single door on a repository it makes itself; it resolves a repository as run_tests does (a venture's by name, else only
the probe's); it runs the function verify_task runs; and its authority evidence: a known answer, a property over
generated suites (fixed seeds, Gate W ruling A-15; one outcome per test, review I1), and its edges, including the
verdicts the review of Tasks 4-6 added (a run that stops early with exit code 0 is ``incomplete``; git unreadable after
the run is a refusal that says so). Real git and pytest in temp folders; offline.
"""
from __future__ import annotations

import os
import random
import subprocess
import time
from pathlib import Path

import pytest
from g6.workforce import repo_tests as rt
from g6.workforce import store
from g6.workforce import bench
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("from calc import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
         "def test_sub():\n    assert sub(5, 3) == 2\n")
NODES = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_sub"]
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   check=True, capture_output=True, env=GIT_ENV)


def make_repo(root: Path, files: dict[str, str], name: str = "calc", venture: str = "v1") -> Path:
    repo = root / bench.REPOS / venture / name
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path))
    make_repo(tmp_path, {"calc.py": BUGGY, "tests/test_calc.py": TESTS})
    (tmp_path / bench.REPOS / "v1" / "plain").mkdir()  # a venture's folder git does not know (run_tests' kind)
    return tmp_path


def _run(**params) -> dict:
    return bench.run(bench.IN_PLACE, {"repo": "calc", "tests": NODES, **params}, venture="v1")


def _write(root: Path, rel: str, text: str) -> None:
    (root / bench.REPOS / "v1" / "calc" / rel).write_text(text, encoding="utf-8")


class _NoRegistry:
    """The bench is not in the registry: nothing here may be asked about it."""

    def list_all(self):
        return []

    def get_class(self, name):
        raise AssertionError("the registry was asked about a native capability")

    def describe(self, name):
        raise AssertionError("the registry was asked about a native capability")


@pytest.fixture
def caps(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    return wcap.Capabilities(vstore.for_owner("alice"), contracts=wcap.load_contracts(), registry=_NoRegistry())


def test_the_op_is_declared_model_free_and_read_only_with_the_keys_the_bench_reads():
    contract = wcap.load_contracts()[(bench.COMPONENT, bench.IN_PLACE)]
    assert contract.model_free is True and contract.side_effect == "read" and contract.expect_field == "passed"
    assert set(contract.required) | set(contract.optional) == bench.IN_PLACE_PARAMS
    assert bench.IN_PLACE in bench.OPS and "run_tests" in bench.OPS


def test_its_probe_passes_through_the_door_on_a_repository_it_makes(caps):
    assert caps.readiness(bench.COMPONENT, bench.IN_PLACE).level == "deps_installed"
    found = caps.probe(bench.COMPONENT, bench.IN_PLACE, wcall.prober(caps).reply, actor="code")
    assert found.level == "probe_passed", found.reason
    # A probe passes on any answer that carries "passed"; the probe's own test must also pass in its own repository.
    probe = wcap.load_contracts()[(bench.COMPONENT, bench.IN_PLACE)].probe_params
    assert bench.run(bench.IN_PLACE, dict(probe))["result"]["passed"] is True


def test_without_git_the_op_is_not_ready_and_says_why_while_run_tests_stays_ready(caps, monkeypatch):
    """The op reads git's view of the repository before and after the run, so with no git it cannot run at all: the
    ladder stops it below the probe with the words its own refusal uses (task #151), instead of reporting it ready and
    refusing every call. run_tests copies the repository and never calls git, so it stays ready (the control)."""
    find = bench.find_program
    monkeypatch.setattr(bench, "find_program", lambda name, *a, **k: None if name == "git" else find(name, *a, **k))

    found = caps.readiness(bench.COMPONENT, bench.IN_PLACE)

    assert (found.level, found.reason) == ("loads", rt.SAID["no_git"])
    assert caps.readiness(bench.COMPONENT, "run_tests").level == "deps_installed"


def test_known_answer_the_working_tree_is_tested_where_it_is(root):
    out = _run()
    assert out["result"]["passed"] is False and out["result"]["verdict"] == rt.FAILED
    assert out["result"]["counts"] == {"passed": 1, "failed": 1, "not_passed": 0, "total": 2}
    assert out["result"]["interpreter_source"] == "g6"  # no .venv in the repository: G6's Python ran the tests
    _write(root, "calc.py", FIXED)
    out = _run()
    assert out["result"]["passed"] is True and out["result"]["verdict"] == rt.PASSED
    assert out["result"]["counts"] == {"passed": 2, "failed": 0, "not_passed": 0, "total": 2}
    assert out["result"]["said"] == ""


@pytest.mark.parametrize("signal", ["stop", "deadline"])
@pytest.mark.usefixtures("unconfined")
def test_in_place_door_obeys_the_owner_control_during_execution(root, signal):
    from g6.safety_engine.schema import RuntimePolicy
    from g6.workforce.workers import WorkerSpec

    marker = root / "entered"
    _write(root, "tests/test_control.py", "import time\nfrom pathlib import Path\n\ndef test_control():\n"
           f"    Path({str(marker)!r}).touch()\n    time.sleep(3)\n")
    pair = (bench.COMPONENT, bench.IN_PLACE)
    contract = wcap.load_contracts()[pair]
    worker = WorkerSpec(template="doer", purpose="Bound repository execution", rung="deterministic",
                        allowed=(pair,), read_only=True,
                        policy=RuntimePolicy(max_runtime_seconds=2 if signal == "deadline" else 10,
                                             max_cost_cents=0, max_shell_commands=0, max_files_modified=0,
                                             max_lines_changed=0, allowed_tools=frozenset({".".join(pair)})))
    started = time.monotonic()
    result = wcall.Door(worker, contracts=lambda *args: contract, run_id="repository-control",
                        invoke=wcall.venture_invoke("v1"), stop_requested=marker.exists if signal == "stop" else None).call(
        *pair, {"repo": "calc", "tests": ["tests/test_control.py::test_control"], "timeout_s": 10})
    assert marker.exists(), "The control must reach the running test"
    assert time.monotonic() - started < 2.7
    assert result.raised == "timeout" and result.outcome.state == "blocked-escalated" and result.reply is None
    assert result.cost.metadata["execution"]["cleanup_confirmed"] is True


@pytest.mark.parametrize("signal", ["stop", "deadline"])
def test_in_place_door_checks_control_after_the_final_snapshot(root, monkeypatch, signal):
    from g6.core import component_process
    from g6.safety_engine.schema import RuntimePolicy
    from g6.workforce.workers import WorkerSpec

    _write(root, "calc.py", FIXED)
    real_digest, calls, stopped = rt._digest, [], []

    def digest_then_interrupt(path):
        value = real_digest(path)
        if path.name == "calc.py":
            calls.append(value)
            if len(calls) == 2:  # Genuine bytes of the last file in the final snapshot.
                if signal == "stop":
                    stopped.append(True)
                else:
                    component_process._WINDOW.get().deadline = time.monotonic() - 1
        return value

    monkeypatch.setattr(rt, "_digest", digest_then_interrupt)
    pair = (bench.COMPONENT, bench.IN_PLACE)
    contract = wcap.load_contracts()[pair]
    worker = WorkerSpec(template="doer", purpose="Control final repository evidence", rung="deterministic",
                        allowed=(pair,), read_only=True,
                        policy=RuntimePolicy(max_runtime_seconds=10, max_cost_cents=0, max_shell_commands=0,
                                             max_files_modified=0, max_lines_changed=0,
                                             allowed_tools=frozenset({".".join(pair)})))
    result = wcall.Door(worker, contracts=lambda *args: contract, run_id="repository-final-control",
                        invoke=wcall.venture_invoke("v1"), stop_requested=lambda: bool(stopped)).call(
        *pair, {"repo": "calc", "tests": NODES, "timeout_s": 10})

    assert len(calls) == 2 and calls[0] == calls[1]
    assert result.raised == "timeout" and result.outcome.state == "blocked-escalated" and result.reply is None
    assert result.cost.metadata["execution"]["cleanup_confirmed"] is True


#: A generated test's body by its outcome. "fail_teardown" fails and its fixture's teardown errors too: pytest reports
#: that test twice, and the run counts it once (review I1). A pass is drawn three times as often as each other outcome,
#: so some suites pass whole; the survival control below pins that both answers and every outcome occur.
BODIES = {"pass": "    assert True\n", "fail": "    assert False\n", "skip": "    pytest.skip('generated')\n",
          "fail_teardown": "    assert False\n"}
POOL = ("pass", "pass", "pass", "fail", "skip", "fail_teardown")
HEADER = "import pytest\n\n\n@pytest.fixture\ndef broken():\n    yield\n    raise RuntimeError('teardown')\n"
SEEDS = range(8)


def _suite(seed: int) -> list[str]:
    rng = random.Random(seed)
    return [rng.choice(POOL) for _ in range(rng.randint(1, 6))]


def _truth(kinds: list[str]) -> dict[str, int]:
    passed, failed = kinds.count("pass"), kinds.count("fail") + kinds.count("fail_teardown")
    return {"passed": passed, "failed": failed, "not_passed": kinds.count("skip"), "total": passed + failed}


def test_the_generated_suites_hold_every_outcome_and_both_answers():
    """The survival control for the property: its seeds are not blind to an outcome or to an answer."""
    suites = [_suite(seed) for seed in SEEDS]
    assert set().union(*suites) == set(BODIES)
    assert {_truth(kinds)["failed"] == 0 and _truth(kinds)["passed"] > 0 for kinds in suites} == {True, False}


@pytest.mark.parametrize("seed", SEEDS)
def test_property_the_counts_are_the_generated_suites_truth(root, seed):
    kinds = _suite(seed)
    body = HEADER + "".join(f"\n\ndef test_{i}({'broken' if kind == 'fail_teardown' else ''}):\n{BODIES[kind]}"
                            for i, kind in enumerate(kinds))
    make_repo(root, {"tests/test_gen.py": body}, name=f"gen{seed}")
    out = bench.run(bench.IN_PLACE, {"repo": f"gen{seed}", "tests": ["tests/test_gen.py"]}, venture="v1")
    truth = _truth(kinds)
    assert out["result"]["counts"] == truth, kinds
    assert out["result"]["passed"] is (truth["failed"] == 0 and truth["passed"] > 0), kinds


@pytest.mark.parametrize(("params", "why"), [
    ({"tests": []}, "name at least one test"),
    ({"tests": ["../x.py"]}, "inside the repository"),
    ({"tests": ["-p"]}, "inside the repository"),
    ({"tests": ["tests/test_missing.py"]}, "does not exist"),
    ({"roots": ["/abs"]}, "inside the repository"),
    ({"repo": "../calc"}, "repository name"),
    ({"repo": "missing"}, "no repository"),
    ({"repo": "plain"}, rt.SAID["not_git"]),
])
def test_edge_a_request_that_cannot_run_is_refused(root, params, why):
    out = _run(**params)
    assert out["ok"] is False and why in out["error"]


@pytest.mark.usefixtures("unconfined")
def test_edge_a_run_that_changes_a_tracked_file_is_refused_with_the_path(root):
    _write(root, "tests/test_touch.py", "from pathlib import Path\n\n\ndef test_touch():\n"
                                        "    Path(__file__).parents[1].joinpath('calc.py').write_text('X = 1\\n')\n")
    out = _run(tests=["tests/test_touch.py"])
    assert out["ok"] is False and "calc.py" in out["error"] and rt.SAID["changed"] in out["error"]


@pytest.mark.usefixtures("unconfined")
def test_edge_git_unreadable_after_the_run_is_refused_saying_so(root):
    """Review I5: a test that damages the index leaves git unable to say what the run changed."""
    _write(root, "tests/test_w.py", "from pathlib import Path\n\n\ndef test_w():\n"
                                    "    Path(__file__).parents[1].joinpath('.git', 'index').write_bytes(b'garbage')\n")
    out = _run(tests=["tests/test_w.py"])
    assert out["ok"] is False and rt.SAID["git_unreadable"] in out["error"]
    assert rt.SAID["changed"] not in out["error"]


def test_edge_a_run_that_stops_early_with_exit_code_0_is_never_a_pass(root):
    """Review I3: one of three collected tests ran to its end, and pytest still exited 0."""
    _write(root, "tests/test_exit.py", "import pytest\n\n\ndef test_a_ok():\n    assert True\n\n\ndef test_b_stop():\n"
                                       "    pytest.exit('stop', returncode=0)\n\n\ndef test_c_fails():\n    assert False\n")
    out = _run(tests=["tests/test_exit.py"])
    assert out["ok"] is True and out["result"]["passed"] is False
    assert (out["result"]["verdict"], out["result"]["said"]) == ("incomplete", rt.SAID["incomplete"])


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             check=False).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie has ended: it waits only to be collected, which a container's first process may never do
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


@pytest.mark.usefixtures("unconfined")
def test_edge_a_hung_test_ends_at_its_time_bound(root):
    """Review Focus 5: a test that starts a child process and hangs; the time bound ends both."""
    _write(root, "tests/test_hang.py",
           "import subprocess, sys, time\nfrom pathlib import Path\n\n\ndef test_hang():\n"
           "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
           "    Path(__file__).with_name('child.pid').write_text(str(child.pid))\n"
           "    time.sleep(120)\n")
    started = time.monotonic()
    out = _run(tests=["tests/test_hang.py"], timeout_s=5)
    assert time.monotonic() - started < 60
    assert out["result"]["passed"] is False and out["result"]["verdict"] == "timed_out"
    assert out["result"]["said"] == rt.SAID["timed_out"].format(seconds=5)
    pid = int((root / bench.REPOS / "v1" / "calc" / "tests" / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(pid)


def test_edge_without_a_venture_only_the_probe_runs(root):
    assert "venture" in bench.run(bench.IN_PLACE, {"repo": "calc", "tests": NODES})["error"]
    assert bench.run(bench.IN_PLACE, {"repo": bench.PROBE, "tests": [rt.PROBE_TEST]})["result"]["passed"] is True


def test_the_op_runs_the_function_verify_task_runs(root, monkeypatch):
    seen, real = [], rt.run
    monkeypatch.setattr(rt, "run", lambda *a, **k: seen.append(list(a[1])) or real(*a, **k))
    _run()
    assert seen == [NODES]
