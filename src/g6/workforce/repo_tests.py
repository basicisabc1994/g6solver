# SPDX-License-Identifier: Apache-2.0

"""A caller's own repository, read from git and tested in place (plan 2026-10-05-verify-task-real-repos).

``verify_task`` runs the tests of the repository a coding agent changed, on the caller's own computer, and the
workforce bench's ``run_tests_in_place`` op runs the same function, so the census measures what verify_task uses.

What it reads (``change``): the files changed against a base ref (default ``HEAD``: the working tree's change) as git
reports them, plus untracked files that are not ignored; each changed test file's text at the base and now, both read
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
  are not covered. A repository holding a submodule (a gitlink) is refused before anything runs: git would run inside
  it under the submodule's own config;
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

import hashlib
import importlib.machinery
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from g6.core import component_process, confine, git_env, ledger
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.outside import find_program
from g6.workforce import receipt_store, verify

DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 1800
#: The most test files "the tests the change touches" may be; more are counted and said, never run silently.
MAX_DERIVED = 25
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
#: The ``sandbox`` label of a run in place: the isolation that actually ran.
SANDBOX = "repository_in_place"
#: A run's verdicts. Only ``passed`` and ``failed`` mean the tests ran to a result.
PASSED, FAILED, CHANGED = "passed", "failed", "changed_tracked_files"
RAN = frozenset({PASSED, FAILED})
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
}


class RepoRefused(ValueError):
    """The repository or the request cannot be run; ``code`` is its stable ``G6_E_*`` code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: Before every git command: no index refresh (``--no-optional-locks``); paths are paths, never patterns
#: (``--literal-pathspecs``: ``a[1].py`` is not also ``a1.py``); names unquoted; and hunks never merged across the
#: unchanged lines between them, whatever the owner's ``diff.interHunkContext`` (review M7).
_GIT = ("git", "--no-optional-locks", "--literal-pathspecs", "-c", "core.quotepath=off", "-c", "diff.interHunkContext=0")


def _git(repo: Path, *args: str, ok: tuple[int, ...] = (0,), timeout_s: float = GIT_TIMEOUT_S) -> bytes:
    """One read-only git command in ``repo`` (``_GIT``'s options first), in ``git_env.safe_git_env``'s clean
    environment (none of git's own redirections such as GIT_DIR, no transport, no lazy fetch) with every config key
    that would start a program switched off, the drivers any config defines included (one ``git_env.DRIVER_QUERY``
    first, in the same window), and a ``status`` or ``diff`` never entering a submodule (``git_env.safe_git_args``):
    the repository's .git/config may be the agent's (conformance K1, review B2-C1 and B2-C2)."""
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


def _no_gitlinks(repo: Path, index: bytes | None = None) -> None:
    """A refusal (``G6_E_GIT_SUBMODULE_REFUSED``) when the index (``index``: its ``ls-files --stage -z`` listing, when
    already read) or HEAD holds a gitlink: git would run inside the submodule under its own config (review B2-C1)."""
    listed, head = git_env.GITLINK_QUERIES
    try:
        git_env.refuse_gitlinks(_git(repo, *listed) if index is None else index, _git(repo, *head, ok=(0, 128)))
    except git_env.UnsafeGitConfig as exc:
        raise RepoRefused(git_env.SUBMODULE_REFUSED, str(exc)) from exc


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
    """A Python file the tests are made of: a test file by either of pytest's default names (``test_*.py``,
    ``*_test.py``, with ``is_test_file``'s case rule), ``conftest.py`` or a start-up file, or any Python file under a
    ``tests``, ``test`` or ``testing`` folder, such as a helper the tests check through (review C2)."""
    path = PurePosixPath(rel)
    name = os.path.normcase(path.name)
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")
                                     or path.name.lower() in _START_UP
                                     or any(part.lower() in _TEST_FOLDERS for part in path.parts[:-1]))


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


def change(repo: Path, base: str = "HEAD", paths: tuple[str, ...] = (), *,
           read_file: Callable[[Path], str] | None = None) -> Change:
    """The change in ``repo`` against ``base``: changed tracked files (staged or not, and committed since ``base``) and
    untracked ones, within ``paths`` when given; each changed test file's text at the base (absent when it is new) and
    now (absent when it was deleted), both with the line endings ``Path.read_text`` gives; the changed Python files
    that are not tests; the runner files it touches; and the test files it moved. A repository whose index or HEAD
    holds a gitlink is refused (``G6_E_GIT_SUBMODULE_REFUSED``)."""
    if not isinstance(base, str) or not base.strip() or base.startswith("-") or _control(base):
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["bad_base"].format(base=base))
    _no_gitlinks(repo)
    spec = ["--", *(inside(repo, path, "paths") for path in paths)] if paths else []
    try:
        commit = _git(repo, "rev-parse", "--verify", "-q", f"{base}^{{commit}}").decode().strip()
    except RepoRefused as refused:
        if refused.code == "G6_E_GIT_MISSING_BINARY":
            raise
        raise RepoRefused("G6_E_GIT_REPO_INVALID", SAID["bad_base"].format(base=base)) from refused
    head = _git(repo, "rev-parse", "--verify", "-q", "HEAD", ok=(0, 1)).decode().strip()
    files = tuple(sorted(set(_paths(_git(repo, "diff", "--name-only", "-z", "--no-renames", "--no-textconv", commit,
                                         *spec)))
                         | set(_paths(_git(repo, "ls-files", "--others", "--exclude-standard", "-z", *spec)))))
    tests = [rel for rel in files if is_test_file(rel)]
    existed = set(_paths(_git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *tests))) if tests else set()
    before = {rel: _text(_git(repo, "show", f"{commit}:{rel}")) for rel in sorted(existed)}
    reader = read_file or (lambda path: path.read_text(encoding="utf-8", errors="replace"))
    after = {rel: reader(repo / rel) for rel in tests
             if (repo / rel).is_file()}
    sources = tuple(rel for rel in files if rel.endswith(".py") and not is_test_file(rel)
                    and PurePosixPath(rel).name != "conftest.py" and (repo / rel).is_file())
    runner = tuple(rel for rel in files if PurePosixPath(rel).name.lower() in RUNNER_FILES or rel.endswith(".pth"))
    gone, new = [rel for rel in before if rel not in after], [rel for rel in after if rel not in before]
    moved = _moves(repo, commit, gone, new, before, after) if gone and new else {}
    return Change(repo, base, commit, head, files, before, after, sources, runner, moved)


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


def related_texts(change: Change) -> tuple[dict[str, str], dict[str, str]]:
    """Every test-related file the change touches (``is_test_related``), as ``change`` reads its test files: its text
    at the base commit (absent when it is new) and now (absent when it was deleted), with the line endings
    ``Path.read_text`` gives. A file moved since the base (``_moves``) is keyed by its new path on both sides, so it
    is compared with its old text, not read as one file deleted and another added."""
    related = [rel for rel in change.files if is_test_related(rel)]
    if not related:
        return {}, {}
    existed = set(_paths(_git(change.repo, "ls-tree", "-r", "-z", "--name-only", change.base_commit, "--", *related)))
    before = {rel: _text(_git(change.repo, "show", f"{change.base_commit}:{rel}")) for rel in sorted(existed)}
    after = {rel: (change.repo / rel).read_text(encoding="utf-8", errors="replace") for rel in related
             if (change.repo / rel).is_file()}
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
    text = _git(change.repo, "diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", change.base_commit, "--",
                rel).decode("utf-8", "replace")
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
    text = _git(change.repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", change.base_commit).decode(
        "utf-8", "replace")
    untracked = set(_paths(_git(change.repo, "ls-files", "--others", "--exclude-standard", "-z")))
    for rel in sorted(untracked & set(change.files)):
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
#: The runner's bootstrap (``venture.truth._ISOLATED_BOOT``'s pattern): the real pytest is imported before the
#: repository joins the path (its folders arrive as one JSON list, so a ';' or ':' in a path never splits it); then
#: the network guard, unless allowed; then the mutant's import hook, when given. Its own arguments are taken off
#: ``sys.argv`` before the tests run. When pytest would load pytest-xdist (its ``pytest11`` entry point is installed),
#: ``-n0 --dist=no`` come after the repository's own options and win: the tests run in this one process, where the
#: guard and the hook apply (review I8). A small plugin writes, as the session ends, how many tests were collected and
#: deselected, and how many started and finished (a setup and a teardown reported), so a run that stops part-way with
#: exit code 0 is not read as a pass (review I3). Another turns pytest's "fd" capture, its default, into "sys" before
#: capturing starts: "fd" opens the null device for standard input, which a Windows AppContainer may be refused
#: (``truth.CAPTURE``); a capture the repository's own options chose otherwise (``-s``) stays.
_BOOT = r'''
import json, os, socket, sys
sys.path[:] = [p for p in sys.path if p not in ("", os.getcwd())]
import pytest
entries, network, target, source, counted = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
args = sys.argv[6:]
del sys.argv[1:6]
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
    def pytest_runtest_logreport(self, report):
        self.test_outcomes.setdefault(report.nodeid, {})[report.when] = report.outcome
        if report.when in ("setup", "teardown"):
            (self.started if report.when == "setup" else self.finished).add(report.nodeid)
        if report.failed:
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
                           "rootdir": self.rootdir}, handle)
class _Capture:
    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_load_initial_conftests(self, early_config):
        if getattr(early_config.known_args_namespace, "capture", None) == "fd":
            early_config.known_args_namespace.capture = "sys"
        yield
sys.exit(pytest.main(args, plugins=[_Counted(), _Capture()]))
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
    reads ""), of every file so marked (git's own diff skips them; review M4), and of each of ``untracked`` (default:
    the untracked files that are not ignored now; ``run`` passes the ones that existed before it, review I7). A
    repository whose index or HEAD holds a gitlink is refused (``G6_E_GIT_SUBMODULE_REFUSED``: git would run inside the
    submodule under its own config, review B2-C1), before a run and after one alike. Ignored files, and files created
    after ``untracked`` was read, are not part of it. Raises ``RepoRefused`` when git cannot read the repository."""
    marks, index = _git(repo, "ls-files", "-v", "-z"), _git(repo, "ls-files", "--stage", "-z")
    _no_gitlinks(repo, index)
    found = {_HEAD: hashlib.sha256(_git(repo, "rev-parse", "--verify", "-q", "HEAD", ok=(0, 1))).hexdigest(),
             _INDEX: hashlib.sha256(index).hexdigest(), _MARKS: hashlib.sha256(marks).hexdigest()}
    # ``ls-files -v``: a lowercase tag is assume-unchanged, "S" skip-worktree.
    held = [entry[2:] for entry in _paths(marks) if entry[:1].islower() or entry[:1] == "S"]
    for rel in (*_paths(_git(repo, "diff", "--name-only", "-z")), *held,
                *(_untracked(repo) if untracked is None else untracked)):
        found[rel] = _digest(repo / rel)
    return found


def _moved(before: dict[str, str], after: dict[str, str]) -> list[str]:
    # PENDING copy: the name for _MARKS is a proposal (the fix pass after the review of Tasks 4-5).
    names = {_HEAD: "the commit checked out (HEAD)", _INDEX: "the staged files (the index)",
             _MARKS: "git's assume-unchanged and skip-worktree marks"}
    return [names.get(key, key) for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]


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


def interpreter(repo: Path) -> str:
    """The Python that runs the repository's tests: its own virtual environment (``.venv`` or ``venv`` at its top),
    else the one running G6 (``python`` on PATH in a frozen build, which has no interpreter of its own)."""
    for folder in (".venv", "venv"):
        for exe in (repo / folder / "Scripts" / "python.exe", repo / folder / "bin" / "python"):
            if exe.is_file():
                return str(exe)
    if getattr(sys, "frozen", False) or not sys.executable:
        return "python"
    return sys.executable


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
      (``finished``); fewer, or a count that cannot be read, is "incomplete" (I3). ``run`` always sets ``expected``."""
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
    if exit_code == 1:
        return FAILED if bad else "inconsistent_report"
    if exit_code in (2, 3, 4):
        # Errors whose culprit is not in the tally are taken as collection errors G6 cannot place (exit 2-4's usual).
        unplaced = counts.get("collection", counts["errors"]) - counts.get("placed", 0)
        return FAILED if bad - outside - unplaced > 0 else "collection_error"
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


def _configures_pytest(folder: Path) -> bool:
    for name, section in _PYTEST_CONFIG:
        path = folder / name
        try:
            if path.is_file() and (not section or re.search(section, path.read_text(encoding="utf-8", errors="replace"),
                                                            re.MULTILINE)):
                return True
        except OSError:
            continue
    return False


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
            "rootdir": found.get("rootdir") or "",
            "passed": max(0, counts.get("passed", 0) - max(0, started - finished))}


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
        network: bool = False, mutant: tuple[str, str] | None = None, receipt: bool = False) -> dict:
    """Run ``tests`` (files, folders or ``file::test`` ids, relative to ``repo``) against the working tree, in place,
    and say what pytest found. A run under which a tracked file, HEAD or the index changed is ``changed_tracked_files``
    with the paths, whatever the tests said, and so is one after which git cannot read the repository (with that
    said). ``mutant`` (path, source): that module is imported from the source given instead of its file, which is
    never written (the changed-lines mutation check). ``interpreter_source`` says whose Python ran the tests
    ("repository": its own ``.venv`` or ``venv``; "g6": the one running G6), ``python`` its path, and
    ``source_before`` the digest of the ``snapshot`` the run began on (the digest the patch review calls its
    ``artifact_digest``), and ``rootdir`` the folder pytest named the tests relative to ("" for the repository's top;
    a pytest configuration below the top moves it). Raises ``RepoRefused`` before anything runs when the request
    cannot run.

    ``receipt=True``: a run that started confined (its ``isolation`` names a backend), whose repository git could
    read after it, and beside which ``ignored_inputs`` found no git-ignored file the run could load, is written to
    G6's test-receipt store (``g6.workforce.receipt_store``), and ``receipt`` is the receipt's id; every other run, an
    unconfined one included, has ``receipt`` "" (with ``receipt_withheld`` and ``ignored_inputs`` when ignored files
    stopped it, and ``receipt_error`` when the store could not take the receipt). A mutant is not the artifact under review: ``receipt=True`` with ``mutant`` raises
    ``ValueError``."""
    if receipt and mutant is not None:
        raise ValueError("a mutant run is not the artifact under review, so it is never given a test receipt")
    repo = checked_repo(repo)
    if mutant is not None:
        deadline = time.monotonic() + max(1, min(int(timeout_s), MAX_TIMEOUT_S))
        try:
            with repository_copy(repo, deadline=deadline) as copy:
                return _run(copy, tests, roots=roots, timeout_s=timeout_s, network=network, mutant=mutant,
                            copied=True, python=interpreter(repo), scratch_dir=copy.parent, deadline=deadline)
        except CopyNotRun as exc:
            raise RepoRefused("G6_E_TESTS_DID_NOT_RUN", str(exc)) from exc
    return _run(repo, tests, roots=roots, timeout_s=timeout_s, network=network, receipt=receipt)


def _run(repo: Path, tests: list[str], *, roots: tuple[str, ...], timeout_s: int, network: bool,
         mutant: tuple[str, str] | None = None, copied: bool = False, python: str = "",
         scratch_dir: Path | None = None, deadline: float | None = None, receipt: bool = False) -> dict:
    """The shared execution path; only old-test replay uses a copy without git metadata."""
    if not tests:
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["no_tests"])
    ids = [inside(repo, test, "test") for test in tests]
    missing = [test for test in ids if not (repo / test.partition("::")[0]).exists()]
    if missing:
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["missing"].format(value=missing[0]))
    entries = [str(repo), *([str(repo / "src")] if (repo / "src").is_dir() else []),
               *(str(repo / inside(repo, root, "import root")) for root in roots)]
    bound = max(1, min(int(timeout_s), MAX_TIMEOUT_S))
    python = python or interpreter(repo)
    whose = "repository" if any(Path(python).is_relative_to(repo / folder) for folder in (".venv", "venv")) else "g6"
    untracked = [] if copied else _untracked(repo)
    take_snapshot = (lambda: _copy_snapshot(repo, deadline=deadline)) if copied else (lambda: snapshot(repo, untracked))
    before, started = take_snapshot(), time.monotonic()
    began = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    head = _git(repo, "rev-parse", "--verify", "-q", "HEAD", ok=(0, 1)).decode().strip() if receipt else ""
    command = f"pytest {len(ids)} test id(s) in place"
    isolation = confine.summary([])

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
                "source_before": ledger.digest(before), "rootdir": _root(repo, tally.get("rootdir") or "")}

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
        # the run's own log file, empty (truth.no_null_device): with none named, pytest's is the null device, which an
        # AppContainer may be refused; one the repository names could not be written into a repository in place
        cmd = [python, "-I", "-B", "-X", "utf8", "-c", _BOOT, json.dumps(entries), "1" if network else "0",
               target, source, str(counted), *ids, "-q", "-p", "no:cacheprovider",
               f"--junitxml={report}", *_config_args(repo, ids, own), f"--log-file={own / 'pytest.log'}",
               "--log-file-level=100"]
        mine = confine.owned(repo)
        confined = confine.plan(confine.Confinement(
            read_write=(str(work), str(own), *([str(repo)] if mine else [])),
            read_only=(*confine.interpreter_paths(python), *([] if mine else [str(repo)]), top), network=network),
            python)
        isolation = confined.record()
        said = confine.refused(confined)
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
    ``IGNORED_SCAN_LIMIT`` entries (if not, no receipt either)."""

    loadable: tuple[str, ...]
    other: dict[str, str]
    complete: bool


def _module(path: PurePosixPath) -> str:
    """The module name a Python file gives on the import path: its stem, or its package's for ``__init__.py``."""
    return path.parent.name if path.stem == "__init__" else path.stem


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
                path.suffix == ".py" and (
                    name.startswith("test_") or name.endswith("_test.py") or _module(path) in modules)):
            loadable.append(rel)
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
        return Ignored(tuple(sorted(loadable)), dict(sorted(other.items())), complete)

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


def _copy_ignored(folder: str, names: list[str]) -> set[str]:
    """Bench-style copy, without secrets, environments or links back into the caller's tree."""
    ignored = shutil.ignore_patterns(".git", ".env*", "__pycache__", ".pytest_cache", "*.pyc", ".venv", "venv")(
        folder, names)
    return set(ignored) | {name for name in names if (Path(folder) / name).is_symlink()
                           or getattr(Path(folder) / name, "is_junction", lambda: False)()}


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
def repository_copy(repo: Path, *, deadline: float, before: dict[str, str] | None = None):
    """Copy git's tracked and non-ignored untracked files, with bounded reads and old-test overlays."""
    before = before or {}
    known = _paths(_git(repo, "ls-files", "-co", "--exclude-standard", "-z",
                        timeout_s=min(GIT_TIMEOUT_S, _copy_remaining(deadline))))
    with confine.run_folder(prefix="g6_repo_copy_", dir=outside_temp(repo), ignore_cleanup_errors=True) as scratch:
        copy = Path(scratch) / "repo"
        copy.mkdir()
        count, size = 0, 0
        for rel in sorted(set(known) | set(before)):
            _copy_caps(count, size, deadline)
            parts = PurePosixPath(rel).parts
            if any(part in _copy_ignored(str(repo.joinpath(*parts[:index])), [part])
                   for index, part in enumerate(parts)):
                continue
            source = repo / inside(repo, rel, "file")
            if rel not in before and not source.is_file():
                continue
            data = before[rel].encode("utf-8") if rel in before else None
            count += 1
            _copy_caps(count, size + (len(data) if data is not None else source.stat().st_size), deadline)
            target = copy / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if data is not None:
                target.write_bytes(data)
                size += len(data)
            else:
                with source.open("rb") as src, target.open("wb") as dst:
                    while True:
                        _copy_caps(count, size, deadline)
                        block = src.read(min(1 << 20, COPY_MAX_BYTES - size + 1))
                        if not block:
                            break
                        size += len(block)
                        _copy_caps(count, size, deadline)
                        dst.write(block)
                shutil.copymode(source, target)
        _copy_caps(count, size, deadline)
        yield copy


def _copy_snapshot(repo: Path, *, deadline: float | None = None) -> dict[str, str]:
    found, size = {}, 0
    for folder, dirs, files in os.walk(repo):
        _copy_caps(len(found), size, deadline)
        ignored = _copy_ignored(folder, dirs + files)
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
def _base_revision(change: Change, copy: Path, deadline: float):
    """Temporarily restore all base-revision differences in the bounded replay copy."""
    from contextlib import ExitStack

    changed = set(_paths(_git(change.repo, "diff", "--name-only", "--no-renames", "-z", change.base_commit)))
    changed.update(_untracked(change.repo))
    entries = _paths(_git(change.repo, "ls-tree", "-r", "-l", "-z", change.base_commit))
    base = {}
    for entry in entries:
        meta, rel = entry.split("\t", 1)
        mode, kind, oid, size = meta.split()
        if rel in changed:
            base[rel] = (mode, kind, oid, size)
    count, size = 0, 0
    with ExitStack() as restore:
        for rel in sorted(changed):
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
               network: bool = False) -> dict[str, dict]:
    """Execute the old changed/deleted tests against copied current code, never overwrite the caller's tests.

    Same file/time caps as the main run. All old files are restored together for cross-file imports; each file gets
    a fresh copy so a previous test cannot contaminate the next file's evidence. Git metadata is never copied. A
    ``not_run`` entry made after the old-code baseline ran carries that baseline's verdict (``baseline``), and, when
    the baseline passed but the new run did not match its tests, the new run's verdict (``patched``). A file of
    ``change.before`` that ``change.renamed`` moved there runs at its old path at the base commit, where the new one
    is absent, and its tests are matched across the move (review I6).
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
            with repository_copy(change.repo, deadline=started + bound, before=change.before) as copy:
                with _base_revision(change, copy, started + bound):
                    baseline = _run(copy, [old_path.get(rel, rel)], roots=roots, timeout_s=bound, network=network,
                                    copied=True, python=interpreter(change.repo), scratch_dir=copy.parent,
                                    deadline=started + bound)
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
                                    deadline=started + bound)
                if moved(baseline["test_outcomes"], old_path.get(rel, rel), rel) != set(
                        results[rel].get("test_outcomes") or {}):
                    reason = results[rel].get("said") or (SAID["collection_error"] if results[rel]["verdict"] == FAILED
                                                         and not results[rel].get("failed_ids") else OLD_BASELINE_NOT_RUN)
                    results[rel] = {"verdict": "not_run", "said": reason, "baseline": PASSED,
                                    "patched": results[rel]["verdict"]}
        except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
            raise
        except (OSError, RepoRefused, CopyNotRun) as exc:
            results[rel] = {"verdict": "not_run", "said": str(exc)}
    return results


# COPY: reason for old tests omitted by the replay's file cap (A1 amendment B).
OLD_TEST_LIMIT = "the limit of {n} old test file(s) was reached"


def probe_repo(scratch: Path) -> Path:
    """A one-test git repository in ``scratch`` for the census probe: nothing of the owner's is read. Its test file is
    untracked, which the run does not mind."""
    repo = Path(scratch) / "probe"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_probe.py").write_text("def test_probe():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    _git(repo, "init", "-q")
    return repo
