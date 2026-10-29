# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A caller's tests run in place (plan 2026-10-05-verify-task-real-repos, Task 5).

Pinned with real pytest and git subprocesses on temp repositories: the counts are pytest's own tallies, never text
the tests print or a repository ``pytest.py``; a tracked file or the index changed during the run is named, whoever
changed it; an untracked file the run creates is not covered (a known limit); the tests see none of the caller's
environment and reach nothing off this machine unless allowed; a hung test and the process it started end at the time bound; a mutant is
imported in place of its file, which is never written; an import that fails here reads as not collected; a path with
spaces and non-ASCII letters runs; a request that cannot run is refused before anything runs. Offline.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from g6.core import component_process
from g6.workforce import repo_tests as rt

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("from calc import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
         "def test_sub():\n    assert sub(5, 3) == 2\n")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   check=True, capture_output=True, env=GIT_ENV)


def make_repo(root: Path, name: str = "calc") -> Path:
    repo = root / name
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(BUGGY, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def _write(repo: Path, rel: str, text: str) -> str:
    (repo / rel).write_text(text, encoding="utf-8")
    return rel


@pytest.mark.parametrize("signal", ["stop", "deadline"])
def test_snapshot_digest_checks_control_between_file_blocks(tmp_path, monkeypatch, signal):
    path = tmp_path / "large.bin"
    path.write_bytes(b"x" * ((1 << 20) + 1))
    real_open, reads, stopped = open, [], []

    class InterruptingReader:
        def __enter__(self):
            self.handle = real_open(path, "rb")
            return self

        def __exit__(self, *args):
            self.handle.close()

        def read(self, size):
            data = self.handle.read(size)
            reads.append(len(data))
            if signal == "stop":
                stopped.append(True)
            else:
                component_process._WINDOW.get().deadline = time.monotonic() - 1
            return data

    monkeypatch.setattr(rt, "open", lambda *args: InterruptingReader(), raising=False)
    with component_process.window(10, lambda: bool(stopped)), pytest.raises(component_process.ExecutionInterrupted):
        rt._digest(path)
    assert reads == [1 << 20], "Control must stop hashing before the next file block"


@pytest.mark.parametrize("signal", ["stop", "deadline", "expired"])
def test_git_child_obeys_the_enclosing_control(repo, tmp_path, monkeypatch, signal):
    marker = tmp_path / "git-entered"
    script = f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(2.5)"
    monkeypatch.setattr(rt, "_GIT", (sys.executable, "-c", script))
    started = time.monotonic()
    with component_process.window(0 if signal == "expired" else 1, marker.exists if signal == "stop" else None), \
            pytest.raises(component_process.ExecutionInterrupted):
        rt._git(repo, "status", timeout_s=5)
    assert time.monotonic() - started < 2
    assert marker.exists() is (signal != "expired")


def test_a_git_local_timeout_remains_a_command_refusal(repo, monkeypatch):
    monkeypatch.setattr(rt, "_GIT", (sys.executable, "-c", "import time; time.sleep(2.5)"))
    with pytest.raises(rt.RepoRefused) as caught:
        rt._git(repo, "status", timeout_s=.2)
    assert caught.value.code == "G6_E_GIT_COMMAND_FAILED"


def test_git_output_is_not_limited_by_the_registry_message_size(repo, monkeypatch):
    size = component_process.MAX_MESSAGE_BYTES + 1
    monkeypatch.setattr(rt, "_GIT", (sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {size})"))
    result = rt._git(repo, "show", timeout_s=5)
    assert len(result) == size and result[:1] == result[-1:] == b"x"


def test_cleanup_failure_cannot_be_cleared_by_a_later_git_window(repo, monkeypatch, tmp_path):
    marker = tmp_path / "later-child"
    monkeypatch.setattr(rt, "_GIT", (sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"))
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
    with component_process.window(5):
        with pytest.raises(component_process.ContainmentFailure):
            component_process.run_contained([sys.executable, "-c", "pass"], env=GIT_ENV)
        with pytest.raises(component_process.ContainmentFailure):
            rt._git(repo, "status")
    assert not marker.exists()


def test_known_answer_a_failing_test_is_counted_from_pytests_own_report(repo):
    found = rt.run(repo, ["tests/test_calc.py::test_add", "tests/test_calc.py::test_sub"])
    assert (found["verdict"], found["passed"], found["failed"], found["total"]) == (rt.FAILED, 1, 1, 2)
    assert found["changed"] == [] and found["said"] == ""


def test_a_fix_in_the_working_tree_passes_without_a_commit(repo):
    _write(repo, "calc.py", FIXED)
    found = rt.run(repo, ["tests/test_calc.py"])
    assert (found["verdict"], found["passed"], found["total"]) == (rt.PASSED, 2, 2)


def test_printed_counts_are_never_believed(repo):
    _write(repo, "tests/test_print.py", "def test_lies():\n    print('999 passed')\n    assert False\n")
    found = rt.run(repo, ["tests/test_print.py"])
    assert (found["verdict"], found["passed"], found["failed"]) == (rt.FAILED, 0, 1)


def test_a_pytest_module_in_the_repository_cannot_stand_in_for_the_runner(repo):
    _write(repo, "pytest.py", "print('999 passed')\nraise SystemExit(0)\n")
    assert rt.run(repo, ["tests/test_calc.py"])["verdict"] == rt.FAILED


@pytest.mark.usefixtures("unconfined")
def test_a_tracked_file_changed_during_the_run_is_named_whoever_changed_it(repo):
    """Review Focus 1: the run cannot tell the tests from another writer, so it says what changed, not who."""
    _write(repo, "tests/test_touch.py", "from pathlib import Path\n\n\ndef test_touch():\n"
                                        "    Path(__file__).parents[1].joinpath('calc.py').write_text('X = 1\\n')\n")
    found = rt.run(repo, ["tests/test_touch.py"])
    assert (found["verdict"], found["changed"], found["said"]) == (rt.CHANGED, ["calc.py"], rt.SAID["changed"])


@pytest.mark.usefixtures("unconfined")
def test_a_test_that_stages_a_file_changes_the_index_and_is_named(repo):
    _write(repo, "tests/test_stage.py", "import subprocess\nfrom pathlib import Path\n\n\ndef test_stage():\n"
                                        "    root = Path(__file__).parents[1]\n"
                                        "    root.joinpath('calc.py').write_text('X = 1\\n')\n"
                                        "    subprocess.run(['git', '-C', str(root), 'add', 'calc.py'], check=True)\n")
    found = rt.run(repo, ["tests/test_stage.py"])
    assert found["verdict"] == rt.CHANGED and "the staged files (the index)" in found["changed"]


@pytest.mark.usefixtures("unconfined")
def test_known_limit_a_file_git_does_not_track_is_not_covered(repo):
    _write(repo, "tests/test_scratch.py", "from pathlib import Path\n\n\ndef test_scratch():\n"
                                          "    Path(__file__).with_name('scratch.txt').write_text('x')\n")
    assert rt.run(repo, ["tests/test_scratch.py"])["verdict"] == rt.PASSED


@pytest.mark.usefixtures("unconfined")
def test_the_tests_see_none_of_the_callers_environment(repo, monkeypatch):
    monkeypatch.setenv("G6_REPO_TESTS_SECRET_PROBE", "secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-real-looking")
    _write(repo, "tests/test_env.py",
           "import os\n\n\ndef test_env():\n"
           "    assert 'G6_REPO_TESTS_SECRET_PROBE' not in os.environ\n"
           "    assert os.environ['OPENROUTER_API_KEY'] == 'sk-or-dummy-not-a-key'\n"
           "    assert os.environ['G6_LLM_BACKEND_OVERRIDE'] == 'ollama'\n"
           "    assert os.path.realpath(os.environ['HOME']) == os.path.realpath(os.environ['TMP'])\n"
           f"    assert os.path.realpath(os.environ['HOME']) != os.path.realpath({str(Path.home())!r})\n")
    assert rt.run(repo, ["tests/test_env.py"])["verdict"] == rt.PASSED


NETWORK_TEST = (
    "import socket\n\n\n"
    "def test_this_machine_is_reachable_and_nothing_else_is():\n"
    "    server = socket.socket()\n"
    "    server.bind(('127.0.0.1', 0))\n"
    "    server.listen(1)\n"
    "    client = socket.socket()\n"
    "    client.connect(server.getsockname())\n"
    "    client.close()\n"
    "    server.close()\n"
    "    outside = socket.socket()\n"
    "    try:\n"
    "        outside.connect(('0.0.0.0', 9))\n"
    "    except OSError as exc:\n"
    "        assert 'network connections are refused' in str(exc), exc\n"
    "    else:\n"
    "        raise AssertionError('a connection off this machine was allowed')\n"
    "    finally:\n"
    "        outside.close()\n")


def test_network_connections_are_refused_by_default_and_this_machine_stays_reachable(repo):
    """The control address 0.0.0.0 never leaves this machine, even when the guard is off."""
    _write(repo, "tests/test_net.py", NETWORK_TEST)
    assert rt.run(repo, ["tests/test_net.py"])["verdict"] == rt.PASSED
    assert rt.run(repo, ["tests/test_net.py"], network=True)["verdict"] == rt.FAILED  # the guard was what refused


PROXY_TEST = ("import os\n\n\ndef test_proxy():\n"
              "    assert os.environ.get('HTTPS_PROXY') == 'http://127.0.0.1:9'\n"
              "    assert os.environ.get('NO_PROXY') == 'localhost,127.0.0.1,::1'\n")


@pytest.mark.usefixtures("unconfined")
def test_programs_the_tests_start_are_pointed_at_no_proxy_unless_the_network_is_allowed(repo, monkeypatch):
    """The second half of the guard (ruling VR-R6): a program the tests start that honours a proxy reaches nothing."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")  # the caller's own proxy never crosses
    _write(repo, "tests/test_proxy.py", PROXY_TEST)
    assert rt.run(repo, ["tests/test_proxy.py"])["verdict"] == rt.PASSED
    assert rt.run(repo, ["tests/test_proxy.py"], network=True)["verdict"] == rt.FAILED


def test_confined_tests_receive_no_model_keys_or_proxy_configuration(repo, monkeypatch, bwrap_backend):
    monkeypatch.setenv("OPENROUTER_API_KEY", "planted-not-a-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    _write(repo, "tests/test_env.py",
           "import os\n\ndef test_env():\n"
           "    for key in ('OPENROUTER_API_KEY', 'HTTPS_PROXY', 'NO_PROXY', 'G6_LLM_BACKEND_OVERRIDE'):\n"
           "        assert key not in os.environ\n"
           "    assert os.path.realpath(os.environ['HOME']) == os.path.realpath(os.environ['TMP'])\n")
    result = rt.run(repo, ["tests/test_env.py"])
    assert result["verdict"] == rt.PASSED, result


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
def test_a_hung_test_and_the_process_it_started_end_at_the_time_bound(repo):
    """Review Focus 5."""
    _write(repo, "tests/test_hang.py",
           "import subprocess, sys, time\nfrom pathlib import Path\n\n\ndef test_hang():\n"
           "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
           "    Path(__file__).with_name('child.pid').write_text(str(child.pid))\n"
           "    time.sleep(120)\n")
    started = time.monotonic()
    found = rt.run(repo, ["tests/test_hang.py"], timeout_s=5)
    assert found["verdict"] == "timed_out" and time.monotonic() - started < 60
    assert found["said"] == rt.SAID["timed_out"].format(seconds=5)
    pid = int((repo / "tests" / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(pid)


def test_a_mutant_is_imported_in_place_of_its_file_and_the_file_is_never_written(repo):
    _write(repo, "calc.py", FIXED)
    before = (repo / "calc.py").read_bytes()
    found = rt.run(repo, ["tests/test_calc.py"], mutant=("calc.py", BUGGY))
    assert (found["verdict"], found["failed"], found["changed"]) == (rt.FAILED, 1, [])
    assert (repo / "calc.py").read_bytes() == before
    assert rt.run(repo, ["tests/test_calc.py"])["verdict"] == rt.PASSED


def test_a_test_that_cannot_import_here_reads_as_not_collected_never_as_failed(repo):
    """Review Focus 4."""
    _write(repo, "tests/test_dep.py", "import g6_missing_dependency_xyz\n\n\ndef test_x():\n    assert True\n")
    found = rt.run(repo, ["tests/test_dep.py"])
    assert found["verdict"] == "collection_error" and found["said"] == rt.SAID["collection_error"]


def test_a_repository_path_with_spaces_and_non_ascii_letters_runs(tmp_path):
    """Review Focus 2."""
    repo = make_repo(tmp_path, "a repo é")
    _write(repo, "calc.py", FIXED)
    assert rt.run(repo, ["tests/test_calc.py"])["verdict"] == rt.PASSED


def test_a_request_that_cannot_run_is_refused_before_anything_runs(repo):
    cases = [(lambda: rt.run(repo, []), "G6_E_INPUT_VALIDATION"),
             (lambda: rt.run(repo, ["../outside.py"]), "G6_E_INPUT_VALIDATION"),
             (lambda: rt.run(repo, ["-p", "evil"]), "G6_E_INPUT_VALIDATION"),
             (lambda: rt.run(repo, ["tests/test_missing.py"]), "G6_E_INPUT_VALIDATION"),
             (lambda: rt.run(repo, ["tests/test_calc.py"], roots=("/abs",)), "G6_E_INPUT_VALIDATION"),
             (lambda: rt.run("relative/path", ["tests/test_calc.py"]), "G6_E_INPUT_VALIDATION"),
             (lambda: rt.run(repo / "tests", ["test_calc.py"]), "G6_E_GIT_REPO_INVALID")]
    for call, code in cases:
        with pytest.raises(rt.RepoRefused) as refused:
            call()
        assert refused.value.code == code
    assert (repo / "calc.py").read_text(encoding="utf-8") == BUGGY


NO_TALLY = None
OK = {"passed": 2, "failures": 0, "errors": 0, "skipped": 0}


@pytest.mark.parametrize(("code", "counts", "output", "moved", "verdict"), [
    (0, OK, "", [], "passed"),
    (1, {"passed": 1, "failures": 1, "errors": 0, "skipped": 0}, "", [], "failed"),
    (0, {"passed": 0, "failures": 0, "errors": 0, "skipped": 2}, "", [], "inconsistent_report"),  # all skipped: no pass
    # the owner's ruling (2026-10-29), asymmetric on purpose: a non-zero exit after a session pytest counts as all
    # passed is a run that cannot be counted as a pass, not a sign of tampering (it cannot get a patch accepted)...
    (1, OK, "", [], "run_inconsistent"),
    (134, OK, "", [], "run_inconsistent"),
    (1, {**OK, "expected": 2, "finished": 2}, "", [], "run_inconsistent"),
    # ...while what a tampered runner would show to be accepted stays untrusted: exit 0 with a failure, an error, or
    # fewer tests finished than collected; and a non-zero exit with fewer finished, or with no pass, is not that case
    (0, {"passed": 1, "failures": 1, "errors": 0, "skipped": 0}, "", [], "inconsistent_report"),
    (0, {"passed": 1, "failures": 0, "errors": 1, "skipped": 0}, "", [], "inconsistent_report"),
    (0, {**OK, "expected": 3, "finished": 2}, "", [], "incomplete"),
    (1, {**OK, "expected": 3, "finished": 2}, "", [], "inconsistent_report"),
    (1, {"passed": 0, "failures": 0, "errors": 0, "skipped": 2}, "", [], "inconsistent_report"),
    (0, NO_TALLY, "", [], "inconsistent_report"),
    (1, NO_TALLY, "ModuleNotFoundError: No module named 'pytest'", [], "pytest_not_found"),
    (5, NO_TALLY, "", [], "no_tests_collected"),
    (2, {"passed": 0, "failures": 0, "errors": 1, "skipped": 0}, "", [], "collection_error"),
    (None, NO_TALLY, "", [], "timed_out"),
    (0, OK, "", ["calc.py"], "changed_tracked_files"),
])
def test_the_verdict_believes_only_an_exit_code_its_tallies_agree_with(code, counts, output, moved, verdict):
    assert rt._judge(code, counts, output, moved) == verdict


#: A process that ends with an error after pytest's session passed (as a GUI test suite's X connection can at exit).
EXITS_AFTER = "import atexit\nimport os\n\natexit.register(os._exit, 1)\n"


def test_a_run_that_exits_with_an_error_after_every_test_passed_is_not_counted_as_a_pass(repo):
    """The owner's ruling (2026-10-29): pytest's own tallies say every test passed, then the process exits 1."""
    _write(repo, "calc.py", FIXED)
    _write(repo, "conftest.py", EXITS_AFTER)
    found = rt.run(repo, ["tests/test_calc.py"])
    assert found["verdict"] == "run_inconsistent", (found["verdict"], found["output"][-500:])
    assert found["said"] == rt.SAID["run_inconsistent"]
    assert found["passed"] == 2 and found["failed"] == 0
    assert "passed by pytest's own count" in found["said"] and "exited with an error" in found["said"]


def test_the_repositorys_own_virtual_environment_runs_its_tests(tmp_path):
    repo = tmp_path / "r"
    exe = repo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert rt.interpreter(repo) == str(exe)
    assert rt.interpreter(tmp_path / "none") == sys.executable


def test_the_probe_repository_passes_and_reads_nothing_of_the_owners(tmp_path):
    assert rt.run(rt.probe_repo(tmp_path), [rt.PROBE_TEST])["verdict"] == rt.PASSED


# --- the fix pass after the independent review of Tasks 4-5 (I1-I8, M1-M3, M9) -----------------------------------

TEARDOWN_ERRS = ("import pytest\n\n\n@pytest.fixture\ndef res():\n    yield 1\n    raise RuntimeError('teardown broke')\n\n\n"
                 "def test_fails(res):\n    assert res == 2\n")


def test_the_run_never_asks_the_child_to_open_the_null_device(repo):
    """Windows CI (2026-10-26): an AppContainer there may not open the null device, which pytest's default ("fd") capture
    opens for standard input (test_truth_suite_runner.py says more); the boot turns that default into "sys" capture,
    which opens nothing. "sys" leaves what is written straight to file descriptor 1 uncaptured, so it reaches the run's
    output; a repository's own -s stays as it is (I6). pytest's log file, when none is named, is the null device too:
    the run names one in its own folder."""
    _write(repo, "tests/test_fd.py", "import logging\nimport os\n\n\ndef test_fd():\n    os.write(1, b'WRITTEN-TO-FD-1\\n')\n"
                                     "    null = os.path.abspath(os.devnull)\n"
                                     "    assert [h for h in logging.getLogger().handlers\n"
                                     "            if getattr(h, 'baseFilename', None) == null] == []\n")
    found = rt.run(repo, ["tests/test_fd.py"])
    assert found["verdict"] == rt.PASSED, found["output"]
    assert "WRITTEN-TO-FD-1" in found["output"], found["output"]


def test_a_log_file_the_repository_names_is_never_written_into_it(repo):
    """The run's log file is always its own: a run in place is read only, so a log file the repository names inside
    itself could not be opened confined (the run would end before a test ran) and would be written into the repository
    unconfined. The log file is pytest's side output, so the tests see nothing else change."""
    _write(repo, "pytest.ini", "[pytest]\nlog_file = own.log\n")
    _write(repo, "tests/test_log.py", "def test_log():\n    assert True\n")
    found = rt.run(repo, ["tests/test_log.py"])
    assert found["verdict"] == rt.PASSED, found["output"]
    assert not (repo / "own.log").exists()


def test_i1_a_failing_test_whose_teardown_also_errors_is_one_failed_test(repo):
    """I1 (probe P1): the report holds one testcase with a failure and an error; it is one test, and it failed."""
    _write(repo, "tests/test_td.py", TEARDOWN_ERRS)
    found = rt.run(repo, ["tests/test_td.py"])
    assert (found["verdict"], found["passed"], found["failed"], found["total"]) == (rt.FAILED, 0, 1, 1)


def test_i1_a_mixed_suite_is_counted_one_outcome_per_test(repo):
    """I1 (probe P1b): two passing tests and one that fails and errors in teardown are 2 passed, 1 failed."""
    _write(repo, "tests/test_td.py", TEARDOWN_ERRS + "\n\ndef test_ok1():\n    assert True\n\n\ndef test_ok2():\n"
                                                     "    assert True\n")
    found = rt.run(repo, ["tests/test_td.py"])
    assert (found["verdict"], found["passed"], found["failed"], found["total"]) == (rt.FAILED, 2, 1, 3)


def test_i2_a_syntax_error_in_the_changed_code_is_a_failure(repo):
    """I2 (probe P8): the file that does not compile is the repository's own, so the change failed its tests."""
    _write(repo, "calc.py", "def add(a, b)\n    return a + b\n")
    assert rt.run(repo, ["tests/test_calc.py"])["verdict"] == rt.FAILED


def test_i2_a_broken_import_of_a_repository_module_is_a_failure(repo):
    _write(repo, "tests/test_mul.py", "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n")
    assert rt.run(repo, ["tests/test_mul.py"])["verdict"] == rt.FAILED


def test_i2_a_failure_before_an_interruption_is_a_failure(repo):
    """I2 (probe P7): exit code 2 with a failure in the tallies."""
    _write(repo, "tests/test_kbd.py", "def test_a_fails():\n    assert False\n\n\ndef test_b_interrupt():\n"
                                      "    raise KeyboardInterrupt\n")
    found = rt.run(repo, ["tests/test_kbd.py"])
    assert (found["verdict"], found["passed"], found["failed"]) == (rt.FAILED, 0, 1)  # the interrupted test: no pass


STOPS_AT_0 = ("import pytest\n\n\ndef test_a_ok():\n    assert True\n\n\ndef test_b_stop():\n"
              "    pytest.exit('stop', returncode=0)\n\n\ndef test_c_fails():\n    assert False\n")


def test_i3_a_run_that_stops_part_way_with_exit_code_0_is_never_a_pass(repo):
    """I3 (probe P2): one of three collected tests reported, exit code 0."""
    _write(repo, "tests/test_exit.py", STOPS_AT_0)
    found = rt.run(repo, ["tests/test_exit.py"])
    assert found["verdict"] == "incomplete" and found["said"] == rt.SAID["incomplete"]
    assert found["passed"] == 1  # the test that called pytest.exit has an empty testcase: not a pass


def test_i3_a_last_test_that_ends_the_session_with_exit_code_0_is_never_a_pass(repo):
    """Every collected test has a testcase in the report, but the last one never finished."""
    _write(repo, "tests/test_exit.py", "import pytest\n\n\ndef test_a_ok():\n    assert True\n\n\ndef test_b_stop():\n"
                                       "    pytest.exit('stop', returncode=0)\n    assert False\n")
    found = rt.run(repo, ["tests/test_exit.py"])
    assert (found["verdict"], found["passed"]) == ("incomplete", 1)


def test_i3_deselected_and_module_skipped_tests_still_let_a_full_run_pass(repo):
    """The control for I3: a test the repository's own config deselects, and a module skipped as it is collected,
    are not "not reported"."""
    _write(repo, "calc.py", FIXED)
    _write(repo, "pytest.ini", "[pytest]\naddopts = --deselect=tests/test_calc.py::test_add\n")
    _write(repo, "tests/test_skipmod.py", "import pytest\n\npytest.importorskip('g6_missing_dep_abc')\n\n\n"
                                          "def test_x():\n    assert True\n")
    found = rt.run(repo, ["tests/test_calc.py", "tests/test_skipmod.py"])
    assert (found["verdict"], found["passed"], found["failed"]) == (rt.PASSED, 1, 0)


def test_i4_a_dependency_missing_here_imported_inside_the_code_under_test_reads_as_not_collected(repo):
    """I4 (probe P4): the only failure is an import of a module from outside the repository."""
    _write(repo, "lazy.py", "def frame():\n    import g6_missing_dep_abc\n    return g6_missing_dep_abc.x\n")
    _write(repo, "tests/test_lazy.py", "from lazy import frame\n\n\ndef test_frame():\n    assert frame() is not None\n")
    found = rt.run(repo, ["tests/test_lazy.py"])
    assert found["verdict"] == "collection_error" and found["said"] == rt.SAID["collection_error"]


def test_i4_a_real_failure_next_to_a_missing_dependency_is_still_a_failure(repo):
    _write(repo, "lazy.py", "def frame():\n    import g6_missing_dep_abc\n    return g6_missing_dep_abc.x\n")
    _write(repo, "tests/test_lazy.py", "from lazy import frame\n\n\ndef test_frame():\n    assert frame() is not None\n")
    assert rt.run(repo, ["tests/test_lazy.py", "tests/test_calc.py"])["verdict"] == rt.FAILED


@pytest.mark.skipif(subprocess.run([getattr(sys, "_base_executable", sys.executable), "-c", "import pytest"],
                                    capture_output=True).returncode != 0,
                    reason="environment: the base interpreter a --system-site-packages venv shares has no pytest")
def test_i4_the_run_says_which_python_ran_the_tests(repo, tmp_path):
    """G6's own interpreter, and the repository's own virtual environment (a real one, sharing G6's packages)."""
    found = rt.run(repo, ["tests/test_calc.py"])
    assert (found["interpreter_source"], found["python"]) == ("g6", sys.executable)
    # --copies: an environment linked to G6's own Python is refused (L1)
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", "--copies",
                    str(repo / ".venv")],
                   check=True, capture_output=True, timeout=180)
    found = rt.run(repo, ["tests/test_calc.py"])
    assert (found["verdict"], found["interpreter_source"]) == (rt.FAILED, "repository")
    assert Path(found["python"]).is_relative_to(repo / ".venv")


@pytest.mark.usefixtures("unconfined")
def test_i5_git_unreadable_after_the_run_is_a_change_never_a_refusal(repo):
    """I5 (probe S4): a test that damages the index leaves git unable to read the repository after the run."""
    _write(repo, "tests/test_w.py", "from pathlib import Path\n\n\ndef test_w():\n"
                                    "    Path(__file__).parents[1].joinpath('.git', 'index').write_bytes(b'garbage')\n")
    found = rt.run(repo, ["tests/test_w.py"])
    assert (found["verdict"], found["said"], found["changed"]) == (
        rt.CHANGED, rt.SAID["git_unreadable"], [rt.SAID["git_unreadable_name"]])


LEAVES_A_CHILD = ("import subprocess, sys\nfrom pathlib import Path\n\n\n"
                  "def test_starts_a_server_and_forgets_it():\n"
                  "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                  "    Path(__file__).with_name('left.pid').write_text(str(child.pid))\n")


def _ends(pid: int, within_s: float = 10) -> bool:
    deadline = time.monotonic() + within_s
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    return not _alive(pid)


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False, timeout=30)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


@pytest.mark.usefixtures("unconfined")
def test_i6_a_process_a_passing_test_leaves_running_ends_with_the_run(repo):
    """I6 (probe B2)."""
    _write(repo, "tests/test_left.py", LEAVES_A_CHILD)
    found = rt.run(repo, ["tests/test_left.py"], timeout_s=60)
    pid = int((repo / "tests" / "left.pid").read_text(encoding="utf-8"))
    try:
        assert found["verdict"] == rt.PASSED
        assert _ends(pid)
    finally:
        _kill(pid)


@pytest.mark.usefixtures("unconfined")
def test_i6_with_capture_off_a_leftover_process_neither_holds_the_run_nor_outlives_it(repo):
    """I6 (probe B3): the process holds the run's output; the run waits for pytest, not for its output to close."""
    _write(repo, "pytest.ini", "[pytest]\naddopts = -s\n")
    _write(repo, "tests/test_left.py", LEAVES_A_CHILD)
    started = time.monotonic()
    found = rt.run(repo, ["tests/test_left.py"], timeout_s=30)
    took = time.monotonic() - started
    pid = int((repo / "tests" / "left.pid").read_text(encoding="utf-8"))
    try:
        assert found["verdict"] == rt.PASSED and took < 20, (found["verdict"], took)
        assert _ends(pid)
    finally:
        _kill(pid)


def test_i6_the_output_kept_is_bounded_however_much_the_tests_print(repo):
    """I6 (probe M1): 40 MB printed; the caller's process never holds it."""
    import tracemalloc

    _write(repo, "pytest.ini", "[pytest]\naddopts = -s\n")
    _write(repo, "tests/test_big.py", "import sys\n\n\ndef test_big():\n    line = 'x' * 1023 + '\\n'\n"
                                      "    for _ in range(40 * 1024):\n        sys.stdout.write(line)\n")
    was = tracemalloc.is_tracing()
    if not was:
        tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        found = rt.run(repo, ["tests/test_big.py"], timeout_s=120)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        if not was:
            tracemalloc.stop()
    assert found["verdict"] == rt.PASSED
    assert len(found["output"].encode("utf-8")) < rt._RETURN_HEAD + rt._RETURN_TAIL + 200
    assert "bytes of output omitted" in found["output"]
    assert peak < 8 * 2**20, peak


@pytest.mark.usefixtures("unconfined")
def test_i7_an_untracked_file_that_existed_before_the_run_is_covered(repo):
    """I7 (probe S5): the change's own new module, rewritten by a test."""
    _write(repo, "newmod.py", "def f():\n    return 1\n")
    _write(repo, "tests/test_new.py", "from pathlib import Path\nimport newmod\n\n\ndef test_new():\n"
                                      "    assert newmod.f() == 1\n"
                                      "    Path(__file__).parents[1].joinpath('newmod.py').write_text('def f():\\n"
                                      "    return 2\\n')\n")
    found = rt.run(repo, ["tests/test_new.py"])
    assert (found["verdict"], found["changed"]) == (rt.CHANGED, ["newmod.py"])


SETS_A_FLAG = ("import subprocess\nfrom pathlib import Path\n\n\ndef test_w():\n"
               "    root = Path(__file__).parents[1]\n"
               "    subprocess.run(['git', '-C', str(root), 'update-index', '--FLAG', 'calc.py'], check=True)\n"
               "    root.joinpath('calc.py').write_text('X = 1\\n')\n")


@pytest.mark.usefixtures("unconfined")
def test_m4_a_file_the_owner_marked_assume_unchanged_is_still_covered(repo):
    """M4 (probe S1): git's diff skips the file, so its bytes are read directly."""
    _write(repo, "settings.py", "DEBUG = False\n")
    git(repo, "add", "settings.py")
    git(repo, "commit", "-q", "-m", "settings")
    git(repo, "update-index", "--assume-unchanged", "settings.py")
    _write(repo, "tests/test_w.py", "from pathlib import Path\n\n\ndef test_w():\n"
                                    "    Path(__file__).parents[1].joinpath('settings.py').write_text('DEBUG = True\\n')\n")
    found = rt.run(repo, ["tests/test_w.py"])
    assert (found["verdict"], found["changed"]) == (rt.CHANGED, ["settings.py"])


@pytest.mark.parametrize("flag", ["assume-unchanged", "skip-worktree"])
@pytest.mark.usefixtures("unconfined")
def test_m4_a_test_that_hides_a_file_from_git_and_rewrites_it_is_named(repo, flag):
    """M4 (probes S2, S3)."""
    _write(repo, "tests/test_w.py", SETS_A_FLAG.replace("FLAG", flag))
    found = rt.run(repo, ["tests/test_w.py"])
    assert found["verdict"] == rt.CHANGED and "calc.py" in found["changed"]


def test_m4_a_repository_holding_a_submodule_is_refused_before_anything_runs(repo, tmp_path):
    """M4 (probe S12) read a dirty submodule's own state; review B2-C1: git's diff of the outer repository runs git
    inside the submodule under the submodule's own config, which G6's settings never reach, so such a repository is
    refused (``G6_E_GIT_SUBMODULE_REFUSED``) and its tests are not run."""
    sub = make_repo(tmp_path, "subsrc")
    subprocess.run(["git", "-C", str(repo), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub),
                    "sub"], check=True, capture_output=True, env=GIT_ENV, timeout=60)
    git(repo, "commit", "-q", "-m", "sub")
    _write(repo, "tests/test_w.py", "from pathlib import Path\n\n\ndef test_w():\n"
                                    "    Path(__file__).parents[1].joinpath('ran').write_text('')\n")
    with pytest.raises(rt.RepoRefused) as caught:
        rt.run(repo, ["tests/test_w.py"])
    assert caught.value.code == "G6_E_GIT_SUBMODULE_REFUSED" and "sub" in str(caught.value)
    assert not (repo / "ran").exists()


XDIST_STUB = ("def pytest_addoption(parser):\n    group = parser.getgroup('xdist')\n"
              "    group._addoption('-n', '--numprocesses', dest='numprocesses', default=None)\n"  # as xdist registers it
              "    group.addoption('--dist', dest='dist', default='no')\n")


def test_i8_xdist_is_told_to_run_the_tests_in_process(repo):
    """I8, with a stub: real pytest-xdist is not installed here. A distribution in the repository declares the plugin
    pytest loads as ``xdist`` (as pytest-xdist's own does); the repository's config asks for two workers. Proves the
    options are added and win over the config; it cannot prove what real xdist does with them."""
    info = repo / "xdiststub-0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: xdiststub\nVersion: 0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text("[pytest11]\nxdist = xdiststub\n", encoding="utf-8")
    _write(repo, "xdiststub.py", XDIST_STUB)
    _write(repo, "pytest.ini", "[pytest]\naddopts = -n 2 --dist loadfile\n")
    _write(repo, "tests/test_inproc.py", "def test_in_process(request):\n"
                                         "    assert request.config.getoption('numprocesses') == '0'\n"
                                         "    assert request.config.getoption('dist') == 'no'\n")
    assert rt.run(repo, ["tests/test_inproc.py"])["verdict"] == rt.PASSED


def test_i8_a_folder_named_xdist_that_is_not_the_plugin_adds_no_option(repo):
    """``find_spec('xdist')`` finds a bare folder named xdist (this machine's site-packages has one); pytest would
    refuse ``-n0`` without the plugin, so the plugin's own entry point decides."""
    (repo / "xdist").mkdir()
    _write(repo, "xdist/notes.txt", "not a plugin\n")
    _write(repo, "calc.py", FIXED)
    assert rt.run(repo, ["tests/test_calc.py"])["verdict"] == rt.PASSED


def test_m2_a_control_character_anywhere_in_a_test_id_is_refused_never_a_bare_error(repo):
    """M2 (probe C4): a NUL after '::' reached the command line and raised ValueError."""
    for value in ("tests/test_calc.py::te\x00st", "tests/test_calc.py::test_add\n-p", "@tests/test_calc.py"):
        with pytest.raises(rt.RepoRefused) as refused:
            rt.run(repo, [value])
        assert refused.value.code == "G6_E_INPUT_VALIDATION"


def test_m3_a_parent_folders_pytest_config_and_conftest_are_never_read(tmp_path):
    """M3 (probe C7): the repository has no pytest config; the folder above it has one that deselects the failing
    test, and a conftest that would make every test pass."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "pytest.ini").write_text("[pytest]\naddopts = --deselect=tests/test_calc.py::test_add\n", encoding="utf-8")
    (outer / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n    outcome.get_result().outcome = 'passed'\n", encoding="utf-8")
    repo = make_repo(outer, "inner")
    found = rt.run(repo, ["tests/test_calc.py"])
    assert (found["verdict"], found["passed"], found["failed"]) == (rt.FAILED, 1, 1)


def test_m3_the_repositorys_own_config_in_a_test_folder_is_still_read(repo):
    """The control for M3: a config below the top, found from the tests named, is the repository's own (its folder
    is pytest's rootdir, so the id it deselects is relative to it)."""
    _write(repo, "tests/pytest.ini", "[pytest]\naddopts = --deselect=test_calc.py::test_add\n")
    found = rt.run(repo, ["tests/test_calc.py"])
    assert (found["verdict"], found["passed"]) == (rt.PASSED, 1)


@pytest.mark.parametrize("name", ["pytest.toml", ".pytest.toml"])
def test_c1_a_pytest_toml_is_the_repositorys_own_config_and_no_empty_one_replaces_it(repo, tmp_path, name):
    """Review C1: G6 did not know pytest.toml, so it read the folder as unconfigured and ran the tests with an empty
    config of its own in place of the repository's."""
    _write(repo, name, '[pytest]\naddopts = ["--deselect=tests/test_calc.py::test_add"]\n')
    assert rt._config_args(repo, ["tests/test_calc.py"], tmp_path) == []
    found = rt.run(repo, ["tests/test_calc.py"])
    assert (found["verdict"], found["passed"]) == (rt.PASSED, 1)


def test_i6_a_moved_test_file_is_replayed_at_its_old_path_at_the_base_commit(repo):
    """Review I6 (probe S4): the base commit has no file at the new path, so the base run of a moved test file found
    nothing to run, and a regression it showed on the patch read as a failure that predates the patch."""
    from dataclasses import replace

    _write(repo, "calc.py", FIXED)
    _write(repo, "tests/test_neg.py", "from calc import add\n\n\ndef test_neg():\n    assert add(-2, -3) == -5\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "the base: add sums, and a negative test")
    _write(repo, "calc.py", "def add(a, b):\n    return abs(a + b)\n\n\ndef sub(a, b):\n    return a - b\n")
    (repo / "tests" / ".moved").mkdir()
    git(repo, "mv", "tests/test_neg.py", "tests/.moved/test_neg.py")   # out of pytest's walk of tests/
    change = rt.change(repo)
    assert change.renamed == {"tests/test_neg.py": "tests/.moved/test_neg.py"}
    rel = "tests/.moved/test_neg.py"
    found = rt.run_before(replace(change, before={rel: (repo / rel).read_text(encoding="utf-8")}))[rel]
    assert found["verdict"] == rt.FAILED, found
    assert found["failed_ids"] == [f"{rel}::test_neg"]


def test_i9_a_run_says_the_root_folder_pytest_named_its_tests_against(repo):
    """Review I9(a): pytest names each test relative to its root folder, which a config below the top moves."""
    assert rt.run(repo, ["tests/test_calc.py"])["rootdir"] == ""
    _write(repo, "tests/pytest.ini", "[pytest]\n")
    found = rt.run(repo, ["tests/test_calc.py"])
    assert found["rootdir"] == "tests" and found["failed_ids"] == ["test_calc.py::test_add"]


def test_i8_tests_as_they_are_now_run_against_the_base_commits_code(repo):
    """Review I8: the stated checks, with the test files' text as it is now, against every other changed file as it
    was at the base commit, in a copy; the repository itself is not touched."""
    _write(repo, "calc.py", FIXED)
    _write(repo, "tests/test_calc.py", TESTS + "\n\ndef test_new():\n    assert add(1, 1) == 2\n")
    ids = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_sub", "tests/test_calc.py::test_new"]
    found = rt.run_at_base(rt.change(repo), ids)
    assert found["verdict"] == rt.FAILED, found
    assert sorted(found["failed_ids"]) == ["tests/test_calc.py::test_add", "tests/test_calc.py::test_new"]
    assert found["test_outcomes"]["tests/test_calc.py::test_sub"]["call"] == "passed"
    assert (repo / "calc.py").read_text(encoding="utf-8") == FIXED   # the patched tree is as it was


def test_m9_a_repository_path_with_the_path_separator_in_it_runs(tmp_path):
    """M9: the import path was one string joined with os.pathsep, so a ';' (':' on POSIX) in the path split it."""
    repo = make_repo(tmp_path, f"a{os.pathsep}b")
    _write(repo, "calc.py", FIXED)
    assert rt.run(repo, ["tests/test_calc.py"])["verdict"] == rt.PASSED


@pytest.mark.parametrize(("code", "counts", "verdict"), [
    (0, {**OK, "tests": 2, "finished": 2, "expected": 3}, "incomplete"),
    (0, {**OK, "tests": 2, "expected": None}, "incomplete"),  # the plugin's count could not be read
    (0, {**OK, "tests": 2, "finished": 2, "expected": 2}, "passed"),
    (2, {"passed": 0, "failures": 1, "errors": 0, "skipped": 0}, "failed"),
    (2, {"passed": 0, "failures": 0, "errors": 1, "skipped": 0, "collection": 1, "placed": 1}, "failed"),
    (2, {"passed": 0, "failures": 0, "errors": 1, "skipped": 0, "collection": 1, "placed": 1, "outside": 1},
     "collection_error"),
    (1, {"passed": 1, "failures": 1, "errors": 0, "skipped": 0, "outside": 1}, "collection_error"),
    (1, {"passed": 1, "failures": 1, "errors": 1, "skipped": 0, "outside": 1}, "failed"),
])
def test_the_verdict_after_the_review_fixes(code, counts, verdict):
    assert rt._judge(code, counts, "", []) == verdict
