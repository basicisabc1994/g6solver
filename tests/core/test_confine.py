# SPDX-License-Identifier: Apache-2.0
"""Operating-system isolation for the children that run untrusted code (``g6.core.confine``). Offline.

The Windows tests run a child inside G6's AppContainer. They need a Python the container can read: this interpreter
when the owner has made the one-time grant, otherwise a copy of its base installation in a folder this test session
made (``confined_python`` in ``tests/conftest.py``). Every probe is of host access only: reading sentinel files the
test put outside the run folder, writing outside it, the network, and the processes the child starts.
"""
from __future__ import annotations

import ctypes
import os
import socket
import sys
import textwrap
import time
from pathlib import Path

import pytest

from g6.core import confine, sandbox
from g6.core.errors import ISOLATION_UNAVAILABLE

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="the AppContainer backend is Windows's")


def _off(monkeypatch: pytest.MonkeyPatch, why: str = "unavailable: switched off by this test") -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, why))


# --- with no backend: the run proceeds as before and says so ------------------------------------------------------------


def test_with_no_backend_the_run_proceeds_and_its_result_says_it_was_not_isolated(monkeypatch) -> None:
    _off(monkeypatch)
    run = sandbox.run_script("open('out.txt', 'w').write('x')", timeout_s=30)
    assert run.status == "ok" and run.outputs == {"out.txt": b"x"}
    # fails if sandbox.run_script stops recording confine.plan(...).record() in ScriptRun.isolation
    assert (run.isolation["backend"], run.isolation["why"]) == (None, "unavailable: switched off by this test")
    assert run.limits["os_sandbox"] is False


def test_isolation_required_and_unavailable_refuses_with_its_code_and_runs_nothing(monkeypatch, tmp_path) -> None:
    _off(monkeypatch)
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    marker = tmp_path / "ran.txt"
    run = sandbox.run_script(f"open({str(marker)!r}, 'w').write('ran')", timeout_s=30)
    # fails if sandbox.run_script drops its `if plan.backend is None and confine.required()` refusal
    assert run.status == "refused" and run.returncode is None
    assert run.stderr.startswith(f"[{ISOLATION_UNAVAILABLE}]") and "switched off by this test" in run.stderr
    assert not marker.exists()
    assert run.isolation["backend"] is None


@pytest.mark.parametrize("value", ["", "0", "no"])
def test_isolation_is_only_required_when_the_setting_says_so(monkeypatch, value) -> None:
    _off(monkeypatch)
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", value)
    assert confine.required() is False
    assert sandbox.run_script("print(1)", timeout_s=30).status == "ok"


@pytest.mark.parametrize(("platform", "said"), [("linux", "not tested on this platform"), ("darwin", "macOS")])
def test_no_untested_backend_claims_isolation(monkeypatch, platform, said) -> None:
    monkeypatch.setattr(confine.sys, "platform", platform)
    name, why = confine.backend(sys.executable)
    # fails if confine._select returns a backend for a platform whose backend never ran its tests
    assert name is None and why.startswith("unavailable:") and said in why


def test_g6_changes_the_access_only_of_folders_it_created(tmp_path) -> None:
    theirs = tmp_path / "not-made-by-g6"
    theirs.mkdir()
    before = confine._sddl(str(theirs)) if WINDOWS else None
    # fails if confine.plan stops refusing a read_write folder that confine.run_folder did not make
    with pytest.raises(ValueError, match="created"):
        confine.plan(confine.Confinement(read_write=(str(theirs),)), sys.executable)
    if WINDOWS:
        assert confine._sddl(str(theirs)) == before
    with confine.run_folder("g6_test_") as mine:
        assert confine.owned(mine) and confine.owned(os.path.join(mine, "inner"))
    assert not confine.owned(mine) and not os.path.exists(mine)


@windows_only
def test_an_interpreter_the_container_cannot_read_names_the_owners_one_time_grant(monkeypatch) -> None:
    if confine._readable_all(confine.interpreter_paths(sys.executable)):
        pytest.skip("the owner has granted this interpreter to the container: nothing to name")
    name, why = confine.backend(sys.executable, probe=False)
    assert name is None
    # fails if confine's reason stops naming the exact icacls grant for each interpreter folder
    for folder in confine.interpreter_paths(sys.executable):
        if not confine._readable(folder):
            assert f'icacls "{folder}" /grant "*S-1-15-2-1:(OI)(CI)(RX)"' in why


def test_doctor_names_the_isolation_backend_or_how_to_get_one(monkeypatch) -> None:
    from g6.cli.doctor import _isolation

    _off(monkeypatch, "unavailable: not tested on this platform")
    check = _isolation()
    # fails if the doctor's isolation check is removed or stops passing the reason through
    assert check.name == "isolation" and check.status == "warn" and "not tested on this platform" in check.detail
    assert "your permissions" in check.detail


@windows_only
def test_doctor_prints_the_grant_when_the_interpreter_is_not_readable() -> None:
    from g6.cli.doctor import _isolation

    if confine._readable_all(confine.interpreter_paths(sys.executable)):
        pytest.skip("the owner has granted this interpreter to the container")
    check = _isolation()
    assert check.status == "warn" and 'icacls "' in check.fix and "*S-1-15-2-1:(OI)(CI)(RX)" in check.fix


def test_a_record_says_whether_the_network_was_allowed(monkeypatch) -> None:
    _off(monkeypatch)
    with confine.run_folder("g6_test_") as folder:
        quiet = confine.plan(confine.Confinement(read_write=(folder,)), sys.executable)
        open_ = confine.plan(confine.Confinement(read_write=(folder,), network=True), sys.executable)
    # fails if Plan.record stops carrying the confinement's network setting
    assert quiet.record()["network"] is False and open_.record()["network"] is True


def test_a_nested_recording_keeps_the_inner_runs(monkeypatch) -> None:
    _off(monkeypatch)
    with confine.run_folder("g6_test_") as folder, confine.recording() as outer:
        confine.plan(confine.Confinement(read_write=(folder,)), sys.executable)
        with confine.recording() as inner:
            confine.plan(confine.Confinement(read_write=(folder,)), sys.executable)
    # fails if recording() stops adding the inner block's records to the outer block's on exit
    assert len(inner) == 1 and len(outer) == 2


def test_the_interpreter_paths_of_a_virtual_environment_are_its_base_and_the_environment(tmp_path) -> None:
    base, venv = tmp_path / "base", tmp_path / "venv"
    exe = venv / ("Scripts/python.exe" if WINDOWS else "bin/python")
    exe.parent.mkdir(parents=True)
    base.mkdir()
    exe.write_bytes(b"")
    exe.chmod(0o755)
    (venv / "pyvenv.cfg").write_text(f"home = {base}\ninclude-system-site-packages = false\n", encoding="utf-8")
    # fails if interpreter_paths stops reading pyvenv.cfg's home for an interpreter that is not this one
    assert confine.interpreter_paths(str(exe)) == (os.path.realpath(base), os.path.realpath(venv))


def _stand_in_home(monkeypatch, home: Path, g6: Path) -> None:
    for name in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("G6_HOME", str(g6))


def test_the_home_folder_what_holds_it_and_what_holds_g6s_folder_are_guarded(tmp_path, monkeypatch) -> None:
    home, elsewhere = tmp_path / "home", tmp_path / "elsewhere"
    _stand_in_home(monkeypatch, home, elsewhere / "g6")
    for folder in (home / "proj", elsewhere / "g6", elsewhere / "other"):
        folder.mkdir(parents=True)
    # fails if confine._guarded stops covering the home folder, an ancestor of it, or a folder holding G6's own
    assert all(confine._guarded(str(p)) for p in (home, tmp_path, elsewhere))
    assert not any(confine._guarded(str(p)) for p in (home / "proj", elsewhere / "other"))


@windows_only
def test_g6_never_suggests_a_grant_on_a_guarded_folder(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    _stand_in_home(monkeypatch, home, home / ".g6")
    (home / ".g6").mkdir(parents=True)
    (home / "proj").mkdir()
    chosen, _ = confine._windows()
    refused = chosen.cannot_read([str(home / "proj"), str(home)])
    # fails if cannot_read prints an icacls grant for the home folder (it holds G6's folder and the key file)
    assert "icacls" not in refused and "home folder" in refused and "key file" in refused
    assert f'icacls "{home / "proj"}" /grant' in chosen.cannot_read([str(home / "proj")])


def test_doctor_says_when_only_the_access_lists_were_read_and_checks_g6_python(monkeypatch, tmp_path) -> None:
    from g6.cli.doctor import _isolation

    asked = []
    monkeypatch.setattr(confine, "backend", lambda python=None, probe=True: (asked.append(python), ("appcontainer", ""))[1])
    other = str(tmp_path / "other-python.exe")
    monkeypatch.setenv("G6_PYTHON", other)
    check = _isolation()
    # fails if the doctor says "ok" without saying no child was started, or ignores G6_PYTHON
    assert check.status == "ok" and "access list ok; not started" in check.detail
    assert set(asked) == {sys.executable, other}


# --- each native extension's own access list (a protected list never takes the folder's grant) -------------------------

#: A file that inherited the owner's grant on its folder, and one whose access list is protected (inheritance off).
GRANTED = "D:(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;0x1200a9;;;AC)"
PROTECTED = "D:PAI(A;;FA;;;SY)(A;;FA;;;BA)"


def _environment(tmp_path: Path) -> tuple[str, Path, Path]:
    """An interpreter in a virtual environment of its own, in the test's folder: its base with python312.dll, and the
    environment with one native extension and a module beside it. Nothing here is granted to anything."""
    base, venv = tmp_path / "base", tmp_path / "venv"
    exe = venv / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    base.mkdir()
    (base / "python312.dll").write_bytes(b"")
    (venv / "pyvenv.cfg").write_text(f"home = {base}\n", encoding="utf-8")
    core = venv / "Lib" / "site-packages" / "numpy" / "_core"
    core.mkdir(parents=True)
    (core / "_multiarray_umath.cp312-win_amd64.pyd").write_bytes(b"")
    (core / "__init__.py").write_text("", encoding="utf-8")
    return str(exe), venv, core / "_multiarray_umath.cp312-win_amd64.pyd"


@windows_only
def test_a_native_extension_whose_own_access_list_is_protected_makes_isolation_unavailable(tmp_path,
                                                                                           monkeypatch) -> None:
    python, venv, pyd = _environment(tmp_path)
    chosen, _ = confine._windows()
    monkeypatch.setattr(chosen, "readable", lambda path: True)   # the folders carry the owner's grant
    monkeypatch.setattr(confine, "_sddl", lambda path: PROTECTED if path.endswith(".pyd") else GRANTED)
    name, why = confine.backend(python, probe=False)
    # fails if _AppContainer.ready judges only the folders' access lists, not each native file's own
    # (confine._unreadable_beneath)
    assert name is None and why.startswith("unavailable:") and os.path.realpath(pyd) in why, why
    assert "1 native extension" in why
    # fails if _grant_command drops the plain grant on every file beneath (the folder's inheritable grant never reaches
    # a file whose access list is protected)
    folder = os.path.realpath(venv)
    assert (f'icacls "{folder}" /grant "*S-1-15-2-1:(OI)(CI)(RX)", then '
            f'icacls "{folder}\\*" /grant "*S-1-15-2-1:(RX)" /T /C /Q') in why, why
    assert why.count("icacls") == 2                                 # only the folder that holds the file is named


@windows_only
def test_with_every_native_extension_granted_the_access_check_is_ok(tmp_path, monkeypatch) -> None:
    python, _, _ = _environment(tmp_path)
    chosen, _ = confine._windows()
    monkeypatch.setattr(chosen, "readable", lambda path: True)
    read: list[str] = []
    monkeypatch.setattr(confine, "_sddl", lambda path: (read.append(path), GRANTED)[1])
    # fails if the check calls a granted file unreadable, or reads anything but the native extensions
    assert confine.backend(python, probe=False) == ("appcontainer", "")
    assert sorted(Path(path).name for path in read) == ["_multiarray_umath.cp312-win_amd64.pyd", "python312.dll"]


@windows_only
def test_a_file_whose_access_list_cannot_be_read_counts_as_unreadable(tmp_path, monkeypatch) -> None:
    python, _, pyd = _environment(tmp_path)
    chosen, _ = confine._windows()
    monkeypatch.setattr(chosen, "readable", lambda path: True)

    def refused(path: str) -> str:
        if path.endswith(".pyd"):
            raise OSError(5, "Access is denied")
        return GRANTED

    monkeypatch.setattr(confine, "_sddl", refused)
    name, why = confine.backend(python, probe=False)
    # fails if confine._unreadable_beneath lets an access list it could not read pass as granted
    assert name is None and os.path.realpath(pyd) in why, why


@windows_only
def test_the_doctor_prints_the_grant_with_t_for_protected_files(tmp_path, monkeypatch) -> None:
    from g6.cli.doctor import _isolation

    python, _, _ = _environment(tmp_path)
    chosen, _ = confine._windows()
    monkeypatch.setattr(chosen, "readable", lambda path: True)
    monkeypatch.setattr(confine, "_sddl", lambda path: PROTECTED if path.endswith(".pyd") else GRANTED)
    why = confine.backend(python, probe=False)[1]
    monkeypatch.setattr(confine, "backend", lambda python=None, probe=True: (None, why))
    check = _isolation()
    # fails if the doctor says "ok" here, or its fix text loses the second command, the plain grant with /T
    assert check.status == "warn" and "native extension" in check.detail
    assert check.fix.startswith('Run once in a terminal: icacls "') and check.fix.count("icacls") == 2, check.fix
    assert check.fix.endswith('\\*" /grant "*S-1-15-2-1:(RX)" /T /C /Q'), check.fix


def test_the_printed_grant_is_the_folders_then_a_plain_grant_on_every_file_beneath() -> None:
    # fails if _grant_command prints only the folder's inheritable grant, which icacls never adds to a file whose
    # access list is protected, or puts /T on that first command instead of a plain (RX) grant on everything beneath
    assert confine._grant_command(r"C:\envs\venv") == (
        r'icacls "C:\envs\venv" /grant "*S-1-15-2-1:(OI)(CI)(RX)", then '
        r'icacls "C:\envs\venv\*" /grant "*S-1-15-2-1:(RX)" /T /C /Q')


@pytest.mark.parametrize(("sddl", "readable"), [
    ("D:(A;ID;0x1200a9;;;AC)", True),                       # the owner's grant, inherited
    ("D:(A;ID;FA;;;AC)", True),
    ("D:(A;OICIID;GRGX;;;AC)", True),                        # generic rights, mapped to the file rights
    ("D:(A;;FRFX;;;S-1-15-2-1)", True),                       # the group's SID written out
    ("D:NO_ACCESS_CONTROL", True),                           # no access list: everyone has everything
    (PROTECTED, False),                                      # protected, no grant to the group
    ("D:(A;OICIIO;FA;;;AC)", False),                         # inherit-only: it applies to what is beneath, not here
    ("D:(D;;FX;;;AC)(A;;FA;;;AC)", False),                   # execute denied
    ("D:(A;;FR;;;AC)", False),                               # read without execute
    ("D:(A;;FA;;;WD)", False),                               # everyone's grant does not reach an AppContainer
    ("D:(A;;ZZ;;;AC)", False),                               # a right G6 cannot read grants nothing
    ("D:(A;;FA;;;AC)(D;;ZZ;;;AC)", False),                   # and a denial G6 cannot read denies everything
    ("", False),
])
def test_an_access_list_lets_containers_read_only_with_read_and_execute_for_all_application_packages(
        sddl: str, readable: bool) -> None:
    # fails if confine._lets_containers_read misreads an access list's text (it judges each native file)
    assert confine._lets_containers_read(sddl) is readable


# --- with the Windows backend active ------------------------------------------------------------------------------------


@pytest.fixture
def contained(confined_python, monkeypatch):
    monkeypatch.setenv("G6_PYTHON", confined_python)
    name, why = confine.backend(confined_python)
    if name is None:
        pytest.skip(f"the Windows backend cannot run here: {why}")
    return confined_python


def _probe(path: Path) -> str:
    return textwrap.dedent(f"""
        try:
            print('READ', open({str(path)!r}).read())
        except OSError as exc:
            print('DENIED', type(exc).__name__)
    """)


@windows_only
def test_a_confined_child_cannot_read_a_sentinel_outside_its_run_folder(contained, tmp_path, monkeypatch) -> None:
    sentinel = tmp_path / "outside" / "sentinel.txt"
    home_key = tmp_path / "stand-in-home" / ".g6" / ".env"   # a stand-in for the owner's key file, never the real one
    for path in (sentinel, home_key):
        path.parent.mkdir(parents=True)
        path.write_text("G6_TEST_SENTINEL=not-a-key\n", encoding="utf-8")
    for path in (sentinel, home_key):
        run = sandbox.run_script(_probe(path), timeout_s=60)
        # fails if component_process stops starting the child through confine.popen (the AppContainer)
        assert run.status == "ok" and run.stdout.startswith("DENIED PermissionError"), run.stdout + run.stderr
        assert (run.isolation["backend"], run.isolation["why"]) == ("appcontainer", "") and run.limits["os_sandbox"]
    _off(monkeypatch)
    control = sandbox.run_script(_probe(sentinel), timeout_s=60)   # the same probe, unconfined, reads it
    assert control.stdout.startswith("READ G6_TEST_SENTINEL"), control.stdout + control.stderr


@windows_only
def test_a_confined_child_cannot_write_outside_its_run_folder(contained, tmp_path, monkeypatch) -> None:
    target = tmp_path / "outside" / "planted.txt"
    target.parent.mkdir()
    script = (f"try:\n    open({str(target)!r}, 'w').write('planted')\n    print('WROTE')\n"
              "except OSError as exc:\n    print('DENIED', type(exc).__name__)\n")
    run = sandbox.run_script(script, timeout_s=60)
    assert run.stdout.startswith("DENIED PermissionError"), run.stdout + run.stderr
    assert not target.exists()
    _off(monkeypatch)
    assert sandbox.run_script(script, timeout_s=60).stdout.startswith("WROTE") and target.exists()


@windows_only
def test_a_confined_child_cannot_reach_a_loopback_listener_even_through_the_c_socket(contained, monkeypatch) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    script = textwrap.dedent(f"""
        import _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(('127.0.0.1', {port}))
            print('CONNECTED')
        except OSError as exc:
            print('REFUSED', exc)
    """)
    try:
        run = sandbox.run_script(script, timeout_s=60)
        with pytest.raises(OSError):   # nothing reached the listener
            listener.accept()
        # fails if the AppContainer is given a network capability (or the child is not confined)
        assert run.stdout.startswith("REFUSED"), run.stdout + run.stderr
        _off(monkeypatch)
        control = sandbox.run_script(script, timeout_s=60)   # Python's own guard allows this machine
        assert control.stdout.startswith("CONNECTED"), control.stdout + control.stderr
    finally:
        listener.close()


def _alive(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        return code.value == 259   # STILL_ACTIVE
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


@windows_only
def test_a_process_the_child_starts_is_confined_too_and_dies_with_the_job(contained, tmp_path) -> None:
    sentinel = tmp_path / "outside" / "sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("secret", encoding="utf-8")
    grandchild = (f"import os, time\ntry:\n    open({str(sentinel)!r}).read()\n    said = 'READ'\n"
                  "except OSError as exc:\n    said = 'DENIED ' + type(exc).__name__\n"
                  "open('grandchild.txt', 'w').write(said + ' ' + str(os.getpid()))\ntime.sleep(120)\n")
    script = textwrap.dedent(f"""
        import os, subprocess, sys, time
        subprocess.Popen([sys.executable, '-I', '-c', {grandchild!r}])
        end = time.monotonic() + 30
        while not os.path.exists('grandchild.txt') and time.monotonic() < end:
            time.sleep(0.05)
        time.sleep(0.2)
    """)
    run = sandbox.run_script(script, timeout_s=60)
    said, pid = run.outputs["grandchild.txt"].decode().rsplit(" ", 1)
    assert said == "DENIED PermissionError", run.stderr
    deadline = time.monotonic() + 10
    while _alive(int(pid)) and time.monotonic() < deadline:
        time.sleep(0.05)
    # fails if the confined child is started outside the Job Object (the grandchild outlives the run)
    assert not _alive(int(pid))


@windows_only
def test_an_honest_confined_script_runs_and_returns_its_files(contained) -> None:
    run = sandbox.run_script("import json\nrows = json.load(open('in.json'))\n"
                             "open('out.txt', 'w').write(str(sum(rows)))\nprint('done')",
                             {"in.json": "[1, 2, 3]"}, timeout_s=60)
    assert run.status == "ok" and "done" in run.stdout, run.stderr
    assert run.outputs == {"out.txt": b"6"} and run.isolation["backend"] == "appcontainer"
    assert "no network" in run.limits["network"]


@windows_only
def test_a_confined_child_cannot_open_the_owners_other_processes(contained, monkeypatch) -> None:
    script = textwrap.dedent(f"""
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x0410, False, {os.getpid()})   # query information, read its memory
        print('OPENED' if handle else 'DENIED ' + str(ctypes.get_last_error()))
    """)
    run = sandbox.run_script(script, timeout_s=60)
    # fails if the child is started outside the AppContainer (as the owner, it can open the owner's processes)
    assert run.stdout.startswith("DENIED 5"), run.stdout + run.stderr
    _off(monkeypatch)
    assert sandbox.run_script(script, timeout_s=60).stdout.startswith("OPENED")


@windows_only
def test_a_confined_script_can_use_the_temporary_folders_it_makes(contained) -> None:
    script = ("import os, tempfile\nwith tempfile.TemporaryDirectory() as folder:\n"
              "    open(os.path.join(folder, 'x.txt'), 'w').write('kept')\n"
              "    print(open(os.path.join(folder, 'x.txt')).read())\n")
    run = sandbox.run_script(script, timeout_s=60)
    # fails if confine.CHILD_PRELUDE is dropped from sandbox._BOOT (Python's owner-only 0o700 folders shut the
    # container out of the folders it makes)
    assert run.status == "ok" and run.stdout.strip() == "kept", run.stderr
    assert run.isolation["backend"] == "appcontainer"


@windows_only
def test_a_confined_child_with_the_network_asked_for_still_cannot_read_outside(contained, tmp_path) -> None:
    sentinel = tmp_path / "outside" / "sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("secret", encoding="utf-8")
    with confine.run_folder("g6_test_net_") as cwd:
        plan = confine.plan(confine.Confinement(read_write=(cwd,), read_only=confine.interpreter_paths(contained),
                                                network=True), contained)
        assert plan.backend == "appcontainer"
        from g6.core import component_process

        try:
            with component_process.window(60):
                done = component_process.run_contained([contained, "-I", "-c", _probe(sentinel)],
                                                       env={k: os.environ[k] for k in ("SYSTEMROOT",)}, cwd=cwd,
                                                       confined=plan)
        finally:
            assert plan.release() == ""   # the run's container profile is deleted
    assert done.stdout.decode().startswith("DENIED PermissionError"), done.stderr


@windows_only
def test_each_run_has_its_own_container_and_cannot_read_another_runs_folder(contained) -> None:
    from g6.core import component_process

    env = {key: os.environ[key] for key in ("SYSTEMROOT",)}
    reads = confine.interpreter_paths(contained)
    with confine.run_folder("g6_test_a_") as first_folder, confine.run_folder("g6_test_b_") as second_folder:
        Path(first_folder, "mine.txt").write_text("the first run's", encoding="utf-8")
        first = confine.plan(confine.Confinement(read_write=(first_folder,), read_only=reads), contained)
        second = confine.plan(confine.Confinement(read_write=(second_folder,), read_only=reads), contained)
        try:
            # fails if every run shares one container identity (a single PROFILE)
            assert first.record()["container"] and first.record()["container"] != second.record()["container"]
            with component_process.window(60):
                done = component_process.run_contained(
                    [contained, "-I", "-c", _probe(Path(first_folder, "mine.txt"))], env=env, cwd=second_folder,
                    confined=second)
            assert done.stdout.decode().startswith("DENIED PermissionError"), done.stdout + done.stderr
        finally:
            left = [first.release(), second.release()]
    assert left == ["", ""]
    packages = Path(os.environ["LOCALAPPDATA"]) / "Packages"
    # fails if a run's container profile is kept after the run
    assert not any((packages / plan.record()["profile"]).exists() for plan in (first, second))


@windows_only
def test_a_run_reuses_the_lowest_container_name_no_live_run_holds(contained) -> None:
    chosen, _ = confine._windows()
    first = chosen.identity()
    try:
        second = chosen.identity()
        try:
            # fails if two live runs ever share a container (one SID, one named-object folder)
            assert second.name != first.name and second.sid_text != first.sid_text
        finally:
            second.close()
        name = first.name
    finally:
        assert first.close() == ""
    again = chosen.identity()
    try:
        # fails if each run takes a fresh random name: Windows keeps every container SID's named-object folder (three
        # directories, two links and a file in the System process) until logoff, so random names pile up until it
        # refuses to start a container at all and the run goes unconfined
        assert again.name == name, (again.name, name)
    finally:
        again.close()


@windows_only
def test_a_container_name_whose_folder_is_still_there_is_not_reused(contained, monkeypatch, tmp_path) -> None:
    chosen, _ = confine._windows()
    probe = chosen.identity()
    name = probe.name
    assert probe.close() == ""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "Packages" / name).mkdir(parents=True)   # what a profile Windows could not fully delete leaves
    taken = chosen.identity()
    try:
        # fails if a name is reused while its last run's container folder (its temporary files) is still on disk
        assert taken.name != name
    finally:
        taken.close()


@windows_only
def test_a_confined_child_that_never_started_is_not_reported_confined(contained, monkeypatch) -> None:
    def refuse(self, argv, *identity, **options):
        raise OSError("the test refused to start it")

    monkeypatch.setattr(confine._AppContainer, "start", refuse)
    run = sandbox.run_script("print(1)", timeout_s=30)
    # fails if sandbox keeps the plan's record when the confined start failed
    assert run.status == "error" and run.isolation["backend"] is None, run.isolation
    assert "did not start" in run.isolation["why"]