"""The wrapper beside each kind of observer: the renderer never waits for one, and no observer is stopped.

Ordering is asserted from markers the processes write, not from a clock compared with a bound: the inventory in
test_wrapper_inventory.py holds the timing bound itself.
"""
import ast
import base64
import inspect
import io
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from g6._allowance import statusline

SHELL = "cmd" if os.name == "nt" else "sh"
GOOD = b'{"rate_limits":{"five_hour":{"used_percentage":50,"resets_at":2000000000}},"account_id":"discard"}'
OBSERVE = [sys.executable, "-P", "-m", "g6._allowance.statusline", "--observe", "--profile=p", "--version=2.1.319"]


def command_for(*args):
    return " ".join(f'"{a}"' for a in args) if os.name == "nt" else shlex.join([str(a) for a in args])


def installed(command, profile="p"):
    p = statusline.preview({"statusLine": {"type": "command", "command": command}}, python=sys.executable,
                           profile=profile, shell=SHELL, invocation_shell=SHELL)
    return json.loads(p["settings_line"])["statusLine"]["command"]


def invoke(command, raw):
    args = statusline.shell_argv(SHELL, command)
    if SHELL == "cmd":
        args = subprocess.list2cmdline(args[:4]) + ' "' + command + '"'
    return subprocess.run(args, input=raw, capture_output=True, timeout=60)


def echo_renderer(tmp_path):
    renderer = tmp_path / "renderer with spaces.py"
    renderer.write_text("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n"
                        "sys.stderr.buffer.write(b'\\x80err')\nraise SystemExit(7)\n", encoding="utf-8")
    return command_for(sys.executable, renderer)


def plant(tmp_path, monkeypatch, body):
    """Every process started below runs ``body`` first; G6's own code is otherwise unchanged."""
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "sitecustomize.py").write_text(body, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(planted) + os.pathsep + os.environ.get("PYTHONPATH", ""))


def until(condition, seconds=15):
    end = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > end:
            return False
        time.sleep(.02)
    return True


def test_the_renderer_is_started_before_the_observer_is_called(monkeypatch):
    original = subprocess.Popen
    started = threading.Event()
    order = []

    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        started.set()
        return child

    monkeypatch.setattr(statusline.subprocess, "Popen", popen)
    statusline.run_wrapper(b"", [sys.executable, "-c", "pass"], observer=lambda raw: order.append(started.is_set()))
    assert order == [True]


def test_an_observer_that_never_returns_holds_neither_the_renderer_nor_the_wrapper(capfdbinary):
    entered, release = threading.Event(), threading.Event()

    def observer(raw):
        entered.set()
        release.wait(8)

    start = time.monotonic()
    try:
        code = statusline.run_wrapper(b"\x00\xff", [sys.executable, "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); "
            "sys.stderr.buffer.write(b'\\x80err'); sys.exit(7)"], observer=observer)
        elapsed = time.monotonic() - start
        output = capfdbinary.readouterr()
        assert entered.is_set() and not release.is_set()
        assert (code, output.out, output.err) == (7, b"\x00\xff", b"\x80err")
        assert elapsed < 4, "the wrapper waited for an observer that was given eight seconds"
    finally:
        release.set()


def test_output_arrives_while_the_renderer_runs_and_the_observer_hangs(tmp_path):
    renderer = tmp_path / "renderer.py"
    release = tmp_path / "release"
    renderer.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "sys.stdout.buffer.write(b'\\xffout'); sys.stdout.buffer.flush()\n"
        "sys.stderr.buffer.write(b'\\x80err'); sys.stderr.buffer.flush()\n"
        "end=time.monotonic()+60\n"
        "while not Path(sys.argv[1]).exists() and time.monotonic()<end: time.sleep(.01)\n"
        "raise SystemExit(255)\n", encoding="utf-8")
    observed = tmp_path / "observed"
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import sys,time\nfrom pathlib import Path\nfrom g6._allowance.statusline import run_wrapper\n"
        "def observer(raw):\n    time.sleep(3)\n    Path(sys.argv[1]).write_text('observed')\n"
        "raise SystemExit(run_wrapper(b'', sys.argv[2:], observer=observer))\n", encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(wrapper), str(observed), sys.executable, str(renderer), str(release)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    results = []
    reader = threading.Thread(target=lambda: results.append((child.stdout.read(4), child.stderr.read(4))), daemon=True)
    reader.start()
    try:
        reader.join(30)
        assert results == [(b"\xffout", b"\x80err")]
        assert not observed.exists(), "the renderer's output waited for the observer"
        assert child.poll() is None, "the bytes must arrive before the renderer has ended"
        release.write_text("go", encoding="utf-8")
        assert child.wait(timeout=30) == 255
    finally:
        release.write_text("go", encoding="utf-8")
        if child.poll() is None:
            child.kill()
        reader.join(5)
        child.communicate(timeout=5)


@pytest.mark.parametrize("mode", ["hung", "slow", "failing"])
def test_the_wrapper_leaves_and_never_stops_its_observer(tmp_path, monkeypatch, mode):
    """Through the previewed command: a hung or slow observer ends by itself after the wrapper has returned."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    started, ended = tmp_path / "started", tmp_path / "ended"
    observer = tmp_path / "observer.py"
    observer.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text('started')\n" + {
            "hung": "time.sleep(4)\n",
            "slow": "sys.stdin.buffer.read()\ntime.sleep(4)\n",
            "failing": "raise SystemExit(17)\n",
        }[mode] + f"Path({str(ended)!r}).write_text(repr(time.time()))\n", encoding="utf-8")
    plant(tmp_path, monkeypatch,
          "import subprocess,sys\noriginal_popen=subprocess.Popen\n"
          "def popen(args, **kwargs):\n"
          f"    if '--observe' in args: args=[sys.executable, {str(observer)!r}]\n"
          "    return original_popen(args, **kwargs)\nsubprocess.Popen=popen\n")
    raw = b"\x00\xff" * (statusline.MAX_INPUT // 2)  # as large as an observer accepts: a pipe cannot hold it
    wrapped = invoke(installed(echo_renderer(tmp_path)), raw)
    returned = time.time()
    assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (7, raw, b"\x80err")
    assert until(started.exists), "the observer fault must actually be exercised"
    if mode == "failing":
        return
    assert until(ended.exists), "the wrapper's exit stopped its observer"
    assert float(ended.read_text()) > returned, "the wrapper waited for its observer to end"


def test_an_observation_that_ends_after_the_wrapper_left_is_there_for_the_next_report(tmp_path, monkeypatch):
    from g6._allowance.store import Store
    from g6.allowance import report

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    root = tmp_path / "home" / "allowances"
    root.mkdir(parents=True)
    (root / "settings.json").write_text(json.dumps({"profiles": {"claude": "p"}}), encoding="utf-8")
    assert report(refresh=False)["providers"]["claude"]["windows"] == []  # binds the profile; nothing observed yet
    # G6's own observer, held back for longer than a wrapper stays.
    plant(tmp_path, monkeypatch, "import sys,time\nif '--observe' in sys.argv: time.sleep(3)\n")
    wrapped = invoke(installed(echo_renderer(tmp_path)), GOOD)
    returned = datetime.now().astimezone()
    assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (7, GOOD, b"\x80err")
    capture = Store(root).capture_path("p")
    assert until(capture.exists), "the observer did not finish by itself"
    assert capture.stat().st_mtime > returned.timestamp(), "the wrapper waited for its observer"
    read_at = datetime.fromisoformat(json.loads(capture.read_text(encoding="utf-8"))["read_at"])
    assert read_at < returned, "a reading carries the time its input was read, not the time it was written"
    row = next(w for w in report(refresh=False)["providers"]["claude"]["windows"] if w["identity"] == "five_hour")
    assert datetime.fromisoformat(row["observed_at"]) == read_at and row["age_seconds"] > 0
    assert row["availability"] == "available" and row["headroom_share"] == .5
    late = report(now=read_at + timedelta(seconds=121), refresh=False)["providers"]["claude"]
    assert all(w["availability"] != "available" and w["headroom_share"] is None for w in late["windows"])
    assert next(w for w in late["windows"] if w["identity"] == "five_hour")["availability"] == "stale"
    assert late["recommendation"] == "unknown" and not late["suggest_increase"]


def observer_child(tmp_path, *more, stdin):
    env = dict(os.environ, G6_HOME=str(tmp_path / "home"))
    return subprocess.run([*OBSERVE, *more], input=stdin, capture_output=True, timeout=60, env=env)


def test_a_hand_over_cut_short_records_neither_a_reading_nor_a_failure(tmp_path):
    from g6._allowance.store import Store

    at = datetime.now().astimezone() - timedelta(seconds=60)
    whole = observer_child(tmp_path, f"--length={len(GOOD)}", "--observed-at=" + at.isoformat(), stdin=GOOD)
    assert whole.returncode == 0
    store = Store(tmp_path / "home" / "allowances")
    before = store.load("claude", "p")
    assert before.windows[0].used_share == .5 and not before.windows[0].failure
    later = (at + timedelta(seconds=5)).isoformat()
    cut = observer_child(tmp_path, f"--length={len(GOOD)}", "--observed-at=" + later, stdin=GOOD[:20])
    assert cut.returncode == 1
    assert store.load("claude", "p") == before
    # The same bytes handed over as a whole input are malformed, and say so.
    malformed = observer_child(tmp_path, "--length=20", "--observed-at=" + later, stdin=GOOD[:20])
    after = store.load("claude", "p")
    assert malformed.returncode == 1 and after.windows[0].failure and after.attempted_at > before.attempted_at
    assert after.windows[0].observed_at == before.windows[0].observed_at


def test_an_observer_that_ends_after_a_newer_one_changes_nothing(tmp_path):
    """Observers end in any order now; the cache and the capture keep the input that was read last."""
    from g6._allowance.store import Store

    at = datetime.now().astimezone() - timedelta(seconds=60)
    newer = GOOD.replace(b"50", b"70")
    assert observer_child(tmp_path, f"--length={len(newer)}", "--observed-at=" + (at + timedelta(seconds=10)).isoformat(),
                          stdin=newer).returncode == 0
    store = Store(tmp_path / "home" / "allowances")
    before, capture = store.load("claude", "p"), store.capture_path("p").read_bytes()
    assert before.windows[0].used_share == .7
    observer_child(tmp_path, f"--length={len(GOOD)}", "--observed-at=" + at.isoformat(), stdin=GOOD)
    assert store.load("claude", "p") == before and store.capture_path("p").read_bytes() == capture


def test_an_input_over_the_limit_is_a_failure_and_its_bytes_are_never_handed_over(tmp_path, monkeypatch):
    from g6._allowance.store import Store

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    received = tmp_path / "received"
    recorder = tmp_path / "recorder.py"
    recorder.write_text("import sys\nfrom pathlib import Path\n"
                        f"Path({str(received)!r}).write_text(str(len(sys.stdin.buffer.read())))\n", encoding="utf-8")
    original = subprocess.Popen
    seen = []

    def popen(args, **kwargs):
        seen.append(list(args))
        return original([sys.executable, str(recorder)], **kwargs)

    over = b" " * (statusline.MAX_INPUT + 1)
    with monkeypatch.context() as patched:
        patched.setattr(statusline.subprocess, "Popen", popen)
        assert statusline.observe_in_worker(over, "p", "2.1.319", timeout=30)
    assert received.read_text() == "0" and f"--length={len(over)}" in seen[0]
    at = datetime.now().astimezone() - timedelta(seconds=60)
    assert observer_child(tmp_path, f"--length={len(GOOD)}", "--observed-at=" + at.isoformat(), stdin=GOOD).returncode == 0
    child = observer_child(tmp_path, f"--length={len(over)}", "--observed-at=" + (at + timedelta(seconds=5)).isoformat(),
                           stdin=b"")
    reading = Store(tmp_path / "home" / "allowances").load("claude", "p")
    assert child.returncode == 1 and reading.windows[0].failure
    assert reading.windows[0].observed_at == at and reading.attempted_at == at + timedelta(seconds=5)


def test_a_detached_observer_bounds_its_own_life(tmp_path):
    env = dict(os.environ, G6_HOME=str(tmp_path / "home"))
    assert observer_child(tmp_path, f"--length={len(GOOD)}", stdin=GOOD).returncode == 0  # the module runs
    start = time.monotonic()
    starved = subprocess.Popen(OBSERVE, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=env)
    try:
        assert starved.wait(timeout=20) == 1, "an observer nobody feeds must end by itself"
        assert time.monotonic() - start >= statusline.OBSERVER_SECONDS * .9, "it ended for another reason"
    finally:
        if starved.poll() is None:
            starved.kill()
        starved.stdin.close()
        starved.wait(timeout=5)


def test_only_a_process_of_its_own_bounds_itself(tmp_path, monkeypatch):
    """Called inside another program, the observer must never arm an exit of that program."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    armed = []

    class Unarmed:
        daemon = True

        def __init__(self, *args, **kwargs):
            armed.append(args)

        def start(self):
            pass

    monkeypatch.setattr(statusline.threading, "Timer", Unarmed)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(GOOD)))
    assert statusline.main(["--observe", "--profile=p", f"--length={len(GOOD)}"]) == 0
    assert armed == []
    source = inspect.getsource(statusline)
    assert source.rstrip().endswith("raise SystemExit(exit_code(main(own_process=True)))")


def test_the_wrappers_own_interpreter_does_no_observer_work(tmp_path, monkeypatch):
    """No binding lookup, parsing or store access can hold the renderer: their modules are never loaded there."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    loaded = tmp_path / "loaded"
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import json,sys\nfrom g6._allowance import statusline\ncode=statusline.main(sys.argv[2:])\n"
        "names=[m for m in ('sqlite3','g6._allowance.store','g6._allowance.readers') if m in sys.modules]\n"
        "open(sys.argv[1],'w').write(json.dumps(names))\nraise SystemExit(code)\n", encoding="utf-8")
    token = base64.urlsafe_b64encode(echo_renderer(tmp_path).encode()).decode("ascii")
    done = subprocess.run([sys.executable, str(driver), str(loaded), "--renderer=" + token, "--shell=" + SHELL,
                           "--profile=p"], input=GOOD, capture_output=True, timeout=60)
    assert (done.returncode, done.stdout, done.stderr) == (7, GOOD, b"\x80err")
    assert json.loads(loaded.read_text()) == []


def test_a_renderer_that_cannot_be_started_still_leaves_its_observation():
    seen = []
    with pytest.raises(OSError):
        statusline.run_wrapper(b"synthetic", [str(Path(sys.executable).parent / "no such renderer")],
                               observer=seen.append)
    assert seen == [b"synthetic"]


def test_the_reading_carries_the_time_the_input_was_read(tmp_path, monkeypatch):
    from g6.allowance import utc_now

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))

    class SlowInput(io.BytesIO):
        received_at = None

        def read(self, *args):
            time.sleep(.2)
            self.received_at = utc_now()
            return super().read(*args)

    raw = SlowInput(b"{}")
    seen = []
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw))
    monkeypatch.setattr(statusline, "observe_in_worker", lambda *args, **kwargs: seen.append(kwargs["observed_at"]))
    command = command_for(sys.executable, "-c", "import sys; sys.stdin.buffer.read()")
    token = base64.urlsafe_b64encode(command.encode()).decode("ascii")
    assert statusline.main(["--profile=p", "--renderer=" + token, "--shell=" + SHELL]) == 0
    assert seen and seen[0] >= raw.received_at


def _references(source):
    """A module's tree, and every use of a name it imports, called or passed, as the dotted name it resolves to."""
    tree = ast.parse(source)
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound[alias.asname or alias.name] = (node.module or "") + "." + alias.name
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
    return tree, uses


def test_no_observer_is_stopped_and_every_observer_is_detached_from_the_code():
    """From the module's own source, with every name resolved through its imports.

    What it imports is listed whole, so a new way to start or end a process has to come in by a name that is not
    here; and of ``os`` and ``subprocess``, the two it holds that can do either, every use is listed, called or not.
    """
    tree, uses = _references(inspect.getsource(statusline))
    imported = {node.module if isinstance(node, ast.ImportFrom) else alias.name for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert imported == {
        "__future__", "argparse", "base64", "collections.abc", "copy", "datetime", "json", "os", "pathlib", "shlex",
        "subprocess", "sys", "threading", "typing", "g6.allowance", "g6._allowance.programs",
        "g6._allowance.readers", "g6._allowance.store"}
    # Nothing reaches a module by a name made at run time.
    assert not {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} & {
        "getattr", "setattr", "__import__", "eval", "exec", "globals", "locals", "vars", "compile", "importlib"}

    def taken(module):
        found = {}
        for name, _ in uses:
            if name.startswith(module + "."):
                found[name.split(".")[1]] = found.get(name.split(".")[1], 0) + 1
        return found

    assert taken("os") == {"environ": 5, "name": 4, "_exit": 1}
    assert taken("subprocess") == {"Popen": 2, "PIPE": 2, "DEVNULL": 2, "list2cmdline": 2, "CREATE_NEW_PROCESS_GROUP": 1}
    assert taken("sys") == {"executable": 1, "stdin": 3, "stderr": 1}

    # The two starts: each is a call, one in the observer's thread body and one for the renderer.
    calls = {id(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
    starts = [calls.get(id(node)) for name, node in uses if name == "subprocess.Popen"]
    assert all(starts), "a starter handed on instead of called"
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    def inside(name):
        return [call for call in starts if any(call is node for node in ast.walk(functions[name]))]

    (observer,), (renderer,) = inside("hand_over"), inside("run_wrapper")
    assert any(k.arg is None and ast.unparse(k.value) == "_DETACHED" for k in observer.keywords)
    assert {k.arg: ast.unparse(k.value) for k in observer.keywords if k.arg in ("stdout", "stderr")} == {
        "stdout": "subprocess.DEVNULL", "stderr": "subprocess.DEVNULL"}, "an observer must not hold the caller's output"
    assert {k.arg for k in renderer.keywords} == {"stdin", "env"}, "the renderer keeps the caller's own output handles"

    # What is done with a started process: its input, the wait for it and its exit code, and nothing that ends it.
    started = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(node.value is call for call in starts):
            started |= {target.id for target in node.targets if isinstance(target, ast.Name)}
        if isinstance(node, ast.withitem) and any(node.context_expr is call for call in starts):
            started.add(node.optional_vars.id)
    assert started == {"process"}
    assert {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id in started} == {
        "stdin", "wait", "communicate", "returncode"}
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in started
                and not any(isinstance(parent, ast.Attribute) and parent.value is node for parent in ast.walk(tree))
                and isinstance(node.ctx, ast.Load)], "a started process handed to something else"

    # The one exit is the observer's own bound on itself, handed to a timer that is armed only in a process of its own.
    timers = [calls[id(node)] for name, node in uses if name == "threading.Timer"]
    assert [ast.unparse(timer) for timer in timers] == ["threading.Timer(OBSERVER_SECONDS, os._exit, (1,))"]
    guard = next(node for node in ast.walk(functions["main"]) if isinstance(node, ast.If)
                 and any(timers[0] is inner for inner in ast.walk(node)) and ast.unparse(node.test) == "own_process")
    assert guard is not None

    # Both platforms' detachment, read from the source: only one of the two branches runs on any host.
    detached = next(node for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)
                    and ast.unparse(node.target) == "_DETACHED").value
    assert isinstance(detached, ast.IfExp) and ast.unparse(detached.test) == "os.name == 'nt'"
    assert ast.unparse(detached.body) == "{'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}"
    assert ast.unparse(detached.orelse) == "{'start_new_session': True}"
    assert statusline._DETACHED == ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                                    else {"start_new_session": True})


def test_a_wrapper_stays_for_less_than_the_bound_its_inventory_allows():
    assert 0 < statusline.STAY_SECONDS < statusline.OBSERVER_SECONDS


@pytest.mark.skipif(os.name != "nt", reason="native Windows console")
def test_an_observer_opens_no_console_window_of_its_own(tmp_path, monkeypatch):
    """A virtual environment's python.exe starts the real one as its child; neither may open a window."""
    import ctypes

    seen = tmp_path / "window"
    probe = tmp_path / "probe.py"
    probe.write_text("import ctypes,json\nfrom pathlib import Path\n"
                     "window=ctypes.windll.kernel32.GetConsoleWindow()\n"
                     f"Path({str(seen)!r}).write_text(json.dumps([window, "
                     "bool(window and ctypes.windll.user32.IsWindowVisible(window))]))\n", encoding="utf-8")
    original = subprocess.Popen
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(statusline.subprocess, "Popen",
                        lambda args, **kwargs: original([sys.executable, str(probe)], **kwargs))
    assert statusline.observe_in_worker(b"{}", "p", "2.1.319", timeout=30)
    window, visible = json.loads(seen.read_text())
    assert not visible or window == ctypes.windll.kernel32.GetConsoleWindow(), "the observer opened a window"


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell invocation")
@pytest.mark.parametrize("code", [0, 1, 7, 255])
def test_powershell_passes_on_every_exit_code_exactly(tmp_path, monkeypatch, code):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    original = f"[Console]::Out.Write('out'); [Console]::Error.Write('err'); exit {code}"
    p = statusline.preview({"statusLine": {"type": "command", "command": original}},
                           python=sys.executable, profile="p", shell="powershell", invocation_shell="powershell")
    result = subprocess.run(statusline.shell_argv("powershell", json.loads(p["settings_line"])["statusLine"]["command"]),
                            input=b"{}", capture_output=True, timeout=60)
    assert (result.returncode, result.stdout, result.stderr) == (code, b"out", b"err")
