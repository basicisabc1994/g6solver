# SPDX-License-Identifier: Apache-2.0
"""Challenge startup authority and byte-based stop/start read-back, without live services."""
import ast
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.mcp import improve
from g6.self_maintenance import envelope
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog


# Python 3.12/3.13 command-line environment names, including platform-specific
# names and names with no effect in a venv. Unknown future PYTHON* names are
# challenged separately: an allow-list must exclude them without an update.
PYTHON_VARIABLES = """
PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONUSERBASE PYTHONEXECUTABLE
PYTHONPLATLIBDIR PYTHONSAFEPATH PYTHONCASEOK PYTHONBREAKPOINT PYTHONDEBUG
PYTHONINSPECT PYTHONOPTIMIZE PYTHONWARNINGS PYTHONUNBUFFERED PYTHONVERBOSE
PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX PYTHONHASHSEED PYTHONIOENCODING
PYTHONNOUSERSITE PYTHONFAULTHANDLER PYTHONTRACEMALLOC PYTHONPROFILEIMPORTTIME
PYTHONASYNCIODEBUG PYTHONMALLOC PYTHONMALLOCSTATS PYTHONCOERCECLOCALE
PYTHONDEVMODE PYTHONUTF8 PYTHONLEGACYWINDOWSFSENCODING
PYTHONLEGACYWINDOWSSTDIO PYTHONINTMAXSTRDIGITS PYTHONNODEBUGRANGES
PYTHONWARNDEFAULTENCODING PYTHON_FROZEN_MODULES PYTHONPERFSUPPORT
PYTHON_CPU_COUNT PYTHON_GIL PYTHON_COLORS PYTHON_BASIC_REPL
PYTHON_HISTORY PYTHON_PRESITE PYTHON_PERF_JIT_SUPPORT PYTHONUNHANDLED_FUTURE
""".split()


@pytest.mark.parametrize("launcher", ["act", "runner"])
@pytest.mark.parametrize("name", PYTHON_VARIABLES)
def test_each_python_setting_is_absent_at_real_child_startup(tmp_path, monkeypatch, launcher, name):
    root = tmp_path / "loop"
    root.mkdir()
    trap = root / "runtime/Lib/encodings"
    trap.mkdir(parents=True)
    marker = root / "startup-ran"
    planted = f"import _io\n_io.open({str(marker)!r}, 'wb').write(b'ran')\nraise RuntimeError('startup trap')\n"
    (trap / "__init__.py").write_text(planted, encoding="utf-8")
    (root / "startup.py").write_text(planted, encoding="utf-8")
    (root / "sitecustomize.py").write_text(planted, encoding="utf-8")
    fake_bin = root / "bin"
    fake_bin.mkdir()
    (fake_bin / "python.cmd").write_text("@exit /b 91\n", encoding="utf-8")
    (fake_bin / "python.exe").write_bytes(b"planted interpreter must never be selected")
    values = {"PYTHONHOME": "runtime", "PYTHONPATH": ".", "PYTHONSTARTUP": "startup.py",
              "PYTHONUSERBASE": "runtime", "PYTHONEXECUTABLE": "bin/python.cmd",
              "PYTHONPLATLIBDIR": "runtime", "PYTHONPYCACHEPREFIX": "runtime",
              "PYTHONMALLOC": "malloc", "PYTHONIOENCODING": "utf-8", "PYTHONWARNINGS": "default",
              "PYTHONBREAKPOINT": "startup.hook", "PYTHONINTMAXSTRDIGITS": "640"}
    monkeypatch.setenv(name, values.get(name, "1"))
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("G6_UNLISTED_TRAP", "must not reach the child")
    real_popen = subprocess.Popen
    seen = []

    def probe(command, **kwargs):
        # Keep the real interpreter and all startup flags. Only imports run.
        end = command.index("-c") if "-c" in command else command.index("-m")
        program = ("import json, os, sys; print(json.dumps({'isolated': sys.flags.isolated, "
                   "'env': dict(os.environ)}))")
        kwargs.update(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      text=True, encoding="utf-8", errors="replace")
        child = real_popen([*command[:end], "-c", program], **kwargs)
        try:
            out, err = child.communicate(timeout=15)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=15)
        seen.append((child.returncode, out, err))
        return child

    monkeypatch.setattr(improve.subprocess, "Popen", probe)
    if launcher == "act":
        improve._run_act(root, {}, {"act": "enrol", "folder": str(root), "binding": {}, "who": "Ada"})
    else:
        improve._spawn(root, {}).wait(timeout=15)
    assert not marker.exists(), (launcher, name)
    status, out, err = seen[0]
    assert status == 0, (launcher, name, err)
    result = json.loads(out)
    assert result["isolated"] == 1
    assert not any(key.upper().startswith("PYTHON") for key in result["env"])
    assert "G6_UNLISTED_TRAP" not in result["env"]
    assert Path(result["env"]["PATH"].split(os.pathsep)[0]) == Path(sys.executable).resolve().parent


def test_python_help_names_are_all_challenged():
    output = subprocess.check_output([sys.executable, "-I", "--help-env"], text=True,
                                     env=improve._environment())
    assert set(re.findall(r"\bPYTHON[A-Z_]+\b", output)) <= set(PYTHON_VARIABLES)


def test_environment_paths_are_checked_and_fixed_in_the_parent(tmp_path, monkeypatch):
    for name in ("SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        monkeypatch.setenv(name, "relative-planted")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    env = improve._environment()
    assert set(env) <= {"SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                        "TEMP", "TMP", "G6_HOME", "PATH"}
    for name, value in env.items():
        entries = value.split(os.pathsep) if name == "PATH" else [value]
        assert entries and all(improve._stands_alone(entry) for entry in entries), (name, value)
    assert env["G6_HOME"] == str((tmp_path / "state").resolve())


@pytest.mark.parametrize("settings", [{}, {"python": sys.executable}])
def test_isolated_bootstrap_imports_only_the_roots_g6_sets(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    (tmp_path / "g6").mkdir()
    (tmp_path / "g6/__init__.py").write_text("raise RuntimeError('foreign G6')", encoding="utf-8")
    command = [*improve._interpreter(settings), "-c", "import g6; print(g6.__file__)"]
    result = subprocess.run(command, cwd=tmp_path, env=improve._environment(), capture_output=True,
                            text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    imported = Path(result.stdout.strip()).resolve()
    assert not imported.is_relative_to(tmp_path)
    if not settings:
        assert imported == Path(improve.g6.__file__).resolve()


def test_bootstrap_keeps_a_separator_in_its_source_root_as_one_argument(tmp_path, monkeypatch):
    root = tmp_path / ("server" + os.pathsep + "planted")
    package = root / "g6"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(improve.g6, "__file__", str(package / "__init__.py"))
    command = [*improve._interpreter({}), "-c", "import g6; print(g6.__file__)"]
    result = subprocess.run(command, cwd=tmp_path, env=improve._environment(), capture_output=True,
                            text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == (package / "__init__.py").resolve()


class Interrupted:
    def __init__(self, handle, stage):
        self.handle, self.stage = handle, stage

    def __enter__(self):
        return self

    def write(self, text):
        if self.stage == "first-byte":
            raise OSError("before first byte")
        if self.stage == "mid-line":
            self.handle.write(text[:max(1, len(text) // 2)])
            self.handle.flush()
            raise OSError("mid-line")
        if self.stage == "before-flush":
            self.handle.write(text)
            raise OSError("before explicit flush")
        return self.handle.write(text)

    def flush(self):
        return self.handle.flush()

    def fileno(self):
        return self.handle.fileno()

    def __exit__(self, *args):
        self.handle.close()


@pytest.mark.parametrize("filename,part", [("STOP", "its line in the STOP file"),
                                           ("stops.log", "its count in stops.log")])
@pytest.mark.parametrize("stage", ["open", "first-byte", "mid-line", "before-flush"])
def test_stop_parts_are_complete_own_entries_read_from_bytes(tmp_path, monkeypatch, filename, part, stage):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    # Old material, including a complete unrelated token, cannot stand in for ours.
    (tmp_path / filename).write_bytes(b"an earlier stop\nold-id\n")
    real_open = open

    def opened(path, mode="r", *args, **kwargs):
        target = Path(path).name == filename and mode == "a"
        if target and stage == "open":
            raise OSError("at open")
        handle = real_open(path, mode, *args, **kwargs)
        return Interrupted(handle, stage) if target else handle

    monkeypatch.setattr(envelope, "open", opened, raising=False)
    reply = improve.stop(str(tmp_path), "new stop reason")
    assert reply["recorded"][part] is (stage == "before-flush"), reply
    assert reply["recorded"]["its note in the constraint log"] is False


@pytest.mark.parametrize("filename,part", [("STOP", "its line in the STOP file"),
                                           ("stops.log", "its count in stops.log")])
def test_stop_part_unreadable_is_unknown(tmp_path, monkeypatch, filename, part):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    original = Path.read_bytes
    def unreadable(path):
        if path.name == filename:
            raise PermissionError("unreadable entry")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert improve.stop(str(tmp_path))["recorded"][part] is None


@pytest.mark.parametrize("filename,part", [("STOP", "its line in the STOP file"),
                                           ("stops.log", "its count in stops.log")])
def test_another_writers_growth_is_not_this_stops_entry(tmp_path, monkeypatch, filename, part):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    real_open = open

    def opened(path, mode="r", *args, **kwargs):
        if Path(path).name == filename and mode == "a":
            with real_open(path, "a", encoding="utf-8") as other:
                other.write("another writer's entry\nunrelated-id\n")
            raise OSError("our open failed after another writer appended")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(envelope, "open", opened, raising=False)
    assert improve.stop(str(tmp_path), "our stop")["recorded"][part] is False


def test_an_old_unchanged_stop_file_cannot_stand_in_for_this_stop(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "STOP").write_bytes(b"an old stop\nold-id\n")
    real_open = open

    def opened(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return Interrupted(handle, "first-byte") if Path(path).name == "STOP" and mode == "a" else handle

    monkeypatch.setattr(envelope, "open", opened, raising=False)
    reply = improve.stop(str(tmp_path), "our new stop")
    assert reply["recorded"]["its line in the STOP file"] is False
    assert reply["recorded"]["its count in stops.log"] is True
    assert reply["recorded"]["the stop"] is False
    assert reply["code"] == improve.STOP_FAILED


@pytest.mark.parametrize("state,missing", [("missing", True), ("empty", False), ("unreadable", None)])
def test_before_stop_presence_is_read_from_bytes(tmp_path, monkeypatch, state, missing):
    if state == "empty":
        (tmp_path / "STOP").write_bytes(b"")
    if state == "unreadable":
        original = Path.read_bytes
        def unreadable(path):
            if path.name == "STOP":
                raise PermissionError("cannot read before stop")
            return original(path)
        monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert improve._stop_before(tmp_path)["missing"] is missing


def test_one_stop_identity_names_both_file_entries_and_the_ledger_note(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    CycleLog(tmp_path / CYCLE_LOG).start("Ada", record_id="earlier-start")
    monkeypatch.setattr(improve, "uuid4", lambda: SimpleNamespace(hex="own-stop"))
    reply = improve.stop(str(tmp_path), "our stop")
    assert all(reply["recorded"].values())
    assert b"own-stop\n" in (tmp_path / "STOP").read_bytes()
    assert b"own-stop\n" in (tmp_path / "stops.log").read_bytes()
    assert CycleLog(tmp_path / CYCLE_LOG).record("stopped", "own-stop") is not None


@pytest.mark.parametrize("kind", ["started", "stopped"])
@pytest.mark.parametrize("stage", ["open", "first-byte", "mid-line", "before-flush"])
def test_each_start_and_stop_ledger_write_is_read_by_id(tmp_path, monkeypatch, kind, stage):
    log = CycleLog(tmp_path / CYCLE_LOG)
    if kind == "stopped":
        log.start("Ada", record_id="earlier-start")
    before = improve._stop_before(tmp_path)
    real_open = Path.open

    def opened(path, mode="r", *args, **kwargs):
        target = path == tmp_path / CYCLE_LOG and mode == "a"
        if target and stage == "open":
            raise OSError("at open")
        handle = real_open(path, mode, *args, **kwargs)
        return Interrupted(handle, stage) if target else handle

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(OSError):
        if kind == "started":
            log.start("Ada", record_id="own-row")
        else:
            log.stop("new stop", record_id="own-row")
    try:
        reopened = CycleLog(tmp_path / CYCLE_LOG)
    except ValueError:
        assert stage == "mid-line"
    else:
        assert (reopened.record(kind, "own-row") is not None) is (stage == "before-flush")
    if kind == "started":
        reply = improve._start_as_recorded(tmp_path, "own-row", improve._blocked(improve.START_FAILED, "interrupted", "-"))
        assert reply["recorded"] == (None if stage == "mid-line" else {"the start": stage == "before-flush"})
    else:
        args = (tmp_path, before, "own-row")
        if "reason" in inspect.signature(improve._stop_on_record).parameters:
            args += ("new stop",)
        found = improve._stop_on_record(*args)
        assert found["its note in the constraint log"] is (None if stage == "mid-line" else stage == "before-flush")
        assert found["its line in the STOP file"] is False
        assert found["its count in stops.log"] is False


def test_recorded_start_and_stop_inventory_names_byte_or_row_readers():
    readers = {
        # The merge with the stop's follow-up: the stop's rule is read through the one reading (``stop_in_force``), and
        # for a caller that holds a start of its own through ``what_stops``; ``stop_standing`` takes the folder alone.
        "_start_on_record": {"log.record", "log.latest_id", "stop_in_force"},
        "_stop_on_record": {"_stop_entry_on_record", "log.record", "what_stops"},
    }
    for name, required in readers.items():
        tree = ast.parse(inspect.getsource(getattr(improve, name)))
        calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert required <= calls
        assert "_stop_file_size" not in calls
    tree = ast.parse(inspect.getsource(improve._stop_entry_on_record))
    assert any(isinstance(n, ast.Call) and ast.unparse(n.func).endswith("read_bytes") for n in ast.walk(tree))
