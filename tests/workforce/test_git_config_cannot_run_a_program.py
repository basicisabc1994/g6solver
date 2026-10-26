# SPDX-License-Identifier: Apache-2.0
"""A working copy the agent controlled can carry a planted ``.git/config``: ``core.fsmonitor`` or a filter driver that
names a program git would run on the host. Every git command the workforce runs in such a copy (``verify`` and
``repo_tests``) runs with a clean environment and those keys switched off, so the planted program never runs (conformance
K1). The same holds for a nested repository's own config (review B2-C1) and for a promisor remote git would fetch a
missing object from (review B2-C2). Offline; real git."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from g6.workforce import repo_tests, verify

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
#: The refusal of a copy that holds a gitlink (``g6.core.git_env.SUBMODULE_REFUSED``).
SUBMODULE_REFUSED = "G6_E_GIT_SUBMODULE_REFUSED"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60, check=False)


def _commit(repo: Path, message: str = "c") -> None:
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", message)


def _marker_program(tmp_path: Path) -> tuple[Path, list[str]]:
    """A marker file and the argv of a program that appends its arguments to it (as a filter, it passes stdin on; as
    anything else it exits at once, so a transport waiting on it never hangs)."""
    marker, script = tmp_path / "marker", tmp_path / "plant.py"
    script.write_text(f"import pathlib, sys\npathlib.Path(r'{marker}').open('a').write(' '.join(sys.argv) + '\\n')\n"
                      "if sys.argv[-1] in ('clean', 'smudge'):\n    sys.stdout.write(sys.stdin.read())\n",
                      encoding="utf-8")
    return marker, [Path(sys.executable).as_posix(), script.as_posix()]


def _shell(argv: list[str]) -> str:
    """``argv`` as the command line git gives its shell (a filter, a driver, ``uploadpack``)."""
    return " ".join(f'"{arg}"' for arg in argv)


def _calc_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "calc.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text("from calc import x\n\ndef test_x():\n    assert x\n", encoding="utf-8")
    _commit(repo)
    return repo


@pytest.fixture
def planted(tmp_path) -> tuple[Path, Path]:
    """A committed repository with a changed file, whose .git/config names a marker-writing program for
    ``core.fsmonitor`` and for a clean filter every file uses."""
    repo = _calc_repo(tmp_path)
    marker, argv = _marker_program(tmp_path)
    command = _shell(argv)
    (repo / "calc.py").write_text("x = 22\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    for key, value in (("core.fsmonitor", command), ("filter.evil.clean", command + " clean"),
                       ("filter.evil.smudge", command + " smudge"), ("diff.external", command + " diff")):
        _git(repo, "config", key, value)
    return repo, marker


def test_the_planted_program_runs_under_plain_git(planted):
    """Survival control: the plant is live, so its absence below is the hardening's doing."""
    repo, marker = planted
    _git(repo, "diff", "--name-only", "HEAD")
    assert marker.exists()


def test_verify_never_runs_a_program_the_working_copys_git_config_names(planted):
    repo, marker = planted
    assert "calc.py" in verify.changed_files(repo)
    snapshot = verify.head_snapshot(repo)
    assert snapshot is not None and (snapshot / "calc.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_repo_tests_never_runs_a_program_the_repositorys_git_config_names(planted):
    repo, marker = planted
    found = repo_tests.change(repo_tests.checked_repo(repo))
    assert "calc.py" in found.files
    repo_tests.diff_text(found)
    repo_tests.snapshot(found.repo)
    assert not marker.exists(), marker.read_text(encoding="utf-8")


# --- review B2-C1: a nested repository's own config ---------------------------------------------------------------


@pytest.fixture
def nested(tmp_path) -> tuple[Path, Path]:
    """A committed repository holding a nested repository ``sub/`` added as a gitlink (mode 160000), whose own
    .git/config names a marker-writing clean filter that its .gitattributes gives every file, and one of its files
    stat-dirty: git's status or diff of the outer repository runs ``git status`` inside ``sub`` under ``sub``'s config,
    which the outer repository's settings never reach."""
    repo = _calc_repo(tmp_path)
    marker, argv = _marker_program(tmp_path)
    sub = repo / "sub"
    sub.mkdir()
    _git(sub, "init", "-q")
    (sub / "a.txt").write_text("hello\n", encoding="utf-8")
    _commit(sub)
    _commit(repo, "sub")   # git adds the nested repository as a gitlink
    assert "160000" in _git(repo, "ls-files", "--stage", "sub").stdout
    (sub / ".gitattributes").write_text("* filter=evil2\n", encoding="utf-8")
    _git(sub, "config", "filter.evil2.clean", _shell(argv) + " clean")
    later = time.time() + 5
    os.utime(sub / "a.txt", (later, later))   # same bytes, new time: git must read it through the filter to compare
    return repo, marker


def test_the_nested_repositorys_filter_runs_under_plain_git(nested):
    """Survival control."""
    repo, marker = nested
    _git(repo, "status", "--porcelain")
    assert marker.exists()


def test_verify_never_runs_a_nested_repositorys_filter(nested):
    repo, marker = nested
    verify.changed_files(repo)   # a sensor: it falls back to every .py file, never halts
    with pytest.raises(verify.UnsafeGitConfig, match="submodule"):
        verify.head_snapshot(repo)
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_repo_tests_refuses_a_repository_holding_a_gitlink_and_never_runs_its_filter(nested):
    repo, marker = nested
    for call in (lambda: repo_tests.change(repo_tests.checked_repo(repo)), lambda: repo_tests.snapshot(repo),
                 lambda: repo_tests.run(repo, ["tests/test_calc.py"])):
        with pytest.raises(repo_tests.RepoRefused) as caught:
            call()
        assert caught.value.code == SUBMODULE_REFUSED and "sub" in str(caught.value)
    repo_tests._git(repo, "status", "--porcelain")   # a direct status (as coding.py asks) skips submodules too
    assert not marker.exists(), marker.read_text(encoding="utf-8")


# --- review B2-C2: a promisor remote git would fetch a missing object from -------------------------------------------


@pytest.fixture(params=["ext", "file"])
def promisor(request, tmp_path) -> tuple[Path, Path]:
    """A committed repository with one blob deleted (the base text of its changed test file) and a planted promisor
    remote: ``ext::<program>``, or a file URL whose ``uploadpack`` is the program, each with its ``protocol.*.allow``
    set to ``always``, which outranks ``protocol.allow=never``. Reading the blob makes plain git fetch it lazily."""
    repo = _calc_repo(tmp_path)
    marker, argv = _marker_program(tmp_path)
    oid = _git(repo, "rev-parse", "HEAD:tests/test_calc.py").stdout.strip()
    loose = repo / ".git" / "objects" / oid[:2] / oid[2:]
    loose.chmod(0o666)
    loose.unlink()
    (repo / "tests" / "test_calc.py").write_text("from calc import x\n\ndef test_x():\n    assert x == 1\n",
                                                 encoding="utf-8")
    if request.param == "ext":  # ext:: splits its command at spaces; "% " is a space inside an argument
        url = "ext::" + " ".join(arg.replace("%", "%%").replace(" ", "% ") for arg in [*argv, "ext"])
        planted = (("protocol.ext.allow", "always"), ("remote.evil.url", url))
    else:
        other = tmp_path / "other"
        _git(tmp_path, "init", "-q", "--bare", str(other))
        planted = (("protocol.file.allow", "always"), ("remote.evil.url", other.as_uri()),
                   ("remote.evil.uploadpack", _shell(argv) + " uploadpack"))
    for key, value in (("extensions.partialClone", "evil"), ("remote.evil.promisor", "true"), *planted):
        _git(repo, "config", key, value)
    return repo, marker


def test_the_promisor_remote_runs_under_plain_git(promisor):
    """Survival control."""
    repo, marker = promisor
    _git(repo, "show", "HEAD:tests/test_calc.py")
    assert marker.exists()


def test_verify_never_fetches_a_missing_object(promisor):
    repo, marker = promisor
    with pytest.raises(subprocess.CalledProcessError):
        verify.head_snapshot(repo)   # git archive needs the missing blob
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_repo_tests_never_fetches_a_missing_object(promisor):
    repo, marker = promisor
    with pytest.raises(repo_tests.RepoRefused):
        repo_tests.change(repo_tests.checked_repo(repo))   # git show <base>:tests/test_calc.py needs it
    for args in (("cat-file", "-p", "HEAD:tests/test_calc.py"), ("diff", "HEAD")):
        with pytest.raises(repo_tests.RepoRefused):
            repo_tests._git(repo, *args)
    assert not marker.exists(), marker.read_text(encoding="utf-8")
