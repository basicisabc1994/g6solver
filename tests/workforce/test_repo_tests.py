# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A caller's repository read from git (plan 2026-10-05-verify-task-real-repos, Task 4): the change against a base,
each changed test file's text then and now, the changed lines, and the tests the change touches.

Pinned: what git reports is the change (tracked edits staged or not, commits since the base, untracked files,
deletions), within ``paths`` when given; a test file's text at the base is git's, now the disk's, both read with the
same line endings, so a file whose only change is its line endings has the same text then and now; every line of a new
file is changed; the tests the change touches are its changed test files first, then the files that name a changed
module, at most the cap, the rest counted; a folder that is not a repository's top, a base that is not a commit, and a
path that leaves the repository are refused. Real git in temp folders; offline.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from g6.workforce import repo_tests as rt
from g6.workforce import verify

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("from calc import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
         "def test_sub():\n    assert sub(5, 3) == 2\n")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True, text=True, env=GIT_ENV).stdout


def make_repo(root: Path, name: str = "calc") -> Path:
    """A two-function repository, committed: calc.py (add is wrong) and tests/test_calc.py."""
    repo = root / name
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(BUGGY, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def test_the_change_is_what_git_reports(repo):
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    (repo / "new.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "tests" / "test_new.py").write_text("def test_n():\n    assert True\n", encoding="utf-8")
    found = rt.change(repo)
    assert found.files == ("calc.py", "new.py", "tests/test_new.py")
    assert found.sources == ("calc.py", "new.py") and found.runner_files == ()
    assert found.before == {} and set(found.after) == {"tests/test_new.py"}
    assert found.base == "HEAD" and len(found.base_commit) == 40 and found.head == found.base_commit


def test_read_at_gives_a_files_text_at_a_commit_or_none_when_it_was_not_there(repo):
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    (repo / "new.py").write_text("X = 1\n", encoding="utf-8")
    assert rt.read_at(repo, base, "calc.py") == BUGGY   # the commit's text, not the disk's
    assert rt.read_at(repo, base, "new.py") is None and rt.read_at(repo, base, "tests/test_calc.py") == TESTS
    with pytest.raises(rt.RepoRefused):
        rt.read_at(repo, base, "../outside.py")


def test_a_changed_test_files_text_at_the_base_is_gits_and_now_is_the_disks(repo):
    now = TESTS.replace("assert sub(5, 3) == 2", "assert sub(5, 3)")
    (repo / "tests" / "test_calc.py").write_text(now, encoding="utf-8")
    found = rt.change(repo)
    assert found.before == {"tests/test_calc.py": TESTS} and found.after == {"tests/test_calc.py": now}
    assert verify.weakened_tests(found.before, found.after) == [
        "tests/test_calc.py: test_sub: an assert was removed or changed"]


def test_a_test_file_whose_only_change_is_its_line_endings_reads_the_same_then_and_now(repo):
    """Review Focus 3 at its source: git keeps the base's bytes as they were committed, the disk's may differ only in
    line endings (Windows ``autocrlf``); both are read with the same line endings, so nothing reads as weakened."""
    path = repo / "tests" / "test_calc.py"
    raw = path.read_bytes()
    flipped = raw.replace(b"\r\n", b"\n") if b"\r\n" in raw else raw.replace(b"\n", b"\r\n")
    path.write_bytes(flipped)
    found = rt.change(repo)
    assert "tests/test_calc.py" in found.files  # git sees the bytes change
    assert found.before == found.after == {"tests/test_calc.py": TESTS}
    assert verify.weakened_tests(found.before, found.after) == []


def test_a_deleted_test_file_has_no_text_now(repo):
    (repo / "tests" / "test_calc.py").unlink()
    found = rt.change(repo)
    assert "tests/test_calc.py" in found.files and found.after == {}
    assert verify.weakened_tests(found.before, found.after) == ["tests/test_calc.py: test file deleted"]


def test_a_base_ref_takes_in_the_commits_since_it(repo):
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    git(repo, "commit", "-q", "-am", "fix add")
    assert rt.change(repo).files == ()
    assert rt.change(repo, "HEAD~1").files == ("calc.py",)


def test_paths_limit_the_change_to_the_slice(repo):
    """Review Focus 1: in a shared tree another lane's edits are not this change."""
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    (repo / "other.py").write_text("Y = 2\n", encoding="utf-8")
    assert rt.change(repo, paths=("calc.py",)).files == ("calc.py",)


def test_a_runner_file_the_change_touches_is_named(repo):
    (repo / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    assert rt.change(repo).runner_files == ("conftest.py",)


def test_the_changed_lines_are_the_added_or_rewritten_ones(repo):
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    (repo / "new.py").write_text("A = 1\nB = 2\n", encoding="utf-8")
    found = rt.change(repo)
    assert rt.changed_lines(found, "calc.py") == {2}
    assert rt.changed_lines(found, "new.py") == {1, 2}  # untracked: every line is new
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")  # sub deleted, nothing added
    assert rt.changed_lines(rt.change(repo), "calc.py") == set()


def test_the_tests_the_change_touches_are_its_test_files_then_the_files_naming_its_modules(repo):
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    (repo / "tests" / "test_other.py").write_text("def test_o():\n    assert True\n", encoding="utf-8")
    assert rt.derive_tests(rt.change(repo)) == (["tests/test_other.py", "tests/test_calc.py"], 0)


def test_the_derived_tests_are_capped_and_the_rest_counted(repo):
    for i in range(5):
        (repo / "tests" / f"test_calc_{i}.py").write_text("import calc\n\n\ndef test_x():\n    assert calc\n",
                                                          encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "more tests")
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    assert rt.derive_tests(rt.change(repo), limit=3) == (
        ["tests/test_calc.py", "tests/test_calc_0.py", "tests/test_calc_1.py"], 3)


def test_a_deleted_test_file_is_never_derived(repo):
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    (repo / "tests" / "test_calc.py").unlink()
    assert rt.derive_tests(rt.change(repo)) == ([], 0)


def test_run_set_reads_the_candidates_it_is_given_instead_of_walking_the_folder(repo, monkeypatch):
    monkeypatch.setattr(Path, "rglob", lambda *a, **k: pytest.fail("the folder was walked"))
    assert verify.run_set(repo, ["calc.py"], candidates=["tests/test_calc.py", "README.md"]) == ["tests/test_calc.py"]


def test_a_folder_that_is_not_a_repositorys_top_is_refused(repo, tmp_path):
    with pytest.raises(rt.RepoRefused, match="top folder") as below:
        rt.checked_repo(repo / "tests")
    assert below.value.code == "G6_E_GIT_REPO_INVALID"
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(rt.RepoRefused, match="not a git repository|top folder"):
        rt.checked_repo(plain)
    with pytest.raises(rt.RepoRefused, match="full path") as relative:
        rt.checked_repo("calc")
    assert relative.value.code == "G6_E_INPUT_VALIDATION"
    with pytest.raises(rt.RepoRefused, match="no folder"):
        rt.checked_repo(tmp_path / "missing")


def test_a_git_too_old_for_g6s_settings_is_named_as_the_reason_not_as_no_repository(repo, tmp_path, monkeypatch):
    # V7's pilot (2026-10-25): an image's git 2.30.2 ignores GIT_CONFIG_COUNT, so G6 rightly ran no git there, but
    # checked_repo said "the folder is not a git repository" and hid why.
    real = rt.git_env.safe_git_env

    def before_2_31(scratch, drivers=(), **kw):   # git before 2.31 reads no GIT_CONFIG_* settings from the environment
        return {k: v for k, v in real(scratch, drivers, **kw).items() if not k.startswith("GIT_CONFIG_")}

    monkeypatch.setattr(rt.git_env, "safe_git_env", before_2_31)
    with pytest.raises(rt.RepoRefused, match=r"2\.31 or later") as old:
        rt.checked_repo(repo)
    assert old.value.code == "G6_E_GIT_COMMAND_FAILED"
    monkeypatch.setattr(rt.git_env, "safe_git_env", real)
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(rt.RepoRefused, match="not a git repository") as none:   # git's own answer stays as it was
        rt.checked_repo(plain)
    assert none.value.code == "G6_E_GIT_REPO_INVALID"


@pytest.mark.parametrize(("base", "code"), [("-p", "G6_E_INPUT_VALIDATION"), ("--output=x", "G6_E_INPUT_VALIDATION"),
                                            ("", "G6_E_INPUT_VALIDATION"), ("no-such-ref", "G6_E_GIT_REPO_INVALID")])
def test_a_base_that_is_not_a_commit_is_refused(repo, base, code):
    """An option-shaped or empty base is refused before git is asked; a name git does not know, by git."""
    with pytest.raises(rt.RepoRefused) as refused:
        rt.change(repo, base)
    assert refused.value.code == code


def test_a_renamed_test_file_reads_as_deleted_and_added(repo):
    """``--no-renames``: git's rename detection never hides that the old test file is gone (erring strict)."""
    git(repo, "mv", "tests/test_calc.py", "tests/test_calc2.py")
    found = rt.change(repo)
    assert found.files == ("tests/test_calc.py", "tests/test_calc2.py")
    assert verify.weakened_tests(found.before, found.after) == ["tests/test_calc.py: test file deleted"]


@pytest.mark.parametrize("value", ["../x.py", "/etc/x.py", "C:/x.py", "-p", ":(exclude)x", "", "a/../../b", 3])
def test_a_path_that_cannot_be_inside_the_repository_is_named_before_the_disk_is_read(value):
    assert rt.shape_problem(value, "tests")


@pytest.mark.parametrize("value", ["tests/test_calc.py", "tests/test_calc.py::test_add", "tests", "src\\pkg\\x.py"])
def test_a_path_inside_the_repository_has_no_shape_problem(value):
    assert rt.shape_problem(value, "tests") == ""


# --- the fix pass after the independent review of Tasks 4-5 (M1, M2, M7, D3, D15) --------------------------------


@pytest.mark.parametrize("value", ["@opts", "@tests/test_calc.py", "tests/test_calc.py::te\x00st", "tests/te\x00st.py",
                                   "tests/test_calc.py::test_add\n-p", "tests/test_calc.py::\x1b[0m", "tests\t/x.py"])
def test_m1_m2_an_argument_file_or_a_control_character_anywhere_is_a_shape_problem(value):
    """M1 (probe C1e): pytest reads '@name' as a file of arguments. M2 (probe C4): a NUL after '::' was not checked."""
    assert rt.shape_problem(value, "test")


def test_m2_a_base_with_a_control_character_is_refused_never_a_bare_error(repo):
    with pytest.raises(rt.RepoRefused) as refused:
        rt.change(repo, "HE\x00AD")
    assert refused.value.code == "G6_E_INPUT_VALIDATION"


def test_m7_the_changed_lines_ignore_the_owners_diff_settings_and_read_names_literally(repo):
    """M7 (probes D4, D10): ``diff.interHunkContext`` merged two hunks with the lines between; ``a[1].py`` was a glob
    that also matched ``a1.py``; a textconv filter rewrote the text git compared."""
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a[1].py").write_text("X = 1\nY = 2\n", encoding="utf-8")
    (repo / "pkg" / "a1.py").write_text("P = 1\nQ = 2\nR = 3\nS = 4\n", encoding="utf-8")
    (repo / "m.py").write_text("".join(f"V{i} = {i}\n" for i in range(1, 11)), encoding="utf-8")
    (repo / "t.py").write_text("A = 1\nB = 2\nC = 3\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("t.py diff=cut\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "more")
    git(repo, "config", "diff.interHunkContext", "5")
    git(repo, "config", "diff.cut.textconv", "sed 1d")
    (repo / "pkg" / "a[1].py").write_text("X = 10\nY = 2\n", encoding="utf-8")
    (repo / "pkg" / "a1.py").write_text("P = 1\nQ = 2\nR = 3\nS = 40\n", encoding="utf-8")
    (repo / "m.py").write_text("".join(f"V{i} = {i * (100 if i in (2, 8) else 1)}\n" for i in range(1, 11)),
                               encoding="utf-8")
    (repo / "t.py").write_text("A = 1\nB = 20\nC = 3\n", encoding="utf-8")
    found = rt.change(repo)
    assert rt.changed_lines(found, "pkg/a[1].py") == {1}
    assert rt.changed_lines(found, "m.py") == {2, 8}
    assert rt.changed_lines(found, "t.py") == {2}
    assert rt.change(repo, paths=("pkg/a[1].py",)).files == ("pkg/a[1].py",)


def test_d3_a_test_file_moved_with_git_is_recorded_old_to_new(repo):
    """D3 (probe D3): the move is recorded so the new file is compared with the old path's text at the base; the
    files, before and after stay as they were (``test_a_renamed_test_file_reads_as_deleted_and_added``)."""
    git(repo, "mv", "tests/test_calc.py", "tests/test_calc2.py")
    found = rt.change(repo)
    assert found.renamed == {"tests/test_calc.py": "tests/test_calc2.py"}
    moved = {found.renamed.get(rel, rel): text for rel, text in found.before.items()}
    assert verify.weakened_tests(moved, found.after) == []


def test_d3_a_test_file_moved_on_the_disk_without_git_is_recorded_too(repo):
    (repo / "tests" / "test_calc.py").rename(repo / "tests" / "test_moved.py")
    assert rt.change(repo).renamed == {"tests/test_calc.py": "tests/test_moved.py"}


def test_d3_a_deleted_test_file_and_an_unrelated_new_one_are_not_a_move(repo):
    (repo / "tests" / "test_calc.py").unlink()
    (repo / "tests" / "test_other.py").write_text("def test_o():\n    assert True\n", encoding="utf-8")
    found = rt.change(repo)
    assert found.renamed == {}
    assert verify.weakened_tests(found.before, found.after) == ["tests/test_calc.py: test file deleted"]


def test_14b_a_test_file_is_named_with_the_case_pytest_matches_on_this_system():
    """Review 14b: pytest matches ``test_*.py`` with the system's case rule (``fnmatch``, through
    ``os.path.normcase``), so on Windows ``Test_Calc.py`` is a test file it collects."""
    ignores_case = os.path.normcase("A") == "a"
    assert rt.is_test_file("tests/test_calc.py") and not rt.is_test_file("tests/calc.py")
    assert rt.is_test_file("tests/Test_Calc.py") is ignores_case
    assert rt.is_test_file("tests/TEST_CALC.PY") is ignores_case


def test_c2_every_changed_test_related_file_is_read_then_and_now_and_a_move_keeps_its_old_text(repo):
    """Review C2: the weakening scan gets each changed test file by either of pytest's default names, conftest.py,
    and every Python file under a test folder, at the base and now; a moved one is keyed by its new path."""
    (repo / "tests" / "helpers.py").write_text("def check():\n    assert True\n", encoding="utf-8")
    (repo / "tests" / "calc_test.py").write_text("def test_c():\n    assert 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "helpers")
    (repo / "tests" / "calc_test.py").write_text("def test_c():\n    pass\n", encoding="utf-8")
    (repo / "conftest.py").write_text("\n", encoding="utf-8")
    (repo / "tests" / "data.json").write_text("{}\n", encoding="utf-8")   # not Python: not read
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")                # not a test: not read
    git(repo, "mv", "tests/helpers.py", "tests/checks.py")
    before, after = rt.related_texts(rt.change(repo))
    assert before == {"tests/calc_test.py": "def test_c():\n    assert 1\n",
                      "tests/checks.py": "def check():\n    assert True\n"}
    assert after == {"conftest.py": "\n", "tests/calc_test.py": "def test_c():\n    pass\n",
                     "tests/checks.py": "def check():\n    assert True\n"}


def test_d15_a_changed_conftest_or_setup_names_no_test_by_its_stem(repo):
    """D15 (probe D15): a test file that mentions the words 'conftest' or 'setup' is not a test of them."""
    (repo / "tests" / "test_doc.py").write_text("# the conftest sets things up; see setup in the README\n"
                                                "def test_doc():\n    assert True\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "doc")
    (repo / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    (repo / "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
    assert rt.derive_tests(rt.change(repo)) == ([], 0)
