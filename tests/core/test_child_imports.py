# SPDX-License-Identifier: Apache-2.0
"""G6's trusted module workers import the parent's checkout after safe startup."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import g6


@pytest.mark.parametrize("isolated", [False, True])
def test_child_imports_uninstalled_source_without_pythonpath(isolated):
    from g6.core.child_imports import python_command

    argv = python_command("g6", isolated=isolated)
    # -S removes installed packages and editable-install hooks. The real g6 __init__ needs only stdlib.
    argv.insert(1, "-S")
    argv[-1:] = ["json.tool", "--help"]  # first prove run_module preserves the ordinary module CLI
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    done = subprocess.run(argv, cwd=Path(__file__).resolve().parents[2] / ".build", env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert "usage:" in done.stdout


@pytest.mark.parametrize("isolated", [False, True])
def test_child_reports_the_parents_package_and_excludes_cwd(isolated):
    from g6.core.child_imports import python_command

    # The bootstrap supports -c as well as modules so import location can be observed directly.
    argv = python_command("g6", isolated=isolated)
    argv.insert(1, "-S")
    argv[-2:] = ["-c", "import g6,json,sys; print(json.dumps([g6.__file__, sys.path]))"]
    cwd = Path(__file__).resolve().parents[2] / ".build"
    env = dict(os.environ, PYTHONPATH=str(cwd))
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    location, paths = json.loads(done.stdout)
    assert Path(location).resolve() == Path(g6.__file__).resolve()
    assert "" not in paths and str(cwd) not in paths


def test_mcp_registration_passes_parent_package_root():
    from g6.cli.setup import SERVER_ARGS

    assert "-P" in SERVER_ARGS
    assert str(Path(g6.__file__).resolve().parent.parent) in SERVER_ARGS
    assert SERVER_ARGS[-2:] == ("-c", "from g6.mcp import main; main()")


def test_statusline_preview_passes_parent_package_root():
    from g6._allowance.statusline import preview

    result = preview({"statusLine": {"type": "command", "command": "echo fixture"}},
                     python=sys.executable, profile="fixture")
    command = json.loads(result["settings_line"])["statusLine"]["command"]
    assert str(Path(g6.__file__).resolve().parent.parent) in command
    assert "-P" in command


def test_algebra_worker_uses_explicit_parent_root(monkeypatch):
    from g6.algebra import runner

    class Captured(Exception):
        pass

    def execute(argv, *args, **kwargs):
        assert "-I" in argv and "-c" in argv
        assert str(Path(g6.__file__).resolve().parent.parent) in argv
        assert argv[-2:] == ["-m", "g6.algebra.worker"]
        raise Captured

    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises(Captured):
        runner.call_worker({}, seconds=1, memory_mb=128, cap=1000, record={})
