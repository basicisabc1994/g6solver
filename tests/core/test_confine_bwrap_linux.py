# SPDX-License-Identifier: Apache-2.0
"""What a run confined by bubblewrap can reach on Linux (``g6.core.confine``), probed from inside the child: a key file
and a file planted outside the run folder, the network, the folders it may write, a grandchild that leaves the run's
session, and a repository's git folder. Each needs bubblewrap able to confine a child of this interpreter
(``bwrap_backend`` in ``tests/conftest.py``): elsewhere they skip, and on CI's Linux jobs, where
``G6_EXPECT_BACKEND=bubblewrap``, they fail instead. Offline: the only network is this machine's."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from g6.core import component_process, confine, sandbox
from g6.workforce import repo_tests


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))


def _probe(path: Path) -> str:
    return textwrap.dedent(f"""
        try:
            print('READ', open({str(path)!r}).read())
        except OSError as exc:
            print('DENIED', type(exc).__name__)
    """)


def test_a_planted_key_file_and_a_file_outside_the_run_folder_stay_hidden(bwrap_backend, tmp_path,
                                                                          monkeypatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "stand-in-g6-home"))
    key = tmp_path / "stand-in-g6-home" / ".env"   # a stand-in for the owner's key file, never the real one
    sentinel = tmp_path / "outside" / "sentinel.txt"
    for path in (key, sentinel):
        path.parent.mkdir(parents=True)
        path.write_text("G6_TEST_SENTINEL=not-a-key\n", encoding="utf-8")
    for path in (key, sentinel):
        run = sandbox.run_script(_probe(path), timeout_s=60)
        # fails if sandbox.run_script's child is not started inside bubblewrap, or bubblewrap binds the path
        assert run.status == "ok" and run.stdout.startswith("DENIED FileNotFoundError"), run.stdout + run.stderr
        assert (run.isolation["backend"], run.isolation["why"]) == (confine.BUBBLEWRAP, "") and run.limits["os_sandbox"]
    _off(monkeypatch)
    control = sandbox.run_script(_probe(sentinel), timeout_s=60)   # the same probe, unconfined, reads it
    assert control.stdout.startswith("READ G6_TEST_SENTINEL"), control.stdout + control.stderr


def test_the_network_is_blocked_even_through_the_c_socket(bwrap_backend, monkeypatch) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    script = textwrap.dedent(f"""
        import _socket
        for address in (('127.0.0.1', {port}), ('192.0.2.1', 80)):
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            s.settimeout(5)
            try:
                s.connect(address)
                print('CONNECTED', address[0])
            except OSError as exc:
                print('REFUSED', address[0], type(exc).__name__)
    """)
    try:
        run = sandbox.run_script(script, timeout_s=60)
        with pytest.raises(OSError):   # nothing reached the listener
            listener.accept()
        # fails if the child shares G6's network namespace: its loopback is a private one with nothing listening, and
        # it has no route anywhere else
        lines = run.stdout.splitlines()
        assert lines[0].startswith("REFUSED 127.0.0.1") and lines[1].startswith("REFUSED 192.0.2.1"), run.stdout
        assert run.isolation["backend"] == confine.BUBBLEWRAP
        _off(monkeypatch)
        control = sandbox.run_script(script, timeout_s=60)   # Python's own guard allows this machine
        assert control.stdout.startswith("CONNECTED 127.0.0.1"), control.stdout + control.stderr
    finally:
        listener.close()


def test_only_the_run_folder_is_writable(bwrap_backend, tmp_path) -> None:
    target = tmp_path / "outside" / "planted.txt"
    target.parent.mkdir()
    script = textwrap.dedent(f"""
        for path in ({str(target)!r}, '/usr/g6-planted', '/g6-planted', '/tmp/g6-planted', 'mine.txt'):
            try:
                open(path, 'w').write('planted')
                print('WROTE', path)
            except OSError as exc:
                print('DENIED', path, type(exc).__name__)
    """)
    run = sandbox.run_script(script, timeout_s=60)
    said = dict(line.split(" ", 2)[1::-1] for line in run.stdout.splitlines())
    # fails if anything outside the run's own folder can be written from inside bubblewrap (the root is remounted read
    # only, /usr is bound read only, the host's /tmp and the caller's folders are not there at all)
    assert said == {str(target): "DENIED", "/usr/g6-planted": "DENIED", "/g6-planted": "DENIED",
                    "/tmp/g6-planted": "DENIED", "mine.txt": "WROTE"}, run.stdout + run.stderr
    assert run.outputs == {"mine.txt": b"planted"} and not target.exists()
    assert not Path("/tmp/g6-planted").exists() and not Path("/usr/g6-planted").exists()


def test_a_grandchild_that_leaves_the_session_is_killed_when_the_run_times_out(bwrap_backend) -> None:
    grandchild = ("import os, time\nos.setsid()\n"
                  "while True:\n    open('beat.txt', 'a').write('.')\n    time.sleep(0.05)\n")
    script = (f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-I', '-c', {grandchild!r}])\n"
              "time.sleep(120)\n")
    with confine.run_folder("g6_test_session_") as folder:
        plan = confine.plan(confine.Confinement(read_write=(folder,), read_only=confine.interpreter_paths(sys.executable)),
                            sys.executable)
        assert plan.backend == confine.BUBBLEWRAP, plan.why
        beat = Path(folder, "beat.txt")
        with pytest.raises(component_process.ExecutionInterrupted), component_process.window(3):
            component_process.run_contained([sys.executable, "-I", "-c", script], env={}, cwd=folder, confined=plan)
        assert beat.exists(), "the grandchild never started"
        time.sleep(1.0)
        size = beat.stat().st_size
        time.sleep(1.0)
        # fails if the grandchild, which left the run's session (so the process tree's kill of the session's group
        # misses it), outlives the run: bubblewrap's process namespace ends with the run
        assert beat.stat().st_size == size, "the grandchild is still writing after the run was killed"


GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH",) if key in os.environ}}
WRITES = textwrap.dedent("""
    import pathlib
    import subprocess

    ROOT = pathlib.Path(__file__).resolve().parents[1]


    def test_the_repository_and_its_git_folder_cannot_be_written():
        refused = []
        for path in (ROOT / ".git" / "planted", ROOT / "planted.txt"):
            try:
                path.write_text("planted")
            except OSError:
                refused.append(path.name)
        done = subprocess.run(["git", "-C", str(ROOT), "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                               "--allow-empty", "-q", "-m", "planted"], capture_output=True)
        assert refused == ["planted", "planted.txt"] and done.returncode != 0
""")


def test_git_is_read_only_inside_a_repositorys_tests_and_g6s_own_git_reads_it_after(bwrap_backend, tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_writes.py").write_text(WRITES, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("commit", "-q", "-m", "base")):
        subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                       env=GIT_ENV)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          env=GIT_ENV).stdout
    found = repo_tests.run(repo, ["tests/test_writes.py"])
    # fails if the repository's tests run outside bubblewrap or with the repository writable: a test (or a git command
    # it runs) could then write into the repository and its git folder
    assert found["isolation"]["backend"] == confine.BUBBLEWRAP, found["isolation"]
    assert found["verdict"] == "passed" and found["passed"] == 1, found["output"]
    assert not (repo / ".git" / "planted").exists() and not (repo / "planted.txt").exists()
    # G6's own git calls run in the parent, after the run: they read the repository and found nothing changed
    assert found["changed"] == []
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          env=GIT_ENV).stdout == head
