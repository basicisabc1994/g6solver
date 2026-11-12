"""One conversion for every exit code, and one quoting rule for every value that reaches a shell form."""
import ast
import base64
import inspect
import json
import os
import shlex
import subprocess
import sys

import pytest

from g6._allowance import statusline
from g6.allowance import CLAUDE_VERSION

windows = pytest.mark.skipif(os.name != "nt", reason="native Windows shells and exit codes")
SHELLS = ("cmd", "powershell", "sh")


# ---- exit codes

WINDOWS_CODES = [0, 1, 2, 7, 127, 255, 256, 2 ** 31 - 1, 2 ** 31, 2 ** 31 + 1, 0xC000013A, 2 ** 32 - 2, 2 ** 32 - 1]


@pytest.mark.parametrize("code", WINDOWS_CODES)
def test_every_windows_code_goes_out_as_the_signed_number_with_the_same_bits(code):
    out = statusline.exit_code(code, windows=True)
    assert -2 ** 31 <= out < 2 ** 31 and out % 2 ** 32 == code
    assert (out == 0) == (code == 0)


@pytest.mark.parametrize("ended,out", [(0, 0), (1, 1), (2, 2), (127, 127), (255, 255), (-1, 129), (-2, 130), (-9, 137),
                                       (-15, 143), (-64, 192), (-127, 255), (-128, 255), (-10 ** 6, 255), (256, 255)])
def test_elsewhere_a_code_is_itself_and_a_signal_death_is_128_plus_the_signal(ended, out):
    """It cannot be run here: -N is what ``Popen.returncode`` holds for a renderer ended by signal N."""
    assert statusline.exit_code(ended, windows=False) == out
    assert (out == 0) == (ended == 0), "nothing that failed goes out as success"


def test_the_one_exit_of_the_module_is_through_the_conversion():
    source = inspect.getsource(statusline)
    tree = ast.parse(source)
    exits = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
             and ast.unparse(node.func) in {"SystemExit", "sys.exit", "exit", "quit", "os._exit", "os.abort"}]
    assert exits == ["SystemExit(exit_code(main(own_process=True)))"]
    assert source.rstrip().endswith("raise SystemExit(exit_code(main(own_process=True)))")
    assert [ast.unparse(node.exc) for node in ast.walk(tree) if isinstance(node, ast.Raise) and node.exc is not None
            and "SystemExit" in ast.unparse(node.exc)] == exits
    # The renderer's code leaves run_wrapper and main untouched; only the exit converts it.
    wrapper = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_wrapper")
    nested = {id(node) for function in ast.walk(wrapper) if isinstance(function, ast.FunctionDef)
              and function is not wrapper for node in ast.walk(function)}
    returned = [ast.unparse(node.value) for node in ast.walk(wrapper)
                if isinstance(node, ast.Return) and node.value is not None and id(node) not in nested]
    assert returned == ["process.returncode"]


def _run(shell, command, **more):
    args = statusline.shell_argv(shell, command)
    if shell == "cmd":
        args = subprocess.list2cmdline(args[:4]) + ' "' + command + '"'
    return subprocess.run(args, input=b"{}", capture_output=True, timeout=240, **more)


@windows
@pytest.mark.parametrize("invocation_shell", ["cmd", "powershell"])
@pytest.mark.parametrize("code", [0, 7, 2 ** 31 - 1, 2 ** 31, 0xC000013A, 2 ** 32 - 2, 2 ** 32 - 1])
def test_a_renderers_code_reaches_the_caller_through_each_windows_form(tmp_path, monkeypatch, invocation_shell, code):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    original = f"echo rendered& exit {code if code < 2 ** 31 else code - 2 ** 32}"
    baseline = _run("cmd", original)
    assert (baseline.returncode, baseline.stdout) == (code, b"rendered\r\n"), "the fixture"
    p = statusline.preview({"statusLine": {"type": "command", "command": original}}, python=sys.executable,
                           profile="p", shell="cmd", invocation_shell=invocation_shell)
    wrapped = _run(invocation_shell, json.loads(p["settings_line"])["statusLine"]["command"])
    # One value, one host: cmd.exe writes "^C" itself when any child of its own ends with the Ctrl-C status (the
    # control below). The code still arrives whole; what the host's shell adds is not the wrapper's to remove.
    added = b"^C" if (code, invocation_shell) == (0xC000013A, "cmd") else b""
    assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (code, b"rendered\r\n", added)


@windows
@pytest.mark.parametrize("code,added", [(0xC000013A, b"^C"), (0xC000013A - 1, b""), (0xC000013A + 1, b"")])
def test_control_cmd_itself_writes_caret_c_for_any_child_that_ends_with_the_ctrl_c_status(code, added):
    """No wrapper is involved: a harmless child of cmd ends with the code, and cmd's own stderr is read."""
    child = f'"{sys.executable}" -c "raise SystemExit({code - 2 ** 32})"'
    done = _run("cmd", child)
    assert (done.returncode, done.stdout, done.stderr) == (code, b"", added)


@windows
@pytest.mark.parametrize("before", ["", "& cmd /c exit 0; ", "& cmd /c exit 5; "])
def test_the_powershell_form_fails_when_no_program_ran_whatever_ran_before_it(tmp_path, before):
    """``$LASTEXITCODE`` is set only by a native program that ran; one left by an earlier command is not this one's."""
    p = statusline.preview({"statusLine": {"type": "command", "command": "echo rendered"}},
                           python=str(tmp_path / "removed environment" / "python.exe"),
                           profile="p", shell="cmd", invocation_shell="powershell")
    wrapped = _run("powershell", before + json.loads(p["settings_line"])["statusLine"]["command"])
    assert wrapped.stdout == b"" and wrapped.returncode == 1


# ---- quoting

def _preview(python, form, **more):
    p = statusline.preview({"statusLine": {"type": "command", "command": "echo rendered"}}, python=python,
                           **{"profile": "p", "shell": "cmd", "invocation_shell": form, **more})
    return json.loads(p["settings_line"])["statusLine"]["command"]


def test_the_values_that_reach_a_shell_form_are_these_and_all_take_the_one_rule():
    """From the code: what preview interpolates, and that nothing reaches a form except through ``_quote``."""
    tree = ast.parse(inspect.getsource(statusline))
    preview = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "preview")
    args = next(node for node in ast.walk(preview) if isinstance(node, ast.Assign)
                and ast.unparse(node.targets[0]) == "args")
    assert [ast.unparse(element) for element in args.value.elts] == [
        "python", "'-P'", "'-m'", "'g6._allowance.statusline'", "'--renderer=' + token", "'--shell=' + shell",
        "'--profile=' + profile", "'--version=' + version"]
    forms = [node for node in ast.walk(preview) if isinstance(node, ast.Call) and ast.unparse(node.func) == "_quote"]
    assert [ast.unparse(node) for node in forms] == ["_quote(args, invocation_shell)"]
    # Of the five values, four have an alphabet of their own that no form reads as syntax; the fifth is free.
    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=.")
    assert set(base64.urlsafe_b64encode(bytes(range(256))).decode()) <= safe and set("".join(SHELLS)) <= safe
    assert set(CLAUDE_VERSION) <= safe
    with pytest.raises(ValueError):
        _preview("python", "sh", version="2.1.319;x")
    with pytest.raises(ValueError):
        _preview("python", "sh", profile="p;x")
    with pytest.raises(ValueError):
        _preview("python", "sh", shell="cmd;x")
    for shell in SHELLS:
        assert not safe & set(statusline.REFUSED[shell]) and not safe & set(statusline.POWERSHELL_QUOTES)
    assert set(statusline.REFUSED) == set(SHELLS)


CONTROLS = [chr(i) for i in range(32)] + ["\x7f"]
PROBES = sorted({chr(i) for i in range(32, 127)} | set(statusline.POWERSHELL_QUOTES)
                | {"\u201c", "\u201d", "\u00e9", "\u65e5", "\u00a0", "\u2028", "\ud800"} | set(CONTROLS))


def _refused(character, shell):
    return character in CONTROLS or character == "\ud800" or character in statusline.REFUSED[shell]


@pytest.mark.parametrize("shell", SHELLS)
def test_every_character_in_the_interpreter_path_is_refused_or_read_back_by_the_forms_own_rules(shell):
    """The refusals are the module's own constant; what is accepted is split the way that shell splits it."""
    accepted = []
    for character in PROBES:
        python = ("C:\\interp" + character + "reter folder\\python.exe" if shell != "sh"
                  else "/opt/interp" + character + "reter folder/python")
        if _refused(character, shell):
            with pytest.raises(ValueError):
                _preview(python, shell)
            continue
        accepted.append((python, _preview(python, shell)))
    assert len(accepted) >= 60
    expected = ["-P", "-m", "g6._allowance.statusline", "--renderer=" + base64.urlsafe_b64encode(b"echo rendered").decode(),
                "--shell=cmd", "--profile=p", "--version=" + CLAUDE_VERSION]
    if shell == "sh":
        for python, command in accepted:
            assert shlex.split(command) == [python, *expected], python
    elif shell == "cmd":
        if os.name != "nt":
            pytest.skip("Windows' own argument rules")
        import ctypes

        ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        for python, command in accepted:
            assert not set(command) & set(statusline.REFUSED["cmd"]) - {'"'}, "nothing left for cmd to read as syntax"
            assert command.startswith('"' + python + '" '), "the program is always quoted"
            count = ctypes.c_int()
            parsed = ctypes.windll.shell32.CommandLineToArgvW(command, ctypes.byref(count))
            assert [parsed[i] for i in range(count.value)] == [python, *expected], python
    else:
        if os.name != "nt":
            pytest.skip("Windows PowerShell reads its own literals")
        # PowerShell itself reads every literal the form wrote, in one process: each must be the value it stands for.
        literals = [command[command.index("& ") + 2:command.index("; if (")] for _, command in accepted]
        groups = []
        for start in range(0, len(literals), 20):  # a command line holds about twenty of them
            script = "; ".join("foreach ($v in @(" + body.replace("' '", "', '") + ")) { [Console]::Out.WriteLine("
                               "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($v))) }; "
                               "[Console]::Out.WriteLine('-')" for body in literals[start:start + 20])
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            done = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                                   encoded], capture_output=True, timeout=240)
            read = [group.split() for group in done.stdout.decode("ascii").split("-")][:len(literals[start:start + 20])]
            assert len(read) == len(literals[start:start + 20]), done.stderr[-300:]
            groups += read
        for (python, _), group in zip(accepted, groups, strict=True):
            assert [base64.b64decode(value).decode("utf-8") for value in group] == [python, *expected], python


@pytest.mark.parametrize("shell", SHELLS)
def test_an_empty_value_and_an_unknown_form_are_refused(shell):
    with pytest.raises(ValueError):
        statusline._quote(["python", ""], shell)
    with pytest.raises(ValueError):
        statusline._quote(["python"], shell + "x")
