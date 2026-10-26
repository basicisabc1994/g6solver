# SPDX-License-Identifier: Apache-2.0
"""``GitCheckpoint`` checkpoints a tree an agent was editing, so the agent may have planted a hook or a filter there.

Review B2-M5: the checkpoint ran ``git add``, ``commit`` and ``reset --hard`` under the repository's own hooks,
filters, fsmonitor and signing settings, so a ``pre-commit`` hook or a ``clean`` filter the agent wrote ran on the host
at checkpoint time. It now runs git as ``g6.core.git_env`` does, and refuses a repository holding a submodule. Offline;
real git; tmp_path repositories only.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from g6.csf.rollback import GitCheckpoint
from g6.csf.schema import CSFError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture
def planted(tmp_path: Path) -> tuple[Path, Path, str]:
    """A repository with one commit of ``a.txt``, then a ``pre-commit`` hook, a ``clean`` and ``smudge`` filter every
    file uses, ``core.fsmonitor``, an external diff and commit signing through a planted program, each writing to a
    marker; ``a.txt`` changed since."""
    repo, marker, script = tmp_path / "repo", tmp_path / "marker", tmp_path / "plant.py"
    script.write_text(f"import pathlib, sys\npathlib.Path(r'{marker}').open('a').write(' '.join(sys.argv) + '\\n')\n"
                      "if sys.argv[-1] in ('clean', 'smudge'):\n    sys.stdout.write(sys.stdin.read())\n",
                      encoding="utf-8")
    command = f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'
    repo.mkdir()
    _git(repo, "init", "-q")
    for key, value in (("user.name", "g6 test"), ("user.email", "test@example.invalid"), ("core.autocrlf", "false")):
        _git(repo, "config", key, value)
    (repo / "a.txt").write_text("one", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={tmp_path / 'none'}", "commit", "-qm", "first")
    first = _git(repo, "rev-parse", "HEAD").stdout.strip()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text(f"#!/bin/sh\n{command} pre-commit\n", encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    (repo / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    for key, value in (("core.hooksPath", hooks.as_posix()), ("filter.evil.clean", command + " clean"),
                       ("filter.evil.smudge", command + " smudge"), ("core.fsmonitor", command + " fsmonitor"),
                       ("diff.external", command + " diff"), ("commit.gpgSign", "true"),
                       ("gpg.program", command + " gpg")):
        _git(repo, "config", key, value)
    (repo / "a.txt").write_text("two", encoding="utf-8")
    return repo, marker, first


def test_the_planted_programs_run_under_plain_git(planted, tmp_path: Path) -> None:
    """Survival control, on a copy of the planted repository: the hook and the filter are live."""
    repo, marker, _ = planted
    copy = tmp_path / "copy"
    shutil.copytree(repo, copy)
    _git(copy, "add", "--update")
    _git(copy, "commit", "--quiet", "-m", "control")
    said = marker.read_text(encoding="utf-8")
    assert "clean" in said and "pre-commit" in said


def test_a_checkpoint_never_runs_a_hook_or_filter_planted_in_the_tree(planted) -> None:
    repo, marker, first = planted
    checkpoint = GitCheckpoint(repo, allow_reset=True)
    sha = checkpoint.snapshot("checkpoint")
    assert sha != first and _git(repo, "show", f"{sha}:a.txt").stdout == "two"   # the file's own bytes, unfiltered
    assert checkpoint.reset(first) == first == checkpoint.head()
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one"
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_a_repository_holding_a_submodule_is_refused(tmp_path: Path) -> None:
    """``git add --update`` checks a submodule's files by running git inside it, under its own config."""
    repo = tmp_path / "repo"
    sub = repo / "sub"
    sub.mkdir(parents=True)
    for folder in (repo, sub):
        _git(folder, "init", "-q")
        (folder / "a.txt").write_text("one", encoding="utf-8")
    for folder, paths in ((sub, ["a.txt"]), (repo, ["a.txt", "sub"])):
        _git(folder, "add", *paths)
        _git(folder, "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", "commit",
             "-qm", "c")
    with pytest.raises(CSFError) as caught:
        GitCheckpoint(repo).snapshot()
    assert caught.value.code == "G6_E_CSF_GIT_UNSAFE" and "submodule" in str(caught.value)
