# SPDX-License-Identifier: Apache-2.0
"""The ``g6`` command (``g6.cli:entrypoint``) owns the process: what an op leaves behind after ``main`` returns, a
closed output pipe and a failed flush at shutdown all end as one G6 line, with no traceback, path or op text.

An in-process ``main()`` captures only for its call (embedding callers own their process); these children start the CLI
the way the console script does, so they hold the process-lifetime guarantees.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

LIBRARY = textwrap.dedent('''
    import threading
    import time
    import warnings


    def late_thread():
        def work():
            time.sleep(0.5)
            raise ZeroDivisionError("private late thread detail")
        threading.Thread(target=work).start()


    def late_warning():
        def work():
            time.sleep(0.5)
            warnings.warn("private late warning detail", RuntimeWarning)
        threading.Thread(target=work).start()
''')

# As the console script runs it: sys.exit(entrypoint()).
OP_SCRIPT = textwrap.dedent('''
    import sys
    sys.path.insert(0, sys.argv[1])
    import privatelib
    from g6.core.registry import get_registry
    from g6.core.result import Result
    from g6.cli import entrypoint
    kind = sys.argv[2]
    def infer(self, data):
        getattr(privatelib, kind)()
        return Result.ok({"answer": 42})
    get_registry().get_class("grounding").infer = infer
    sys.argv = ["g6", *sys.argv[3:]]
    sys.exit(entrypoint())
''')

MARKERS = ("private", "Traceback", "ZeroDivisionError", "Exception ignored", "Warning:")


def _env(tmp_path):
    env = {**os.environ, "G6_HOME": str(tmp_path / "home"),
           "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe", "G6_CODEX": r"C:\nonexistent\g6-no-host.exe"}
    env.pop("PYTHONWARNINGS", None)
    return env


@pytest.mark.parametrize("kind", ["late_thread", "late_warning"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_what_an_op_leaves_running_after_main_returns_never_reaches_stderr_raw(tmp_path, kind, json_mode):
    lib = tmp_path / "private_site"
    lib.mkdir()
    (lib / "privatelib.py").write_text(LIBRARY, encoding="utf-8")
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", OP_SCRIPT, str(lib), kind,
                             "invoke", "grounding", "check_claim", "{}", *(["--json"] if json_mode else [])],
                            capture_output=True, text=True, timeout=120, env=_env(tmp_path))
    leaked = [m for m in (*MARKERS, str(lib)) if m in result.stderr]
    assert not leaked, f"exit {result.returncode}; leaked {leaked}; stderr: {result.stderr[-700:]}"


def test_a_real_closed_pipe_ends_in_one_g6_line(tmp_path):
    script = 'import sys\nfrom g6.cli import entrypoint\nsys.argv = ["g6", *sys.argv[1:]]\nsys.exit(entrypoint())\n'
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-c", script, "invoke", "algebra", "ops", "{}", "--json"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(tmp_path))
    proc.stdout.close()
    err = proc.stderr.read().decode("utf-8", "replace")
    proc.wait(timeout=120)
    assert proc.returncode == 1, err
    assert "Traceback" not in err and "Errno" not in err and "Exception ignored" not in err, err[-700:]
    assert len(err.splitlines()) == 1 and "G6_E_INVOKE_FAILED" in err


@pytest.mark.parametrize("json_mode", [False, True])
def test_a_stdout_flush_failure_at_shutdown_stays_masked(tmp_path, json_mode):
    script = textwrap.dedent('''
        import sys
        from g6.cli import entrypoint
        class Output:
            def write(self, text):
                pass
            def flush(self):
                raise OSError("private flush detail")
        sys.stdout = Output()
        sys.argv = ["g6", *sys.argv[1:]]
        sys.exit(entrypoint())
    ''')
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script,
                             "invoke", "algebra", "ops", "{}", *(["--json"] if json_mode else [])],
                            capture_output=True, text=True, timeout=30, env=_env(tmp_path))
    assert result.returncode == 1, result.stderr[-700:]
    assert result.stdout == ""
    assert "G6_E_INVOKE_FAILED" in result.stderr and "private" not in result.stderr
    assert len(result.stderr.splitlines()) == 1
