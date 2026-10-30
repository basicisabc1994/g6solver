# SPDX-License-Identifier: Apache-2.0
"""A file the patch force-adds under a path git ignores is a changed file (the security review of W1-W5, F4).

``repo_tests.change`` listed new files with ``ls-files --others --exclude-standard`` against the private index built
from the base commit, which leaves out ignored paths; ``ignored_inputs`` lists ``--others --ignored`` against the
repository's own index, which tracks a force-added file. So ``git add -f`` of a file under an ignored path made it
invisible to both, and the review gave supported_within_scope. Every file present now that the base commit does not
hold is now either a changed file, judged as one, or an ignored input, never neither. Benign content only: every file
planted here is empty, a comment, or bytes no test reads; the test runs are recorded, not run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from g6.harness import patch_review
from g6.harness.patch_review import review_patch
from g6.workforce import repo_tests

TEST = "from pkg.mod import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
ADD = "test_mod.py::test_add"
SUM = [{"id": "R1", "text": "add returns the sum", "checks": [ADD]}]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _base(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "mod.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "test_mod.py").write_text(TEST)
    (root / ".gitignore").write_text("data/\nsrc/\nbuild/\n")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (root / "pkg" / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    return root


@pytest.fixture
def repo(tmp_path):
    return _base(tmp_path)


def _plant(repo: Path, rel: str, data: bytes = b"") -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_bytes(data)
    _git(repo, "add", "-f", rel)


def _recorded(monkeypatch, node: str = ADD) -> None:
    passed = {"verdict": "passed", "said": "", "passed": 1, "failed": 0, "not_passed": 0, "total": 1,
              "deselected": 0, "test_outcomes": {node: {"setup": "passed", "call": "passed", "teardown": "passed"}},
              "collected_ids": [node], "omitted_ids": [], "changed": [], "output": "", "seconds": 0.25,
              "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
              "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": [], "setup_errors": {},
              "degraded": False, "receipt": ""}
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, *, timeout_s, change=None, **kw: passed)
    monkeypatch.setattr(patch_review, "_execute_at_base",
                        lambda change, ids, *, timeout_s, **kw: {"verdict": "not_run", "said": "not recorded"})


def _review(repo):
    return review_patch(repo, task="Repair addition.", requirements=SUM)


def _codes(got) -> set[str]:
    return {r["code"] for r in got["assessment"]["reasons"]}


def test_the_patch_alone_is_supported(repo, monkeypatch):
    # the control: nothing planted, and the review supports the patch on its stated check
    _recorded(monkeypatch)
    got = _review(repo)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert got["assessment"]["scope"]["changed_files"] == ["pkg/mod.py"]


@pytest.mark.parametrize("rel", ["data/conftest.py", "data/sub/conftest.py"])
def test_a_force_added_conftest_under_an_ignored_path_is_a_changed_runner_file(repo, monkeypatch, rel):
    _plant(repo, rel, b"# an empty conftest\n")
    _recorded(monkeypatch)
    got = _review(repo)
    assert rel in got["assessment"]["scope"]["changed_files"]
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert "runner_configuration_changed" in _codes(got)


@pytest.mark.parametrize("rel, data", [("src/pkg/mod.py", b"X = 1\n"), ("build/mod.pyc", b"\x00 not bytecode\n"),
                                       ("data/zz.pth", b"# a comment line\n")])
def test_a_force_added_twin_bytecode_or_pth_under_an_ignored_path_is_flagged(repo, monkeypatch, rel, data):
    _plant(repo, rel, data)
    _recorded(monkeypatch)
    got = _review(repo)
    assert rel in got["assessment"]["scope"]["changed_files"]
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert _codes(got) & {"changed_file_can_change_tests", "runner_configuration_changed"}, _codes(got)


def test_every_file_present_now_that_the_base_lacks_is_a_change_or_an_ignored_input(repo):
    for rel in ("data/conftest.py", "src/pkg/mod.py", "build/mod.pyc"):
        _plant(repo, rel, b"# benign\n")
    (repo / "data" / "untracked.py").write_text("# benign\n")      # ignored, untracked
    (repo / "notes.py").write_text("# benign\n")                   # untracked, not ignored
    change = repo_tests.change(repo, "HEAD")
    scan = repo_tests.ignored_inputs(repo)
    present = {path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file()
               and ".git" not in path.relative_to(repo).parts}
    base = {"pkg/__init__.py", "pkg/mod.py", "test_mod.py", ".gitignore"}
    accounted = set(change.files) | set(scan.loadable) | set(scan.other)
    assert present - base <= accounted, (present - base) - accounted
    assert "data/untracked.py" in set(scan.loadable) | set(scan.other) and "data/untracked.py" not in change.files


def test_a_base_tracked_file_a_nested_gitignore_matches_still_raises_nothing(tmp_path, monkeypatch):
    # Black's case: the fixture is in the base commit, tracked, and matched by a nested .gitignore
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (tmp_path / "tests" / "data" / "case").mkdir(parents=True)
    (tmp_path / "tests" / "data" / "case" / ".gitignore").write_text("calc.py\n")
    (tmp_path / "tests" / "data" / "case" / "calc.py").write_text("print('fixture')\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "add", "-f", "tests/data/case/calc.py")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _recorded(monkeypatch, "test_calc.py::test_add")
    got = review_patch(tmp_path, task="Repair addition.",
                       requirements=[{"id": "R1", "text": "add returns the sum", "checks": ["test_calc.py::test_add"]}])
    assert got["assessment"]["scope"]["changed_files"] == ["calc.py"]
    assert got["assessment"]["reasons"] == []


def test_an_untracked_ignored_conftest_still_compromises_integrity(repo, monkeypatch):
    (repo / "data").mkdir()
    (repo / "data" / "conftest.py").write_text("# an empty conftest\n")
    _recorded(monkeypatch)
    got = _review(repo)
    assert "data/conftest.py" not in got["assessment"]["scope"]["changed_files"]
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert "ignored_file_can_change_tests" in _codes(got)


def test_a_force_added_file_is_in_the_diff_the_snapshot_and_absent_at_the_base(repo, tmp_path_factory):
    """F4 at each place it reaches, beyond ``change`` (the fresh review's mutation run: reverting F4 in ``diff_text``,
    ``snapshot`` and ``_base_revision`` failed no test): the gate reads its text, a later edit to it changes the
    snapshot, and the base-revision copy does not hold it."""
    import shutil
    import time

    _plant(repo, "data/helper.py", b"# a comment\n")
    change = repo_tests.change(repo)
    assert "+++ b/data/helper.py" in repo_tests.diff_text(change)
    assert "data/helper.py" in repo_tests.snapshot(repo)
    copy = tmp_path_factory.mktemp("copy") / "repo"
    shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git"))
    with repo_tests._base_revision(change, copy, time.monotonic() + 30):
        assert not (copy / "data" / "helper.py").exists()
    assert (copy / "data" / "helper.py").read_bytes() == b"# a comment\n"


MARKER = "OUTSIDE-MARKER benign text\n"


def _crafted_entry(root: Path, wanted: str, *, mode: str = "100644", mark: str = "") -> None:
    """Rename one entry in the repository's own index to ``wanted`` (same length), as a crafted index would: git
    itself never stages a name above the work tree, an absolute one or one in .git. The bytes it names are benign."""
    import hashlib
    placeholder = "q" * len(wanted)
    oid = (subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, check=True).stdout
           if mode == "160000" else
           subprocess.run(["git", "-C", str(root), "hash-object", "-w", "--stdin"], input=MARKER.encode(),
                          capture_output=True, check=True).stdout).decode().strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{placeholder}")
    if mark:
        _git(root, "update-index", mark, placeholder)
    index = root / ".git" / "index"
    body = index.read_bytes()[:-20]
    assert body.count(placeholder.encode()) == 1
    body = body.replace(placeholder.encode(), wanted.encode())
    index.write_bytes(body + hashlib.sha1(body).digest())


@pytest.mark.parametrize("form,mode,mark", [
    ("above", "100644", ""), ("absolute", "100644", ""), ("git_folder", "100644", ""),
    ("above", "100644", "--assume-unchanged"), ("above", "160000", "")])
def test_an_index_entry_naming_a_path_git_never_stages_refuses_the_review(tmp_path, monkeypatch, form, mode, mark):
    """The fresh review of the F1-F8 fix pass, finding 1: F4 took the repository's own index unchecked, so an entry
    naming ``../outside.txt`` or an absolute path put that file's text in the diff the gate and the proposer read, and
    its hash in the snapshot. The marked (assume-unchanged) and gitlink entries G6 reads from the same index are
    checked alike, and so is one in the repository's .git folder, whose config can hold a remote's credentials."""
    root = _base(tmp_path / "repo")
    outside = tmp_path / "outside.txt"
    outside.write_text(MARKER)
    wanted = {"above": "../outside.txt", "absolute": outside.as_posix(), "git_folder": ".git/config"}[form]
    _crafted_entry(root, wanted, mode=mode, mark=mark)
    for call in (repo_tests.change, repo_tests.snapshot):
        with pytest.raises(repo_tests.RepoRefused) as refused:
            call(root)
        assert refused.value.code == "G6_E_GIT_REPO_INVALID" and wanted in str(refused.value)
    _recorded(monkeypatch)
    got = _review(root)
    assert got["completion_state"] == "blocked-escalated" and "G6_E_GIT_REPO_INVALID" in str(got)
    assert "OUTSIDE-MARKER" not in str(got)


@pytest.mark.parametrize("wanted", [".git./config", ".git /config", "GIT~1/config", ".git::$INDEX_ALLOCATION/config"])
def test_an_index_entry_naming_the_git_folder_as_windows_reads_it_refuses_the_review(tmp_path, monkeypatch, wanted):
    """The fresh review of the second fix pass, I2: Windows drops a name's trailing dots and spaces, reads ``GIT~1`` as
    ``.git``'s short name and ``::$INDEX_ALLOCATION`` as its folder stream, so each of these opens .git's config there.
    git's own core.protectNTFS rule (on by default everywhere) never stages them, and neither is a path G6 reads."""
    root = _base(tmp_path / "repo")
    _crafted_entry(root, wanted)
    for call in (repo_tests.change, repo_tests.snapshot):
        with pytest.raises(repo_tests.RepoRefused) as refused:
            call(root)
        assert refused.value.code == "G6_E_GIT_REPO_INVALID" and wanted in str(refused.value)


def test_an_index_entry_beyond_a_link_refuses_the_review(tmp_path, monkeypatch):
    """The fresh review of the second fix pass, I2: a link ``cfg`` to the .git folder, kept out of git by
    .git/info/exclude, and an index entry ``cfg/config``: every part of the name passed, the file resolved inside the
    repository, and G6 put the repository's git config in the diff the gate and the proposer read. git never stages a
    path beyond a link, and G6 now refuses one. The marker in the config is benign."""
    root = _base(tmp_path / "repo")
    try:
        (root / "cfg").symlink_to(root / ".git", target_is_directory=True)
    except OSError:
        pytest.skip("this system does not let the test make a link")
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text("cfg\n")
    _git(root, "config", "g6test.marker", "GITCONFIG-MARKER")
    _crafted_entry(root, "cfg/config")
    for call in (repo_tests.change, repo_tests.snapshot):
        with pytest.raises(repo_tests.RepoRefused) as refused:
            call(root)
        assert refused.value.code == "G6_E_GIT_REPO_INVALID" and "cfg/config" in str(refused.value)
    _recorded(monkeypatch)
    got = _review(root)
    assert got["completion_state"] == "blocked-escalated" and "G6_E_GIT_REPO_INVALID" in str(got)
    assert "GITCONFIG-MARKER" not in str(got)


@pytest.mark.parametrize("name", ["@scope/util.py", "-notes.txt", ".github/notes.txt"])
def test_a_tracked_name_that_looks_like_an_option_is_still_a_changed_file(repo, name):
    # survival control for the check above: names git does stage are never refused
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text("# a comment\n")
    _git(repo, "add", "--", name)
    assert name in repo_tests.change(repo).files
