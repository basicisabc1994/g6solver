# SPDX-License-Identifier: Apache-2.0
"""What a run confined by bubblewrap can reach on Linux (``g6.core.confine``), probed from inside the child: a key file
and a file planted outside the run folder, the network, the folders it may write, a grandchild that leaves the run's
session, and a repository's git folder. Each needs bubblewrap able to confine a child of this interpreter
(``bwrap_backend`` in ``tests/conftest.py``): elsewhere they skip, and on CI's Linux jobs, where
``G6_EXPECT_BACKEND=bubblewrap``, they fail instead. Offline: the only network is this machine's."""
from __future__ import annotations

import ast
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from g6.core import component_process, confine, sandbox
from g6.workforce import repo_tests


def test_parent_never_collects_a_child_symlink_to_denied_host_data(bwrap_backend, tmp_path):
    sentinel = tmp_path / "host-only.txt"
    sentinel.write_text("PLANTED_SENTINEL_NOT_A_SECRET")
    code = f"""import os
try:
    open({str(sentinel)!r}).read()
    print('UNEXPECTED_ACCESS')
except OSError:
    print('DIRECT_READ_DENIED')
os.symlink({str(sentinel)!r}, 'returned.txt')
"""
    result = sandbox.run_script(code)
    assert "DIRECT_READ_DENIED" in result.stdout
    assert result.isolation["backend"] == "bubblewrap"
    assert "returned.txt" not in result.outputs
    assert result.status == "error" and "G6_E_UNSAFE_OUTPUT" in result.stderr


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead


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
    # fails if anything outside the run's own folders can be written from inside bubblewrap (the root is remounted read
    # only, /usr is bound read only, the caller's folders are not there at all, and the host's /tmp is not there; the
    # child has a private one of its own)
    assert said == {str(target): "DENIED", "/usr/g6-planted": "DENIED", "/g6-planted": "DENIED",
                    "/tmp/g6-planted": "WROTE", "mine.txt": "WROTE"}, run.stdout + run.stderr
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


def test_spawn_boundary_removes_secrets_even_from_explicit_environment(bwrap_backend, monkeypatch):
    monkeypatch.setenv("G6_PLANTED_SECRET", "not-a-real-key")
    with confine.run_folder("g6_test_env_") as folder:
        plan = confine.plan(confine.Confinement(read_write=(folder,)), sys.executable)
        try:
            child = confine.popen(plan, [sys.executable, "-I", "-c",
                                        "import os; print('G6_PLANTED_SECRET' in os.environ)"],
                                 env=dict(os.environ), cwd=folder, stdout=subprocess.PIPE)
            stdout, _ = child.communicate(timeout=30)
            assert child.returncode == 0 and stdout.strip() == b"False"
        finally:
            plan.release()


WRITES = textwrap.dedent("""
    import pathlib
    import subprocess

    ROOT = pathlib.Path(__file__).resolve().parents[1]


    def test_the_repository_and_its_git_folder_cannot_be_written():
        home = pathlib.Path.home()
        (home / "scratch-home-sentinel").write_text("not a secret")
        assert (home / "scratch-home-sentinel").read_text() == "not a secret"
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


# --- the child's own /tmp and hosts file ---------------------------------------------------------------------------------

def _confined(script: str, *args: str, network: bool = False, folder: str | None = None,
              read_only: tuple[str, ...] = ()) -> tuple[int, str, str]:
    """``script`` run once by this interpreter inside bubblewrap (``confine.popen``), in a run folder of its own or
    ``folder``: its exit code, output and errors. The plan is released after it, as every caller in G6 does."""
    with contextlib.ExitStack() as stack:
        cwd = folder or stack.enter_context(confine.run_folder("g6_test_tmp_"))
        plan = confine.plan(confine.Confinement(read_write=(cwd,), network=network,
                                                read_only=(*confine.interpreter_paths(sys.executable), *read_only)),
                            sys.executable)
        assert plan.backend == confine.BUBBLEWRAP, plan.why
        try:
            child = confine.popen(plan, [sys.executable, "-I", "-c", script, *args], env={}, cwd=cwd,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = child.communicate(timeout=60)
        finally:
            assert plan.release() == ""
        return child.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _mount_points(*paths: str) -> list[str]:
    """What a child's /tmp holds when it starts: the first folder under /tmp of each path it is given, nothing else."""
    return sorted({Path(path).relative_to("/tmp").parts[0] for path in paths
                   if Path(path).is_relative_to("/tmp") and Path(path) != Path("/tmp")})


def _private_folders() -> set[str]:
    """The children's own folders G6 has made and not removed yet, where it makes them."""
    place = confine._private_place(())
    assert place, "nowhere for a child's own folder"
    return {entry.name for entry in os.scandir(place) if entry.name.startswith("g6_child_")}


X11 = textwrap.dedent("""
    import os, socket, stat
    os.makedirs('/tmp/.X11-unix')
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind('/tmp/.X11-unix/X99')
    server.listen(1)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect('/tmp/.X11-unix/X99')
    peer, _ = server.accept()
    client.sendall(b'ready')
    print(peer.recv(5).decode(), stat.S_ISSOCK(os.stat('/tmp/.X11-unix/X99').st_mode))
""")


def test_a_child_can_listen_on_a_unix_socket_in_its_own_tmp_as_xvfb_does(bwrap_backend) -> None:
    there = os.path.exists("/tmp/.X11-unix/X99")
    code, out, err = _confined(X11)
    # fails if the child has no writable /tmp of its own: Xvfb, which pytest-xvfb starts for every run, makes
    # /tmp/.X11-unix/X99 and listens on it
    assert (code, out.split()) == (0, ["ready", "True"]), err
    assert os.path.exists("/tmp/.X11-unix/X99") == there   # nothing of it reached the host's /tmp


def test_the_childs_tmp_is_owner_only_and_what_one_run_writes_there_is_gone_for_the_next(bwrap_backend) -> None:
    listing = "import os\nprint(oct(os.stat('/tmp').st_mode & 0o777), sorted(os.listdir('/tmp')))\n"
    reads = confine.interpreter_paths(sys.executable)
    with confine.run_folder("g6_test_first_") as first, confine.run_folder("g6_test_second_") as second:
        code, out, err = _confined("open('/tmp/left-by-the-first-run.txt', 'w').write('x')\n" + listing, folder=first)
        assert code == 0 and "left-by-the-first-run.txt" in out, err   # it could write in its /tmp
        code, out, err = _confined(listing, folder=second)
    mode, _, names = out.strip().partition(" ")
    # fails if two runs share a /tmp, or a child's /tmp holds anything but the mount points of its own declared
    # folders when it starts, or others may open it
    assert code == 0 and mode == "0o700", (out, err)
    assert ast.literal_eval(names) == _mount_points(second, *reads), (names, err)


def test_a_file_in_the_hosts_tmp_is_not_there_for_the_child(bwrap_backend) -> None:
    with tempfile.NamedTemporaryFile(dir="/tmp", prefix="g6_test_host_tmp_") as planted:
        script = (f"import os\nprint(os.path.exists({planted.name!r}), "
                  f"{os.path.basename(planted.name)!r} in os.listdir('/tmp'))\n")
        code, out, err = _confined(script)
        control = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
    # fails if anything of the host's /tmp shows through the child's own
    assert (code, out.split()) == (0, ["False", "False"]), err
    assert control.stdout.split() == ["True", "True"], control.stderr   # the same look, outside bubblewrap, sees it


def test_a_run_folder_and_a_read_only_copy_in_the_hosts_tmp_are_still_there_inside(bwrap_backend) -> None:
    with confine.run_folder("g6_test_in_tmp_", dir="/tmp") as folder, \
            tempfile.TemporaryDirectory(dir="/tmp", prefix="g6_test_copy_") as copy:
        Path(copy, "code.py").write_text("x = 1\n", encoding="utf-8")
        script = (f"print(open({str(Path(copy, 'code.py'))!r}).read().strip())\n"
                  f"open({str(Path(folder, 'written.txt'))!r}, 'w').write('kept')\n")
        code, out, err = _confined(script, folder=folder, read_only=(copy,))
        # fails if the child's own /tmp covers a run folder or a copy that lives in the host's /tmp
        assert (code, out.strip()) == (0, "x = 1"), err
        assert Path(folder, "written.txt").read_text(encoding="utf-8") == "kept"


def _hosts_names() -> list[str]:
    """The names the host's /etc/hosts gives, localhost aside, that resolve here outside bubblewrap."""
    names: set[str] = set()
    for line in Path("/etc/hosts").read_text(encoding="utf-8").splitlines():
        names.update(line.split("#", 1)[0].split()[1:])
    found = []
    for name in sorted(names - {"localhost"}):
        with contextlib.suppress(OSError):
            socket.getaddrinfo(name, 0)
            found.append(name)
    return found


RESOLVE = textwrap.dedent("""
    import json, socket, sys
    said = {}
    for name in sys.argv[1:]:
        try:
            said[name] = sorted({info[4][0] for info in socket.getaddrinfo(name, 0)})
        except OSError as exc:
            said[name] = type(exc).__name__
    print(json.dumps(said))
""")


def test_with_the_network_off_localhost_resolves_and_no_other_name_of_the_hosts_file_does(bwrap_backend) -> None:
    names = _hosts_names()
    # the host's file names others, and they resolve outside bubblewrap, so the check below can fail
    assert names, "the host's /etc/hosts names nothing but localhost here"
    code, out, err = _confined(RESOLVE, "localhost", *names)
    said = json.loads(out)
    # fails if localhost (pytest-rerunfailures needs it) does not resolve in a child with the network off, or a name
    # from the host's hosts file, which can name machines inside the owner's network, does
    assert code == 0 and said["localhost"] and set(said["localhost"]) <= {"127.0.0.1", "::1"}, (out, err)
    assert all(isinstance(said[name], str) for name in names), said
    # /etc/hosts is G6's own file, and read only
    code, out, err = _confined("print(open('/etc/hosts').read(), end='')\ntry:\n    open('/etc/hosts', 'a')\n"
                               "    print('WROTE')\nexcept OSError:\n    print('DENIED')\n")
    assert (code, out) == (0, confine.BWRAP_HOSTS + "DENIED\n"), err


def test_with_the_network_on_etc_hosts_is_the_hosts_as_before(bwrap_backend) -> None:
    code, out, err = _confined("print(open('/etc/hosts').read(), end='')", network=True)
    # fails if a run that asked for the network no longer sees the host's own name resolution
    assert (code, out) == (0, Path("/etc/hosts").read_text(encoding="utf-8")), err


def test_a_run_killed_at_its_deadline_leaves_no_private_folder_behind(bwrap_backend) -> None:
    before = _private_folders()
    run = sandbox.run_script("import time\nopen('/tmp/alive', 'w').write('x')\ntime.sleep(120)\n", timeout_s=3)
    assert run.status == "timeout" and run.isolation["backend"] == confine.BUBBLEWRAP, (run.status, run.stderr)
    # fails if a run that was killed keeps its /tmp on the host
    assert _private_folders() == before
    # the look above can fail: a run not yet released still holds its child's folder
    with confine.run_folder("g6_test_held_") as folder:
        plan = confine.plan(confine.Confinement(read_write=(folder,)), sys.executable)
        try:
            child = confine.popen(plan, [sys.executable, "-I", "-c", "pass"], env={}, cwd=folder)
            assert child.wait(timeout=60) == 0
            held = _private_folders() - before
        finally:
            assert plan.release() == ""
    assert len(held) == 1 and _private_folders() == before


#: A path that is nowhere, for the self-check's look outside the run folder (this test plants in the host's /tmp only).
ABSENT = "/g6-test-absent/planted.txt"


def test_the_self_check_sees_the_hosts_tmp_where_a_run_would_bind_it(bwrap_backend) -> None:
    from g6.core.storage import g6_home

    reads = confine.interpreter_paths(sys.executable)
    home, g6 = os.path.realpath(os.path.expanduser("~")), os.path.realpath(g6_home())
    # the home folder's entries that hold paths the run may read (a checkout in the home folder, as on CI's runners),
    # as bubblewrap's own test run allows them
    allowed = {Path(read).relative_to(home).parts[0] for read in reads
               if Path(read).is_relative_to(home) and Path(read) != Path(home)}
    with confine.run_folder("g6_test_canary_") as cwd, tempfile.TemporaryDirectory(prefix="g6_test_private_") as own, \
            tempfile.NamedTemporaryFile(dir="/tmp", prefix="g6_test_planted_") as planted:
        os.mkdir(os.path.join(own, "tmp"), 0o700)
        Path(own, "hosts").write_text(confine.BWRAP_HOSTS, encoding="utf-8")

        def leaks(read_only: tuple[str, ...]) -> list[str]:
            args = confine.bwrap_arguments(confine.Confinement(read_write=(cwd,), read_only=read_only), reads,
                                           uid=os.getuid(), gid=os.getgid(), cwd=cwd, disable_userns=False,
                                           layout=confine._layout(), private=own)
            done = subprocess.run([bwrap_backend.program, *args, "--", sys.executable, "-I", "-c",
                                   confine._BWRAP_CANARY, ABSENT, home, g6, planted.name],
                                  capture_output=True, timeout=60, check=False)
            assert done.returncode == 0, done.stderr
            seen = json.loads(done.stdout.decode("utf-8").splitlines()[-1])
            return confine._bwrap_leaks(seen, planted=ABSENT, tmp_planted=planted.name,
                                        home=home, g6=g6, allowed=allowed,
                                        mounts=confine._tmp_mounts([*reads, *read_only, cwd]))

        clean, bound = leaks(()), leaks(("/tmp",))
    # fails if the self-check would pass a bubblewrap whose child sees the host's /tmp (here bound on purpose)
    assert clean == []
    assert any(planted.name in leak for leak in bound), bound


TMP_WRITER = textwrap.dedent("""
    import pathlib


    def test_writes_in_tmp():
        pathlib.Path("/tmp/written-by-a-test.txt").write_text("x")
        assert pathlib.Path("/tmp/written-by-a-test.txt").read_text() == "x"
""")


def test_a_file_a_repositorys_test_writes_in_tmp_changes_nothing_g6_snapshots(bwrap_backend, tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_tmp.py").write_text(TMP_WRITER, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("commit", "-q", "-m", "base")):
        subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                       env=GIT_ENV)
    found = repo_tests.run(repo, ["tests/test_tmp.py"])
    # fails if a file a test writes in its /tmp lands in the repository or anywhere G6's snapshot reads
    assert found["isolation"]["backend"] == confine.BUBBLEWRAP, found["isolation"]
    assert found["verdict"] == "passed" and found["passed"] == 1, found["output"]
    assert found["changed"] == [] and not Path("/tmp/written-by-a-test.txt").exists()
