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


# --- the review of 286c173 (2026-10-30) ------------------------------------------------------------------------------

#: What build tools write: black's own template (the file black #5634's image holds), hatch-vcs's and setuptools-scm's
#: older template, and setuptools-scm 8's and 9's typed templates.
SCM_8 = '''# file generated by setuptools-scm
# don't change, don't track in version control

__all__ = ["__version__", "__version_tuple__", "version", "version_tuple"]

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Tuple
    from typing import Union

    VERSION_TUPLE = Tuple[Union[int, str], ...]
else:
    VERSION_TUPLE = object

version: str
__version__: str
__version_tuple__: VERSION_TUPLE
version_tuple: VERSION_TUPLE

__version__ = version = '8.1.dev3+g0123abc'
__version_tuple__ = version_tuple = (8, 1, 'dev3', 'g0123abc')
'''
SCM_9 = '''"""Generated by setuptools-scm."""
from __future__ import annotations

__all__ = ["__version__", "__version_tuple__", "version", "version_tuple", "__commit_id__", "commit_id"]

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Tuple, Union

    VERSION_TUPLE = Tuple[Union[int, str], ...]
    COMMIT_ID = Union[str, None]
else:
    VERSION_TUPLE = object
    COMMIT_ID = object

version: str
__version__: str
__version_tuple__: VERSION_TUPLE
version_tuple: VERSION_TUPLE
commit_id: COMMIT_ID
__commit_id__: COMMIT_ID

__version__ = version = '9.0.1'
__version_tuple__ = version_tuple = (9, 0, 1)

__commit_id__ = commit_id = None
'''
TAKEN = {"black": 'version = "26.10.0"\n',
         "older template": "# coding: utf-8\n# file generated by setuptools_scm\n"
                           "__version__ = version = '1.2.3'\n__version_tuple__ = version_tuple = (1, 2, 3)\n",
         "setuptools-scm 8": SCM_8, "setuptools-scm 9": SCM_9}
#: Bodies that run code (or could) on import: the patch's author writes the ignored file, it is in no diff, and both
#: copies import it, so code in it could forge both halves of a fail-before, pass-after check.
REFUSED = {"import": 'import os\nversion = "1"\n',
           "call": 'version = str(1)\n',
           "statement": 'version = "1"\nprint(version)\n',
           "function": 'def version():\n    return "1"\n',
           "class": 'class Version:\n    pass\n',
           "exec": 'exec("import os")\n',
           "attribute": 'version = "1".upper\n',
           "conditional": 'version = "1" if True else "2"\n',
           "dunder": '__path__ = ["elsewhere"]\n',
           "flag set true": SCM_8.replace("TYPE_CHECKING = False", "TYPE_CHECKING = True"),
           "flag reassigned": SCM_8.replace("version: str\n", "TYPE_CHECKING = 1\nversion: str\n"),
           "code in the stub": SCM_8.replace("    VERSION_TUPLE = object", "    import os\n    VERSION_TUPLE = object"),
           "import in the stub": SCM_8.replace("from typing import Tuple", "from os import system"),
           "call in an annotation": 'version: print("x") = "1"\n',
           "not Python": "version = (\n"}


def made(tmp_path: Path, body: str) -> Path:
    repo = make_repo(tmp_path)
    (repo / "src" / "_pkg_version.py").write_text(body, encoding="utf-8")
    return repo


@pytest.mark.parametrize("body", list(TAKEN.values()), ids=list(TAKEN))
def test_the_files_build_tools_write_are_taken(tmp_path, body):
    files, report = copied(made(tmp_path, body))
    assert "src/_pkg_version.py" in files and set(report["generated_copied"]) == {"src/_pkg_version.py"}
    assert "generated_left_out" not in report


@pytest.mark.parametrize("body", list(REFUSED.values()), ids=list(REFUSED))
def test_a_generated_file_holding_more_than_literal_data_is_not_taken_and_says_why(tmp_path, body):
    files, report = copied(made(tmp_path, body))
    assert "src/_pkg_version.py" not in files and "generated_copied" not in report
    assert report["generated_left_out"]["src/_pkg_version.py"]   # a reason, said


def test_the_bytes_copied_are_the_bytes_checked_and_hashed(tmp_path, monkeypatch):
    """Read once: a file rewritten after G6 checked it is copied as G6 checked it, and its SHA-256 is of those bytes."""
    repo = make_repo(tmp_path)
    original = (repo / "src" / "_pkg_version.py").read_bytes()
    outside_temp = rt.outside_temp

    def rewritten(where, *args):   # called after the check, before the files are copied
        (repo / "src" / "_pkg_version.py").write_text("import os\nos.system('x')\n", encoding="utf-8")
        return outside_temp(where, *args)

    monkeypatch.setattr(rt, "outside_temp", rewritten)
    report: dict = {}
    with rt.repository_copy(repo, deadline=time.monotonic() + 60, report=report) as copy:
        held = (copy / "src" / "_pkg_version.py").read_bytes()
    assert held == original
    assert report["generated_copied"] == {"src/_pkg_version.py": hashlib.sha256(original).hexdigest()}


def test_a_name_only_a_commit_after_the_base_adds_stays_out(tmp_path):
    """The names come from the review's base commit, not HEAD: setup.py is no runner file, so a setup.py the agent
    committed after the base could name any ignored file unflagged."""
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "setup.py").write_text('extra = "src/other_gen.py"\n', encoding="utf-8")
    git(repo, "add", "setup.py")
    git(repo, "commit", "-q", "-m", "after the base")
    (repo / "src" / "other_gen.py").write_text("Y = 2\n", encoding="utf-8")
    assert "src/other_gen.py" in copied(repo)[0]          # named at HEAD: taken when there is no base
    report: dict = {}
    with rt.repository_copy(repo, deadline=time.monotonic() + 60, report=report, base=base) as copy:
        files = {p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.is_file()}
    assert "src/other_gen.py" not in files and set(report["generated_copied"]) == {"src/_pkg_version.py"}


class Stop(Exception):
    pass


def test_every_copy_made_for_a_change_reads_the_names_at_its_base(tmp_path, monkeypatch):
    from g6.harness import verification_search as vs

    repo = make_repo(tmp_path)
    (repo / "src" / "pkg" / "core.py").write_text("X = 2\n", encoding="utf-8")
    change = rt.change(repo, "HEAD")
    seen: list = []

    def recorded(where, base=None, deadline=None):
        seen.append(base)
        raise Stop

    monkeypatch.setattr(rt, "_generated_files", recorded)
    for call in (lambda: rt.run_at_base(change, ["tests"]),
                 lambda: vs._run_check(change, "t_x.py", "", "t_x.py", at_base=False, timeout_s=5),
                 lambda: vs._run_mutant(change, "src/pkg/core.py", "X = 3\n", ["t_x.py"], 5)):
        with pytest.raises(Stop):
            call()
    with pytest.raises(Stop):   # the old tests' replay: a changed test file it runs at the base
        (repo / "tests").mkdir()
        (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
        git(repo, "add", "tests/test_a.py")
        git(repo, "commit", "-q", "-m", "a test")
        (repo / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")
        rt.run_before(rt.change(repo, "HEAD"))
    assert seen == [change.base_commit] * 3 + [git(repo, "rev-parse", "HEAD").strip()]


def test_the_scan_keeps_to_the_copy_deadline(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    timeouts: list = []
    scanning = [False]
    real, generated = rt._git, rt._generated_files

    def timed(where, *args, timeout_s=rt.GIT_TIMEOUT_S, **kwargs):
        if scanning[0]:
            timeouts.append(timeout_s)
        return real(where, *args, timeout_s=timeout_s, **kwargs)

    def watched(*args, **kwargs):   # only the git calls the generated-file rule makes
        scanning[0] = True
        try:
            return generated(*args, **kwargs)
        finally:
            scanning[0] = False

    monkeypatch.setattr(rt, "_git", timed)
    monkeypatch.setattr(rt, "_generated_files", watched)
    report: dict = {}
    with rt.repository_copy(repo, deadline=time.monotonic() + 30, report=report):
        pass
    assert report["generated_copied"] and timeouts and max(timeouts) <= 30


def test_a_scan_git_refuses_takes_nothing_and_the_copy_is_still_made(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)

    def refused(*args, **kwargs):
        raise rt.RepoRefused("G6_E_GIT_COMMAND_FAILED", "git ls-files failed (TimeoutExpired)")

    monkeypatch.setattr(rt, "ignored_inputs", refused)
    files, report = copied(repo)
    assert "src/pkg/__init__.py" in files and "src/_pkg_version.py" not in files
    assert "generated_copied" not in report
