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

#: The file each child runs first, in the parent's resolved G6 package.
START = str(Path(g6.__file__).resolve().parent / "core" / "child_imports.py")


@pytest.mark.parametrize("isolated", [False, True])
def test_child_imports_uninstalled_source_without_pythonpath(isolated, tmp_path):
    from g6.core.child_imports import python_command

    argv = python_command("g6", isolated=isolated)
    # -S removes installed packages and editable-install hooks. The real g6 __init__ needs only stdlib.
    argv.insert(1, "-S")
    argv[-1:] = ["json.tool", "--help"]  # run_module keeps the ordinary module CLI
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    done = subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert "usage:" in done.stdout


@pytest.mark.parametrize("isolated", [False, True])
def test_child_reports_the_parents_package_and_excludes_cwd(isolated, tmp_path):
    from g6.core.child_imports import python_command

    # The start file runs -c code as well as modules, so the import location can be observed directly.
    argv = python_command("g6", isolated=isolated)
    argv.insert(1, "-S")
    argv[-2:] = ["-c", "import g6,json,sys; print(json.dumps([g6.__file__, sys.path]))"]
    project, person = tmp_path / "project", tmp_path / "person"
    project.mkdir()
    person.mkdir()
    env = dict(os.environ, PYTHONPATH=str(person))
    done = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    location, paths = json.loads(done.stdout)
    assert Path(location).resolve() == Path(g6.__file__).resolve()
    assert "" not in paths and str(project) not in paths
    # A registered client's child keeps the person's own PYTHONPATH; an isolated worker ignores it.
    assert (str(person) in paths) is not isolated


@pytest.mark.parametrize("isolated", [False, True])
def test_a_sitecustomize_on_the_persons_pythonpath_runs_only_in_a_registered_clients_child(isolated, tmp_path):
    from g6.core.child_imports import python_command

    person = tmp_path / "person"
    person.mkdir()
    marker = tmp_path / "ran"
    (person / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    argv = python_command("import g6", mode="-c", isolated=isolated)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(person), *filter(None, [os.environ.get("PYTHONPATH")])]))
    done = subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert marker.exists() is not isolated


@pytest.mark.parametrize("isolated,flags", [(False, ["-P"]), (True, ["-I"])])
def test_the_command_is_the_flag_the_start_file_and_the_target(isolated, flags):
    """The child runs G6's own start file by its resolved path: the command holds no program text of its own, so a
    status line command or a registration stays far inside Windows' command-line limits (a 400-character -c bootstrap
    pushed the PowerShell form past them)."""
    from g6.core.child_imports import python_args, python_command

    assert python_args("g6.x", isolated=isolated) == [*flags, START, "-m", "g6.x"]
    assert python_command("g6.x", python="py", isolated=isolated) == ["py", *flags, START, "-m", "g6.x"]
    assert python_command("pass", mode="-c", isolated=isolated) == [sys.executable, *flags, START, "-c", "pass"]


def test_server_args_stay_the_plugins_start_and_name_no_root():
    from g6.cli.setup import SERVER_ARGS

    # The plugin's .mcp.json holds SERVER_ARGS itself and cannot know a root; setup registers the same start through this
    # installation's start file (tests/core/test_mcp_registered_command.py holds that command exactly).
    assert SERVER_ARGS == ("-P", "-c", "from g6.mcp import main; main()")


def test_statusline_preview_passes_parent_package_root():
    from g6._allowance.statusline import preview

    result = preview({"statusLine": {"type": "command", "command": "echo fixture"}},
                     python=sys.executable, profile="fixture")
    command = json.loads(result["settings_line"])["statusLine"]["command"]
    assert START in command
    assert "-P" in command


def test_algebra_worker_uses_explicit_parent_root(monkeypatch):
    from g6.algebra import runner

    class Captured(Exception):
        pass

    def execute(argv, *args, **kwargs):
        assert argv[1:] == ["-I", START, "-m", "g6.algebra.worker"]
        raise Captured

    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises(Captured):
        runner.call_worker({}, seconds=1, memory_mb=128, cap=1000, record={})
