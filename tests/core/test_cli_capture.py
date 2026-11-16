# SPDX-License-Identifier: Apache-2.0
"""CLI output belongs to G6 through encoding and interpreter shutdown."""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import tomllib

import pytest


SCRIPT = textwrap.dedent('''
    import atexit
    import importlib
    import logging
    import os
    import sys
    import threading
    import warnings
    from g6.core.registry import get_registry
    from g6.core.result import Result
    module, name = os.environ['G6_TEST_CLI_ENTRYPOINT'].split(':')
    entrypoint = getattr(importlib.import_module(module), name)

    kind = sys.argv[1]
    replied = threading.Event()
    def noisy():
        print("CAPTURE_SECRET")
        print("CAPTURE_SECRET", file=sys.stderr)
        warnings.warn("CAPTURE_SECRET")
        logging.getLogger("capture-test").error("CAPTURE_SECRET")
        class Finaliser:
            def __del__(self):
                raise ValueError("CAPTURE_SECRET")
        Finaliser()

    class Encoded:
        def __str__(self):
            noisy()
            return "answer"

    def infer(self, data):
        if kind == "encoder":
            return Result.ok({"answer": Encoded()})
        if kind == "shutdown":
            atexit.register(noisy)
        elif kind == "thread":
            def late():
                if not replied.wait(timeout=5):
                    raise RuntimeError("reply did not finish")
                noisy()
                raise ValueError("CAPTURE_SECRET")
            threading.Thread(target=late).start()
        elif kind == "uncaught":
            sys.excepthook(ValueError, ValueError("CAPTURE_SECRET"), None)
            noisy()
        else:
            noisy()
        return Result.ok({"answer": 42})
    get_registry().get_class("grounding").infer = infer
    code = entrypoint(["invoke", "grounding", "check_claim", "{}", *sys.argv[2:]])
    replied.set()
    raise SystemExit(code)
''')


@pytest.mark.parametrize("kind", ["op", "encoder", "shutdown", "thread", "uncaught"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_captures_python_output_until_shutdown(tmp_path, kind, json_mode):
    result = _run_console(tmp_path, SCRIPT, [kind, *(["--json"] if json_mode else [])])
    assert result.returncode == 0, result.stderr
    assert "CAPTURE_SECRET" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    answer = json.loads(result.stdout)
    if json_mode:
        assert answer["ok"] is True and answer["request_id"]
        assert answer["completion_state"] == "qualified-draft"
        answer = answer["result"]
    assert answer["answer"] == ("answer" if kind == "encoder" else 42)
    assert len(result.stderr.splitlines()) == 1
    assert "G6:" in result.stderr and "warnings" in result.stderr and "log records" in result.stderr
    if kind in ("shutdown", "thread"):
        assert "after the reply" in result.stderr


def _run_console(tmp_path, script, args):
    env = {**os.environ, "G6_HOME": str(tmp_path / "home"),
           "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe", "G6_CODEX": r"C:\nonexistent\g6-no-host.exe"}
    config = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text(encoding='utf-8'))
    env['G6_TEST_CLI_ENTRYPOINT'] = config['project']['scripts']['g6']
    return subprocess.run([sys.executable, "-X", "utf8", "-c", script, *args],
                          env=env, capture_output=True, text=True, timeout=30)


REENTRY_SCRIPT = textwrap.dedent('''
    import importlib
    import os
    import warnings
    from g6.cli import main
    from g6.core.registry import get_registry
    from g6.core.result import Result

    module, name = os.environ['G6_TEST_CLI_ENTRYPOINT'].split(':')
    entrypoint = getattr(importlib.import_module(module), name)
    def infer(self, data):
        print('CAPTURE_SECRET')
        warnings.warn('CAPTURE_SECRET')
        return Result.ok({'answer': 42})
    get_registry().get_class('grounding').infer = infer
    argv = ['invoke', 'grounding', 'check_claim', '{}', '--json']
    assert entrypoint(argv) == 0
    assert main(argv) == 0
    assert main(argv) == 0
''')


def test_main_twice_inside_console_capture_writes_three_whole_replies(tmp_path):
    result = _run_console(tmp_path, REENTRY_SCRIPT, [])
    assert result.returncode == 0, result.stderr
    remaining = result.stdout.lstrip()
    replies = []
    while remaining:
        reply, end = json.JSONDecoder().raw_decode(remaining)
        replies.append(reply)
        remaining = remaining[end:].lstrip()
    assert len(replies) == 3
    assert len({reply['request_id'] for reply in replies}) == 3
    assert all(reply['ok'] and reply['result'] == {'answer': 42} for reply in replies)
    assert len(result.stderr.splitlines()) == 3
    assert all('1 warnings' in line for line in result.stderr.splitlines())
    assert 'CAPTURE_SECRET' not in result.stdout + result.stderr

