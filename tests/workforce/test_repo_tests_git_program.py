# SPDX-License-Identifier: Apache-2.0
"""Every git command patch review runs starts the git the person installed, never one in the current folder (the
security review of W1-W5, F1).

``repo_tests._git`` ran its commands as a bare ``git``. On Windows, CreateProcess looks in the parent's current folder
(the project's, where the client starts G6's server) before PATH, unless ``NoDefaultCurrentDirectoryInExePath`` is set,
and G6 must not rely on that (``tests/core/test_outside.py``): a project could ship a ``git.exe`` that G6 then ran as
the person, before any boundary. The stand-in planted here is a copy of Python, which fails on git's arguments.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from g6.core.outside import find_program
from g6.workforce import repo_tests as rt

SRC = Path(rt.__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text("X = 1\n")
    git = find_program("git")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                                "-c", "commit.gpgsign=false", "commit", "-qm", "base")):
        subprocess.run([git, "-C", str(root), *args], check=True, capture_output=True)
    return root


@pytest.mark.skipif(os.name != "nt", reason="CreateProcess's search of the current folder is Windows'")
def test_a_git_exe_in_the_current_folder_is_never_the_git_that_runs(repo, tmp_path, monkeypatch):
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy2(sys.executable, project / "git.exe")      # run with git's arguments, it fails
    monkeypatch.chdir(project)
    head = rt._git(repo, "rev-parse", "HEAD").decode().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", head), head


def test_no_argv_g6_starts_begins_with_a_bare_git():
    # the list form was already guarded (test_outside's test_programs_are_found_only_through_this_module); a tuple too
    bare = re.compile(r"""[\[(]\s*["']git(?:\.exe)?["']\s*,""")
    hits = [f"{path.relative_to(SRC).as_posix()}:{number}" for path in sorted(SRC.rglob("*.py"))
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if bare.search(line)]
    assert hits == []
