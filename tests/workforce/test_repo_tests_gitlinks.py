# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A repository shaped like openlibrary in the study pool: a gitlink whose submodule was never set up here (no .git
inside its folder, and git holds no copy of the commit it names), its folder populated, and a tracked relative link
into it that the tests import through.

Pinned: such a repository is accepted, and git never enters the folder (every status and diff skips submodules, with
``submodule.recurse`` off, and no git command runs inside it); G6 hashes the folder's files itself into the snapshot
(links as links), so a file changed, added or removed there, untracked and ignored ones included, changes the snapshot,
a run that changes one is named, and the ignored-file scan leaves them to that hash; a folder over the file, byte or
time cap is not hashed and says so, and a run beside it gets no receipt; a gitlink entry that differs from the base
commit's is a change of the submodule; an initialised submodule (a .git inside its folder, or anything under
.git/modules) is still refused. Copies recreate a tracked link only when it is relative and resolves, component by
component on the copy itself, inside the copy; every link left out is listed with its reason, and where a link cannot
be made (Windows without the privilege) its target is copied in its place and said. Development fixtures only; real
git; offline. Tests that make links skip where this system cannot (Windows without the privilege); Docker runs them.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from g6.core import component_process, ledger
from g6.workforce import receipt_store
from g6.workforce import repo_tests as rt

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("from calc import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
         "def test_sub():\n    assert sub(5, 3) == 2\n")
LIB_TEST = "from lib import VALUE\n\n\ndef test_value():\n    assert VALUE == 7\n"
#: The commit the gitlink names: made up, so git holds no copy of it, as in the study pool's images.
GITLINK = "0123456789abcdef0123456789abcdef01234567"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}
REFUSED = "G6_E_GIT_SUBMODULE_REFUSED"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                           "-c", "core.symlinks=true", *args], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout


def _commit(repo: Path, message: str) -> None:
    git(repo, "commit", "-q", "-m", message)


def make_openlib(root: Path, *, gitlink: bool = True) -> Path:
    """calc.py (add is wrong) and tests/test_calc.py, committed, with vendor/lib populated (lib/__init__.py, x.py) and,
    when ``gitlink``, committed as a gitlink to a commit git does not hold, with no .git inside."""
    repo = root / "openlib"
    (repo / "tests").mkdir(parents=True)
    (repo / "vendor" / "lib" / "lib").mkdir(parents=True)
    (repo / "calc.py").write_text(BUGGY, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")
    (repo / "vendor" / "lib" / "lib" / "__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
    (repo / "vendor" / "lib" / "x.py").write_text("X = 1\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", "calc.py", "tests", *([] if gitlink else ["vendor"]))
    if gitlink:
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{GITLINK},vendor/lib")
    _commit(repo, "base")
    return repo


def _link(repo: Path, rel: str, target: str, *, folder: bool = False) -> None:
    """A link at ``rel`` naming ``target``, added as git's mode 120000; a skip where this system cannot make one."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, path, target_is_directory=folder)
    except OSError as exc:
        pytest.skip(f"this system cannot make a symbolic link ({exc})")
    git(repo, "add", rel)
    assert git(repo, "ls-files", "--stage", rel).startswith("120000 ")


@pytest.fixture
def openlib(tmp_path):
    return make_openlib(tmp_path)


@pytest.fixture
def linked(openlib):
    """``openlib`` with openlibrary's link, ``lib -> vendor/lib/lib``, which tests/test_lib.py imports through."""
    _link(openlib, "lib", "vendor/lib/lib", folder=True)
    (openlib / "tests" / "test_lib.py").write_text(LIB_TEST, encoding="utf-8")
    git(openlib, "add", "tests/test_lib.py")
    _commit(openlib, "the link")
    return openlib


def _copy(repo: Path, report: dict | None = None):
    return rt.repository_copy(repo, deadline=time.monotonic() + 60, **({} if report is None else {"report": report}))


SUBMODULE = "vendor/lib (files in a submodule folder git does not look into; G6 hashed them itself)"


# --- requirement 1: accepted, never entered by git, hashed by G6 -----------------------------------------------------


def test_an_unregistered_gitlink_with_no_git_inside_is_accepted(openlib):
    repo = rt.checked_repo(openlib)
    found = rt.change(repo)
    assert found.submodules == ("vendor/lib",) and found.submodule_changes == () and found.files == ()
    assert len(rt.snapshot(repo)[rt._SUBMODULE + "vendor/lib"]) == 64
    rt.diff_text(found)
    rt.derive_tests(found)
    assert rt.ignored_inputs(repo).loadable == ()


def test_git_never_enters_the_gitlink_folder_nor_reads_config_inside_it(openlib, monkeypatch):
    """Every git command runs from the repository's top, never with a work tree or git folder of its own, with
    ``--ignore-submodules=all`` on each status and diff and ``submodule.recurse`` off in its command scope."""
    calls: list[tuple[list[str], dict[str, str]]] = []
    real = component_process.run_process

    def recording(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs.get("env") or {})))
        return real(cmd, **kwargs)

    monkeypatch.setattr(rt.component_process, "run_process", recording)
    (openlib / "calc.py").write_text(FIXED, encoding="utf-8")
    found = rt.change(rt.checked_repo(openlib))
    rt.snapshot(openlib)
    rt.diff_text(found)
    rt.derive_tests(found)
    rt.related_texts(found)
    rt.ignored_inputs(openlib)
    assert len(calls) > 10
    for cmd, env in calls:
        at = cmd.index("-C")
        assert Path(cmd[at + 1]).resolve() == openlib.resolve(), cmd
        # git's own options come before the command: never a work tree or git folder of its own, nor in the environment
        assert not any(arg.startswith(("--work-tree", "--git-dir")) for arg in cmd[:at + 2]), cmd
        assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"} & set(env), cmd
        if cmd[at + 2] in ("status", "diff"):
            assert cmd[at + 3] == "--ignore-submodules=all", cmd
        settings = {env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
                    for index in range(int(env["GIT_CONFIG_COUNT"]))}
        assert settings["submodule.recurse"] == "false" and settings["diff.ignoreSubmodules"] == "all", cmd


@pytest.mark.parametrize("how", ["edited", "added", "removed", "replaced by a link to the same bytes"])
def test_a_change_inside_the_gitlink_folder_changes_the_snapshot(openlib, how):
    folder = openlib / "vendor" / "lib"
    (folder / "twin.py").write_text("X = 1\n", encoding="utf-8")   # x.py's bytes
    before = rt.snapshot(openlib)
    if how == "edited":
        (folder / "x.py").write_text("X = 2\n", encoding="utf-8")
    elif how == "added":
        (folder / "lib" / "new.py").write_text("N = 1\n", encoding="utf-8")
    elif how == "removed":
        (folder / "x.py").unlink()
    else:   # read through, the link would give the same bytes: G6 records it as a link, never following it
        (folder / "x.py").unlink()
        try:
            os.symlink("twin.py", folder / "x.py")
        except OSError as exc:
            pytest.skip(f"this system cannot make a symbolic link ({exc})")
    after = rt.snapshot(openlib)
    assert rt._moved(before, after) == [SUBMODULE]
    assert before[rt._HEAD] == after[rt._HEAD] and before[rt._INDEX] == after[rt._INDEX]


@pytest.mark.usefixtures("unconfined")
def test_a_test_that_changes_a_file_inside_the_gitlink_folder_is_named(openlib):
    (openlib / "tests" / "test_w.py").write_text(
        "from pathlib import Path\n\n\ndef test_w():\n"
        "    Path(__file__).parents[1].joinpath('vendor', 'lib', 'x.py').write_text('X = 2\\n')\n", encoding="utf-8")
    found = rt.run(openlib, ["tests/test_w.py"])
    assert (found["verdict"], found["changed"]) == (rt.CHANGED, [SUBMODULE])


def test_untracked_and_ignored_files_inside_the_gitlink_folder_are_in_its_hash_not_the_ignored_scan(openlib):
    (openlib / ".gitignore").write_text("conftest.py\n*.local\n", encoding="utf-8")
    git(openlib, "add", ".gitignore")
    _commit(openlib, "ignore")
    folder = openlib / "vendor" / "lib"
    for name in ("conftest.py", "notes.local", "untracked.py"):   # ignored, ignored, untracked
        (folder / name).write_text("A = 1\n", encoding="utf-8")
    scan = rt.ignored_inputs(openlib)
    assert [rel for rel in (*scan.loadable, *scan.other) if rel.startswith("vendor/")] == []
    assert [rel for rel in rt._untracked(openlib) if rel.startswith("vendor/")] == []
    before = rt.snapshot(openlib)
    for name in ("conftest.py", "notes.local", "untracked.py"):
        (folder / name).write_text("A = 2\n", encoding="utf-8")
        after = rt.snapshot(openlib)
        assert rt._moved(before, after) == [SUBMODULE], name
        before = after


@pytest.mark.parametrize(("cap", "value", "said"), [("COPY_MAX_FILES", 1, "files"), ("COPY_MAX_BYTES", 3, "bytes"),
                                                    ("SUBMODULE_HASH_S", 0, "seconds")])
def test_a_gitlink_folder_over_a_cap_is_not_hashed_and_says_so(openlib, monkeypatch, cap, value, said):
    """Never a hash of part of it read as the whole: the snapshot says the folder was not hashed, and why."""
    monkeypatch.setattr(rt, cap, value)
    found = rt.snapshot(openlib)
    missing = rt.submodules_not_hashed(found)
    assert list(missing) == ["vendor/lib"] and said in missing["vendor/lib"], missing
    assert rt.submodules_not_hashed(rt.snapshot(make_openlib(openlib.parent / "plain", gitlink=False))) == {}


def test_bytecode_python_writes_inside_the_gitlink_folder_is_not_a_change(openlib):
    """Review I2 (the reviewer's P2): running the vendored code once writes __pycache__ there, which read as the
    repository changing during the review. A stray .pyc outside __pycache__ is code Python can load: still hashed."""
    before = rt.snapshot(openlib)
    cache = openlib / "vendor" / "lib" / "lib" / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-312.pyc").write_bytes(b"\0" * 16)
    assert rt._moved(before, rt.snapshot(openlib)) == []
    (openlib / "vendor" / "lib" / "stray.pyc").write_bytes(b"\0" * 16)
    assert rt._moved(before, rt.snapshot(openlib)) == [SUBMODULE]


def test_a_folder_hashed_on_one_side_only_is_not_hashed_never_changed(openlib, monkeypatch):
    """Review I2: a cap reached before or after, but not both, is a folder G6 did not hash, never a change."""
    hashed = rt.snapshot(openlib)
    monkeypatch.setattr(rt, "COPY_MAX_FILES", 1)
    capped = rt.snapshot(openlib)
    assert rt._moved(hashed, capped) == [] and rt._moved(capped, hashed) == []
    assert list(rt.submodules_not_hashed(capped)) == ["vendor/lib"]


@pytest.mark.parametrize("how", ["pointed at another commit", "removed", "added"])
def test_a_gitlink_entry_that_differs_from_the_base_is_a_submodule_change(openlib, how):
    """git's own diff skips submodules, so G6 compares the gitlink entries of the base commit and the index."""
    path = "vendor/lib"
    if how == "pointed at another commit":
        git(openlib, "update-index", "--cacheinfo", f"160000,{'f' * 40},vendor/lib")
    elif how == "removed":
        git(openlib, "rm", "-q", "--cached", "vendor/lib")
    else:
        (openlib / "vendor" / "other").mkdir()
        git(openlib, "update-index", "--add", "--cacheinfo", f"160000,{'e' * 40},vendor/other")
        path = "vendor/other"
    found = rt.change(openlib)
    assert found.submodule_changes == (path,) and path in found.submodules


# --- requirement 2: an initialised submodule is still refused --------------------------------------------------------


@pytest.mark.parametrize("kind", ["a .git file", "a .git folder", "an entry under .git/modules"])
def test_an_initialised_submodule_is_still_refused(openlib, kind):
    if kind == "a .git file":
        (openlib / "vendor" / "lib" / ".git").write_text("gitdir: ../../.git/modules/lib\n", encoding="utf-8")
    elif kind == "a .git folder":
        (openlib / "vendor" / "lib" / ".git").mkdir()
    else:
        (openlib / ".git" / "modules" / "lib").mkdir(parents=True)
        (openlib / ".git" / "modules" / "lib" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (openlib / "tests" / "test_w.py").write_text("from pathlib import Path\n\n\ndef test_w():\n"
                                                 "    Path(__file__).parents[1].joinpath('ran').write_text('')\n",
                                                 encoding="utf-8")
    for call in (lambda: rt.change(rt.checked_repo(openlib)), lambda: rt.snapshot(openlib),
                 lambda: rt.run(openlib, ["tests/test_w.py"])):
        with pytest.raises(rt.RepoRefused) as caught:
            call()
        assert caught.value.code == REFUSED and "vendor/lib" in str(caught.value)
    assert not (openlib / "ran").exists()


# --- requirement 4: tracked links in copies --------------------------------------------------------------------------


def test_a_tracked_link_into_the_gitlink_folder_is_recreated_in_a_copy(linked):
    with _copy(linked) as copy:
        # the link names vendor/lib/lib, with the system's separator (Windows resolves no forward slash in one)
        assert (copy / "lib").is_symlink() and os.readlink(copy / "lib").replace(os.sep, "/") == "vendor/lib/lib"
        assert (copy / "lib" / "__init__.py").read_text(encoding="utf-8") == "VALUE = 7\n"
        assert (copy / "vendor" / "lib" / "x.py").read_text(encoding="utf-8") == "X = 1\n"


def test_an_absolute_link_and_one_that_leaves_the_root_are_left_out_and_listed(linked, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("S = 1\n", encoding="utf-8")
    _link(linked, "absolute", str(outside), folder=True)
    _link(linked, "pkg/up", "../../outside", folder=True)   # one step up stays inside; the second leaves
    _commit(linked, "links out")
    report: dict = {}
    with _copy(linked, report) as copy:
        assert not os.path.lexists(copy / "absolute") and not os.path.lexists(copy / "pkg" / "up")
        assert (copy / "lib").is_symlink()
    assert report == {"links_left_out": {"absolute": rt.LINK_ABSOLUTE, "pkg/up": rt.LINK_OUTSIDE}}


def test_a_link_that_leaves_the_root_through_another_link_is_left_out(linked):
    """Resolved component by component: ``c``'s text reads as ``a``, inside, but ``a/b`` is a link to the top, so
    ``a/b/..`` is above it."""
    _link(linked, "a/b", "..", folder=True)        # the repository's top: inside
    _link(linked, "c", "a/b/..", folder=True)
    _link(linked, "d", "c", folder=True)           # a chain through c
    _commit(linked, "a chain")
    report: dict = {}
    with _copy(linked, report) as copy:
        assert (copy / "a" / "b").is_symlink()
        assert not os.path.lexists(copy / "c") and not os.path.lexists(copy / "d")
    assert report == {"links_left_out": {"c": rt.LINK_OUTSIDE, "d": rt.LINK_OUTSIDE}}


def test_the_link_check_reads_the_copy_itself(linked, monkeypatch):
    """A link whose target changed between G6 reading it and making it (a swap) is judged by what the copy holds."""
    real = os.symlink

    def swapped(target, path, target_is_directory=False, **kwargs):
        return real("../../elsewhere" if Path(path).name == "lib" else target, path, target_is_directory, **kwargs)

    monkeypatch.setattr(rt.os, "symlink", swapped)
    report: dict = {}
    with _copy(linked, report) as copy:
        assert not os.path.lexists(copy / "lib")
    assert report == {"links_left_out": {"lib": rt.LINK_OUTSIDE}}


def test_where_a_link_cannot_be_made_its_target_is_copied_in_its_place_and_said(linked, monkeypatch):
    _link(linked, "conf.py", "vendor/lib/x.py")
    _link(linked, "pkg/up", "../../outside", folder=True)
    _commit(linked, "more links")

    def no_privilege(*args, **kwargs):
        error = OSError(1, "A required privilege is not held by the client")
        error.winerror = 1314   # Windows: ERROR_PRIVILEGE_NOT_HELD
        raise error

    monkeypatch.setattr(rt.os, "symlink", no_privilege)
    report: dict = {}
    with _copy(linked, report) as copy:
        assert (copy / "lib").is_dir() and not (copy / "lib").is_symlink()
        assert (copy / "lib" / "__init__.py").read_text(encoding="utf-8") == "VALUE = 7\n"
        assert (copy / "conf.py").read_text(encoding="utf-8") == "X = 1\n" and not (copy / "conf.py").is_symlink()
        assert not os.path.lexists(copy / "pkg" / "up")
    assert report == {"links_left_out": {"pkg/up": rt.LINK_OUTSIDE},
                      "links_materialised": {"conf.py": "vendor/lib/x.py", "lib": "vendor/lib/lib"}}


@pytest.mark.parametrize("privilege", [True, False], ids=["link-made", "privilege-refused"])
def test_on_windows_a_link_is_made_with_backslashes_and_as_a_folders_link_when_its_target_is_one(tmp_path, monkeypatch,
                                                                                                 privilege):
    """CI on Windows (run 40780356106): a copied link kept git's forward slashes (``vendor/lib/lib``), which a Windows
    link does not resolve, so reading through it failed. On Windows the link is made with the system's separator, as a
    folder's link when its target in the copy is a folder; where the privilege is refused, the target is still copied
    in its place. The link-making call is recorded, never made, so this runs on any system."""
    copy = tmp_path / "copy"
    (copy / "vendor" / "lib" / "lib").mkdir(parents=True)
    (copy / "vendor" / "lib" / "lib" / "__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
    (copy / "vendor" / "lib" / "x.py").write_text("X = 1\n", encoding="utf-8")
    made: list[tuple[str, str, bool]] = []

    def symlink(target, path, target_is_directory=False, **kwargs):
        made.append((target, Path(path).relative_to(copy).as_posix(), target_is_directory))
        if not privilege:
            error = OSError(1, "A required privilege is not held by the client")
            error.winerror = 1314   # Windows: ERROR_PRIVILEGE_NOT_HELD
            raise error
        Path(path).mkdir() if target_is_directory else Path(path).write_text("")   # a stand-in, never followed

    monkeypatch.setattr(rt, "_WINDOWS", True, raising=False)
    monkeypatch.setattr(rt.os, "symlink", symlink)
    left: dict[str, str] = {}

    def put(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    materialised = rt._copy_links(copy, {"lib": ("vendor/lib/lib", False), "conf.py": ("vendor/lib/x.py", False)},
                                  left, put)
    assert sorted(made) == [("vendor\\lib\\lib", "lib", True), ("vendor\\lib\\x.py", "conf.py", False)]
    assert left == {}
    if not privilege:
        assert materialised == {"conf.py": "vendor/lib/x.py", "lib": "vendor/lib/lib"}
        assert (copy / "lib" / "__init__.py").read_text(encoding="utf-8") == "VALUE = 7\n"
        assert (copy / "conf.py").read_text(encoding="utf-8") == "X = 1\n"


@pytest.mark.usefixtures("unconfined")
def test_a_copy_run_lists_the_links_it_left_out(linked):
    _link(linked, "pkg/up", "../../outside", folder=True)
    _commit(linked, "a link out")
    (linked / "calc.py").write_text(FIXED, encoding="utf-8")
    found = rt.run_at_base(rt.change(linked), ["tests/test_lib.py"])
    assert found["verdict"] == rt.PASSED, found.get("output") or found
    assert found["links_left_out"] == {"pkg/up": rt.LINK_OUTSIDE}


@pytest.mark.usefixtures("unconfined")
def test_a_patch_that_changes_a_tracked_link_leaves_the_base_run_not_run(tmp_path):
    """The base copy cannot put the link back as it was, so the run at the base commit is not made."""
    repo = make_openlib(tmp_path, gitlink=False)
    _link(repo, "lib", "vendor/lib/lib", folder=True)
    _commit(repo, "the link")
    os.unlink(repo / "lib")
    os.symlink("vendor/lib", repo / "lib", target_is_directory=True)
    found = rt.run_at_base(rt.change(repo), ["tests/test_calc.py"])
    assert found == {"verdict": "not_run", "said": rt.OLD_BASELINE_NOT_RUN}


# --- confined (Linux bubblewrap; Docker on Windows) ------------------------------------------------------------------


def test_confined_the_import_through_the_link_works_in_place_and_in_a_copy(linked, bwrap_backend):
    in_place = rt.run(linked, ["tests/test_lib.py"])
    assert in_place["verdict"] == rt.PASSED, in_place["output"]
    copied = rt.run(linked, ["tests/test_lib.py"], mutant=("calc.py", FIXED))
    assert copied["verdict"] == rt.PASSED, copied["output"]


def test_confined_a_receipt_binds_the_gitlink_folders_files(openlib, bwrap_backend):
    found = rt.run(openlib, ["tests/test_calc.py"], receipt=True)
    assert found["receipt"], found
    digest = found["source_before"]
    assert receipt_store.check(found["receipt"], artifact_digest=digest)[0] == receipt_store.MATCHED
    (openlib / "vendor" / "lib" / "x.py").write_text("X = 2\n", encoding="utf-8")
    assert ledger.digest(rt.snapshot(openlib)) != digest


def test_confined_a_run_beside_a_folder_g6_could_not_hash_gets_no_receipt(openlib, bwrap_backend, monkeypatch):
    monkeypatch.setattr(rt, "COPY_MAX_FILES", 1)
    found = rt.run(openlib, ["tests/test_calc.py"], receipt=True)
    assert found["receipt"] == "" and "vendor/lib" in found["receipt_withheld"], found
    assert list(found["submodules_not_hashed"]) == ["vendor/lib"]
