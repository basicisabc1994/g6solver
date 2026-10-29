# SPDX-License-Identifier: Apache-2.0

"""A caller's own repository, read from git and tested in place (plan 2026-10-05-verify-task-real-repos).

``verify_task`` runs the tests of the repository a coding agent changed, on the caller's own computer, and the
workforce bench's ``run_tests_in_place`` op runs the same function, so the census measures what verify_task uses.

What it reads (``change``): the files changed against a base ref (default ``HEAD``: the working tree's change) as git
reports them with a private index built from the base tree (``_private``: never the repository's own index or its
replace refs, so every file's content is compared with the base's blob), plus untracked files that are not ignored,
plus each file git's assume-unchanged or skip-worktree mark
hides from its diff whose text differs from the base's (``_held``; one so marked that is not in the working tree is
listed apart, ``Change.unseen``, never read as unchanged); each changed test file's text at the base and now, both read
with the same line endings, and the test files moved since the base; the changed source files and the changed lines in
each (``changed_lines``); and the tests the change touches (``derive_tests``: its changed test files, then every test
file that names a changed module, ``verify.run_set``'s rule, at most ``MAX_DERIVED``). Git is asked with the owner's
diff settings that would change the answer turned off, and paths read literally.

How it runs (``run``), and what that does and does not protect:

* in place, against the working tree as it is, with no copy (G6's own repository is about 80,000 files). Before and
  after the run it reads git's view (``snapshot``: HEAD, the index, git's assume-unchanged and skip-worktree marks,
  the bytes of each tracked file that differs from the index or is so marked, a nested repository's own state, and
  the bytes of each untracked file that existed before the run); a run that changed any of it is
  ``changed_tracked_files``, with the paths, and so is a run after which git cannot read the repository. Ignored
  files, files the run created, files outside the repository, and anything changed and changed back within the run
  are not covered. A submodule (a gitlink) that was never set up here, with no .git in its folder, is a folder git
  does not look into: git never enters it, and G6 hashes its files itself into the snapshot (``_submodule``), so a
  change there is covered too. A repository holding a submodule that was set up is refused before anything runs: git
  would run inside it under the submodule's own config;
* ``python -I -B -X utf8``, with the real pytest imported before the repository joins the path (``venture.truth``'s
  pattern); the repository's own pytest configuration is read as it is, and a parent folder's never is; pytest-xdist,
  when installed, runs the tests in the one process, where the guards below apply;
* a minimal environment: a few system variables, HOME and the temp folders in a scratch folder, and the paid-model
  block; nothing else of the caller's (no key, token or database URL);
* network connections refused by default: a connection to anything but this machine raises inside the test process,
  and HTTP proxies point nowhere for programs it starts. A guard against tests that reach out by accident, not a
  boundary against code written to escape it (confined, below, the operating system refuses every connection);
* a time bound; after every run, finished or not, every process it started is ended (a Windows Job Object; a process
  group elsewhere), and the output is kept in a file, of which a bounded head and tail are read;
* pytest's own tallies, one outcome per test from a JUnit report outside the repository, checked against its exit code
  (``_judge``: the archive coder's verdict rule, restated here) and against the number of tests it collected, never
  text the tests print.

Where ``g6.core.confine`` has a backend (bubblewrap on Linux, a Windows AppContainer), the tests run confined: they
read the interpreter's folders and the repository (and, on Windows, what every AppContainer may read: the system
folders, Program Files), write only the run's own folders (HOME and the temp folders; the report) and, on Windows, the
run's container folder (deleted after it), so a test that writes into the repository fails and the result says so,
and reach no network, this machine included, so a test that starts a local server fails too. G6's own git calls run
here, in the parent. G6 never changes the access of the caller's folders: until the owner grants the container read on
the repository and its interpreter (the reason names the command, and never names the home folder or a folder holding
G6's own), no backend can confine the run, and it is refused (``G6_E_ISOLATION_UNAVAILABLE``) unless the person
allowed such runs (``confine.allowed_by``); ``G6_REQUIRE_ISOLATION=1`` refuses it whatever was allowed. An allowed run
uses the caller's permissions, in the caller's repository, not an operating-system sandbox. The result's
``isolation`` says which (``{"backend": null, "why": ..., "allowed_by": ...}``). Either way the code under test
shares its process with pytest and its report. Shares the process control (``g6.core.component_process``) and ``g6.workforce.verify``; no coder or model router is
invoked here.
"""
from __future__ import annotations

import configparser
import fnmatch
import hashlib
import importlib.machinery
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from xml.etree import ElementTree

from g6.core import component_process, confine, git_env, ledger
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.outside import find_program
from g6.workforce import receipt_store, verify

DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 1800
#: The most test files "the tests the change touches" may be; more are counted and said, never run silently.
MAX_DERIVED = 25
#: The most modules a run lists as loaded from outside the tree it tested (``shadowed``), and the most bytes of that
#: list G6 reads; past either, the list is incomplete and says so.
LOADED_LIMIT = 10_000
LOADED_BYTES = 16 * 1024 * 1024
#: The changed-lines mutation check: at most this many changed source files, within this many seconds in all.
MUTATED_FILES = 3
MUTATION_BUDGET_S = 600
GIT_TIMEOUT_S = 120
COPY_MAX_FILES = 10000
COPY_MAX_BYTES = 64 * 1024 * 1024
# COPY: bounded repository copies for replay and mutation.
COPY_FILE_CAP = "the repository copy cap of {n} files was exceeded"
COPY_BYTE_CAP = "the repository copy cap of {n} bytes was exceeded"
COPY_TIME_CAP = "the time budget for the repository copy was exhausted"
# COPY: copies and reports must stay outside the caller's repository.
COPY_NO_OUTSIDE = "copy checks not run: no scratch location outside the repository is available"
# COPY: replay needs a passing old-code baseline with the same identities.
OLD_BASELINE_NOT_RUN = "old tests not run: the old-code baseline did not pass with the same test identities"
#: The most time one snapshot spends hashing the submodule folders git does not look into (``_submodule``): the time
#: git gets for a listing of its own. They share ``COPY_MAX_FILES`` and ``COPY_MAX_BYTES`` too.
SUBMODULE_HASH_S = GIT_TIMEOUT_S
#: A snapshot's value for a submodule folder it did not hash, followed by why (``submodules_not_hashed``).
NOT_HASHED = "not hashed: "
# PENDING copy (pool compatibility): why a submodule folder was not hashed, and why a copy left a link out.
HASH_FILES = "it holds more than {n} files, the most G6 hashes"
HASH_BYTES = "it holds more than {n} bytes, the most G6 hashes"
HASH_TIME = "hashing it took longer than {n} seconds"
HASH_UNREADABLE = "G6 could not read {path}"
LINK_ABSOLUTE = "left out of the copy: the link names an absolute path"
LINK_OUTSIDE = "left out of the copy: the link resolves outside the repository"
LINK_NOT_MADE = "left out of the copy: the link could not be made ({error})"
LINK_NO_TARGET = "left out of the copy: nothing is at the link's target to copy in its place"
LINK_LOOP = "left out of the copy: the link names a folder that holds it, which cannot be copied in its place"
LINK_FOLDER = "left out of the copy: the submodule folder is a link, or inside one"
#: The ``sandbox`` label of a run in place: the isolation that actually ran.
SANDBOX = "repository_in_place"
#: A run's verdicts. Only ``passed`` and ``failed`` mean the tests ran to a result.
PASSED, FAILED, CHANGED = "passed", "failed", "changed_tracked_files"
RAN = frozenset({PASSED, FAILED})
#: The owner's ruling (2026-10-29): every test passed by pytest's own count, then the process exited with an error.
RUN_INCONSISTENT = "run_inconsistent"
#: Files that configure, or could stand in for, the runner (the bench's lists, ``bench.py:60-63``, and every
#: configuration file pytest looks for by name): a change to one is said, not judged.
RUNNER_FILES = frozenset({"conftest.py", "pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml",
                          "setup.cfg", "tox.ini", "pytest.py", "sitecustomize.py", "usercustomize.py"})
#: The census probe's own repository and test (``probe_repo``).
PROBE_TEST = "tests/test_probe.py::test_probe"
#: Every sentence this module says to a person: why the tests did not run, or a refusal. PENDING the founder's copy
#: review: VR-6 and VR-12.
SAID = {
    "not_absolute": "the repository path must be the full path of a folder on this computer",
    "not_a_folder": "no folder exists at the repository path",
    "no_git": "git is not installed here, and G6 needs it to read the change and to tell whether the run changed "
              "your files",
    "not_git": "the folder is not a git repository",
    "not_top": "name the repository's top folder: {top}",
    "bad_base": "git does not know the base {base!r}",
    "outside": "{what} {value!r} must be a path inside the repository",
    "no_tests": "name at least one test to run (a file, a folder or '<file>::<test>')",
    "missing": "{value!r} does not exist in the repository",
    "timed_out": "the tests did not finish within {seconds} seconds",
    "collection_error": "the tests could not be collected (often an import that fails in this environment)",
    "no_tests_collected": "pytest collected no tests from the tests named",
    "pytest_not_found": "pytest is not installed for {python}",
    "inconsistent_report": "pytest's exit code and its own tallies disagree, so neither is believed",
    # PENDING copy (the owner's ruling of 2026-10-29): a non-zero exit after a session pytest counts as all passed.
    "run_inconsistent": ("the tests passed by pytest's own count, but the process exited with an error afterwards, so "
                         "G6 does not count them as passed"),
    "could_not_start": "the test run could not start ({error})",
    "changed": "files in your repository changed while your tests ran",
    "network_off": "G6: network connections are refused for this test run",
    # PENDING copy (the fix pass after the review of Tasks 4-5): proposals, not yet reviewed with VR-6.
    "incomplete": "the test run stopped before every test it collected had run, so G6 cannot say they passed",
    "git_unreadable": "git could not read your repository after your tests ran, so G6 cannot tell what they changed",
    "git_unreadable_name": "git's view of the repository (it could not be read after the run)",
    # PENDING copy: why a confined run's test failed on an access the operating system refused.
    "confined_write": ("the tests ran confined by the operating system, where your repository is read only and the "
                       "network is closed: a test was refused an access (PermissionError) that a run with your "
                       "permissions would have had"),
    # PENDING copy (pool compatibility): a submodule folder git does not look into, as a changed name and a withheld
    # receipt.
    "submodule_name": "{path} (files in a submodule folder git does not look into; G6 hashed them itself)",
    "submodule_withheld": "no test receipt: G6 could not hash the files inside {paths}, so the digest does not bind them",
    # PENDING copy (pool compatibility): a G6_REPO_PYTHON that names no Python.
    "repo_python": ("{setting} {problem}: {value!r}. Set it, where you start G6, to the full path of the Python that "
                    "has your repository's dependencies, or unset it"),
    # PENDING copy (L1, the owner's decision): an environment linked to the same Python file as G6's own.
    "repo_linked": ("{python} and the Python running G6 are the same interpreter through links ({file}), which this "
                    "release cannot keep apart inside the operating-system boundary, so G6 ran nothing. Make G6's own "
                    "environment with python -m venv --copies and install G6 there, or set {setting}, where you start "
                    "G6, to a Python that is not linked to G6's"),
    # PENDING copy (reviewer B's critical item): an environment whose confined run would read a folder G6 does not open.
    "repo_reads": ("a confined run of {python} would read {folder!r}, which is outside the repository and holds no "
                   "Python standard library (it is the base installation the environment's pyvenv.cfg names, or "
                   "where the environment's folder leads), so G6 does not open it to the tests and ran nothing. Make "
                   "the environment again (python -m venv), or set {setting}, where you start G6, to the full path of "
                   "the Python that has your repository's dependencies"),
    # PENDING copy (review M1): why a run that loaded the tree's code from elsewhere, or cannot say, gets no receipt.
    "shadowed_withheld": ("no test receipt: the run loaded the repository's own modules from outside it, an installed "
                          "copy in place of its files ({modules})"),
    "loaded_unknown_withheld": ("no test receipt: G6 cannot say which code the run loaded (its list of the modules "
                                "loaded from outside the repository is incomplete)"),
    # PENDING copy (pool compatibility): a G6_REPO_ENV G6 cannot use. It never repeats a value.
    "repo_env": ("{setting} {problem}. Set it, where you start G6, to a JSON object of the variables your tests need, "
                 "names to text, without the names that would change how G6 runs them (PYTHON..., LD_..., DYLD_..., "
                 "PYTEST_..., G6_..., COV_CORE_..., TMP..., PATH, HOME, USERPROFILE, TEMP) and those G6 sets itself "
                 "(OPENROUTER_API_KEY, the HTTP proxies, NO_PROXY), or unset it"),
}


class RepoRefused(ValueError):
    """The repository or the request cannot be run; ``code`` is its stable ``G6_E_*`` code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: Before every git command: no index refresh (``--no-optional-locks``); paths are paths, never patterns
#: (``--literal-pathspecs``: ``a[1].py`` is not also ``a1.py``); names unquoted; and hunks never merged across the
#: unchanged lines between them, whatever the owner's ``diff.interHunkContext`` (review M7). And nothing in .git the
#: patch's author controls changes what git reports (the review of VS-4, finding 1): no replace ref
#: (``--no-replace-objects``), no file-system monitor or untracked cache answering for the working tree, and no sparse
#: checkout marking the entries of the private index ``_private`` builds.
_GIT = ("git", "--no-optional-locks", "--literal-pathspecs", "--no-replace-objects", "-c", "core.quotepath=off",
        "-c", "diff.interHunkContext=0", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", "core.sparseCheckout=false")


def _git(repo: Path, *args: str, ok: tuple[int, ...] = (0,), timeout_s: float = GIT_TIMEOUT_S,
         index: Path | None = None) -> bytes:
    """One read-only git command in ``repo`` (``_GIT``'s options first), in ``git_env.safe_git_env``'s clean
    environment (none of git's own redirections such as GIT_DIR, no transport, no lazy fetch) with every config key
    that would start a program switched off, the drivers any config defines included (one ``git_env.DRIVER_QUERY``
    first, in the same window), and a ``status`` or ``diff`` never entering a submodule (``git_env.safe_git_args``):
    the repository's .git/config may be the agent's (conformance K1, review B2-C1 and B2-C2). ``index``: a private
    index file G6 made (``_private``) in place of the repository's own."""
    if find_program("git") is None:
        raise RepoRefused("G6_E_GIT_MISSING_BINARY", SAID["no_git"])
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors, \
                tempfile.TemporaryDirectory(prefix="g6-git-") as scratch:
            with component_process.window(timeout_s):
                code = component_process.run_process([git_env.git_program(), "-C", str(repo), *git_env.DRIVER_QUERY],
                                                     env=git_env.safe_git_env(Path(scratch)), stdout=output,
                                                     stderr=errors)
                output.seek(0)
                env = git_env.safe_git_env(Path(scratch), git_env.driver_settings(code, output.read()))
                if index is not None:
                    env["GIT_INDEX_FILE"] = str(index)
                output.seek(0)
                output.truncate()
                code = component_process.run_process([*_GIT, "-C", str(repo), *git_env.safe_git_args(*args)],
                                                     env=env, stdout=output, stderr=errors)
            if code not in ok:
                errors.seek(0)
                said = errors.read(800).decode("utf-8", "replace").strip()[:200]
                raise RepoRefused("G6_E_GIT_COMMAND_FAILED", f"git {args[0]} failed: {said}")
            output.seek(0)
            return output.read()
    except component_process.ContainmentFailure:
        raise
    except component_process.ExecutionInterrupted as exc:
        if exc.stopped:
            raise
        component_process.checkpoint()  # an enclosing allowance must reach the Door
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", f"git {args[0]} failed (TimeoutExpired)") from exc
    except git_env.UnsafeGitConfig as exc:
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", f"git {args[0]} not run: {exc}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", f"git {args[0]} failed ({type(exc).__name__})") from exc


def _private(repo: Path, commit: str, *commands: tuple[str, ...]) -> list[bytes]:
    """Each of ``commands`` run with a private index G6 builds from ``commit``'s tree in a scratch folder (the review of
    VS-4, finding 1): the repository's own index, its stat cache, its assume-unchanged and skip-worktree flags and any
    entry written into it are never consulted, so ``git diff`` with it hashes every tracked file and compares the
    content with the base commit's blob (after git's line-ending conversion, so an untouched file is never a change),
    and ``ls-files --others`` lists every file the base tree does not hold."""
    with tempfile.TemporaryDirectory(prefix="g6-index-") as scratch:
        index = Path(scratch) / "index"
        _git(repo, "read-tree", *([commit] if commit else ["--empty"]), index=index)   # no commit yet: empty
        return [_git(repo, *command, index=index) for command in commands]


def _gitlinks(repo: Path, index: bytes | None = None) -> tuple[str, ...]:
    """The gitlinks (mode 160000) the index (``index``: its ``ls-files --stage -z`` listing, when already read) or HEAD
    holds, each a submodule that was never set up here: a folder git does not enter (each status and diff skips
    submodules, ``git_env.safe_git_args``, and ``submodule.recurse`` is off) and G6 hashes itself (``_submodule``). A
    refusal (``G6_E_GIT_SUBMODULE_REFUSED``) when one was set up, a .git file or folder in its folder or anything under
    the repository's .git/modules: git would run inside it under that submodule's own config (review B2-C1)."""
    listed, head = git_env.GITLINK_QUERIES
    records = [record for listing in (_git(repo, *listed) if index is None else index, _git(repo, *head, ok=(0, 128)))
               for record in listing.split(b"\0") if record.startswith(b"160000 ")]
    if not records:
        return ()
    paths = {record: record.partition(b"\t")[2].decode("utf-8", "surrogateescape") for record in records}
    folders = _git(repo, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir").decode(
        "utf-8", "surrogateescape").splitlines()
    modules = any(_holds_anything(Path(folder) / "modules") for folder in folders if folder)
    set_up = [record for record, rel in paths.items() if modules or os.path.lexists(repo / rel / ".git")]
    if set_up:
        try:
            git_env.refuse_gitlinks(b"\0".join(set_up))
        except git_env.UnsafeGitConfig as exc:
            raise RepoRefused(git_env.SUBMODULE_REFUSED, str(exc)) from exc
    return tuple(sorted(set(paths.values())))


def _holds_anything(folder: Path) -> bool:
    """Whether ``folder`` has an entry; one that cannot be read may (True)."""
    try:
        return folder.is_dir() and any(folder.iterdir())
    except OSError:
        return True


def _gitlink_entries(listing: bytes, field: int) -> dict[str, str]:
    """Each gitlink of a ``ls-files --stage`` (``field`` 1) or ``ls-tree`` (``field`` 2) listing: path to commit."""
    found = {}
    for record in _paths(listing):
        meta, _, rel = record.partition("\t")
        if meta.startswith("160000 "):
            found[rel] = meta.split()[field]
    return found


def _paths(raw: bytes) -> list[str]:
    return [path for path in raw.decode("utf-8", "surrogateescape").split("\0") if path]


def _text(raw: bytes) -> str:
    """Bytes git holds as text, with the line endings ``Path.read_text`` gives (universal newlines), so a file read
    from git and the same file read from the disk compare equal whatever ``core.autocrlf`` did to either."""
    return raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _control(value: str) -> bool:
    """A control character anywhere (a NUL ends a command-line argument; a newline or an escape is never a name)."""
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def shape_problem(value: object, what: str) -> str:
    """Why ``value`` cannot name a path inside a repository, judged without reading the disk ("" when it can): text,
    with no control character anywhere (after ``::`` too), not empty, not shaped like an option, a pytest argument file
    (``@name``) or a git pathspec, not absolute, never above the folder it starts in."""
    if not isinstance(value, str) or _control(value):
        return SAID["outside"].format(what=what, value=value)
    head = value.partition("::")[0].replace("\\", "/")
    if (not head.strip() or value.startswith(("-", ":", "@")) or head.startswith("/") or re.match(r"[A-Za-z]:", head)
            or ".." in PurePosixPath(head).parts):
        return SAID["outside"].format(what=what, value=value)
    return ""


def checked_repo(path: str | Path) -> Path:
    """The repository's top folder, or a refusal: a full path, a folder, a git work tree, and its top."""
    given = Path(str(path))
    if not given.is_absolute():
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["not_absolute"])
    if not given.is_dir():
        raise RepoRefused("G6_E_GIT_REPO_INVALID", SAID["not_a_folder"])
    repo = given.resolve()
    try:
        top = _git(repo, "rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
    except RepoRefused as refused:
        # git missing, or one G6 cannot run safely (older than 2.31, or a config it cannot read): that reason, never
        # "not a git repository"
        if refused.code == "G6_E_GIT_MISSING_BINARY" or isinstance(refused.__cause__, git_env.UnsafeGitConfig):
            raise
        raise RepoRefused("G6_E_GIT_REPO_INVALID", SAID["not_git"]) from refused
    if Path(top).resolve() != repo:
        raise RepoRefused("G6_E_GIT_REPO_INVALID", SAID["not_top"].format(top=top))
    return repo


def inside(repo: Path, value: str, what: str) -> str:
    """``value`` (a path, or ``file::test``) as it names something inside ``repo``, or a refusal: its shape first
    (``shape_problem``), then where it resolves through links and junctions (``verify.contained``)."""
    problem = shape_problem(value, what)
    if problem:
        raise RepoRefused("G6_E_INPUT_VALIDATION", problem)
    head, sep, rest = value.partition("::")
    head = head.replace("\\", "/")
    if isinstance(verify.contained(repo, head), str):
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["outside"].format(what=what, value=value))
    return head + sep + rest


def is_test_file(rel: str) -> bool:
    """pytest's default test files, ``test_*.py`` (the rule ``verify.run_set`` uses), named with the case rule pytest
    matches them by on this system (``fnmatch``, through ``os.path.normcase``: Windows ignores case; review 14b)."""
    name = os.path.normcase(PurePosixPath(rel).name)
    return name.endswith(".py") and name.startswith("test_")


#: Folders whose Python files are the tests' own (``g6.safety_engine.weakening``'s rule), and the files pytest or
#: Python runs before any test.
_TEST_FOLDERS = frozenset({"tests", "test", "testing"})
_START_UP = frozenset({"conftest.py", "sitecustomize.py", "usercustomize.py"})


def is_test_related(rel: str) -> bool:
    """A Python file the tests are made of, by its path alone: a test file by pytest's default names (``test_*.py``,
    ``*_test.py``) or unittest's and Django's (``test*.py``, ``tests.py``), with ``is_test_file``'s case rule;
    ``conftest.py`` or a start-up file; or any Python file under a ``tests``, ``test`` or ``testing`` folder, such as a
    helper the tests check through (review C2). ``test_related`` adds what the repository and the review name."""
    path = PurePosixPath(rel)
    name = os.path.normcase(path.name)
    return name.endswith(".py") and (name.startswith("test") or name.endswith("_test.py")
                                     or path.name.lower() in _START_UP
                                     or any(part.lower() in _TEST_FOLDERS for part in path.parts[:-1]))


def test_related(change: Change, ids: Iterable[str] = ()) -> tuple[str, ...]:
    """The changed files the tests are made of (the owner's ruling on the launch review's C1): ``is_test_related``'s,
    the Python files a test id among ``ids`` names (a stated check's, say, whatever its file is called), and the
    Python files the repository's own ``python_files`` setting names (``python_files``)."""
    ids = list(ids)
    named = {node.partition("::")[0].replace("\\", "/").strip("/") for node in ids}
    patterns = python_files(change.repo, ids)
    return tuple(rel for rel in change.files if is_test_related(rel) or rel.endswith(".py") and (
        rel in named or any(_python_file(rel, pattern) for pattern in patterns)))


def _python_file(rel: str, pattern: str) -> bool:
    """Whether pytest's ``python_files`` ``pattern`` names ``rel``: by its name, or, for a pattern with a ``/``, by
    the end of its path (pytest's ``fnmatch_ex``)."""
    pattern = pattern.replace("\\", "/")
    if "/" not in pattern:
        return fnmatch.fnmatch(PurePosixPath(rel).name, pattern)
    return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, "*/" + pattern.lstrip("/"))


def python_files(repo: Path, ids: Iterable[str] = ()) -> tuple[str, ...]:
    """The repository's own ``python_files`` patterns: from the pytest configuration that applies to ``ids`` (pytest's
    search, as ``_config_args`` follows it: from the folder the tests named share, or the repository's top when none
    is named, up to the top), as that file holds them now; () when none applies, it sets none or it cannot be read."""
    ids = list(ids)
    folder = repo
    if ids:
        dirs = [path if path.is_dir() else path.parent for path in (repo / node.partition("::")[0] for node in ids)]
        folder = Path(os.path.commonpath([str(path) for path in dirs]))
    while True:
        found = _config_file(folder)
        if found is not None:
            return _python_files_in(found)
        if folder == repo or folder.parent == folder or not folder.is_relative_to(repo):
            return ()
        folder = folder.parent


def _python_files_in(path: Path) -> tuple[str, ...]:
    """``python_files`` as one pytest configuration file sets it (a TOML list or string, an ini line of patterns)."""
    name = path.name.lower()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if name.endswith(".toml"):
            data = tomllib.loads(text)
            table = data.get("tool", {}).get("pytest", {}) if name == "pyproject.toml" else data.get("pytest", {})
            table = table.get("ini_options", table) if isinstance(table, dict) else {}
            value = table.get("python_files") if isinstance(table, dict) else None
        else:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read_string(text)
            value = parser.get("tool:pytest" if name == "setup.cfg" else "pytest", "python_files", fallback=None)
    except (OSError, ValueError, configparser.Error, AttributeError):
        return ()
    if isinstance(value, str):
        return tuple(value.split())
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


@dataclass(frozen=True)
class Change:
    """The change in a repository against a base, as git reports it (``change``)."""

    repo: Path
    base: str
    base_commit: str
    head: str
    files: tuple[str, ...]
    before: dict[str, str]
    after: dict[str, str]
    sources: tuple[str, ...]
    runner_files: tuple[str, ...]
    #: Test files moved since the base, old path to new (``_moves``): the new file is compared with the old path's
    #: text at the base, not read as one test file deleted and another added (review D3). ``files``, ``before`` and
    #: ``after`` still hold both paths.
    renamed: dict[str, str] = field(default_factory=dict)
    #: The submodule folders git does not look into (``_gitlinks``), so git cannot check their files against the base.
    submodules: tuple[str, ...] = ()
    #: Those whose gitlink entry differs from the base commit's: added, removed, or naming another commit.
    submodule_changes: tuple[str, ...] = ()
    #: The tracked files git's assume-unchanged or skip-worktree mark hides from its own diff that differ from the
    #: base commit's blob (``_held``): changed files like any other, in ``files`` too.
    held: tuple[str, ...] = ()
    #: The files so marked that are not in the working tree (a sparse checkout): G6 cannot compare them with the base
    #: commit, so whether the patch changed them is unknown, never "unchanged".
    unseen: tuple[str, ...] = ()
    #: The changed files that are, or lie below, a link whose target resolves outside the repository
    #: (``links_outside``): G6 never reads one in its own process, as a copy never holds one (review M1). They stay in
    #: ``files``; ``sources``, ``after`` and every text G6 reads leave them out.
    outside_links: tuple[str, ...] = ()


def change(repo: Path, base: str = "HEAD", paths: tuple[str, ...] = (), *,
           read_file: Callable[[Path], str] | None = None) -> Change:
    """The change in ``repo`` against ``base``: changed tracked files (staged or not, and committed since ``base``) and
    untracked ones, within ``paths`` when given; each changed test file's text at the base (absent when it is new) and
    now (absent when it was deleted), both with the line endings ``Path.read_text`` gives; the changed Python files
    that are not tests; the runner files it touches; the test files it moved; and the submodule folders git does not
    look into, with those whose gitlink entry differs from the base commit's (git's own diff skips submodules). A
    repository holding a submodule that was set up is refused (``G6_E_GIT_SUBMODULE_REFUSED``)."""
    if not isinstance(base, str) or not base.strip() or base.startswith("-") or _control(base):
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["bad_base"].format(base=base))
    gitlinks = _gitlinks(repo)
    wanted = [inside(repo, path, "paths") for path in paths]
    spec = ["--", *wanted] if paths else []
    try:
        commit = _git(repo, "rev-parse", "--verify", "-q", f"{base}^{{commit}}").decode().strip()
    except RepoRefused as refused:
        if refused.code == "G6_E_GIT_MISSING_BINARY":
            raise
        raise RepoRefused("G6_E_GIT_REPO_INVALID", SAID["bad_base"].format(base=base)) from refused
    head = _git(repo, "rev-parse", "--verify", "-q", "HEAD", ok=(0, 1)).decode().strip()
    held, unseen = _held(repo, commit, spec, set(gitlinks))
    # the review of VS-4, finding 1: the list never comes from the repository's own index or its replace refs
    diffed, others = _private(repo, commit, ("diff", "--name-only", "-z", "--no-renames", "--no-textconv", *spec),
                              ("ls-files", "--others", "--exclude-standard", "-z", *spec))
    files = tuple(sorted((set(_paths(diffed)) | set(_paths(others)) | set(held)) - set(unseen)))
    tests = [rel for rel in files if is_test_file(rel)]
    existed = set(_paths(_git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *tests))) if tests else set()
    before = {rel: _text(_git(repo, "show", f"{commit}:{rel}")) for rel in sorted(existed)}
    reader = read_file or (lambda path: path.read_text(encoding="utf-8", errors="replace"))
    outside = tuple(rel for rel in files if links_outside(repo, rel))
    after = {rel: reader(repo / rel) for rel in tests   # a caller's own ``read_file`` judges a link itself
             if (read_file is not None or rel not in outside) and (repo / rel).is_file()}
    sources = tuple(rel for rel in files if rel.endswith(".py") and not is_test_file(rel)
                    and PurePosixPath(rel).name != "conftest.py" and rel not in outside and (repo / rel).is_file())
    runner = tuple(rel for rel in files if PurePosixPath(rel).name.lower() in RUNNER_FILES or rel.endswith(".pth"))
    gone, new = [rel for rel in before if rel not in after], [rel for rel in after if rel not in before]
    moved = _moves(repo, commit, gone, new, before, after) if gone and new else {}
    then, now = (_gitlink_entries(_git(repo, "ls-tree", "-r", "-z", commit), 2),
                 _gitlink_entries(_git(repo, "ls-files", "--stage", "-z"), 1))

    def ours(rel: str) -> bool:   # within ``paths``, or ``paths`` within it
        return not paths or any(rel == path or rel.startswith(path.rstrip("/") + "/") or path.startswith(rel + "/")
                                for path in wanted)

    links = tuple(rel for rel in sorted(set(gitlinks) | set(then) | set(now)) if ours(rel))
    return Change(repo, base, commit, head, files, before, after, sources, runner, moved,
                  submodules=tuple(rel for rel in links if rel in gitlinks),
                  submodule_changes=tuple(rel for rel in links if then.get(rel) != now.get(rel)),
                  held=tuple(sorted(held)), unseen=unseen, outside_links=outside)


def links_outside(repo: Path, rel: str) -> bool:
    """Whether ``rel``, or a folder on its way, is a link or junction and the path resolves outside ``repo`` (or
    cannot be resolved): the rule copies follow (a link is kept only when it resolves inside), so G6 never reads such
    a file in its own process either (review M1)."""
    parts = PurePosixPath(rel).parts
    if not any(_linked(repo.joinpath(*parts[:end])) for end in range(1, len(parts) + 1)):
        return False
    try:
        return not (repo / rel).resolve().is_relative_to(repo.resolve())
    except (OSError, RuntimeError):
        return True


def _held(repo: Path, commit: str, spec: list[str], gitlinks: set[str]) -> tuple[set[str], tuple[str, ...]]:
    """The tracked files git's assume-unchanged or skip-worktree mark (``ls-files -v``: a lowercase tag, or "S") hides
    from its own diff, compared by content with ``commit``'s blob as every other tracked file is (review C2): those
    that differ, or that ``commit`` does not hold, or that are a link; and, apart, those not in the working tree (a
    sparse checkout), which cannot be compared. Change detection never depends on git's index flags."""
    marked = [entry[2:] for entry in _paths(_git(repo, "ls-files", "-v", "-z", *spec))
              if (entry[:1].islower() or entry[:1] == "S") and entry[2:] not in gitlinks]
    if not marked:
        return set(), ()
    unseen = tuple(sorted(rel for rel in marked if not os.path.lexists(repo / rel)))
    present = [rel for rel in marked if rel not in unseen]
    then = texts_at(repo, commit, present, limit=None)
    changed = set()
    for rel in present:   # a link is changed, and never read (review M1)
        path = repo / rel
        if (then.get(rel) is None or links_outside(repo, rel) or _linked(path) or not path.is_file()
                or _text(path.read_bytes()) != then[rel]):
            changed.add(rel)
    return changed, unseen


def _moves(repo: Path, commit: str, gone: list[str], new: list[str], before: dict[str, str],
           after: dict[str, str]) -> dict[str, str]:
    """Test files moved since the base, old path to new: as git pairs them (``-M``: a move git can see, such as
    ``git mv``, with the text unchanged or mostly so), then a test file deleted whose text, unchanged, is now at a new
    untracked path (a move on the disk alone, which git's diff cannot see)."""
    tokens = _paths(_git(repo, "diff", "-M", "--name-status", "-z", "--no-textconv", commit, "--", *gone, *new))
    found: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        if tokens[index].startswith("R") and index + 2 < len(tokens):
            old, now = tokens[index + 1], tokens[index + 2]
            if old in gone and now in new:
                found[old] = now
            index += 3
        else:
            index += 2
    for old in (old for old in gone if old not in found):
        twin = next((now for now in new if now not in found.values() and after[now] == before[old]), None)
        if twin is not None:
            found[old] = twin
    return found


def related_texts(change: Change, related: Iterable[str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Every test-related file the change touches (``related``, by default ``is_test_related``'s; patch review gives
    ``test_related``'s), as ``change`` reads its test files: its text at the base commit (absent when it is new) and
    now (absent when it was deleted), with the line endings ``Path.read_text`` gives. A file moved since the base
    (``_moves``) is keyed by its new path on both sides, so it is compared with its old text, not read as one file
    deleted and another added."""
    wanted = None if related is None else set(related)
    related = [rel for rel in change.files if (is_test_related(rel) if wanted is None else rel in wanted)]
    if not related:
        return {}, {}
    existed = set(_paths(_git(change.repo, "ls-tree", "-r", "-z", "--name-only", change.base_commit, "--", *related)))
    before = {rel: _text(_git(change.repo, "show", f"{change.base_commit}:{rel}")) for rel in sorted(existed)}
    after = {rel: (change.repo / rel).read_text(encoding="utf-8", errors="replace") for rel in related
             if rel not in change.outside_links and (change.repo / rel).is_file()}
    gone, new = [rel for rel in before if rel not in after], [rel for rel in after if rel not in before]
    moved = _moves(change.repo, change.base_commit, gone, new, before, after) if gone and new else {}
    return {moved.get(rel, rel): text for rel, text in before.items()}, after


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def changed_lines(change: Change, rel: str) -> set[int]:
    """The lines of source file ``rel`` (as it is now) that the change added or rewrote; every line of a file new since
    the base. Empty for a file the change did not touch, or touched only by deleting lines. The text compared is the
    file's own, never a ``textconv`` filter's (review M7)."""
    if rel not in change.sources:
        return set()
    [diffed] = _private(change.repo, change.base_commit,   # never the repository's index (the review of VS-4)
                        ("diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", "--", rel))
    text = diffed.decode("utf-8", "replace")
    if not text.strip():  # untracked: new since the base
        return set(range(1, len((change.repo / rel).read_text(encoding="utf-8", errors="replace").splitlines()) + 1))
    found: set[int] = set()
    for start, count in _HUNK.findall(text):
        found.update(range(int(start), int(start) + (int(count) if count != "" else 1)))
    return found


def derive_tests(change: Change, limit: int = MAX_DERIVED, *,
                 read_file: Callable[[Path], str] | None = None) -> tuple[list[str], int]:
    """The tests the change touches: its changed test files first, then every test file that names a changed module
    (``verify.run_set``'s rule over the files git knows: tracked, and untracked but not ignored), each present now, at
    most ``limit``; and how many more there were."""
    known = (_paths(_git(change.repo, "ls-files", "-z"))
             + _paths(_git(change.repo, "ls-files", "--others", "--exclude-standard", "-z")))
    options = {"read_file": read_file} if read_file is not None else {}
    found = [rel for rel in verify.run_set(change.repo, list(change.files), candidates=known, **options)
             if (change.repo / rel).is_file()]
    ordered = [rel for rel in found if rel in change.files] + [rel for rel in found if rel not in change.files]
    return ordered[:limit], max(0, len(ordered) - limit)


#: The most of one untracked file ``diff_text`` reads.
DIFF_FILE_CHARS = 200_000


def diff_text(change: Change) -> str:
    """The change as one unified diff: git's own for tracked files (each file's text, never a ``textconv`` filter's),
    then each untracked file the change holds as added lines (at most ``DIFF_FILE_CHARS`` of it), so a scan of the
    diff sees a new test file too."""
    # a private index built from the base tree (the review of VS-4, finding 1): git's flags, its stat cache and an
    # entry written into the repository's own index hide nothing (review C2 too)
    diffed, others = _private(change.repo, change.base_commit,   # --text: an attribute never hides a file's lines
                              ("diff", "--no-color", "--no-ext-diff", "--no-textconv", "--text"),
                              ("ls-files", "--others", "--exclude-standard", "-z"))
    text = diffed.decode("utf-8", "replace")
    for rel in sorted(set(_paths(others)) & set(change.files) - set(change.outside_links)):   # never read (M1)
        lines = (change.repo / rel).read_text(encoding="utf-8", errors="replace")[:DIFF_FILE_CHARS].splitlines()
        text += f"--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
    return text


# --- the run ----------------------------------------------------------------------------------------------------------

_HEAD, _INDEX, _MARKS = "\0HEAD", "\0index", "\0marks"
_KEEP =("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
#: The paid-model block every test run carries (``venture.truth.run_isolated``'s, with the dummy key every G6 test
#: process uses): no real key reaches the tests, and the model backend is the local one.
_MODEL_BLOCK = {"G6_LLM_BACKEND_OVERRIDE": "ollama", "OPENROUTER_API_KEY": "sk-or-dummy-not-a-key"}
_NOWHERE = "http://127.0.0.1:9"
_PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_RETURN_HEAD, _RETURN_TAIL = 2048, 6144
#: The runner's bootstrap (``venture.truth._ISOLATED_BOOT``'s pattern): first the variables of the run's own file are
#: set (``G6_REPO_ENV``'s, and the folder Hypothesis keeps its database in), which the operating system's boundary
#: would not pass on and which never appear on a command line; the real pytest is imported after them and before the
#: repository joins the path (its folders arrive as one JSON list, so a ';' or ':' in a path never splits it); then
#: the network guard, unless allowed; then the mutant's import hook, when given. Its own arguments are taken off
#: ``sys.argv`` before the tests run. When pytest would load pytest-xdist (its ``pytest11`` entry point is installed),
#: ``-n0 --dist=no`` come after the repository's own options and win: the tests run in this one process, where the
#: guard and the hook apply (review I8). A small plugin writes, as the session ends, how many tests were collected and
#: deselected, and how many started and finished (a setup and a teardown reported), so a run that stops part-way with
#: exit code 0 is not read as a pass (review I3), and the message of each failure, a collection's included, in full
#: (``failure_messages``: pytest's summary line is cut to the terminal's width); and each test or file whose failure was
#: an operating-system error, or was raised from or during one (``os_errors``: read from the exception objects, their
#: ``__cause__`` and ``__context__``, never from text the code printed; reviewer B's B2). Another turns pytest's "fd"
#: capture, its default, into "sys" before
#: capturing starts: "fd" opens the null device for standard input, which a Windows AppContainer may be refused
#: (``truth.CAPTURE``); a capture the repository's own options chose otherwise (``-s``) stays. A third, as the
#: session ends, writes each module the pytest process has loaded from outside the tree under test (the working
#: folder; an environment inside it, below ``site-packages`` or ``dist-packages``, is outside) and outside the standard
#: library, name to file, at most ``LOADED_LIMIT`` of them (``shadowed``): read from each module's own dictionary,
#: never through its attributes (a lazily loaded module would load), and any error makes the list incomplete, never the
#: run fail. Modules of a subprocess or a forked child are not seen. Written for Python 3.8 and later.
_BOOT = r'''
import json, os, socket, sys
sys.path[:] = [p for p in sys.path if p not in ("", os.getcwd())]
with open(sys.argv[6], encoding="utf-8") as _g6_env:
    os.environ.update(json.load(_g6_env))
del _g6_env
import pytest
entries, network, target, source, counted = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
loaded, limit = sys.argv[7], int(sys.argv[8])
args = sys.argv[9:]
del sys.argv[1:9]
sys.path[:0] = [p for p in entries if p]
if network != "1":
    _connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex
    def _here(address):
        if not isinstance(address, tuple) or not address:
            return True
        host = str(address[0])
        if host in ("localhost", "::1"):
            return True
        import ipaddress
        try:
            return ipaddress.ip_address(host.split("%")[0]).is_loopback
        except ValueError:
            return False
    def connect(self, address):
        if not _here(address):
            raise OSError(NETWORK_OFF)
        return _connect(self, address)
    def connect_ex(self, address):
        if not _here(address):
            raise OSError(NETWORK_OFF)
        return _connect_ex(self, address)
    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
if target:
    import importlib.abc, importlib.machinery
    with open(source, encoding="utf-8") as handle:
        mutated = handle.read()
    wanted = os.path.normcase(os.path.realpath(target))
    class _Mutant(importlib.abc.Loader):
        def __init__(self, origin):
            self.origin = origin
        def create_module(self, spec):
            return None
        def exec_module(self, module):
            exec(compile(mutated, self.origin, "exec"), module.__dict__)
    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target_module=None):
            spec = importlib.machinery.PathFinder.find_spec(name, path)
            if spec is not None and spec.origin and os.path.normcase(os.path.realpath(spec.origin)) == wanted:
                spec.loader = _Mutant(spec.origin)
                return spec
            return None
    sys.meta_path.insert(0, _Finder())
try:
    from importlib import metadata
    try:
        plugins = metadata.entry_points(group="pytest11")
    except TypeError:  # Python 3.8 and 3.9: a dict of groups
        plugins = metadata.entry_points().get("pytest11", ())
    if any(plugin.name == "xdist" for plugin in plugins):
        args += ["-n0", "--dist=no"]
except Exception:  # no importlib.metadata (Python 3.7): the repository's options stand as they are
    pass
class _Counted:
    def __init__(self):
        self.collected, self.deselected, self.started, self.finished = None, 0, set(), set()
        self.failed, self.setup_errors = [], {}
        self.test_outcomes, self.collected_ids, self.omitted_ids = {}, set(), set()
        self.rootdir = ""
        self.messages = {}
        self.os_errors = set()
    def pytest_exception_interact(self, node, call, report):
        error, seen = getattr(getattr(call, "excinfo", None), "value", None), set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            if isinstance(error, OSError):
                self.os_errors.add(report.nodeid)
                break
            error = error.__cause__ or error.__context__
        self.write()
    def pytest_itemcollected(self, item):
        self.collected_ids.add(item.nodeid)
    def pytest_deselected(self, items):
        self.deselected += len(items)
        self.omitted_ids.update(item.nodeid for item in items)
    def pytest_collection_finish(self, session):
        self.rootdir = str(getattr(session.config, "rootpath", None) or session.config.rootdir)
        self.collected = len(session.items) + self.deselected
        self.omitted_ids.update(self.collected_ids - {item.nodeid for item in session.items})
        self.write()
    def pytest_collectreport(self, report):
        if report.skipped:
            self.test_outcomes[report.nodeid] = {"collection": "skipped"}
        elif report.failed:
            self.messages[report.nodeid] = str(report.longrepr)[-2000:]
    def pytest_runtest_logreport(self, report):
        self.test_outcomes.setdefault(report.nodeid, {})[report.when] = report.outcome
        if report.when in ("setup", "teardown"):
            (self.started if report.when == "setup" else self.finished).add(report.nodeid)
        if report.failed:
            crash = getattr(report.longrepr, "reprcrash", None)
            self.messages.setdefault(report.nodeid, str(getattr(crash, "message", "") or report.longrepr)[-2000:])
            if type(report.longrepr).__name__ == "FixtureLookupErrorRepr":
                self.setup_errors[report.nodeid] = str(report.longrepr)
            elif report.nodeid not in self.failed:
                self.failed.append(report.nodeid)
        self.write()
    def pytest_sessionfinish(self, session):
        self.write()
    def write(self):
        if self.collected is not None:
            with open(counted, "w", encoding="utf-8") as handle:
                json.dump({"collected": self.collected, "deselected": self.deselected, "started": len(self.started),
                           "finished": len(self.finished), "failed_ids": self.failed,
                           "setup_errors": self.setup_errors, "test_outcomes": self.test_outcomes,
                           "collected_ids": sorted(self.collected_ids), "omitted_ids": sorted(self.omitted_ids),
                           "rootdir": self.rootdir, "messages": self.messages,
                           "os_errors": sorted(self.os_errors)}, handle)
class _Capture:
    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_load_initial_conftests(self, early_config):
        if getattr(early_config.known_args_namespace, "capture", None) == "fd":
            early_config.known_args_namespace.capture = "sys"
        yield
class _Loaded:
    def pytest_sessionfinish(self, session):
        found, complete = {}, True
        try:
            import sysconfig
            def real(path):
                return os.path.normcase(os.path.realpath(path))
            def within(path, top):
                # below top, with no installed packages' folder between: an environment inside the tree is not it
                if not (path == top or path.startswith(top.rstrip(os.sep) + os.sep)):
                    return False
                return not {"site-packages", "dist-packages"} & set(path[len(top):].split(os.sep))
            tree, std = real(os.getcwd()), real(sysconfig.get_paths()["stdlib"])
            for name, module in sorted(list(sys.modules.items()), key=lambda item: item[0]):
                if module is None:
                    continue
                try:
                    try:   # never through the module's own attributes: a lazily loaded one would load now
                        attributes = object.__getattribute__(module, "__dict__")
                    except AttributeError:
                        continue
                    path = attributes.get("__file__")
                    if not isinstance(path, str) or not path:
                        continue
                    here = real(path)
                    if within(here, tree) or within(here, std):
                        continue
                    if len(found) >= limit:
                        complete = False
                        break
                    found[name] = os.path.realpath(path)
                except Exception:
                    complete = False
        except Exception:
            complete = False
        try:
            with open(loaded, "w", encoding="utf-8") as handle:
                json.dump({"modules": found, "complete": complete}, handle)
        except Exception:
            pass
sys.exit(pytest.main(args, plugins=[_Counted(), _Capture(), _Loaded()]))
'''.replace("NETWORK_OFF", repr(SAID["network_off"]))
_BOOT = confine.CHILD_PRELUDE + _BOOT


def _untracked(repo: Path) -> list[str]:
    """The files git lists as untracked and not ignored (a nested repository as its folder)."""
    return _paths(_git(repo, "ls-files", "--others", "--exclude-standard", "-z"))


def _digest(path: Path) -> str:
    """The bytes of a file, read in blocks; a nested repository's own ``snapshot`` (without its untracked files); ""
    when nothing is there. A file or repository that cannot be read reads "unreadable" before and
    after alike, so a change to it is not seen."""
    try:
        component_process.checkpoint()
        if path.is_dir():
            if not (path / ".git").exists():  # never ask git from a plain folder: it would answer for the parent
                return ""
            return hashlib.sha256(json.dumps(sorted(snapshot(path, ()).items())).encode("utf-8")).hexdigest()
        if not path.is_file():
            return ""
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                component_process.checkpoint()
                digest.update(block)
        component_process.checkpoint()
        return digest.hexdigest()
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except (OSError, RepoRefused):
        return "unreadable"


def snapshot(repo: Path, untracked: list[str] | tuple[str, ...] | None = None) -> dict[str, str]:
    """What a run may not change, as git sees it: HEAD, the index (the staged files), git's assume-unchanged and
    skip-worktree marks, and the bytes (``_digest``) of every tracked file that differs from the index (a deleted one
    reads "") or from HEAD's tree by content (``_private``: whatever an entry written into the index says; the review
    of VS-4), of every file so marked (git's own diff skips them; review M4), and of each of ``untracked`` (default:
    the untracked files that are not ignored now; ``run`` passes the ones that existed before it, review I7). Also,
    as git cannot see them, each submodule folder git does not look into (``_gitlinks``), as G6 hashes it
    (``_submodule``: every file in it, untracked and ignored ones included). A repository holding a submodule that was
    set up is refused (``G6_E_GIT_SUBMODULE_REFUSED``: git would run inside it under its own config, review B2-C1),
    before a run and after one alike. Ignored files elsewhere, and files created after ``untracked`` was read, are not
    part of it. Raises ``RepoRefused`` when git cannot read the repository."""
    marks, index = _git(repo, "ls-files", "-v", "-z"), _git(repo, "ls-files", "--stage", "-z")
    gitlinks = _gitlinks(repo, index)
    head = _git(repo, "rev-parse", "--verify", "-q", "HEAD", ok=(0, 1))
    found = {_HEAD: hashlib.sha256(head).hexdigest(),
             _INDEX: hashlib.sha256(index).hexdigest(), _MARKS: hashlib.sha256(marks).hexdigest()}
    # ``ls-files -v``: a lowercase tag is assume-unchanged, "S" skip-worktree.
    held = [entry[2:] for entry in _paths(marks) if entry[:1].islower() or entry[:1] == "S"]
    # The review of VS-4: what differs from HEAD's tree by content, through a private index, so no entry written into
    # the repository's own index keeps a changed file's bytes out of the snapshot (receipts bind the bytes that ran);
    # and each file the index holds that HEAD does not, a staged one, as before.
    diffed, others = _private(repo, head.decode().strip(), ("diff", "--name-only", "-z", "--no-renames",
                                                             "--no-textconv"),
                              ("ls-files", "--others", "--exclude-standard", "-z"))
    staged = set(_paths(others)) & {entry.partition("\t")[2] for entry in _paths(index)}
    for rel in (*_paths(_git(repo, "diff", "--name-only", "-z")), *_paths(diffed), *sorted(staged), *held,
                *(_untracked(repo) if untracked is None else untracked)):
        found[rel] = _digest(repo / rel)
    budget = {"files": 0, "bytes": 0, "until": time.monotonic() + SUBMODULE_HASH_S}
    for rel in gitlinks:
        found[_SUBMODULE + rel] = _submodule(repo, rel, budget)
    return found


#: A snapshot's key for a submodule folder git does not look into, before its path.
_SUBMODULE = "\0submodule\0"


class _OverCap(Exception):
    """A submodule folder holds more than one snapshot may hash."""


def _submodule(repo: Path, rel: str, budget: dict) -> str:
    """The SHA-256 of a submodule folder git does not look into: every file and link in it, untracked and ignored ones
    included (but not ``__pycache__`` folders, the bytecode Python writes as the code runs), by sorted path, with each
    file's bytes and each link's own text, never followed (nor is a link on the way to the folder); "" when nothing is
    there. ``NOT_HASHED`` and why, never a hash of part of it, when this
    snapshot's submodule folders together (``budget``) hold more than ``COPY_MAX_FILES`` files or ``COPY_MAX_BYTES``
    bytes, take longer than ``SUBMODULE_HASH_S`` seconds, or cannot be read."""
    parts = PurePosixPath(rel).parts
    top = repo.joinpath(*parts)
    entries: list[tuple[str, str, str]] = []

    def counted(size: int) -> None:
        component_process.checkpoint()
        budget["bytes"] += size
        if budget["bytes"] > COPY_MAX_BYTES:
            raise _OverCap(HASH_BYTES.format(n=COPY_MAX_BYTES))
        if time.monotonic() >= budget["until"]:
            raise _OverCap(HASH_TIME.format(n=SUBMODULE_HASH_S))

    def failed(error: OSError) -> None:
        raise error

    try:
        for end in range(1, len(parts) + 1):
            here = repo.joinpath(*parts[:end])
            if _linked(here):
                return hashlib.sha256(json.dumps(["link", "/".join(parts[:end]), os.readlink(here)]).encode()).hexdigest()
        if not top.is_dir():
            return _digest(top)
        for folder, dirs, files in os.walk(top, onerror=failed):
            here = Path(folder)
            names = sorted(files) + sorted(name for name in dirs if _linked(here / name))
            # review I2: the bytecode Python writes when the code runs is not the folder's content (a .pyc elsewhere,
            # which Python can load in place of a source, still is)
            dirs[:] = sorted(name for name in dirs if not _linked(here / name) and name != "__pycache__")
            for name in names:
                path = here / name
                budget["files"] += 1
                if budget["files"] > COPY_MAX_FILES:
                    raise _OverCap(HASH_FILES.format(n=COPY_MAX_FILES))
                counted(0)
                key = path.relative_to(top).as_posix()
                if _linked(path):
                    entries.append((key, "link", os.readlink(path)))
                elif not stat.S_ISREG(os.lstat(path).st_mode):
                    entries.append((key, "other", ""))
                else:
                    digest = hashlib.sha256()
                    with open(path, "rb") as handle:
                        for block in iter(lambda: handle.read(1 << 20), b""):
                            counted(len(block))
                            digest.update(block)
                    entries.append((key, "file", digest.hexdigest()))
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except _OverCap as cap:
        return NOT_HASHED + str(cap)
    except OSError as exc:
        where = Path(exc.filename).relative_to(repo).as_posix() if exc.filename and Path(exc.filename).is_relative_to(
            repo) else rel
        return NOT_HASHED + HASH_UNREADABLE.format(path=where)
    return hashlib.sha256(json.dumps(sorted(entries)).encode("utf-8")).hexdigest()


def submodules_not_hashed(found: dict[str, str]) -> dict[str, str]:
    """The submodule folders a ``snapshot`` did not hash (``_submodule``), path to why: a change there is not seen."""
    return {key[len(_SUBMODULE):]: value[len(NOT_HASHED):] for key, value in found.items()
            if key.startswith(_SUBMODULE) and value.startswith(NOT_HASHED)}


def _moved(before: dict[str, str], after: dict[str, str]) -> list[str]:
    # PENDING copy: the name for _MARKS is a proposal (the fix pass after the review of Tasks 4-5).
    names = {_HEAD: "the commit checked out (HEAD)", _INDEX: "the staged files (the index)",
             _MARKS: "git's assume-unchanged and skip-worktree marks"}

    def name(key: str) -> str:
        if key.startswith(_SUBMODULE):
            return SAID["submodule_name"].format(path=key[len(_SUBMODULE):])
        return names.get(key, key)

    def differs(key: str) -> bool:
        # review I2: a folder G6 did not hash on either side is not hashed (submodules_not_hashed), never changed
        if key.startswith(_SUBMODULE) and any(str(side.get(key, "")).startswith(NOT_HASHED) for side in (before, after)):
            return False
        return before.get(key) != after.get(key)

    return [name(key) for key in sorted(set(before) | set(after)) if differs(key)]


def child_env(scratch: Path, *, network: bool) -> dict[str, str]:
    """The tests' whole environment: a few system variables, HOME and the temp folders in ``scratch``, the paid-model
    block, and (network refused) HTTP proxies that point nowhere except for this machine. Nothing else of the caller's."""
    env = {key: os.environ[key] for key in _KEEP if os.environ.get(key)}
    home = str(scratch)
    env.update(HOME=home, USERPROFILE=home, TMP=home, TEMP=home, TMPDIR=home, **_MODEL_BLOCK)
    if not network:
        env.update(dict.fromkeys(_PROXIES, _NOWHERE))
        env.update(NO_PROXY="localhost,127.0.0.1,::1", no_proxy="localhost,127.0.0.1,::1")
    return env


#: The setting, in G6's own environment only, that names the Python for a repository's tests: a full path the person
#: chose (a Poetry or conda environment, or a system Python holding the dependencies). No request names it: an agent
#: cannot point G6 at an interpreter.
REPO_PYTHON = "G6_REPO_PYTHON"
REPO_PYTHON_INVALID = "G6_E_REPO_PYTHON_INVALID"
#: How a Python's file is named: python, python3, python3.11, python.exe, pypy3.
_PYTHON_NAME = re.compile(r"(?:python|pypy)[0-9.]*(?:\.exe)?", re.IGNORECASE)


def _chosen_python() -> str:
    """The Python ``REPO_PYTHON`` names ("" when it is not set, or set to nothing), or a refusal
    (``G6_E_REPO_PYTHON_INVALID``) when it names none: not a full path, nothing there, not a file this user may run, or
    a file not named as a Python is. Never a quiet fall back to another interpreter."""
    value = (os.environ.get(REPO_PYTHON) or "").strip()
    if not value:
        return ""
    path = Path(value)
    problem = ("is not a full path" if _control(value) or not path.is_absolute() else
               "names no file" if not path.is_file() else
               "names a file this user cannot run" if not os.access(path, os.X_OK) else
               "names a file that is not named as a Python (python, python3, python3.11, ...)"
               if not _PYTHON_NAME.fullmatch(path.name) else "")
    if problem:
        raise RepoRefused(REPO_PYTHON_INVALID, SAID["repo_python"].format(setting=REPO_PYTHON, problem=problem,
                                                                          value=value))
    return value


#: The setting, in G6's own environment only, that names the variables a repository's tests need (a JSON object of
#: names to text: qutebrowser's ``QUTE_QT_WRAPPER``, say). G6's bootstrap sets them in the test process before pytest
#: and the repository are imported; the operating system's boundary passes nothing else of G6's environment on.
REPO_ENV = "G6_REPO_ENV"
REPO_ENV_INVALID = "G6_E_REPO_ENV_INVALID"
#: Names the setting may not give: they could change the interpreter, the runner, the libraries loaded, G6's own
#: settings, or where the run may write; and every name G6's own environment for the tests sets (``child_env``: the
#: paid-model block, whose dummy key no real one may replace, and the proxies that point nowhere; review M2).
_RUNNER_NAMES = re.compile(r"(?:PYTHON|LD_|DYLD_|PYTEST_|G6_|COV_CORE_|TMP).*|PATH|HOME|USERPROFILE|TEMP",
                           re.IGNORECASE)
_G6_SETS = frozenset(name.upper() for name in (*_MODEL_BLOCK, *_PROXIES, "NO_PROXY"))


def _runner_name(name: str) -> bool:
    """Whether the setting may not give ``name`` (``_RUNNER_NAMES``, ``_G6_SETS``, or not a name at all)."""
    return not name or "=" in name or "\0" in name or bool(_RUNNER_NAMES.fullmatch(name)) or name.upper() in _G6_SETS
#: A writable folder Hypothesis keeps its database in (it would write ``.hypothesis`` beside the tests, in a
#: repository a confined run keeps read only): the run's own, unless the setting names one.
HYPOTHESIS_STORAGE = "HYPOTHESIS_STORAGE_DIRECTORY"


def _repo_env() -> dict[str, str]:
    """The variables ``REPO_ENV`` names ({} when it is not set, or set to nothing), or a refusal
    (``G6_E_REPO_ENV_INVALID``) that names no value: not a JSON object of names to text, a value holding a NUL, or a
    name that is empty, holds ``=`` or a NUL, or could change the runner (``_RUNNER_NAMES``)."""
    raw = os.environ.get(REPO_ENV) or ""
    if not raw.strip():
        return {}
    try:
        found = json.loads(raw)
    except ValueError:
        found = None
    if not isinstance(found, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                              for key, value in found.items()):
        problem = "is not a JSON object of variable names to text"
    elif any("\0" in value for value in found.values()):
        problem = "gives a value that holds a NUL character"
    else:
        refused = sorted(repr(name) for name in found if _runner_name(name))
        problem = "may not set " + ", ".join(refused) if refused else ""
    if problem:
        raise RepoRefused(REPO_ENV_INVALID, SAID["repo_env"].format(setting=REPO_ENV, problem=problem))
    return found


def _digests(values: dict[str, str]) -> dict[str, str]:
    """Each variable's name with the SHA-256 of its value, never the value (it may be a secret)."""
    return {name: hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
            for name, value in sorted(values.items())}


def _environment_of(python: str) -> tuple[str, ...]:
    """The virtual environment ``python`` belongs to (the folder holding its ``pyvenv.cfg``, beside it or above its
    ``bin``), which a confined run reads too: ``confine.interpreter_paths`` takes an environment made from the Python
    running G6, whose ``python`` links to that same file, for G6's own interpreter and leaves its folder out."""
    folder = Path(python).parent   # never resolved: an environment's python is a link to its base interpreter
    for cfg in (folder / "pyvenv.cfg", folder.parent / "pyvenv.cfg"):
        if cfg.is_file():
            return (os.path.realpath(cfg.parent),)
    return ()


def interpreter(repo: Path) -> str:
    """The Python that runs the repository's tests: the one ``G6_REPO_PYTHON`` names in G6's own environment
    (``_chosen_python``: a refusal when it names none), else the repository's own virtual environment (``.venv`` or
    ``venv`` at its top), else the one running G6 (``python`` on PATH in a frozen build, which has no interpreter of
    its own). Either of the first two only when each folder a confined run of it reads is one G6 opens
    (``_opened``); else a refusal before anything is copied or run."""
    chosen = _chosen_python()
    if chosen:   # the folder the person named, and its environment; never what its pyvenv.cfg names
        folder = Path(chosen).parent
        named = folder.parent if os.name == "posix" and folder.name == "bin" else folder
        return _opened(repo, chosen, f"the Python {REPO_PYTHON} names", own=(str(named), *_environment_of(chosen)))
    for folder in (".venv", "venv"):
        for exe in (repo / folder / "Scripts" / "python.exe", repo / folder / "bin" / "python"):
            if exe.is_file():
                return _opened(repo, str(exe), f"the repository's own {folder}")
    if getattr(sys, "frozen", False) or not sys.executable:
        return "python"
    return sys.executable


def _linked_to_g6(python: str) -> str:
    """The file ``python`` and the Python running G6 both are, through links, when it is that file (``confine._key``)
    at another path, in an environment of its own other than G6's (the folder holding its ``pyvenv.cfg`` is not
    ``sys.prefix``, or G6 runs from a base installation); else "". ``confine.interpreter_paths`` takes such a Python
    for G6's own and binds G6's folders in place of its environment's, so a confined run of it cannot start: refused
    before anything runs (L1, the owner's decision; confine is fixed in the post-launch issuance window). G6 installed
    in that environment itself (the same path), and a link to the same file with no environment, are not refused."""
    if not sys.executable:
        return ""
    exe = find_program(python) or python
    if confine._key(exe) != confine._key(sys.executable):
        return ""
    if os.path.normcase(os.path.abspath(python)) == os.path.normcase(os.path.abspath(sys.executable)):
        return ""
    environment = _environment_of(python)
    if not environment:
        return ""
    own = os.path.normcase(os.path.realpath(sys.prefix))
    if os.path.normcase(environment[0]) != own or sys.prefix == sys.base_prefix:
        return os.path.realpath(exe)
    return ""


#: A folder that holds a Python's standard library: a base installation (CPython's, on POSIX or Windows, or a source
#: build's; PyPy's).
_STANDARD_LIBRARY = ("Lib/os.py", "lib*/python3*/os.py", "lib*/pypy3*/os.py", "lib-python/3*/os.py")


def _opened(repo: Path, python: str, what: str, own: tuple[str, ...] = ()) -> str:
    """``python``, when each folder its confined run reads besides the repository (``confine.interpreter_paths``: the
    base installation the environment's ``pyvenv.cfg`` names, and the environment's folder, its links followed; and
    ``_environment_of``) is inside the repository, one G6's own interpreter reads, one of ``own`` (the folder the
    person's setting names, and its environment), or holds a Python's standard library. Else a refusal
    (``G6_E_REPO_PYTHON_INVALID``),
    with nothing run: the boundary binds those folders, read only, so a ``home`` the repository (or the patch) writes
    in a ``pyvenv.cfg`` would open any folder of the person's to the tests (reviewer B's critical item)."""
    linked = _linked_to_g6(python)
    if linked:
        raise RepoRefused(REPO_PYTHON_INVALID, SAID["repo_linked"].format(python=what, file=linked,
                                                                          setting=REPO_PYTHON))

    def key(path: str) -> str:
        return os.path.normcase(os.path.realpath(path))

    top = key(str(repo))
    opened = {key(path) for path in (*(confine.interpreter_paths(sys.executable) if sys.executable else ()), *own)}
    for folder in dict.fromkeys([*confine.interpreter_paths(python), *_environment_of(python)]):
        here = key(folder)
        if (here == top or here.startswith(top.rstrip(os.sep) + os.sep) or here in opened
                or any(any(Path(here).glob(pattern)) for pattern in _STANDARD_LIBRARY)):
            continue
        raise RepoRefused(REPO_PYTHON_INVALID, SAID["repo_reads"].format(python=what, folder=folder,
                                                                         setting=REPO_PYTHON))
    return python


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: The exception a report's text ends on: "E   ModuleNotFoundError: No module named 'x'" (or a message that starts so).
_RAISED = re.compile(r"^(?:E\s+)?((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception)): (.*)$", re.MULTILINE)
#: The module an import error names: missing, a name it lacks, or a binary part that would not load.
_MODULE = re.compile(r"No module named '([\w.]+)'|cannot import name .*? from (?:partially initialized module )?"
                     r"'([\w.]+)'|while importing ([\w.]+)")
#: A source location in a traceback: native style (and a syntax error's), or pytest's ``path:line: ...``.
_PLACE = re.compile(r'File "([^"\n]+\.py)", line \d+|^(\S[^\n]*?\.py):\d+: ', re.MULTILINE)
#: Folders inside a repository that hold other people's code: an installed package is not the repository's own.
_THEIRS = frozenset({"site-packages", "dist-packages"})
_ENVIRONMENTS = frozenset({".venv", "venv", ".tox", ".nox"})


def _module_here(name: str, roots: tuple[Path, ...]) -> bool:
    """Whether module ``name``'s top package is the repository's own: a ``.py`` file or a package folder in one of the
    folders the run puts on the import path (the top, ``src``, the roots named)."""
    top = name.partition(".")[0]
    for root in roots:
        folder = root / top
        if (root / f"{top}.py").is_file() or (folder / "__init__.py").is_file() or (
                folder.is_dir() and any(folder.glob("*.py"))):
            return True
    return False


def _file_here(name: str, repo: Path) -> bool:
    """Whether a file a traceback names is the repository's own: inside it, and not in an installed package or a
    virtual environment it holds."""
    path = Path(name)
    try:
        rel = (path if path.is_absolute() else repo / path).resolve().relative_to(repo)
    except (OSError, ValueError, RuntimeError):
        return False
    return not (_THEIRS & set(rel.parts) or rel.parts[:1] and rel.parts[0] in _ENVIRONMENTS)


def _blame(problem: ElementTree.Element, repo: Path, roots: tuple[Path, ...], *, collection: bool) -> str:
    """Where one failure or error in the report comes from, read from its own text: "outside" (an import of a module
    that is not the repository's: a dependency this environment lacks; or, for a collection error, a failing file that
    is not the repository's), "inside" (anything else in a test that ran; for a collection error, an import of the
    repository's own module, a syntax error in its file, or its file raising), or "" (a collection error whose module
    or file cannot be read from the text). A module the change deleted or renamed, still imported, reads "outside"."""
    text = _ANSI.sub("", f"{problem.get('message') or ''}\n{problem.text or ''}")
    raised = _RAISED.findall(text)
    kind = raised[-1][0].rpartition(".")[2] if raised else ""
    if kind in ("ModuleNotFoundError", "ImportError"):
        named = _MODULE.search(raised[-1][1])
        module = next((group for group in named.groups() if group), "") if named else ""
        if module:
            return "inside" if _module_here(module, roots) else "outside"
    if not collection:
        return "inside"
    places = list(_PLACE.finditer(text))
    if not places:
        return ""
    return "inside" if _file_here(places[-1].group(1) or places[-1].group(2), repo) else "outside"


def _counts(report: Path, repo: Path, roots: tuple[Path, ...]) -> dict[str, int] | None:
    """pytest's own tallies from its JUnit report, or None when it wrote none that can be read. One outcome per test
    (its ``<testcase>`` elements, by class and name: pytest writes a second one for a test that failed and then
    errored in its teardown): a failure, else an error, else a skip, else a pass (review I1; a test interrupted before
    its result also has one, with nothing in it: ``run`` takes those off the passes). Also: ``collection``, the
    collection errors, and ``placed``, those whose module or file was read; ``outside``, the failed tests whose every
    failure and error comes from outside the repository (``_blame``)."""
    try:
        root = ElementTree.parse(report).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    tests: dict[tuple[str, str], list[ElementTree.Element]] = {}
    for case in root.iter("testcase"):
        tests.setdefault((case.get("classname") or "", case.get("name") or ""), []).extend(case)
    total = dict.fromkeys(("tests", "passed", "failures", "errors", "skipped", "collection", "placed", "outside"), 0)
    for children in tests.values():
        failures = [child for child in children if child.tag == "failure"]
        errors = [child for child in children if child.tag == "error"]
        skipped = [child for child in children if child.tag == "skipped"]
        collection = (any(child.get("message") == "collection failure" for child in errors)
                      or any(child.get("message") == "collection skipped" for child in skipped))
        total["tests"] += 1
        if not failures and not errors:
            total["skipped" if skipped else "passed"] += 1
            continue
        total["failures" if failures else "errors"] += 1
        blames = {_blame(problem, repo, roots, collection=collection) for problem in failures + errors}
        total["collection"] += collection
        total["placed"] += collection and "" not in blames
        total["outside"] += blames == {"outside"}
    return total


def _judge(exit_code: int | None, counts: dict[str, int] | None, output: str, moved: list[str]) -> str:
    """One verdict for one run. Files that changed under it decide first (nothing it reported can be trusted). Then
    the archive coder's verdict rule, restated here (the library has no coder): the time bound, pytest's exit code, and
    its own tallies, which must agree with the exit code. Refined after the review of
    Tasks 4-5:

    * failures and errors that all come from outside the repository (a dependency this environment lacks) are "could
      not be collected", even when a test ran into one (I4);
    * exit 2-4 (interrupted, internal error, usage error) is "failed" when the tallies hold a failure, or a collection
      error G6 places in the repository's own code (a syntax error, a broken import of its own module); a collection
      error G6 cannot place stays "could not be collected" (I2);
    * a pass needs every test pytest meant to run (collected, less deselected: ``expected``) to have run to its end
      (``finished``); fewer, or a count that cannot be read, is "incomplete" (I3). ``run`` always sets ``expected``;
    * the owner's ruling (2026-10-29), asymmetric on purpose: an exit other than 0 and 2-5 after a session whose
      tallies hold passes, no failure or error, every test it meant to run finished, and whose own per-test record
      (``_Counted``: ``failed_ids``, ``setup_errors``) holds no failure either (the launch review's M1), is
      ``run_inconsistent``
      (not a pass, and no sign of tampering, since it cannot get a patch accepted: a GUI suite's X connection that
      fails at exit). Exit 0 with a failure, an error or fewer tests finished stays untrusted, as a tampered runner
      would show it."""
    if moved:
        return CHANGED
    if exit_code is None:
        return "timed_out"
    if counts is None and "No module named 'pytest'" in output:
        return "pytest_not_found"
    if exit_code == 5:
        return "no_tests_collected"
    if counts is None:
        return "collection_error" if exit_code in (2, 3, 4) else "inconsistent_report"
    bad, outside = counts["failures"] + counts["errors"], counts.get("outside", 0)
    if bad and outside == bad:
        return "collection_error"
    if exit_code == 0:
        if bad or counts["passed"] <= 0:
            return "inconsistent_report"
        if "expected" in counts and (counts["expected"] is None or counts["expected"] != counts.get("finished")):
            return "incomplete"
        return PASSED
    if exit_code == 1 and bad:
        return FAILED
    if exit_code in (2, 3, 4):
        # Errors whose culprit is not in the tally are taken as collection errors G6 cannot place (exit 2-4's usual).
        unplaced = counts.get("collection", counts["errors"]) - counts.get("placed", 0)
        return FAILED if bad - outside - unplaced > 0 else "collection_error"
    complete = "expected" not in counts or (counts["expected"] is not None
                                            and counts["expected"] == counts.get("finished"))
    # the launch review's M1: and G6's own per-test record (``_Counted``) holds no failure and no setup error either
    if (not bad and counts["passed"] > 0 and complete and not counts.get("failed_ids")
            and not counts.get("setup_errors")):
        return RUN_INCONSISTENT
    return "inconsistent_report"


def _bounded(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout_s: int, log: Path,
             confined: confine.Plan | None = None) -> int | None:
    """Own the test tree while streaming merged output directly to its log.

    A local timeout returns None. Operator stop, an enclosing allowance or
    uncertain cleanup propagate as control failures, never test evidence.
    """
    try:
        with open(log, "wb") as sink, component_process.window(timeout_s):
            return component_process.run_process(cmd, cwd=cwd, env=env, stdout=sink, stderr=subprocess.STDOUT,
                                                 confined=confined)
    except component_process.ExecutionInterrupted as exc:
        if exc.stopped:
            raise
        component_process.checkpoint()
        return None


def _output(log: Path) -> str:
    """What the run printed: all of it when it is short, else its first ``_RETURN_HEAD`` and last ``_RETURN_TAIL``
    bytes with the size of the cut, read from the file without loading the rest."""
    try:
        with open(log, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(0)
            if size <= _RETURN_HEAD + _RETURN_TAIL:
                return handle.read().decode("utf-8", "replace")
            head = handle.read(_RETURN_HEAD)
            handle.seek(size - _RETURN_TAIL)
            tail = handle.read(_RETURN_TAIL)
    except OSError:
        return ""
    cut = size - _RETURN_HEAD - _RETURN_TAIL
    return (f"{head.decode('utf-8', 'ignore')}\n... [{cut} bytes of output omitted] ...\n"
            f"{tail.decode('utf-8', 'ignore')}")


#: The files pytest reads its configuration from, and the section that makes each one count ("": any content).
_PYTEST_CONFIG = (("pytest.toml", ""), (".pytest.toml", ""), ("pytest.ini", ""), (".pytest.ini", ""),
                  ("pyproject.toml", r"^\s*\[\s*tool\.pytest(?:\.ini_options)?\s*\]"),
                  ("tox.ini", r"^\s*\[pytest\]"), ("setup.cfg", r"^\s*\[tool:pytest\]"))


def _config_file(folder: Path) -> Path | None:
    """The file in ``folder`` pytest would read its configuration from (the first of ``_PYTEST_CONFIG`` that counts)."""
    for name, section in _PYTEST_CONFIG:
        path = folder / name
        try:
            if path.is_file() and (not section or re.search(section, path.read_text(encoding="utf-8", errors="replace"),
                                                            re.MULTILINE)):
                return path
        except OSError:
            continue
    return None


def _configures_pytest(folder: Path) -> bool:
    return _config_file(folder) is not None


def _config_args(repo: Path, ids: list[str], own: Path) -> list[str]:
    """Keep pytest's configuration inside the repository (review M3). pytest looks for its config from the folder the
    tests named have in common up to the drive's root, and reads the conftest.py files up to that config's folder.
    When a folder from there up to the repository's top has one, that is the repository's own and pytest finds it
    first (nothing is added: the run reads what the repository's own pytest reads). When none has, pytest would read
    a parent folder's: the run names the repository as pytest's root and conftest limit, with an empty config."""
    dirs = [path if path.is_dir() else path.parent for path in (repo / test.partition("::")[0] for test in ids)]
    folder = Path(os.path.commonpath([str(path) for path in dirs]))
    while True:
        if _configures_pytest(folder):
            return []
        if folder == repo or folder.parent == folder:
            break
        folder = folder.parent
    empty = own / "pytest.ini"
    empty.write_text("[pytest]\n", encoding="utf-8")
    return [f"--confcutdir={repo}", f"--rootdir={repo}", "-c", str(empty)]


def _progress(counts: dict[str, int], counted: Path) -> dict[str, int]:
    """``counts`` with ``_BOOT``'s plugin's own: ``expected``, the tests pytest meant to run (collected, less
    deselected), and ``finished``, those that ran to their end; ``expected`` None when the plugin wrote nothing that
    can be read (the session never ended). A test that started and never finished (interrupted part-way) has an empty
    testcase in the report, which reads as a pass: it is taken off the passes."""
    try:
        found = json.loads(counted.read_text(encoding="utf-8"))
        expected, started = int(found["collected"]) - int(found["deselected"]), int(found["started"])
        finished = int(found["finished"])
    except (OSError, ValueError, KeyError, TypeError):
        return {**counts, "expected": None}
    return {**counts, "expected": expected, "finished": finished,
            "deselected": int(found["deselected"]),
            "test_outcomes": found.get("test_outcomes"), "collected_ids": found.get("collected_ids", []),
            "omitted_ids": found.get("omitted_ids", []),
            "failed_ids": found.get("failed_ids", []), "setup_errors": found.get("setup_errors", {}),
            "rootdir": found.get("rootdir") or "", "failure_messages": found.get("messages") or {},
            "os_errors": found.get("os_errors") or [],
            "passed": max(0, counts.get("passed", 0) - max(0, started - finished))}


def _loaded(path: Path) -> tuple[dict[str, str], bool]:
    """The bootstrap's list of the modules a run loaded from outside the tree it tested (name to file), and whether it
    is whole: ({}, False) when it is missing, larger than ``LOADED_BYTES`` or cannot be read."""
    try:
        if path.stat().st_size > LOADED_BYTES:
            return {}, False
        found = json.loads(path.read_text(encoding="utf-8"))
        return {str(name): str(file) for name, file in found["modules"].items()}, found["complete"] is True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, False


def shadowed(sources: tuple[str, ...] | list[str], ran: dict) -> dict[str, dict[str, str]] | None:
    """The source files (``sources``: a change's, or every Python file of the tree) whose module ``ran`` loaded from
    outside the tree it tested under the same name, an installed copy in place of the tree's own file: each path to the
    module's name and the file it came from. {} when none was, or when ``ran`` records nothing of what it loaded (a
    recorded run); None when it cannot say (its list was cut at ``LOADED_LIMIT``, or never written). What a run lists
    is the modules its pytest process had loaded when the session ended: a subprocess's, or a forked child's, are not
    seen. A module that was never loaded, or a standard-library module named like a source file, is not one."""
    if "loaded_outside" not in ran:
        return {}
    if not ran.get("loaded_complete"):
        return None
    ends: dict[str, list[str]] = {}   # each source by every tail of its path, for one look-up per module
    for rel in sources:
        parts = rel.split("/")
        for start in range(len(parts)):
            ends.setdefault("/".join(parts[start:]), []).append(rel)
    found: dict[str, dict[str, str]] = {}
    for name, file in sorted((ran.get("loaded_outside") or {}).items()):
        if name.startswith("__"):
            continue
        tail = name.replace(".", "/") + ("/__init__.py" if os.path.basename(file).startswith("__init__.") else ".py")
        for rel in ends.get(tail, ()):
            found.setdefault(rel, {"module": name, "file": file})
    return dict(sorted(found.items()))


def _root(repo: Path, rootdir: str) -> str:
    """pytest's root folder, which it names each test relative to, as a path inside ``repo`` ("" for its top, and when
    the run did not say or named a folder outside it)."""
    try:
        rel = Path(rootdir).resolve().relative_to(repo.resolve()).as_posix() if rootdir else ""
    except (OSError, ValueError):
        return ""
    return "" if rel == "." else rel


def _said(verdict: str, *, seconds: int, python: str) -> str:
    if verdict in RAN:
        return ""
    if verdict == CHANGED:
        return SAID["changed"]
    if verdict == "timed_out":
        return SAID["timed_out"].format(seconds=seconds)
    if verdict == "pytest_not_found":
        return SAID["pytest_not_found"].format(python=python)
    return SAID[verdict]


def run(repo: str | Path, tests: list[str], *, roots: tuple[str, ...] = (), timeout_s: int = DEFAULT_TIMEOUT_S,
        network: bool = False, mutant: tuple[str, str] | None = None, receipt: bool = False,
        require_isolation: bool = False) -> dict:
    """Run ``tests`` (files, folders or ``file::test`` ids, relative to ``repo``) against the working tree, in place,
    and say what pytest found. A run under which a tracked file, HEAD or the index changed is ``changed_tracked_files``
    with the paths, whatever the tests said, and so is one after which git cannot read the repository (with that
    said). ``mutant`` (path, source): that module is imported from the source given instead of its file, which is
    never written (the changed-lines mutation check). ``interpreter_source`` says whose Python ran the tests
    ("setting": the one ``G6_REPO_PYTHON`` names; "repository": its own ``.venv`` or ``venv``; "g6": the one running
    G6; ``interpreter``), ``python`` its path, and
    ``source_before`` the digest of the ``snapshot`` the run began on (the digest the patch review calls its
    ``artifact_digest``), and ``rootdir`` the folder pytest named the tests relative to ("" for the repository's top;
    a pytest configuration below the top moves it). ``failure_messages`` holds each failure's message in full, by node
    id; ``import_path`` the folders the run put first on the import path, relative to the repository (the import roots
    given, then "." and, when it is a folder, "src"); ``own_config`` whether G6 named its own empty pytest configuration, rooted at the repository's
    top, because none applied (``_config_args``). Raises ``RepoRefused`` before anything runs when the request cannot
    run.

    ``receipt=True``: a run that started confined (its ``isolation`` names a backend), whose repository git could
    read after it, and beside which ``ignored_inputs`` found no git-ignored file the run could load, is written to
    G6's test-receipt store (``g6.workforce.receipt_store``), and ``receipt`` is the receipt's id; every other run, an
    unconfined one included, has ``receipt`` "" (with ``receipt_withheld`` and ``ignored_inputs`` when ignored files
    stopped it, and ``receipt_error`` when the store could not take the receipt). A mutant is not the artifact under review: ``receipt=True`` with ``mutant`` raises
    ``ValueError``. ``require_isolation``: a run no backend would confine is refused before anything starts
    (``RepoRefused``, ``G6_E_ISOLATION_UNAVAILABLE``), whatever the person allowed (patch review's runs; the launch
    review's I1)."""
    if receipt and mutant is not None:
        raise ValueError("a mutant run is not the artifact under review, so it is never given a test receipt")
    repo = checked_repo(repo)
    python = interpreter(repo)   # a setting that names no Python is refused before anything is copied or run
    _repo_env()                  # and so is a G6_REPO_ENV G6 cannot use
    if mutant is not None:
        deadline = time.monotonic() + max(1, min(int(timeout_s), MAX_TIMEOUT_S))
        report: dict = {}
        try:
            with repository_copy(repo, deadline=deadline, report=report) as copy:
                return _run(copy, tests, roots=roots, timeout_s=timeout_s, network=network, mutant=mutant,
                            copied=True, python=python, scratch_dir=copy.parent, deadline=deadline, links=report,
                            require_isolation=require_isolation)
        except CopyNotRun as exc:
            raise RepoRefused("G6_E_TESTS_DID_NOT_RUN", str(exc)) from exc
    return _run(repo, tests, roots=roots, timeout_s=timeout_s, network=network, receipt=receipt, python=python,
                require_isolation=require_isolation)


def _run(repo: Path, tests: list[str], *, roots: tuple[str, ...], timeout_s: int, network: bool,
         mutant: tuple[str, str] | None = None, copied: bool = False, python: str = "",
         scratch_dir: Path | None = None, deadline: float | None = None, receipt: bool = False,
         require_isolation: bool = False, links: dict | None = None) -> dict:
    """The shared execution path; only old-test replay uses a copy without git metadata. ``require_isolation``: a run
    no backend confines is refused (``G6_E_ISOLATION_UNAVAILABLE``) before it starts, even where the person allowed
    unconfined runs (verification search's proposed checks). ``links``: what the copy (``repository_copy``'s
    ``report``) left out or copied in a link's place, added to the result as it is."""
    if not tests:
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["no_tests"])
    ids = [inside(repo, test, "test") for test in tests]
    missing = [test for test in ids if not (repo / test.partition("::")[0]).exists()]
    if missing:
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["missing"].format(value=missing[0]))
    # the import roots first, then the top and src: a package in a root wins over one of the same name at the top
    entries = [*(str(repo / inside(repo, root, "import root")) for root in roots), str(repo),
               *([str(repo / "src")] if (repo / "src").is_dir() else [])]
    bound = max(1, min(int(timeout_s), MAX_TIMEOUT_S))
    python = python or interpreter(repo)
    # a second line after _repo_env's check: no name G6 sets itself, or that could change the runner, is ever taken
    person = {name: value for name, value in _repo_env().items() if not _runner_name(name)}
    set_by_g6 = [] if HYPOTHESIS_STORAGE in person else [HYPOTHESIS_STORAGE]
    seen: list = [{}, False]   # the modules the run loaded from outside the tree, and whether that list is whole
    whose = ("setting" if python == _chosen_python() else
             "repository" if any(Path(python).is_relative_to(repo / folder) for folder in (".venv", "venv")) else "g6")
    untracked = [] if copied else _untracked(repo)
    take_snapshot = (lambda: _copy_snapshot(repo, deadline=deadline)) if copied else (lambda: snapshot(repo, untracked))
    before, started = take_snapshot(), time.monotonic()
    began = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    head = _git(repo, "rev-parse", "--verify", "-q", "HEAD", ok=(0, 1)).decode().strip() if receipt else ""
    command = f"pytest {len(ids)} test id(s) in place"
    isolation = confine.summary([])
    own_config: list[str] = []   # the arguments of the empty configuration G6 names when the repository has none

    def result(verdict: str, counts: dict | None, output: str, moved: list[str]) -> dict:
        tally = counts or {}
        passed = int(tally.get("passed", 0))
        failed = int(tally.get("failures", 0)) + int(tally.get("errors", 0))
        return {"verdict": verdict, "said": _said(verdict, seconds=bound, python=python), "passed": passed,
                "failed": failed, "not_passed": int(tally.get("skipped", 0)), "total": passed + failed,
                "deselected": int(tally.get("deselected", 0)),
                "test_outcomes": tally.get("test_outcomes"), "collected_ids": tally.get("collected_ids", []),
                "omitted_ids": tally.get("omitted_ids", []),
                "changed": list(moved), "output": output, "seconds": round(time.monotonic() - started, 3),
                "python": python, "interpreter_source": whose, "command": command, "isolation": isolation,
                "failed_ids": list(tally.get("failed_ids", [])), "setup_errors": tally.get("setup_errors", {}),
                "degraded": verdict not in RAN or ("expected" in tally and
                    (tally["expected"] is None or tally["expected"] != tally.get("finished"))), "receipt": "",
                "source_before": ledger.digest(before), "rootdir": _root(repo, tally.get("rootdir") or ""),
                "failure_messages": tally.get("failure_messages") or {},
                "os_errors": list(tally.get("os_errors") or []),
                "import_path": [Path(entry).relative_to(repo).as_posix() for entry in entries],
                "own_config": bool(own_config),
                "repo_env": _digests(person), "env_set_by_g6": list(set_by_g6),
                "loaded_outside": dict(seen[0]), "loaded_complete": seen[1], **(links or {})}

    # Two folders: the tests' HOME and temp folders, and one they are not pointed at, for the report, the count of
    # tests collected, the output, the mutant's source and an empty config.
    # Both in one folder G6 makes, so a confined run can see where its report goes (pytest creates the report's folder,
    # and checks each folder above it, before writing).
    scratch_dir = outside_temp(repo, scratch_dir)
    with confine.run_folder(prefix="g6_repo_run_", dir=scratch_dir, ignore_cleanup_errors=True) as top:
        work, own = Path(top) / "home", Path(top) / "report"
        work.mkdir()
        own.mkdir()
        # A repository in place is read only: the run writes only its two folders (HOME and the temp folders; the
        # report), and reads the repository, the interpreter's folders and ``top``, which holds the two (pytest checks
        # the report's folder before writing). A copy G6 made is writable.
        report, counted, log, target, source = own / "report.xml", own / "counted.json", own / "output.log", "", ""
        if mutant is not None:
            target, source = str(repo / inside(repo, mutant[0], "module")), str(own / "mutant.py")
            Path(source).write_text(mutant[1], encoding="utf-8")
        # the variables the bootstrap sets, in a file only this user may read: values never go on a command line
        store = work / "hypothesis"
        store.mkdir()
        variables = own / "env.json"
        with os.fdopen(os.open(variables, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as sink:
            json.dump({**dict.fromkeys(set_by_g6, str(store)), **person}, sink)
        # the run's own log file, empty (truth.no_null_device): with none named, pytest's is the null device, which an
        # AppContainer may be refused; one the repository names could not be written into a repository in place
        own_config[:] = _config_args(repo, ids, own)
        cmd = [python, "-I", "-B", "-X", "utf8", "-c", _BOOT, json.dumps(entries), "1" if network else "0",
               target, source, str(counted), str(variables), str(own / "loaded.json"), str(LOADED_LIMIT), *ids, "-q",
               "-p", "no:cacheprovider",
               f"--junitxml={report}", *own_config, f"--log-file={own / 'pytest.log'}",
               "--log-file-level=100"]
        mine = confine.owned(repo)
        reads = confine.interpreter_paths(python)
        environment = tuple(path for path in _environment_of(python) if not any(
            Path(path).is_relative_to(os.path.realpath(folder)) for folder in (*reads, str(repo))))
        confined = confine.plan(confine.Confinement(
            read_write=(str(work), str(own), *([str(repo)] if mine else [])),
            read_only=(*reads, *environment, *([] if mine else [str(repo)]), top), network=network),
            python)
        isolation = confined.record()
        said = confine.refused(confined)
        if not said and require_isolation and not isolation.get("backend"):
            said = confine.refusal(confined)
        if said:
            raise RepoRefused(ISOLATION_UNAVAILABLE, said)
        try:
            if deadline is not None:
                bound = min(bound, _copy_remaining(deadline))
            code = _bounded(cmd, cwd=repo, env=child_env(work, network=network), timeout_s=bound, log=log,
                            confined=confined)
        except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
            raise
        except OSError as exc:
            if isinstance(exc, confine.NotStarted):
                isolation = confine.not_started(isolation, exc)
            found = result("could_not_start", None, "", [])
            return {**found, "said": SAID["could_not_start"].format(error=type(exc).__name__)}
        finally:
            left = confined.release()   # the run's container profile, after the run
            if left:
                isolation = {**isolation, "cleanup": left}
        output, counts = _output(log), _counts(report, repo, tuple(Path(entry) for entry in entries))
        progress = _progress(counts or {}, counted)
        seen[:] = _loaded(own / "loaded.json")
        if counts is not None:
            counts = progress
    try:
        after = take_snapshot()
    except CopyNotRun as exc:
        return {**result("timed_out", progress, output, []), "said": str(exc)}
    except RepoRefused:  # git cannot read the repository after the run (review I5): nothing the tests said is believed
        moved = [SAID["git_unreadable_name"]]
        return {**result(CHANGED, counts, output, moved), "said": SAID["git_unreadable"]}
    moved = _moved(before, after)
    component_process.checkpoint()
    verdict = _judge(code, counts, output, moved)
    found = {**result(verdict, progress, output, moved),
             "completed": code in (0, 5) and progress.get("expected") is not None
             and progress["expected"] == progress.get("finished")}
    if verdict == FAILED and isolation.get("backend") and "PermissionError" in output:
        found["said"] = SAID["confined_write"]   # the operating system refused a test something an owner's run allows
    unhashed = {**submodules_not_hashed(before), **submodules_not_hashed(after)}
    if unhashed:   # a change in these folders would not be seen
        found["submodules_not_hashed"] = unhashed
    if receipt and isolation.get("backend") and unhashed:
        found["receipt_withheld"] = SAID["submodule_withheld"].format(paths=", ".join(sorted(unhashed)))
        return found
    if receipt and isolation.get("backend"):   # only a run G6 confined and watched start is given a receipt
        try:
            scan = ignored_inputs(repo)
        except RepoRefused:   # git could not list them: nothing says the digest covers what the run loaded
            scan = Ignored((), {}, False)
        found["ignored_inputs"] = dict(scan.other)
        if scan.loadable or not scan.complete:   # review I2: the digest leaves ignored files out, and pytest loads them
            found["ignored_loadable"] = list(scan.loadable)
            found["receipt_withheld"] = (
                "no test receipt: git ignores files that could change this run, " + ", ".join(scan.loadable[:10])
                if scan.loadable else
                f"no test receipt: the scan for git-ignored files stopped at its limit of {IGNORED_SCAN_LIMIT} entries")
            return found
        # review M1: receipts are reusable evidence, so none vouches for a run that loaded the tree's own modules from
        # elsewhere (an installed copy), or that cannot say what it loaded
        elsewhere = shadowed([rel for rel in _paths(_git(repo, "ls-files", "-co", "--exclude-standard", "-z"))
                              if rel.endswith(".py")], found)
        if elsewhere is None or elsewhere:
            found["receipt_withheld"] = (SAID["loaded_unknown_withheld"] if elsewhere is None else
                                         SAID["shadowed_withheld"].format(modules=", ".join(
                                             f"{where['module']} from {where['file']}"
                                             for where in list(elsewhere.values())[:5])))
            return found
        try:
            found["receipt"] = receipt_store._record(receipt_store._body(
                started_at=began, finished_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                before=before, after=after, base_commit=head, python=python, requested=ids, found=found,
                ignored=scan.other))
        except (OSError, ValueError) as exc:
            found["receipt_error"] = (f"the test receipt could not be recorded ({type(exc).__name__}); if the store is "
                                      f"unreadable, move it aside ({receipt_store.RECOVERY})")
    return found


#: Git-ignored files that can change a run (review I2): runner configuration, start-up and path files.
_LOADABLE = frozenset({"conftest.py", "pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "setup.cfg",
                       "tox.ini", "pyproject.toml", "sitecustomize.py", "usercustomize.py"})
#: Folders the ignored-file scan does not enter: environments, caches and build output; and any folder holding a
#: ``pyvenv.cfg``, a virtual environment whatever its name (review I9).
_UNSCANNED = frozenset({".git", ".venv", "venv", "env", ".tox", ".nox", ".direnv", ".eggs", "__pypackages__", ".conda",
                        "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"})
#: An installed package's metadata folder: pytest loads, at start-up, every plugin one declares (an entry point in its
#: ``pytest11`` group) when the folder's parent is on the import path (review C3).
_METADATA = (".egg-info", ".dist-info")
_PLUGIN_GROUP = re.compile(r"^\s*\[\s*pytest11\s*\]\s*$", re.MULTILINE)
#: Compiled extension modules: Python imports one in place of a ``.py`` file of the same name (review C3).
_EXTENSIONS = tuple(suffix.lower() for suffix in importlib.machinery.EXTENSION_SUFFIXES)
#: The most entries the ignored-file scan reads; past it, the scan is incomplete and says so.
IGNORED_SCAN_LIMIT = 20_000


def _unscanned(repo: Path, rel: str) -> bool:
    """Whether ``rel`` is, or is inside, a folder the scan does not enter (``_UNSCANNED``, or a virtual environment)."""
    parts = PurePosixPath(rel).parts
    return any(part in _UNSCANNED for part in parts) or any(
        (repo.joinpath(*parts[:end]) / "pyvenv.cfg").is_file() for end in range(1, len(parts) + 1))


def _linked(path: Path) -> bool:
    """A symbolic link or, on Windows, a junction: it can put any file or folder under the name it has."""
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _declares_plugin(entry_points: Path) -> bool:
    try:
        return bool(_PLUGIN_GROUP.search(entry_points.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return True   # unread, it may declare one


@dataclass(frozen=True)
class Ignored:
    """What ``ignored_inputs`` found among the files git ignores, which its view (and so the artifact digest) leaves
    out. ``loadable``: those that can change what a run does, so no receipt is written; ``other``: every other ignored
    Python file, path to SHA-256, which a receipt binds instead; ``complete``: the scan finished within
    ``IGNORED_SCAN_LIMIT`` entries (if not, no receipt either); ``named_like_modules``: those of ``loadable`` counted
    only because their module name is a tracked module's, which they could shadow."""

    loadable: tuple[str, ...]
    other: dict[str, str]
    complete: bool
    named_like_modules: tuple[str, ...] = ()


def _module(path: PurePosixPath) -> str:
    """The module name a Python file gives on the import path: its stem, or its package's for ``__init__.py``."""
    return path.parent.name if path.stem == "__init__" else path.stem


def _import_names(rel: str, roots: Iterable[str]) -> set[str]:
    """The dotted names Python files ``rel`` gives with each of ``roots`` (folders, "." the top) on the import path."""
    path, names = PurePosixPath(rel), set()
    for root in roots:
        if root not in (".", "") and not path.is_relative_to(root):
            continue
        parts = list((path if root in (".", "") else path.relative_to(root)).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if parts and all(part.isidentifier() for part in parts):
            names.add(".".join(parts))
    return names


def loadable_changes(change: Change, roots: tuple[str, ...] = ()) -> dict[str, str]:
    """The files the change adds or changes, visible to git, that could change what a test run loads, by
    ``ignored_inputs``' judge (the launch review's I2), each with why: an ``.egg-info`` or ``.dist-info`` folder's
    ``entry_points.txt`` declaring a pytest plugin; a compiled extension module; bytecode (``.pyc``), anywhere; a link
    whose name Python could import; and a new Python file, not a test file, whose dotted name, with any import root a
    run uses (the review's ``roots``, the top, ``src``, and each folder pytest puts first for its tests), is that of a
    module the base commit has, and the patch kept, at another path, which it could shadow. Runner, start-up and test
    files are judged elsewhere (the runner check and the weakening detector)."""
    base = set(_paths(_git(change.repo, "ls-tree", "-r", "-z", "--name-only", change.base_commit)))
    tree = base | set(change.files)
    # pytest's own import mode puts the first folder above a test file with no __init__.py first on the path
    basedirs = set()
    for rel in tree:
        if is_test_related(rel):
            folder = PurePosixPath(rel).parent
            while str(folder) not in (".", "") and f"{folder.as_posix()}/__init__.py" in tree:
                folder = folder.parent
            basedirs.add(folder.as_posix())
    import_roots = (*roots, ".", "src", *sorted(basedirs))
    held: dict[str, set[str]] = {}
    for rel in base:   # the base commit's modules the patch kept (one it deleted or moved can no longer be shadowed)
        if rel.endswith(".py") and os.path.lexists(change.repo / rel):
            for name in _import_names(rel, import_roots):
                held.setdefault(name, set()).add(rel)
    found: dict[str, str] = {}
    for rel in change.files:
        path, full = PurePosixPath(rel), change.repo / rel
        name = path.name.lower()
        if not os.path.lexists(full):
            continue   # deleted
        inside = next((index for index, part in enumerate(path.parts[:-1]) if part.endswith(_METADATA)), None)
        if inside is not None:
            if inside == len(path.parts) - 2 and name == "entry_points.txt" and _declares_plugin(full):
                found[rel] = "it declares a pytest plugin, which pytest loads at start-up"
        elif _linked(full):
            if not path.suffix or name.endswith((".py", ".pyc", ".pth", *_EXTENSIONS)):
                found[rel] = "a link, which can put any code under a name Python imports"
        elif name.endswith(_EXTENSIONS):
            found[rel] = "a compiled extension module, which Python imports in place of a .py file of the same name"
        elif name.endswith(".pyc"):
            found[rel] = "bytecode, which Python can import in place of the source"
        elif path.suffix == ".py" and rel not in base and not is_test_related(rel):   # a test: the weakening detector
            clash = sorted(other for module in _import_names(rel, import_roots)
                           for other in held.get(module, ()) if other != rel)
            if clash:
                found[rel] = f"a new module named like {clash[0]}, which it can shadow on the import path"
    return found


def ignored_inputs(repo: Path) -> Ignored:
    """The git-ignored files in ``repo`` a test run could load. Loadable (``Ignored.loadable``): an ignored
    ``conftest.py``, ``pytest.toml``, ``.pytest.toml``, ``pytest.ini``, ``.pytest.ini``, ``setup.cfg``, ``tox.ini``,
    ``pyproject.toml``,
    ``sitecustomize.py``, ``usercustomize.py`` or ``.pth`` file; a test-named file (``test_*.py``, ``*_test.py``); a
    ``.py`` file whose module name (its stem, or its package's) is a tracked module's anywhere in the repository,
    which it could shadow on the import path; bytecode outside ``__pycache__`` (``.pyc``: Python imports it when no
    source is beside it, in place of a source further down the path) and a compiled extension module
    (``importlib.machinery.EXTENSION_SUFFIXES``: Python prefers one to a ``.py`` file of the same name); a symbolic
    link or junction, which can put any code under any name (the scan never follows one); and an ``.egg-info`` or
    ``.dist-info`` folder whose ``entry_points.txt`` declares a pytest plugin (the ``pytest11`` group), which pytest
    loads at start-up (review C3). Every other ignored ``.py`` file is listed with its SHA-256 (``Ignored.other``).
    Environments, caches and build output (``_UNSCANNED``, and any folder holding a ``pyvenv.cfg``) are not entered,
    nor is a metadata folder past its ``entry_points.txt``. Not covered: the packages and plugins installed in the
    interpreter's own environment, outside the repository or in a virtual environment inside it; stale bytecode in
    ``__pycache__``; and ignored files of any other kind (data the tests read, say), which are neither judged nor
    bound."""
    listed = _paths(_git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"))
    modules = {_module(PurePosixPath(rel)) for rel in _paths(_git(repo, "ls-files", "-z")) if rel.endswith(".py")}
    loadable: list[str] = []
    named: list[str] = []
    other: dict[str, str] = {}
    seen = 0

    def judge(rel: str) -> None:
        path = PurePosixPath(rel)
        name = path.name.lower()
        inside = next((index for index, part in enumerate(path.parts[:-1]) if part.endswith(_METADATA)), None)
        if inside is not None:   # a metadata folder's files: only its own entry_points.txt can load code
            if inside == len(path.parts) - 2 and name == "entry_points.txt" and _declares_plugin(repo / rel):
                loadable.append(rel)
        elif _linked(repo / rel) or name in _LOADABLE or name.endswith((".pth", ".pyc", *_EXTENSIONS)) or (
                path.suffix == ".py" and (name.startswith("test_") or name.endswith("_test.py"))):
            loadable.append(rel)
        elif path.suffix == ".py" and _module(path) in modules:
            loadable.append(rel)
            named.append(rel)
        elif path.suffix == ".py":
            other[rel] = _digest(repo / rel)

    def folder(rel: str) -> bool:
        """Judge a folder the scan would enter: True when its files are to be read."""
        if _linked(repo / rel):
            loadable.append(rel)
        elif rel.endswith(_METADATA):
            if (repo / rel / "entry_points.txt").is_file():
                judge(f"{rel}/entry_points.txt")
        else:
            return True
        return False

    def found(complete: bool) -> Ignored:
        return Ignored(tuple(sorted(loadable)), dict(sorted(other.items())), complete, tuple(sorted(named)))

    for entry in listed:
        seen += 1
        if seen > IGNORED_SCAN_LIMIT:
            return found(False)
        rel = entry.rstrip("/")
        if _unscanned(repo, rel):
            continue
        if not entry.endswith("/"):
            judge(rel)
            continue
        if not folder(rel):
            continue
        for top, dirs, files in os.walk(repo / rel):   # never through a link: os.walk does not follow one
            here = Path(top).relative_to(repo).as_posix()
            dirs[:] = sorted(name for name in dirs if name not in _UNSCANNED
                             and not (Path(top) / name / "pyvenv.cfg").is_file() and folder(f"{here}/{name}"))
            for name in sorted(files):
                seen += 1
                if seen > IGNORED_SCAN_LIMIT:
                    return found(False)
                judge(f"{here}/{name}")
    return found(True)


def read_at(repo: Path, commit: str, rel: str) -> str | None:
    """``rel``'s text at ``commit`` (None when it was not there), with the line endings ``Path.read_text`` gives,
    read with the hardened git call (``_git``: the repository's own configuration may be the agent's)."""
    rel = inside(repo, rel, "path")
    if not _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", rel).strip(b"\0"):
        return None
    return _text(_git(repo, "show", f"{commit}:{rel}"))


#: The most paths one ``texts_at`` git call names.
_TEXTS_AT_BATCH = 100


def texts_at(repo: Path, commit: str, rels: list[str], *, limit: int | None) -> dict[str, str]:
    """``read_at`` for many files in a few git calls: each of ``rels`` that ``commit`` holds as a regular file of at
    most ``limit`` bytes (any size when None), by path, with the line endings ``Path.read_text`` gives. The sizes come
    from git's tree before any blob is read; the blobs, from one ``git show`` per batch, are cut by those sizes."""
    found: dict[str, str] = {}
    for start in range(0, len(rels), _TEXTS_AT_BATCH):
        batch = [inside(repo, rel, "path") for rel in rels[start:start + _TEXTS_AT_BATCH]]
        sizes: dict[str, int] = {}
        for entry in _paths(_git(repo, "ls-tree", "-r", "-l", "-z", commit, "--", *batch)):
            meta, _, rel = entry.partition("\t")
            mode, kind, _, size = meta.split()
            if kind == "blob" and mode in ("100644", "100755") and (limit is None or int(size) <= limit):
                sizes[rel] = int(size)
        wanted = [rel for rel in batch if rel in sizes]
        if not wanted:
            continue
        raw = _git(repo, "show", "--no-textconv", *(f"{commit}:{rel}" for rel in wanted))
        if len(raw) != sum(sizes[rel] for rel in wanted):   # never cut a text G6 cannot account for
            found.update({rel: text for rel in wanted if (text := read_at(repo, commit, rel)) is not None})
            continue
        offset = 0
        for rel in wanted:
            found[rel] = _text(raw[offset:offset + sizes[rel]])
            offset += sizes[rel]
    return found


#: The names a copy never takes: git's own folder, secrets, environments and caches.
_SKIPPED = shutil.ignore_patterns(".git", ".env*", "__pycache__", ".pytest_cache", "*.pyc", ".venv", "venv")


def _copy_ignored(folder: str, names: list[str]) -> set[str]:
    """Bench-style copy, without secrets, environments or links back into the caller's tree (``repository_copy``
    makes the links it allows itself, ``_copy_links``)."""
    return set(_SKIPPED(folder, names)) | {name for name in names if (Path(folder) / name).is_symlink()
                                           or getattr(Path(folder) / name, "is_junction", lambda: False)()}


#: Windows' ERROR_PRIVILEGE_NOT_HELD: making a symbolic link needs a privilege (or developer mode).
_PRIVILEGE_NOT_HELD = 1314
#: Whether links are made as Windows makes them (``_copy_links``): with its separator, and a folder's link apart.
_WINDOWS = os.name == "nt"
#: The most links one resolution follows: past it, a loop.
_MAX_HOPS = 40


def _absolute(text: str) -> bool:
    """Whether a link's text names an absolute path, on this system or on Windows (a drive, a share, a root)."""
    return text.startswith(("/", "\\")) or bool(PureWindowsPath(text).drive)


def _link_of(path: Path) -> tuple[str, bool]:
    """A link's own text, and whether Windows made it a folder's link (a link is never followed to ask)."""
    return os.readlink(path), bool(getattr(os.lstat(path), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_DIRECTORY)


def _resolve_inside(root: Path, rel: str, virtual: dict[str, str]) -> list[str] | None:
    """The components below ``root`` that ``rel`` resolves to, one component at a time: each link on the way is
    replaced by its own text (read from the disk below ``root``, or from ``virtual`` for a link that is not there), so
    nothing outside ``root`` is ever read. None when the path leaves ``root`` (a ``..`` above it, a link naming an
    absolute path) or follows more than ``_MAX_HOPS`` links (a loop)."""
    pending, done, hops = list(PurePosixPath(rel).parts), [], 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not done:
                return None
            done.pop()
            continue
        here, path = "/".join([*done, part]), root.joinpath(*done, part)
        text = virtual[here] if here in virtual else os.readlink(path) if _linked(path) else None
        if text is None:
            done.append(part)
            continue
        hops += 1
        if hops > _MAX_HOPS or _absolute(text):
            return None
        pending[:0] = PurePosixPath(text.replace("\\", "/")).parts
    return done


def _copy_links(copy: Path, links: dict[str, tuple[str, bool]], left: dict[str, str],
                put: Callable[[Path, Path], None]) -> dict[str, str]:
    """Make each of ``links`` (a path in the copy to its link's text, and whether it is a folder's) in ``copy``, then
    keep those that resolve inside the copy, judged on the copy itself after all are made (``_resolve_inside``): one
    that names an absolute path or resolves outside is removed and named in ``left`` with why. On Windows each is made
    with the system's separator (git keeps a link's text with forward slashes, which a Windows link does not resolve),
    and as a folder's link when its target in the copy is a folder. Where this system refuses to make a link for want
    of the privilege (Windows), its target, resolved the same way, is copied in its place with ``put``: returned, path
    to the target copied."""
    deferred: dict[str, str] = {}
    texts = {rel: text for rel, (text, _) in links.items()}
    for rel, (text, folder) in sorted(links.items()):
        if _absolute(text):
            left[rel] = LINK_ABSOLUTE
            continue
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        made = text
        if _WINDOWS:   # CI run 40780356106: a Windows link resolves no forward slash, and a folder's link is its own
            made = text.replace("/", "\\")
            resolved = _resolve_inside(copy, rel, texts)
            folder = folder or (resolved is not None and copy.joinpath(*resolved).is_dir())
        try:
            os.symlink(made, copy / rel, target_is_directory=folder)
        except OSError as exc:
            if getattr(exc, "winerror", None) == _PRIVILEGE_NOT_HELD:
                deferred[rel] = text
            else:
                left[rel] = LINK_NOT_MADE.format(error=type(exc).__name__)
    outside = [rel for rel in sorted(links)
               if rel not in left and rel not in deferred and _resolve_inside(copy, rel, deferred) is None]
    for rel in outside:   # judged all together first: a link may resolve through another
        os.unlink(copy / rel)
        left[rel] = LINK_OUTSIDE
    materialised: dict[str, str] = {}
    for rel in sorted(deferred):
        resolved = _resolve_inside(copy, rel, deferred)
        source = copy.joinpath(*resolved) if resolved is not None else copy
        if resolved is None:
            left[rel] = LINK_OUTSIDE
        elif tuple(resolved) == PurePosixPath(rel).parts[:len(resolved)]:
            left[rel] = LINK_LOOP
        elif source.is_file() and not _linked(source):
            put(source, copy / rel)
            materialised[rel] = "/".join(resolved)
        elif source.is_dir() and not _linked(source):
            (copy / rel).mkdir(parents=True, exist_ok=True)
            for folder, dirs, files in os.walk(source):
                here = Path(folder)
                dirs[:] = sorted(name for name in dirs if not _linked(here / name))
                for name in sorted(files):
                    if not _linked(here / name) and (here / name).is_file():
                        put(here / name, copy / rel / here.relative_to(source) / name)
            materialised[rel] = "/".join(resolved)
        else:
            left[rel] = LINK_NO_TARGET
    return materialised


class CopyNotRun(ValueError):
    """A repository copy exceeded its file, byte or time cap before execution."""


def outside_temp(repo: Path, preferred: Path | None = None) -> Path:
    repo = repo.resolve()
    candidates = [preferred, tempfile.tempdir, *(os.environ.get(key) for key in ("TMPDIR", "TEMP", "TMP"))]
    roots = [Path(os.fsdecode(candidate)).resolve() for candidate in candidates if candidate]
    root = next((root for root in roots if root.is_dir()), repo.parent)
    if root.is_relative_to(repo):
        root = next((root for root in [repo.parent, *roots] if not root.is_relative_to(repo) and root.is_dir()), None)
    if root is None:
        raise CopyNotRun(COPY_NO_OUTSIDE)
    return root


def _copy_remaining(deadline: float) -> float:
    component_process.checkpoint()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CopyNotRun(COPY_TIME_CAP)
    return remaining


def _copy_caps(files: int, size: int, deadline: float | None) -> None:
    if deadline is not None:
        _copy_remaining(deadline)
    if files > COPY_MAX_FILES:
        raise CopyNotRun(COPY_FILE_CAP.format(n=COPY_MAX_FILES))
    if size > COPY_MAX_BYTES:
        raise CopyNotRun(COPY_BYTE_CAP.format(n=COPY_MAX_BYTES))


@contextmanager
def repository_copy(repo: Path, *, deadline: float, before: dict[str, str] | None = None,
                    report: dict | None = None):
    """Copy git's tracked and non-ignored untracked files, with bounded reads and old-test overlays; every file of each
    submodule folder git does not look into (``_gitlinks``); and each tracked link (git's mode 120000) and each link in
    such a folder as ``_copy_links`` allows. ``report``, when given, receives ``links_left_out`` (path to why) and
    ``links_materialised`` (path to the target copied in its place), each only when it names one. A repository
    holding a submodule that was set up is refused (``G6_E_GIT_SUBMODULE_REFUSED``)."""
    before = before or {}
    stage = _git(repo, "ls-files", "--stage", "-z", timeout_s=min(GIT_TIMEOUT_S, _copy_remaining(deadline)))
    gitlinks = _gitlinks(repo, stage)
    tracked_links = {entry.partition("\t")[2] for entry in _paths(stage) if entry.startswith("120000 ")}
    known = _paths(_git(repo, "ls-files", "-co", "--exclude-standard", "-z",
                        timeout_s=min(GIT_TIMEOUT_S, _copy_remaining(deadline))))
    with confine.run_folder(prefix="g6_repo_copy_", dir=outside_temp(repo), ignore_cleanup_errors=True) as scratch:
        copy = Path(scratch) / "repo"
        copy.mkdir()
        tally = [0, 0]   # files and bytes copied

        def put(source: Path, target: Path, data: bytes | None = None) -> None:
            """One file into the copy within the caps: ``data``, else ``source``'s bytes, read in blocks."""
            tally[0] += 1
            _copy_caps(tally[0], tally[1] + (len(data) if data is not None else source.stat().st_size), deadline)
            target.parent.mkdir(parents=True, exist_ok=True)
            if data is not None:
                target.write_bytes(data)
                tally[1] += len(data)
                return
            with source.open("rb") as src, target.open("wb") as dst:
                while True:
                    _copy_caps(*tally, deadline)
                    block = src.read(min(1 << 20, COPY_MAX_BYTES - tally[1] + 1))
                    if not block:
                        break
                    tally[1] += len(block)
                    _copy_caps(*tally, deadline)
                    dst.write(block)
            shutil.copymode(source, target)

        def failed(error: OSError) -> None:
            raise error

        links: dict[str, tuple[str, bool]] = {}
        left: dict[str, str] = {}
        for rel in sorted(set(known) | set(before)):
            _copy_caps(*tally, deadline)
            parts = PurePosixPath(rel).parts
            skipped = [part in _copy_ignored(str(repo.joinpath(*parts[:index])), [part])
                       for index, part in enumerate(parts)]
            if (rel in tracked_links and rel not in before and not any(skipped[:-1])
                    and not _SKIPPED(str(repo.joinpath(*parts[:-1])), [parts[-1]]) and _linked(repo / rel)):
                links[rel] = _link_of(repo / rel)
                continue
            if any(skipped) or rel in gitlinks:   # a submodule folder is copied below
                continue
            source = repo / inside(repo, rel, "file")
            if rel not in before and not source.is_file():
                continue
            put(source, copy / rel, before[rel].encode("utf-8") if rel in before else None)
        for rel in gitlinks:
            parts = PurePosixPath(rel).parts
            if any(_linked(repo.joinpath(*parts[:end])) for end in range(1, len(parts) + 1)):
                left[rel] = LINK_FOLDER
                continue
            if any(part in _copy_ignored(str(repo.joinpath(*parts[:index])), [part])
                   for index, part in enumerate(parts)) or not (repo / rel).is_dir():
                continue
            for folder, dirs, files in os.walk(repo / rel, onerror=failed):
                here = Path(folder)
                at = here.relative_to(repo).as_posix()
                skipped_here = _copy_ignored(folder, dirs + files)
                for name in sorted(skipped_here):
                    if _linked(here / name) and not _SKIPPED(folder, [name]):
                        links[f"{at}/{name}"] = _link_of(here / name)
                dirs[:] = sorted(name for name in dirs if name not in skipped_here)
                for name in sorted(files):
                    if name not in skipped_here and (here / name).is_file():
                        put(here / name, copy / at / name)
        materialised = _copy_links(copy, links, left, put)
        if report is not None:
            report.update({key: value for key, value in (("links_left_out", dict(sorted(left.items()))),
                                                         ("links_materialised", materialised)) if value})
        _copy_caps(*tally, deadline)
        yield copy


def _copy_snapshot(repo: Path, *, deadline: float | None = None) -> dict[str, str]:
    """A copy's own record of its files, read before and after a run in it: each file's SHA-256, and each link the copy
    holds (``_copy_links``) as its own text, never followed."""
    found, size = {}, 0
    for folder, dirs, files in os.walk(repo):
        _copy_caps(len(found), size, deadline)
        ignored = _copy_ignored(folder, dirs + files)
        for name in sorted(ignored):
            if _linked(Path(folder) / name):
                _copy_caps(len(found) + 1, size, deadline)
                found[(Path(folder) / name).relative_to(repo).as_posix()] = "link:" + os.readlink(Path(folder) / name)
        dirs[:] = [name for name in dirs if name not in ignored]
        for name in files:
            if name not in ignored:
                path = Path(folder) / name
                _copy_caps(len(found) + 1, size + path.stat().st_size, deadline)
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    while True:
                        _copy_caps(len(found) + 1, size, deadline)
                        block = handle.read(min(1 << 20, COPY_MAX_BYTES - size + 1))
                        if not block:
                            break
                        size += len(block)
                        _copy_caps(len(found) + 1, size, deadline)
                        digest.update(block)
                found[path.relative_to(repo).as_posix()] = digest.hexdigest()
    return found


@contextmanager
def _base_revision(change: Change, copy: Path, deadline: float, keep: frozenset[str] = frozenset(),
                   materialised: dict[str, str] | None = None):
    """Temporarily restore all base-revision differences in the bounded replay copy, except the files and folders
    ``keep`` names, which stay as the copy has them. A changed link (at the base or in the copy), or a change at a
    link the copy holds as its target's copy (``materialised``: the copy's ``links_materialised``), or below its
    target, cannot be put back as it was: ``CopyNotRun`` (``OLD_BASELINE_NOT_RUN``)."""
    from contextlib import ExitStack

    diffed, others = _private(change.repo, change.base_commit,   # never the repository's index (the review of VS-4)
                              ("diff", "--name-only", "--no-renames", "-z"),
                              ("ls-files", "--others", "--exclude-standard", "-z"))
    changed = set(_paths(diffed)) | set(_paths(others))
    changed.update(change.held)   # changed, though git's index flags hide them from its diff (review C2)
    changed = {rel for rel in changed if not any(rel == kept or rel.startswith(kept + "/") for kept in keep)}
    entries = _paths(_git(change.repo, "ls-tree", "-r", "-l", "-z", change.base_commit))
    base = {}
    for entry in entries:
        meta, rel = entry.split("\t", 1)
        mode, kind, oid, size = meta.split()
        if rel in changed:
            base[rel] = (mode, kind, oid, size)
    count, size = 0, 0
    copied_in = [path for pair in (materialised or {}).items() for path in pair]
    with ExitStack() as restore:
        for rel in sorted(changed):
            if (base.get(rel, ("",))[0] == "120000" or _linked(copy / rel)
                    or any(rel == path or rel.startswith(path + "/") for path in copied_in)):
                raise CopyNotRun(OLD_BASELINE_NOT_RUN)
            parts = PurePosixPath(rel).parts
            if any(part in _copy_ignored(str(change.repo.joinpath(*parts[:index])), [part])
                   for index, part in enumerate(parts)):
                continue
            target = copy / inside(copy, rel, "file")
            current = target.read_bytes() if target.is_file() else None
            count += 1
            size += len(current) if current is not None else 0
            _copy_caps(count, size, deadline)
            if current is None:
                restore.callback(target.unlink, missing_ok=True)
            else:
                restore.callback(target.write_bytes, current)
            if rel in base:
                mode, kind, oid, length = base[rel]
                if kind != "blob" or mode not in ("100644", "100755"):
                    raise CopyNotRun(OLD_BASELINE_NOT_RUN)
                size += int(length)
                _copy_caps(count, size, deadline)
                data = _git(change.repo, "cat-file", "blob", oid,
                            timeout_s=min(GIT_TIMEOUT_S, _copy_remaining(deadline)))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            elif target.is_file():
                target.unlink()
        yield


def run_before(change: Change, *, roots: tuple[str, ...] = (), timeout_s: int = DEFAULT_TIMEOUT_S,
               network: bool = False, require_isolation: bool = False) -> dict[str, dict]:
    """Execute the old changed/deleted tests against copied current code, never overwrite the caller's tests.

    Same file/time caps as the main run. All old files are restored together for cross-file imports; each file gets
    a fresh copy so a previous test cannot contaminate the next file's evidence. Git metadata is never copied. A
    ``not_run`` entry made after the old-code baseline ran carries that baseline's verdict (``baseline``), and, when
    the baseline passed but the new run did not match its tests, the new run's verdict (``patched``). A file of
    ``change.before`` that ``change.renamed`` moved there runs at its old path at the base commit, where the new one
    is absent, and its tests are matched across the move (review I6). ``require_isolation``: a run no backend would
    confine is refused before it starts, and that refusal is raised, not returned (``run``).
    """
    bound = max(1, min(int(timeout_s), MAX_TIMEOUT_S))
    started, results = time.monotonic(), {}
    old_path = {new: old for old, new in change.renamed.items()}

    def moved(outcomes: dict | None, old: str, new: str) -> set[str]:
        """The test ids of a base run at ``old``, as they read at ``new``."""
        return {new + node[len(old):] if node == old or node.startswith(old + "::") else node for node in outcomes or {}}

    for index, rel in enumerate(sorted(change.before)):
        remaining = bound - (time.monotonic() - started)
        if index >= MAX_DERIVED:
            results[rel] = {"verdict": "not_run", "said": OLD_TEST_LIMIT.format(n=MAX_DERIVED)}
            continue
        if remaining < 1:
            results[rel] = {"verdict": "not_run", "said": SAID["timed_out"].format(seconds=bound)}
            continue
        try:
            report: dict = {}
            with repository_copy(change.repo, deadline=started + bound, before=change.before, report=report) as copy:
                with _base_revision(change, copy, started + bound, materialised=report.get("links_materialised")):
                    baseline = _run(copy, [old_path.get(rel, rel)], roots=roots, timeout_s=bound, network=network,
                                    copied=True, python=interpreter(change.repo), scratch_dir=copy.parent,
                                    deadline=started + bound, links=report, require_isolation=require_isolation)
                if (baseline["verdict"] != PASSED or baseline.get("degraded") or baseline.get("not_passed")
                        or baseline.get("deselected") or not baseline.get("test_outcomes")):
                    reason = baseline.get("said") or "; ".join(baseline.get("setup_errors", {}).values())
                    results[rel] = {"verdict": "not_run", "said": reason or OLD_BASELINE_NOT_RUN,
                                    "baseline": baseline["verdict"]}
                    continue
                remaining = bound - (time.monotonic() - started)
                if remaining < 1:
                    results[rel] = {"verdict": "not_run", "said": SAID["timed_out"].format(seconds=bound)}
                    continue
                results[rel] = _run(copy, [rel], roots=roots, timeout_s=int(remaining), network=network,
                                    copied=True, python=interpreter(change.repo), scratch_dir=copy.parent,
                                    deadline=started + bound, links=report, require_isolation=require_isolation)
                if moved(baseline["test_outcomes"], old_path.get(rel, rel), rel) != set(
                        results[rel].get("test_outcomes") or {}):
                    reason = results[rel].get("said") or (SAID["collection_error"] if results[rel]["verdict"] == FAILED
                                                         and not results[rel].get("failed_ids") else OLD_BASELINE_NOT_RUN)
                    results[rel] = {"verdict": "not_run", "said": reason, "baseline": PASSED,
                                    "patched": results[rel]["verdict"]}
        except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
            raise
        except (OSError, RepoRefused, CopyNotRun) as exc:
            if require_isolation and getattr(exc, "code", "") == ISOLATION_UNAVAILABLE:
                raise
            results[rel] = {"verdict": "not_run", "said": str(exc)}
    return results


# COPY: reason for old tests omitted by the replay's file cap (A1 amendment B).
OLD_TEST_LIMIT = "the limit of {n} old test file(s) was reached"


def run_at_base(change: Change, tests: list[str], *, roots: tuple[str, ...] = (), timeout_s: int = DEFAULT_TIMEOUT_S,
                network: bool = False, overlay: dict[str, str] | None = None, require_isolation: bool = False) -> dict:
    """``tests`` (files, folders or ``file::test`` ids) as they are now, run against the base commit's code: in a copy
    (``repository_copy``, with its caps), every file the change touched is put back as it was at the base commit
    (``_base_revision``) except the files and folders ``tests`` name, which keep their text now. A check that passes
    here does not need the patch to pass (review I8). The repository itself is never written, and the run gets no
    receipt (it is not the artifact under review). ``{"verdict": "not_run", "said": ...}`` when the copy or the run
    could not be made. ``overlay`` ({path: text}) adds files to the copy only, as ``repository_copy``'s ``before``
    does; ``require_isolation`` refuses a run no backend confines (``_run``), and that refusal is raised, not returned."""
    bound = max(1, min(int(timeout_s), MAX_TIMEOUT_S))
    deadline = time.monotonic() + bound
    try:
        keep = frozenset(inside(change.repo, test, "test").partition("::")[0].rstrip("/") for test in tests)
        report: dict = {}
        with repository_copy(change.repo, deadline=deadline, before=overlay, report=report) as copy:
            with _base_revision(change, copy, deadline, keep=keep, materialised=report.get("links_materialised")):
                return _run(copy, tests, roots=roots, timeout_s=bound, network=network, copied=True,
                            python=interpreter(change.repo), scratch_dir=copy.parent, deadline=deadline,
                            require_isolation=require_isolation, links=report)
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except (OSError, RepoRefused, CopyNotRun) as exc:
        if require_isolation and getattr(exc, "code", "") == ISOLATION_UNAVAILABLE:
            raise
        return {"verdict": "not_run", "said": str(exc)}


def probe_repo(scratch: Path) -> Path:
    """A one-test git repository in ``scratch`` for the census probe: nothing of the owner's is read. Its test file is
    untracked, which the run does not mind."""
    repo = Path(scratch) / "probe"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_probe.py").write_text("def test_probe():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    _git(repo, "init", "-q")
    return repo
