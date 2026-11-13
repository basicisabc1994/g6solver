"""Every program the feature starts is an absolute path that never came from the working directory.

Windows starts a bare name from the working directory first, and a status line renders in whatever folder the person
has open. The stand-in for a planted program is a copy of Windows' own ``hostname.exe``: given arguments it refuses
and changes nothing. No test here rests on this session's host setting ``NoDefaultCurrentDirectoryInExePath``: where
a process is started, that variable is taken out of its environment explicitly.
"""
import ast
import base64
import inspect
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from g6._allowance import programs, statusline

SRC = Path(__file__).resolve().parents[3] / "src" / "g6"
FEATURE = [SRC / "allowance.py", *sorted((SRC / "_allowance").glob("*.py"))]
GUARD = "NoDefaultCurrentDirectoryInExePath"
SHELLS = ("cmd", "powershell", "sh")
windows = pytest.mark.skipif(os.name != "nt", reason="the search order of Windows' CreateProcess")


def _git_sh():
    found = Path(os.environ.get("ProgramFiles", "")) / "Git" / "bin"
    return str(found) if (found / "sh.exe").is_file() else ""


def _planted(tmp_path, *names):
    """A folder as a repository could hold it: programs named like the shells."""
    where = tmp_path / "repository"
    where.mkdir()
    for name in names:
        if os.name == "nt":
            shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32" / "hostname.exe", where / (name + ".exe"))
        else:
            (where / name).write_text("#!/bin/sh\necho planted\n", encoding="utf-8")
            (where / name).chmod(0o755)
    return where


def _environment(**changes):
    """This environment without Windows' own guard, so a result never rests on the host having set it."""
    env = {key: value for key, value in os.environ.items() if key.lower() != GUARD.lower()}
    for key, value in changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


# ---- the one lookup

def test_a_bare_name_is_found_in_an_absolute_folder_of_path_only(tmp_path, monkeypatch):
    where = _planted(tmp_path, "tool")
    safe = tmp_path / "installed"
    safe.mkdir()
    shutil.copy(where / ("tool.exe" if os.name == "nt" else "tool"), safe)
    monkeypatch.chdir(where)
    monkeypatch.delenv(GUARD, raising=False)
    sep = os.pathsep
    for path in ("", ".", "." + sep, sep + ".", "repository", os.path.join("..", "repository"), sep.join(["", ".", "nowhere"])):
        with pytest.raises(OSError):
            programs.program("tool", environ={"PATH": path})
    for path in (str(safe), sep.join([".", "", "relative", str(safe)]), sep.join([str(safe), "."])):
        found = programs.program("tool", environ={"PATH": path})
        assert programs.absolute(found) and Path(found).parent == safe
    # An absolute entry that is the working directory is the person's own PATH, and is followed.
    assert Path(programs.program("tool", environ={"PATH": str(where)})).parent == where


def test_a_path_is_started_as_given_only_when_it_names_its_own_root(tmp_path, monkeypatch):
    where = _planted(tmp_path, "tool")
    monkeypatch.chdir(where)
    given = str(where / "tool") + (".exe" if os.name == "nt" else "")
    assert programs.program(given, environ={"PATH": ""}) == given
    missing = str(tmp_path / "removed" / "tool")
    assert programs.program(missing, environ={"PATH": ""}) == missing  # starting it is what fails, plainly
    relative = [os.path.join(".", "tool"), os.path.join("..", "repository", "tool"), "repository/tool", ""]
    if os.name == "nt":
        drive = os.path.splitdrive(str(where))[0]
        relative += [drive + "tool.exe", os.path.splitdrive(given)[1], "tool\x00.exe"]
    for command in relative:
        with pytest.raises(OSError):
            programs.program(command, environ={"PATH": str(where)})


def test_the_lookup_and_g6s_shared_finder_agree_wherever_path_is_absolute(tmp_path, monkeypatch):
    """One fact held in two places (this feature may import no other part of G6 into its wrapper): shown equal."""
    from g6.core.outside import find_program

    where = _planted(tmp_path, "tool")
    monkeypatch.chdir(tmp_path)
    for path in (str(where), os.pathsep.join([str(tmp_path / "empty"), str(where)]), str(tmp_path / "empty")):
        shared = find_program("tool", environ={"PATH": path})
        try:
            mine = programs.program("tool", environ={"PATH": path})
        except OSError:
            mine = None
        assert (mine and os.path.normcase(mine)) == (shared and os.path.normcase(shared))


# ---- from the code: what the feature starts, and with what

def _references(source):
    """A module's tree, what it imports, and every use of an imported name, called or passed, as a dotted name."""
    tree = ast.parse(source)
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound[alias.asname or alias.name] = (node.module or "") + "." + alias.name
    imported = {node.module if isinstance(node, ast.ImportFrom) else alias.name for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    inner = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    uses = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in inner:
            parts, value = [], node
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name) and value.id in bound:
                uses.append((".".join([bound[value.id], *reversed(parts)]), node))
    return tree, imported, uses


# Every module of the feature: all it imports, and all it takes from the modules that can start a program.
STANDARD = {"__future__", "collections.abc", "dataclasses", "datetime", "typing", "pathlib", "json", "math", "re",
            "hashlib", "contextlib", "uuid", "sqlite3", "threading", "argparse", "base64", "copy", "shlex", "sys"}
TAKEN = {
    "allowance.py": {"os": {"environ"}},
    "__init__.py": {},
    "advice.py": {},
    "hook.py": {"os": {"environ"}},
    "programs.py": {"os": {"environ", "name", "path", "pathsep"}, "shutil": {"which"}},
    "readers.py": {},
    "statusline.py": {"os": {"environ", "name", "_exit"},
                      "subprocess": {"Popen", "PIPE", "DEVNULL", "list2cmdline", "CREATE_NEW_PROCESS_GROUP"}},
    "store.py": {"os": {"fsync", "replace", "environ", "path"}},
}
OWN = {"g6.allowance", "g6._allowance", "g6._allowance.advice", "g6._allowance.programs", "g6._allowance.readers",
       "g6._allowance.store", "g6.self_maintenance.usage"}


def test_the_feature_starts_two_programs_and_each_is_resolved_first():
    assert {path.name for path in FEATURE} == set(TAKEN)
    starts = {}
    for path in FEATURE:
        tree, imported, uses = _references(path.read_text(encoding="utf-8") if path.name != "statusline.py"
                                           else inspect.getsource(statusline))
        # Nothing is imported but these, so no other module that can start a program is held at all.
        assert imported <= STANDARD | OWN | set(TAKEN[path.name]), (path.name, imported - STANDARD - OWN)
        assert not {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} & {
            "__import__", "eval", "exec", "importlib"}, path.name
        for module, names in TAKEN[path.name].items():
            assert {name.split(".")[1] for name, _ in uses if name.startswith(module + ".")} == names, (path.name, module)
        calls = {id(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
        for name, node in uses:
            if name == "subprocess.Popen":
                assert id(node) in calls, (path.name, "a starter handed on instead of called")
                starts.setdefault(path.name, []).append(calls[id(node)])
    assert {name: len(found) for name, found in starts.items()} == {"statusline.py": 2}
    tree = ast.parse(inspect.getsource(statusline))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    unparsed = {ast.unparse(call) for call in starts["statusline.py"]}
    for function, argument in (("hand_over", "sys.executable"), ("run_wrapper", "renderer[0]")):
        call = next(node for node in ast.walk(functions[function]) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "subprocess.Popen")
        assert ast.unparse(call) in unparsed
        started = call.args[0]
        assert isinstance(started, ast.Name), "the program is the first element of a list built in this function"
        bindings = [node for node in ast.walk(functions[function]) if isinstance(node, (ast.Assign, ast.AnnAssign))
                    and any(isinstance(t, ast.Name) and t.id == started.id
                            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
        first = min(bindings, key=lambda node: node.lineno)
        assert isinstance(first.value, ast.List) and ast.unparse(first.value.elts[0]) == f"program({argument})"
        for later in bindings:
            if later is not first:  # the cmd form's command line, built from the resolved list and nothing else
                assert {n.id for n in ast.walk(later.value) if isinstance(n, ast.Name)} <= {started.id, "subprocess"}
    assert "from g6._allowance.programs import program\n" in inspect.getsource(statusline)


def test_the_codex_reader_starts_what_g6s_shared_finder_found_and_the_refresh_names_the_same_program():
    """The feature starts no Codex program itself: ``store.refresh_codex`` asks the reader, which finds its own."""
    usage = ast.parse((SRC / "self_maintenance" / "usage.py").read_text(encoding="utf-8"))
    reader = next(node for node in ast.walk(usage) if isinstance(node, ast.FunctionDef) and node.name == "codex_limits")
    found = next(node for node in ast.walk(reader) if isinstance(node, ast.Assign))
    named = "os.environ.get('G6_CODEX', '').strip() or 'codex'"
    assert ast.unparse(found) == f"where = find_program({named})"
    started = next(node for node in ast.walk(reader) if isinstance(node, ast.Call) and ast.unparse(node.func) == "read_limits")
    assert ast.unparse(started.args[0].elts[0]) == "where"
    # The refresh names the program with the same words, and weighs the shared finder's answer against the rule.
    store = ast.parse((SRC / "_allowance" / "store.py").read_text(encoding="utf-8"))
    refresh = next(node for node in ast.walk(store) if isinstance(node, ast.FunctionDef) and node.name == "refresh_codex")
    assigned = {ast.unparse(node.targets[0]): ast.unparse(node.value) for node in ast.walk(refresh)
                if isinstance(node, ast.Assign)}
    assert assigned["named"] == named and assigned["found"] == "find_program(named)"
    assert assigned["strict"] == "program(named)"
    lines = {}
    for node in ast.walk(refresh):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in {"find_program", "program", "codex_limits"}:
            lines.setdefault(ast.unparse(node.func), []).append(node.lineno)
    assert len(lines["codex_limits"]) == 1 and lines["find_program"][0] < lines["program"][0] < lines["codex_limits"][0]


@pytest.mark.parametrize("case", ["a planted program reached by a dot on PATH", "a relative setting",
                                  "a program in an absolute folder", "an absolute setting", "no program anywhere"])
def test_the_codex_reader_is_asked_only_when_its_program_is_on_an_absolute_path(tmp_path, monkeypatch, case):
    from datetime import UTC, datetime

    from g6._allowance.store import Store, refresh_codex
    from g6.self_maintenance import usage

    where = _planted(tmp_path, "codex")
    installed = tmp_path / "installed"
    installed.mkdir()
    name = "codex.exe" if os.name == "nt" else "codex"
    monkeypatch.chdir(where)
    monkeypatch.delenv(GUARD, raising=False)
    monkeypatch.delenv("G6_CODEX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    asked = "a program in an absolute folder", "an absolute setting", "no program anywhere"
    if case == "a planted program reached by a dot on PATH":
        monkeypatch.setenv("PATH", "." + os.pathsep + str(tmp_path / "empty"))
    elif case == "a relative setting":
        monkeypatch.setenv("G6_CODEX", os.path.join(".", name))
    elif case == "a program in an absolute folder":
        shutil.copy(where / name, installed)
        monkeypatch.setenv("PATH", "." + os.pathsep + str(installed))
        (where / name).unlink()
    elif case == "an absolute setting":
        shutil.copy(where / name, installed)
        monkeypatch.setenv("G6_CODEX", str(installed / name))
    calls = []
    monkeypatch.setattr(usage, "codex_limits", lambda: calls.append(1) or {
        "rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 4000000000}}})
    store = Store(tmp_path / "cache")
    store.bind("codex", "p")
    at = datetime.now(UTC)
    refresh_codex(store, "p", now=lambda: at)
    reading = store.load("codex", "p")
    if case in asked:
        assert calls == [1] and reading.failure == "" and reading.windows[0].used_share == .1
    else:
        assert calls == [] and reading.failure == "read failed" and reading.windows == ()


# ---- at the call: what is handed to the system

def _recorded(monkeypatch):
    started = []

    def popen(args, **more):
        started.append(args)
        raise OSError("read here, never started")

    monkeypatch.setattr(statusline.subprocess, "Popen", popen)
    return started


def _first(args):
    if isinstance(args, str):  # the cmd form's command line: its program is the first part, quoted when it has a space
        return args[1:args.index('"', 1)] if args.startswith('"') else args.split(" ", 1)[0]
    return args[0]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("path", ["as it is", "a dot first", "an empty entry first", "a relative entry first"])
def test_the_renderers_shell_is_absolute_at_the_call_and_never_the_working_directorys(tmp_path, monkeypatch, shell, path):
    where = _planted(tmp_path, "cmd", "powershell", "sh", "pwsh")
    monkeypatch.chdir(where)
    monkeypatch.delenv(GUARD, raising=False)
    monkeypatch.delenv("COMSPEC", raising=False)  # the form's bare fallback is what is looked up
    real = os.pathsep.join(filter(None, [os.environ.get("PATH", ""), _git_sh()]))
    monkeypatch.setenv("PATH", {"as it is": real, "a dot first": "." + os.pathsep + real,
                                "an empty entry first": os.pathsep + real,
                                "a relative entry first": os.path.join("..", "repository") + os.pathsep + real}[path])
    started = _recorded(monkeypatch)
    seen = []
    try:
        statusline.run_wrapper(b"{}", statusline.shell_argv(shell, "echo rendered"), observer=seen.append)
    except OSError:
        pass
    assert seen == [b"{}"], "the observation is made whether or not a shell was found"
    if not started:
        assert shell != "cmd" or os.name != "nt", "this host has a command processor on its PATH"
        return  # no such shell in an absolute folder of this host's PATH: refused, nothing started
    program = _first(started[0])
    assert programs.absolute(program) and Path(program).is_file()
    assert Path(program).parent.resolve() != where.resolve()


@windows
@pytest.mark.parametrize("comspec", ["cmd.exe", ".\\cmd.exe", "repository\\cmd.exe", "C:cmd.exe", "\\cmd.exe"])
def test_a_comspec_that_does_not_name_its_own_root_never_starts_the_working_directorys(tmp_path, monkeypatch, comspec):
    where = _planted(tmp_path, "cmd")
    monkeypatch.chdir(where)
    monkeypatch.delenv(GUARD, raising=False)
    monkeypatch.setenv("COMSPEC", comspec)
    started = _recorded(monkeypatch)
    with pytest.raises(OSError):
        statusline.run_wrapper(b"{}", statusline.shell_argv("cmd", "echo rendered"), observer=lambda raw: None)
    if comspec == "cmd.exe":  # a bare name: looked up like any other, in PATH's absolute folders
        assert Path(_first(started[0])).parent.resolve() != where.resolve() and programs.absolute(_first(started[0]))
    else:
        assert started == []


def test_an_absolute_comspec_is_the_persons_own_setting_and_is_started_as_given(tmp_path, monkeypatch):
    own = str(tmp_path / "own shell" / ("cmd.exe" if os.name == "nt" else "cmd"))
    monkeypatch.setenv("COMSPEC", own)
    started = _recorded(monkeypatch)
    with pytest.raises(OSError):
        statusline.run_wrapper(b"{}", statusline.shell_argv("cmd", "echo rendered"), observer=lambda raw: None)
    assert _first(started[0]) == own


def test_the_observer_is_this_interpreter_by_its_absolute_path_or_is_not_started(tmp_path, monkeypatch):
    where = _planted(tmp_path, "python")
    monkeypatch.chdir(where)
    monkeypatch.delenv(GUARD, raising=False)
    started = _recorded(monkeypatch)
    assert statusline.observe_in_worker(b"{}", "p", "2.1.319", timeout=30) is False
    assert started[0][0] == sys.executable and programs.absolute(started[0][0])
    for unknown in ("", os.path.join(".", "python"), "python"):  # an interpreter that does not know its own path
        del started[:]
        monkeypatch.setattr(sys, "executable", unknown)
        assert statusline.observe_in_worker(b"{}", "p", "2.1.319", timeout=30) is False
        if unknown == "python" and started:  # a bare name is looked up like any other: never in this folder
            assert programs.absolute(started[0][0]) and Path(started[0][0]).parent.resolve() != where.resolve()
        else:
            assert started == []


# ---- through the previewed command, in a folder that holds programs named like the shells

def _installed(shell, renderer, **more):
    p = statusline.preview({"statusLine": {"type": "command", "command": renderer}}, python=sys.executable,
                           profile="p", shell=shell, invocation_shell="cmd", **more)
    import json

    return json.loads(p["settings_line"])["statusLine"]["command"]


def _run(command, where, env):
    line = subprocess.list2cmdline([os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe"), "/d", "/s", "/c"])
    return subprocess.run(line + ' "' + command + '"', input=b"{}", capture_output=True, timeout=240, cwd=where, env=env)


@windows
@pytest.mark.parametrize("shell,renderer", [("powershell", "[Console]::Out.Write('rendered')"),
                                            ("sh", "printf rendered"), ("cmd", "echo rendered")])
def test_with_windows_guard_unset_the_persons_shell_renders_beside_a_planted_one(tmp_path, shell, renderer):
    if shell == "sh" and not _git_sh():
        pytest.skip("no POSIX shell on this Windows host")
    where = _planted(tmp_path, "cmd", "powershell", "sh")
    env = _environment(G6_HOME=str(tmp_path / "home"), COMSPEC=None,
                       PATH=os.pathsep.join(filter(None, [".", os.environ.get("PATH", ""), _git_sh()])))
    assert not any(key.lower() == GUARD.lower() for key in env)
    wrapped = _run(_installed(shell, renderer), where, env)
    assert (wrapped.returncode, wrapped.stdout.strip()) == (0, b"rendered"), wrapped.stderr[-300:]


@windows
def test_a_shell_found_only_in_the_working_directory_is_refused_plainly_and_nothing_is_started(tmp_path):
    where = _planted(tmp_path, "sh")
    system = os.path.join(os.environ["SystemRoot"], "System32")
    env = _environment(G6_HOME=str(tmp_path / "home"), PATH=os.pathsep.join([".", "", system]))
    wrapped = _run(_installed("sh", "printf rendered"), where, env)
    assert wrapped.stdout == b"" and wrapped.returncode not in (0, 1), "the planted program's own exit is 1"
    message = wrapped.stderr.decode("utf-8", "replace")
    assert message.startswith("g6 status line: ") and len(message.strip().splitlines()) == 1
    assert "Traceback" not in message and "sh" in message


@windows
def test_a_renderer_shell_that_cannot_be_started_is_a_plain_message_and_a_failure(tmp_path, monkeypatch):
    """Through the wrapper's own entry point: an absolute setting that is not there."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    env = _environment(G6_HOME=str(tmp_path / "home"), COMSPEC=str(tmp_path / "removed" / "cmd.exe"))
    token = base64.urlsafe_b64encode(b"echo rendered").decode("ascii")
    done = subprocess.run([sys.executable, "-P", "-m", "g6._allowance.statusline", "--renderer=" + token,
                           "--shell=cmd", "--profile=p"], input=b"{}", capture_output=True, timeout=240, env=env)
    message = done.stderr.decode("utf-8", "replace")
    assert done.stdout == b"" and done.returncode == 127
    assert message.startswith("g6 status line: ") and "Traceback" not in message
