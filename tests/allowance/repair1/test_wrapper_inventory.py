"""Harmless real renderers across the documented installation/shell forms."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from g6._allowance import statusline

SHELLS = ("cmd", "powershell", "sh")
TOKENS = ("p", "-local-profile", "--", "_", "z" * 80)


def test_shell_inventory_covers_each_production_branch_and_parser_choice():
    import ast
    import inspect
    for function in (statusline.shell_argv, statusline._quote):
        branches = set()
        for node in ast.walk(ast.parse(inspect.getsource(function))):
            if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "shell"
                    and len(node.comparators) == 1 and isinstance(node.comparators[0], ast.Constant)):
                branches.add(node.comparators[0].value)
        assert branches == set(SHELLS)
    tree = ast.parse(inspect.getsource(statusline.main))
    choices = next(keyword.value for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--shell"
                   for keyword in node.keywords if keyword.arg == "choices")
    assert set(ast.literal_eval(choices)) == set(SHELLS)


def invoke(shell, command, raw):
    args = statusline.shell_argv(shell, command)
    if shell == "cmd":
        args = subprocess.list2cmdline(args[:4]) + ' "' + command + '"'
    return subprocess.run(args, input=raw, capture_output=True, timeout=15)


@pytest.mark.parametrize("renderer_shell", SHELLS)
@pytest.mark.parametrize("invocation_shell", SHELLS)
@pytest.mark.parametrize("mode", ["stalled", "slow", "crashing", "oversized"])
@pytest.mark.skipif(os.name != "nt", reason="native Windows installation acceptance")
def test_shell_and_installation_inventory(tmp_path, monkeypatch, record_property, renderer_shell, invocation_shell, mode):
    if not shutil.which("sh") and os.name == "nt":
        git_bin = Path(os.environ.get("ProgramFiles", "")) / "Git" / "bin"
        if (git_bin / "sh.exe").is_file():
            monkeypatch.setenv("PATH", str(git_bin) + os.pathsep + os.environ.get("PATH", ""))
    if "sh" in (renderer_shell, invocation_shell) and not shutil.which("sh"):
        pytest.skip("POSIX execution is not established on this Windows host")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    # Fault injection at the OS child boundary, through real preview commands.
    observer = tmp_path / "observer.py"
    observer.write_text({
        "stalled": "import time; time.sleep(3)",
        "slow": "import sys,time; sys.stdin.buffer.read(); time.sleep(3)",
        "crashing": "raise SystemExit(17)",
        "oversized": "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*2000000)",
    }[mode], encoding="utf-8")
    (tmp_path / "sitecustomize.py").write_text(
        "import subprocess,sys\noriginal_popen=subprocess.Popen\n"
        "def popen(args, **kwargs):\n"
        f"    if '--observe' in args: args=[sys.executable, {str(observer)!r}]\n"
        "    return original_popen(args, **kwargs)\nsubprocess.Popen=popen\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    renderer = tmp_path / "renderer with spaces.py"
    renderer.write_text("import sys,time\ntime.sleep(.02)\np=sys.stdin.buffer.read()\n"
                        "sys.stdout.buffer.write(b'\\xff\\x00\\x81'+p)\n"
                        "sys.stderr.buffer.write(b'\\x80err\\x00')\nraise SystemExit(int(sys.argv[1]))\n",
                        encoding="utf-8")
    launcher = tmp_path / "renderer baseline.py"
    launcher.write_text(
        "import os,subprocess,sys\n"
        "from g6._allowance.statusline import decode_renderer,shell_argv\n"
        "args=shell_argv(sys.argv[1],decode_renderer(sys.argv[2]))\n"
        "if os.name=='nt' and sys.argv[1]=='cmd':\n"
        "    args=subprocess.list2cmdline(args[:4])+' \\\"'+args[4]+'\\\"'\n"
        "raise SystemExit(subprocess.run(args,input=sys.stdin.buffer.read()).returncode)\n", encoding="utf-8")
    raw = b'\x80\x00\xfe\r\n' + b' ' * (statusline.MAX_INPUT + 1)
    for code in (0, 1, 7, 255):
        if renderer_shell == "powershell":
            command = f"& '{sys.executable}' '{renderer}' {code}; exit $LASTEXITCODE"
        elif renderer_shell == "cmd":
            command = f'"{sys.executable}" "{renderer}" {code}'
        else:
            import shlex
            command = shlex.join([sys.executable, str(renderer), str(code)])
        start = time.monotonic()
        baseline = invoke(renderer_shell, command, raw)
        original_time = time.monotonic() - start
        assert baseline.returncode == code and len(baseline.stdout) >= len(raw)
        import base64
        token = base64.urlsafe_b64encode(command.encode()).decode("ascii")
        common_launcher = statusline._quote([sys.executable, str(launcher), renderer_shell, token], invocation_shell)
        start = time.monotonic()
        rendered = invoke(invocation_shell, common_launcher, raw)
        own_time = time.monotonic() - start
        assert (rendered.returncode, rendered.stdout, rendered.stderr) == (
            baseline.returncode, baseline.stdout, baseline.stderr)
        for profile in TOKENS:
            config = {"statusLine": {"type": "command", "command": command, "padding": 2}, "other": True}
            p = statusline.preview(config, python=sys.executable, profile=profile,
                                   shell=renderer_shell, invocation_shell=invocation_shell)
            installed = json.loads(p["settings_line"])
            start = time.monotonic()
            wrapped = invoke(invocation_shell, installed["statusLine"]["command"], raw)
            elapsed = time.monotonic() - start
            record_property(f"case-{code}-{profile}", json.dumps({
                "profile": profile, "exit": [baseline.returncode, wrapped.returncode],
                "stdout_sha256": [hashlib.sha256(p.stdout).hexdigest() for p in (baseline, wrapped)],
                "stderr_sha256": [hashlib.sha256(p.stderr).hexdigest() for p in (baseline, wrapped)],
                "renderer_seconds": own_time, "wrapped_seconds": elapsed,
                "original_renderer_seconds": original_time,
                "observer_budget_seconds": statusline.OBSERVER_SECONDS,
            }))
            assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (
                baseline.returncode, baseline.stdout, baseline.stderr), (code, profile)
            # The baseline includes the same outer shell and Python launch cost,
            # then runs the original renderer with no observer or cache access.
            assert elapsed < own_time + statusline.OBSERVER_SECONDS, (own_time, elapsed)
            assert installed["statusLine"]["padding"] == 2
            assert statusline.rollback(installed, p["original_config"]) == config


@pytest.mark.parametrize("token", ["a b", "a'b", 'a"b', "%", "^", "$", "é", "\ud800", "a\x00b"])
@pytest.mark.parametrize("shell", SHELLS)
def test_tokens_outside_the_token_rule_are_refused_at_preview(token, shell):
    with pytest.raises(ValueError):
        statusline.preview({"statusLine": {"type": "command", "command": "echo harmless"}},
                           python=sys.executable, profile=token, shell=shell, invocation_shell=shell)


@pytest.mark.parametrize("profile", TOKENS)
def test_observer_command_carries_each_accepted_token_exactly(tmp_path, monkeypatch, profile):
    from g6._allowance.store import Store
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    Store(tmp_path / "allowances").bind("claude", profile)
    raw = b'{"rate_limits":{"five_hour":{"used_percentage":100,"resets_at":2000000000}}}'
    assert statusline.observe_in_worker(raw, profile, "2.1.319")
    assert Store(tmp_path / "allowances").capture_path(profile).is_file()


@pytest.mark.parametrize("mode", ["stalled", "slow", "crashing", "oversized"])
def test_observer_inventory_preserves_bytes_exit_and_deadline(tmp_path, monkeypatch, capfdbinary, mode):
    observer = tmp_path / "observer.py"
    observer.write_text({
        "stalled": "import time; time.sleep(3)",
        "slow": "import sys,time; sys.stdin.buffer.read(); time.sleep(3)",
        "crashing": "raise SystemExit(17)",
        "oversized": "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*2000000)",
    }[mode], encoding="utf-8")
    real_popen = subprocess.Popen
    children = []
    observers = []
    def popen(args, **kwargs):
        is_observer = "--observe" in args
        if is_observer:
            args = [sys.executable, str(observer)]
        child = real_popen(args, **kwargs)
        children.append(child)
        if is_observer:
            observers.append(child)
        return child
    monkeypatch.setattr(statusline.subprocess, "Popen", popen)
    raw = b'\x00\xff' * (statusline.MAX_INPUT // 2)
    renderer = tmp_path / "renderer.py"
    renderer.write_text("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n"
                        "sys.stderr.buffer.write(b'\\xfferr')\nraise SystemExit(255)\n", encoding="utf-8")
    start = time.monotonic()
    try:
        code = statusline.run_wrapper(raw, [sys.executable, str(renderer)], observer=lambda data:
                                     statusline.observe_in_worker(data, "--", "2.1.319"))
        elapsed = time.monotonic() - start
        output = capfdbinary.readouterr()
        assert observers, "the child fault must actually be exercised"
        assert (code, output.out, output.err) == (255, raw, b'\xfferr')
        assert elapsed < 1.25
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def test_renderer_starts_without_waiting_for_observation(tmp_path):
    import threading
    renderer = tmp_path / "started"
    observed = threading.Event()
    def observer(raw):
        # A synchronously-called observer cannot see the renderer's marker.
        deadline = time.monotonic() + .6
        while time.monotonic() < deadline:
            if renderer.exists():
                observed.set()
                return
            time.sleep(.005)
    code = statusline.run_wrapper(b"input", [sys.executable, "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')", str(renderer)],
        observer=observer)
    assert code == 0 and observed.is_set()
