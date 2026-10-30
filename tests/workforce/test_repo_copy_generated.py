# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A repository copy takes a git-ignored file the project's own build writes (worked examples, round 2: black's
``src/_black_version.py``, written by hatch-vcs at install time and ignored by git, so no check G6 ran in a copy could
import black), and nothing else that git ignores.

Pinned: the file is taken only when a build configuration file at the top, as committed at HEAD, names its path in
quotes, and the ignored-file scan finished and judged it ``other`` (not a runner, start-up, path or test file, not named
like a tracked module); the copy reports each file it took with its SHA-256. A file named only by an uncommitted edit,
one named like a tracked module, a test-named one, a conftest, and an ignored file no configuration names stay out.
SECURITY-SENSITIVE: pending an independent security review. Real git; offline.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from g6.workforce import repo_tests as rt

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}
PYPROJECT = '[tool.hatch.build.hooks.vcs]\nversion-file = "src/_pkg_version.py"\n'
VERSION = 'version = "1.0"\n'


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True, text=True, env=GIT_ENV).stdout


def make_repo(root: Path, pyproject: str = PYPROJECT) -> Path:
    """src/pkg/__init__.py importing the generated ``_pkg_version``; .gitignore ignoring it; pyproject naming it."""
    repo = root / "proj"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("from _pkg_version import version\n", encoding="utf-8")
    (repo / "src" / "pkg" / "core.py").write_text("X = 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("src/_pkg_version.py\nsrc/pkg/core_gen.py\nsrc/core.py\nsrc/test_gen.py\n"
                                     "src/conftest.py\nsrc/other_gen.py\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "src" / "_pkg_version.py").write_text(VERSION, encoding="utf-8")   # what the build writes at install
    return repo


def copied(repo: Path) -> tuple[set[str], dict]:
    report: dict = {}
    with rt.repository_copy(repo, deadline=time.monotonic() + 60, report=report) as copy:
        files = {p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.is_file()}
    return files, report


def test_a_generated_file_the_committed_build_configuration_names_is_copied(tmp_path):
    repo = make_repo(tmp_path)
    assert git(repo, "status", "--porcelain") == ""          # git ignores it
    files, report = copied(repo)
    assert "src/_pkg_version.py" in files
    digest = hashlib.sha256((repo / "src" / "_pkg_version.py").read_bytes()).hexdigest()
    assert report["generated_copied"] == {"src/_pkg_version.py": digest}


def test_an_ignored_file_no_configuration_names_stays_out(tmp_path):
    repo = make_repo(tmp_path, pyproject="[project]\nname = 'pkg'\n")
    files, report = copied(repo)
    assert "src/_pkg_version.py" not in files and "generated_copied" not in report


def test_a_name_only_an_uncommitted_edit_adds_stays_out(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src" / "other_gen.py").write_text("Y = 2\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(PYPROJECT + 'extra = "src/other_gen.py"\n', encoding="utf-8")
    files, report = copied(repo)
    assert "src/other_gen.py" not in files
    assert set(report["generated_copied"]) == {"src/_pkg_version.py"}


@pytest.mark.parametrize("rel", ["src/core.py", "src/test_gen.py", "src/conftest.py"])
def test_a_named_file_that_could_shadow_or_load_stays_out(tmp_path, rel):
    # named like the tracked src/pkg/core.py, test-named, or a conftest: the scan judges each loadable
    repo = make_repo(tmp_path, pyproject=PYPROJECT + f'other = "{rel}"\n')
    (repo / rel).write_text("Z = 3\n", encoding="utf-8")
    files, report = copied(repo)
    assert rel not in files and set(report["generated_copied"]) == {"src/_pkg_version.py"}


def test_a_path_out_of_the_repository_is_never_taken(tmp_path):
    repo = make_repo(tmp_path, pyproject=PYPROJECT + 'o = "../outside.py"\np = "/etc/x.py"\n')
    (tmp_path / "outside.py").write_text("W = 4\n", encoding="utf-8")
    files, report = copied(repo)
    assert set(report["generated_copied"]) == {"src/_pkg_version.py"}
    assert not any("outside" in f for f in files)
