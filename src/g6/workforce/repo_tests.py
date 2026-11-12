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

import ast
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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from xml.etree import ElementTree

from g6.core import component_process, confine, git_env, ledger
from g6.core.errors import ISOLATION_UNAVAILABLE
# A path git names is a ``GitName``, put into text only by ``shown_name`` (re-exported here, where it always was)
from g6.core.git_name import _ESCAPED, _UNESCAPED, GitName, GitNameAsText, _octal, shown, shown_name, unquoted_name  # noqa: F401
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
#: The same ruling's reasoning: a fatal signal ended the run before its tallies were whole. It cannot get a patch accepted, so it is
#: no sign of tampering (pydantic-14065: a recursion test the patch never touched segfaulted, and a correct patch was called that).
CRASHED = "crashed"
#: What the interpreter's dump says of a crash. Test output is untrusted, so only plain words and a file's own name are kept.
_CRASH_SIGNAL = re.compile(r"(?:Fatal Python error|Windows fatal exception): ([\w ]{1,60})")
_CRASH_FRAME = re.compile(r'File "[^"\n]*?([\w.-]+\.py)", line \d+ in (test\w{0,120})')
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
    "index_entry": "the repository's own index lists {value!r}, a path git never stages itself (outside the "
                   "repository, absolute, in its .git folder, or beyond a link), so G6 read nothing it names and "
                   "checked nothing; rebuild the index (git reset) and stage the change again",
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
    "crashed": "the test process crashed{where}, so this run gave no result",
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
#: checkout marking the entries of the private index ``_private`` builds. Its first part stands for git itself, found
#: when the command runs through ``git_env.git_program`` (on PATH, never the current folder: Windows looks there
#: first for a bare name, so a project could ship its own git.exe; the security review of W1-W5, F1).
_GIT_PROGRAM = object()
_GIT = (_GIT_PROGRAM, "--no-optional-locks", "--literal-pathspecs", "--no-replace-objects", "-c", "core.quotepath=off",
        "-c", "diff.interHunkContext=0", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", "core.sparseCheckout=false")


def _carried(value: bytes) -> bool:
    """Whether a command line carries ``value`` to git here byte for byte: anywhere a system passes bytes; on Windows, which
    passes UTF-16, only UTF-8 (git for Windows reads a lone surrogate on its command line as U+FFFD, so a name's WTF-8
    bytes, and bytes that are not UTF-8, would ask it of another name)."""
    if os.name != "nt":
        return True
    try:
        value.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _argument(value: str | bytes) -> str | bytes:
    """One argument for git: text as it is; bytes (a name's, ``GitName.raw``, given by an explicit call) as they are where
    the system passes bytes, and on Windows as the UTF-8 they are, or a refusal where no command line can carry them to git
    there (``_carried``): git would be asked of another name."""
    if not isinstance(value, bytes) or os.name != "nt":
        return value
    if not _carried(value):
        named = shown_name(value.decode("utf-8", "surrogateescape"))
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", f"git here cannot be given the name {named} on its command line, so "
                                                     "G6 did not ask it about that file"[:200])
    return value.decode("utf-8")


def _git(repo: Path, *args: str | bytes, ok: tuple[int, ...] = (0,), timeout_s: float = GIT_TIMEOUT_S,
         index: Path | None = None) -> bytes:
    """One read-only git command in ``repo`` (``_GIT``'s options first), in ``git_env.safe_git_env``'s clean
    environment (none of git's own redirections such as GIT_DIR, no transport, no lazy fetch) with every config key
    that would start a program switched off, the drivers any config defines included (one ``git_env.DRIVER_QUERY``
    first, in the same window), and a ``status`` or ``diff`` never entering a submodule (``git_env.safe_git_args``):
    the repository's .git/config may be the agent's (conformance K1, review B2-C1 and B2-C2). ``index``: a private
    index file G6 made (``_private``) in place of the repository's own. A name is given as its bytes (``GitName.raw``,
    ``_argument``), never as text."""
    if find_program("git") is None:
        raise RepoRefused("G6_E_GIT_MISSING_BINARY", SAID["no_git"])
    args = tuple(_argument(arg) for arg in args)
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
                command = [git_env.git_program() if part is _GIT_PROGRAM else part for part in _GIT]
                code = component_process.run_process([*command, "-C", str(repo), *git_env.safe_git_args(*args)],
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
    and ``ls-files --others`` lists every file the base tree does not hold. ``_DIFF`` is never run alone: git's own listing
    of the tracked files it cannot see (``_UNSEEN``) is read after it with the same index, and a file it names that the diff
    left out refuses (``_left_none_out``; repair 1's open item A)."""
    with tempfile.TemporaryDirectory(prefix="g6-index-") as scratch:
        index = Path(scratch) / "index"
        _git(repo, "read-tree", *([commit] if commit else ["--empty"]), index=index)   # no commit yet: empty
        found = [_git(repo, *command, index=index) for command in commands]
        if _DIFF in commands:
            _left_none_out(found[commands.index(_DIFF)], _git(repo, *_UNSEEN, index=index), len(commit) or 40)
        return found


def _tracked_new(repo: Path, commit: str, spec: list[str] | tuple[str, ...] = ()) -> set[str]:
    """The files the repository's own index tracks that ``commit`` does not hold, gitlinks aside, present in the
    working tree: staged new files, and among them a file force-added (``git add -f``) under a path git ignores, which
    ``ls-files --others --exclude-standard`` against the private index leaves out as ignored and ``ignored_inputs``
    leaves out as tracked (the security review of W1-W5, F4). Only ever added to what the private index lists, so an
    entry written into the index can add a file to the change, never take one out; one naming a path git never stages
    refuses the change (``_index_path``)."""
    linked: dict[Path, bool] = {}
    staged = {_index_path(repo, rel, linked)
              for meta, rel in _entries(_git(repo, "ls-files", "--stage", "-z", *spec)) if not meta.startswith("160000 ")}
    base = set(_paths(_git(repo, "ls-tree", "-r", "-z", "--name-only", commit, *spec))) if commit else set()
    present = set()
    for rel in staged - base:       # looked for where git looks (``_on_disk``); one G6 cannot look for so is kept, to refuse
        on_disk, exact = _on_disk(rel)
        if rel and (not exact or os.path.lexists(repo / on_disk)):
            present.add(rel)
    return present


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
    linked: dict[Path, bool] = {}
    paths = {record: _index_path(repo, GitName(record.partition(b"\t")[2]), linked) for record in records}
    folders = _git(repo, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir").decode(
        "utf-8", "surrogateescape").splitlines()
    modules = any(_holds_anything(Path(folder) / "modules") for folder in folders if folder)
    set_up = [record for record, rel in paths.items() if modules or os.path.lexists(repo / _on_disk(rel)[0] / ".git")]
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
    for meta, rel in _entries(listing):
        if meta.startswith("160000 "):
            found[rel] = meta.split()[field]
    return found


def _paths(raw: bytes) -> list[GitName]:
    """The paths of a git listing that names paths alone (``-z``: each ended by a NUL), each a ``GitName`` of git's own
    bytes: the listing's reader, where the type is made."""
    return [GitName(path) for path in raw.split(b"\0") if path]


def _entries(raw: bytes) -> list[tuple[str, GitName]]:
    """The records of a git listing whose path follows git's own fields and a tab (``ls-files --stage``, ``ls-tree``), each
    those fields as text and the path, a ``GitName`` of git's own bytes: the path is never read out of text."""
    found = []
    for record in raw.split(b"\0"):
        if record:
            meta, _, path = record.partition(b"\t")
            found.append((meta.decode("utf-8", "surrogateescape"), GitName(path)))
    return found


def _tagged(raw: bytes) -> list[tuple[str, GitName]]:
    """The records of ``ls-files -v -z``: each its one-letter tag and the path after the tag and a space, a ``GitName`` of
    git's own bytes, read at those places as the listing has always been read."""
    return [(record[:1].decode("utf-8", "surrogateescape"), GitName(record[2:])) for record in raw.split(b"\0") if record]


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
        return SAID["outside"].format(what=what, value=shown(value) if isinstance(value, str) else value)
    head = value.partition("::")[0].replace("\\", "/")
    if (not head.strip() or value.startswith(("-", ":", "@")) or head.startswith("/") or re.match(r"[A-Za-z]:", head)
            or ".." in PurePosixPath(head).parts):
        return SAID["outside"].format(what=what, value=shown(value))
    return ""


#: The parts git's own path rule never stages (an empty part is a leading, doubled or trailing slash).
_UNSTAGED_PARTS = frozenset({"", ".", "..", ".git"})
#: What Windows reads as the .git folder, after it drops a name's trailing dots and spaces and its ``:`` stream:
#: ``.git`` and its short name (git's core.protectNTFS rule, on by default everywhere).
_NTFS_GIT = frozenset({".git", "git~1"})


def _index_path(repo: Path, rel: str, linked: dict[Path, bool] | None = None) -> str:
    """``rel``, a path the repository's own index lists, or a refusal (``G6_E_GIT_REPO_INVALID``) when it is one git
    never stages itself: with a control character, a drive, or an empty, ``.``, ``..`` or ``.git`` part (any case, and
    as Windows reads a name: ``.git.``, ``.git ``, ``GIT~1``, ``.git::$INDEX_ALLOCATION``), or beyond a link or junction
    in the working tree (``cfg/config`` when ``cfg`` links to .git). Such an entry is written into the index by hand,
    and would make G6 read, hash or show a file outside the repository or the repository's git config (the fresh
    reviews of the F1-F8 fix pass, finding 1, and of the second fix pass, I2). Names that only look like options
    (``-x``, ``@x``) are git's to stage and stay files. ``linked``: the folders already looked at, kept by a caller
    that checks a whole listing."""
    parts = rel.replace("\\", "/").split("/")
    lower = {part.lower() for part in parts}
    windows = {part.partition(":")[0].rstrip(". ") for part in lower}
    if (not rel or _control(rel) or re.match(r"[A-Za-z]:", rel) or lower & _UNSTAGED_PARTS
            or windows & _NTFS_GIT):
        raise RepoRefused("G6_E_GIT_REPO_INVALID", SAID["index_entry"].format(value=shown_name(rel)))
    linked = {} if linked is None else linked
    disk = _on_disk(rel)[0].replace("\\", "/").split("/")    # each folder looked at where git looks (``_on_disk``)
    for end in range(1, len(parts)):   # git never stages a path beyond a link
        folder = repo.joinpath(*disk[:end])
        if folder not in linked:
            linked[folder] = _linked(folder)
        if linked[folder]:
            raise RepoRefused("G6_E_GIT_REPO_INVALID", SAID["index_entry"].format(value=shown_name(rel)))
    return rel


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
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["outside"].format(what=what, value=shown(value)))
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
    files = tuple(sorted((set(_paths(diffed)) | set(_paths(others)) | _tracked_new(repo, commit, spec) | set(held))
                         - set(unseen)))
    tests = [rel for rel in files if is_test_file(rel)]
    before = _texts_listed(repo, commit, tests)
    reader = read_file or (lambda path: path.read_text(encoding="utf-8", errors="replace"))
    outside = tuple(rel for rel in files if links_outside(repo, rel))
    disk = {rel: _disk(repo, rel) for rel in files}    # each read where git looks (``_on_disk``), or never
    after = {rel: reader(disk[rel]) for rel in tests   # a caller's own ``read_file`` judges a link itself
             if (read_file is not None or rel not in outside) and disk[rel] is not None and disk[rel].is_file()}
    sources = tuple(rel for rel in files if rel.endswith(".py") and not is_test_file(rel)
                    and PurePosixPath(rel).name != "conftest.py" and rel not in outside and disk[rel] is not None
                    and disk[rel].is_file())
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
    a file in its own process either (review M1). Each folder is looked at where git looks (``_on_disk``), and inside is
    ``_within``'s, the one containment rule (repair 3, item 3)."""
    on_disk = _on_disk(rel)[0]
    parts = PurePosixPath(on_disk).parts
    if not any(_linked(repo.joinpath(*parts[:end])) for end in range(1, len(parts) + 1)):
        return False
    try:
        return not _within(repo.resolve(), (repo / on_disk).resolve())
    except (OSError, RuntimeError):
        return True


def _held(repo: Path, commit: str, spec: list[str], gitlinks: set[str]) -> tuple[set[str], tuple[str, ...]]:
    """The tracked files git's assume-unchanged or skip-worktree mark (``ls-files -v``: a lowercase tag, or "S") hides
    from its own diff, compared by content with ``commit``'s blob as every other tracked file is (review C2): those
    that differ, or that ``commit`` does not hold, or that are a link; and, apart, those not in the working tree (a
    sparse checkout), which cannot be compared. Change detection never depends on git's index flags."""
    linked: dict[Path, bool] = {}
    marked = [_index_path(repo, rel, linked) for tag, rel in _tagged(_git(repo, "ls-files", "-v", "-z", *spec))
              if (tag[:1].islower() or tag[:1] == "S") and rel not in gitlinks]
    if not marked:
        return set(), ()
    unseen = tuple(sorted(rel for rel in marked if not os.path.lexists(repo / _on_disk(rel)[0])))
    present = [rel for rel in marked if rel not in unseen]
    then = texts_at(repo, commit, present, limit=None)
    changed = set()
    for rel in present:   # a link is changed, and never read (review M1); one G6 cannot open by its name is changed
        path = _disk(repo, rel)
        if (then.get(rel) is None or path is None or links_outside(repo, rel) or _linked(path) or not path.is_file()
                or _text(path.read_bytes()) != then[rel]):
            changed.add(rel)
    return changed, unseen


def _moves(repo: Path, commit: str, gone: list[str], new: list[str], before: dict[str, str],
           after: dict[str, str]) -> dict[str, str]:
    """Test files moved since the base, old path to new: as git pairs them (``-M``: a move git can see, such as
    ``git mv``, with the text unchanged or mostly so), then a test file deleted whose text, unchanged, is now at a new
    untracked path (a move on the disk alone, which git's diff cannot see)."""
    named = [_raw(rel) for rel in (*gone, *new) if _carried(_raw(rel))]   # one git cannot be given pairs by its text below
    tokens = _paths(_git(repo, "diff", "-M", "--name-status", "-z", "--no-textconv", commit, "--", *named)) if named else []
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
    before = _texts_listed(change.repo, change.base_commit, related)
    disk = {rel: _disk(change.repo, rel) for rel in related}   # each read where git looks (``_on_disk``), or never
    after = {rel: disk[rel].read_text(encoding="utf-8", errors="replace") for rel in related
             if rel not in change.outside_links and disk[rel] is not None and disk[rel].is_file()}
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
                        ("diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", "--", _raw(rel)))
    text = diffed.decode("utf-8", "replace")
    if not text.strip():  # untracked: new since the base
        path = _disk(change.repo, rel)     # a source is one G6 could open by its name (``change``)
        return set(range(1, len(path.read_text(encoding="utf-8", errors="replace").splitlines()) + 1)) if path else set()
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
             if (path := _disk(change.repo, rel)) is not None and path.is_file()]
    ordered = [rel for rel in found if rel in change.files] + [rel for rel in found if rel not in change.files]
    return ordered[:limit], max(0, len(ordered) - limit)


#: The most of one untracked file ``diff_text`` reads.
DIFF_FILE_CHARS = 200_000


def _at_newlines(text: str) -> list[str]:
    """``text``'s lines, cut at newlines and nowhere else, as git cuts a tracked file's. ``str.splitlines`` also cuts at
    characters Python ends no line at, such as a form feed or U+2028, so a comment would read as code, and two files that run
    differently as one patch. On text with none of those characters, the two give the same lines."""
    lines = text.split("\n")
    return lines[:-1] if not lines[-1] else lines


#: A byte that is not UTF-8, as ``_decoded`` first reads it.
_NOT_UTF8 = re.compile("[\udc80-\udcff]")
#: A file's mode in git, and the mode git gives one a side does not hold.
LINK_MODE, NO_MODE = "120000", "000000"


def _decoded(raw: bytes) -> str:
    """``raw`` as text, as read: each byte that is not UTF-8 one U+FFFD of its own, and every line ending as it stands."""
    return _NOT_UTF8.sub("�", raw.decode("utf-8", "surrogateescape"))


def _normal(text: str) -> str:
    """``text`` with the line endings ``Path.read_text`` gives (``_text``): each CRLF, and each CR alone, one newline."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _cut_lines(text: str, limit: int) -> str:
    """``text`` cut to its first ``limit`` characters, each line then ending in a newline: a new file as the patch shows it."""
    return "".join(f"{line}\n" for line in _at_newlines(text[:limit]))


@dataclass(frozen=True)
class FileRead:
    """One file a patch holds, as ``diff_read`` found it: its path; its text now as read (``_decoded``: no line ending changed
    and nothing cut; None where the file is gone); ``digest``, the SHA-256 of its bytes, or of the path a link names, where
    the text the patch shows of it is not that (``""`` where it is; ``now_line`` names it, a new link's always); whether it
    is new since the base, which then holds no text of it;
    whether G6 left it unread, as it leads out of the repository, and G6 never reads such a file in its own process (review
    M1); how much of its text the patch shows, with the line endings ``_normal`` gives (``limit``; None: all of it); its
    mode at the base and now, as git's listing gives them (``""`` where it gives none); ``base``, the object name of its
    blob at the base, as git's listing gives it (``""`` where the base holds none); ``old``, that blob's bytes as this
    same read read them by that name (None where the base holds none or G6 left the file unread; the refusal itself where
    git would not give them, so a copy made from this read refuses too and never shows a base it could not read); and
    ``refusal``, where git lists the file as there and G6 cannot open it by the name git gives it, the refusal a copy made
    from this read raises, so it is never shown as gone (review-maskfu2, finding 3)."""

    rel: str
    new: str | None
    digest: str = ""
    added: bool = False
    unread: bool = False
    limit: int | None = None
    modes: tuple[str, str] = ("", "")
    base: str = ""
    old: bytes | RepoRefused | None = None
    refusal: RepoRefused | None = None


@dataclass(frozen=True)
class DiffRead:
    """One read of a patch. ``text`` is what the patch is known by (``diff_text``), and ``files`` is each file it holds, as
    that same read found it, so what a reader is shown of the patch can be made from the files themselves, the bytes
    ``text`` names, with no second read that could differ from the first."""

    text: str
    files: tuple[FileRead, ...]


def _part(raw: bytes, shown: str) -> str:
    """The SHA-256 of ``raw`` where ``shown``, the text the patch gives of it, is not ``raw`` byte for byte, else ``""``."""
    return "" if shown.encode("utf-8") == raw else hashlib.sha256(raw).hexdigest()


def _part_line(what: str, digest: str) -> str:
    """The line that says ``what`` shows here only in part and names the ``digest`` of its bytes (``_part``), else ``""``."""
    return f"\\ G6: {what} shows here only in part; its bytes have SHA-256 {digest}\n" if digest else ""


def _bytes_line(raw: bytes, shown: str, what: str) -> str:
    """A line for the reader where ``shown``, the text the patch gives of ``raw``, is not ``raw`` byte for byte, naming the
    digest of its bytes, else ``""``. The text leaves out bytes that are not UTF-8 (all read as one mark), line endings and a
    last newline (as ``_text`` reads them) and anything past ``DIFF_FILE_CHARS``, so without this line two files that run
    differently would read as one patch, and the patch's text is what a review of it is held to. With it, two patches with one
    text hold the same bytes. The line starts as git's "\\ No newline at end of file" does, so no reader takes it for code."""
    return _part_line(what, _part(raw, shown))


#: Why a file git lists as there is not read (``diff_read``'s ``found``): its name is one git reads, on Windows, as another
#: file, by git's own fallback for bytes that are not UTF-8, which G6 does not copy; or nothing is at its name.
CANNOT_OPEN = "is listed by git under bytes git reads here as another file's name, which G6 cannot open"
NOT_THERE = "is listed by git as there, and G6 finds no file at that name to read"
#: Why a thing git lists as there that is no file is not read: a folder git lists whole and never looks into (it holds a
#: repository of its own, or a ``.git`` file), a submodule's folder, a pipe. What it holds is in no patch, so it is named
#: and the copy refuses, as for ``NOT_THERE``: it is never shown as nothing (review revmaskfu2a, finding 1).
NOT_A_FILE = ("is listed by git as there and is not a file (a folder git lists whole and does not look into, or a thing of "
              "another kind), so what it holds is not in this patch")
#: Why a patch whose listing holds such a name is not read at all (``diff_read``, by ``_as_named``): as there (untracked,
#: added or changed), an open of the name reaches another file or a device, and never what is stored under it; as
#: removed, something is stored under the name itself (``_stored``), which git, looking through the same rewriting, did
#: not see (review revmaskfu2a, finding 2).
READ_AS_ANOTHER = "is listed by git under a name Windows reads as another's, which G6 does not open, so the patch is not read"


def _link_line(name: str, digest: str) -> str:
    """The line naming the ``digest`` of the path the link ``name`` names as it stands."""
    return f"\\ G6: the link {name} as it stands names a path with SHA-256 {digest}\n"


def now_line(one: FileRead, name: str) -> str:
    """The line ``one`` is followed by: the digest of its bytes as they stand where the text shown of them is not them, and a
    new link's path, always (``one.digest``), with ``name`` its name as shown. One rule for what the patch is known by
    (``diff_read``: ``name`` as ``shown_name`` gives it) and for what the reviewer is shown (the copy: ``name`` as sent)."""
    return _link_line(name, one.digest) if one.added and one.modes[1] == LINK_MODE else _part_line(name, one.digest)


def name_line(rel: str) -> str:
    """The line naming the bytes of a name ``shown_name`` quotes, else ``""``. No text G6 writes holds it any more, as the
    quoting itself names each byte; it is kept for callers of this module outside those texts."""
    shown = shown_name(rel)
    return _bytes_line(rel.encode("utf-8", "surrogateescape"), shown, f"the name {shown}")


#: The diff ``diff_read`` asks git for: its listing of the tracked files that changed, one record each (``--raw -z``, so a
#: name is never quoted, and ``--no-abbrev``, so each object name is whole), then the patch itself, in one run over one read
#: of each file. ``--text``: an attribute never hides a file's lines; with ``--no-renames`` a moved file is a file gone and a
#: file added; and the sides are ``a/`` and ``b/`` whatever the repository's ``diff.noprefix`` or ``diff.mnemonicPrefix``
#: says, so each header is the one ``_named_patch`` checks against the listing.
_DIFF = ("diff", "--patch-with-raw", "-z", "--no-abbrev", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv",
         "--text", "--src-prefix=a/", "--dst-prefix=b/")
#: git's own listing of the tracked files it cannot see, read with the private index ``_DIFF`` runs with: ``--deleted``
#: names each one git's ``lstat`` fails on (a file gone, or a path git cannot use here, such as one too long for it), and
#: ``--stage -t`` marks it ``R`` after its ``H`` record, with its mode, so a gitlink, which ``_DIFF`` never enters, is told
#: apart. git's diff skips such a file, says so on its error output alone and ends well (``_left_none_out``).
_UNSEEN = ("ls-files", "--deleted", "--stage", "-t", "-z")
#: What a record of ``_DIFF`` may hold, by git's documented raw format: a mode git writes, and, with ``--no-renames``, an
#: added, deleted, modified or retyped file. Anything else (an unmerged file, a status git reserves) refuses the read.
_RAW_MODES = frozenset({NO_MODE, "100644", "100755", LINK_MODE, "160000"})
_RAW_STATUSES = frozenset("ADMT")


def _listing(output: bytes, length: int = 40) -> tuple[list[tuple[str, str, str, str]], bytes]:
    """The records ``_DIFF`` gives first, each (path, mode at the base, mode now, object name at the base, ``""`` where the
    base holds none), and the patch after them, one NUL past the last record. Each record is read by git's own format: a
    colon, two modes of ``_RAW_MODES``, two object names of ``length`` hexadecimal digits (the repository's hash, which
    ``diff_read`` takes from its base commit; SHA-1's where none is given), a status of
    ``_RAW_STATUSES``, then a path that is not empty. A record of any other shape refuses the read, as G6 would not know
    what the patch holds; so does a patch whose files git did not list."""
    found, at, hexes = [], 0, re.compile(rf"[0-9a-f]{{{length}}}")
    try:
        while output.startswith(b":", at):
            meta = output.index(b"\0", at)
            end = output.index(b"\0", meta + 1)
            fields = output[at + 1:meta].decode("ascii").split(" ")
            if len(fields) != 5:
                raise ValueError(f"a record of {len(fields)} fields")
            old, now, before, _, status = fields
            path = GitName(output[meta + 1:end])    # the name, of git's own bytes: never read out of text
            if old not in _RAW_MODES or now not in _RAW_MODES:
                raise ValueError(f"a mode git does not write ({old} {now})")
            if not all(hexes.fullmatch(name) for name in fields[2:4]):
                raise ValueError("an object name that is not the repository's")
            if status not in _RAW_STATUSES:
                raise ValueError(f"a status this read cannot hold ({status})")
            if not path:
                raise ValueError("a record with no path")
            found.append((path, old, now, "" if old == NO_MODE else before))
            at = end + 1
    except (ValueError, UnicodeDecodeError) as exc:
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", f"git diff gave a listing G6 cannot read ({exc})"[:200]) from exc
    if found and not output.startswith(b"\0", at):    # each record is exactly one path; then one NUL ends the listing
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", "git diff gave a listing G6 cannot read (a record with more than one path)")
    patch = output[at + 1:] if found else output[at:]
    if patch and (not found or not patch.startswith(b"diff --git ")):    # a patch its listing does not account for
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", "git diff gave a patch that its listing does not account for")
    return found, patch


#: The bytes git's C-style quoting (``quote_c_style``) writes by a letter.
_GIT_NAMED = {7: b"\\a", 8: b"\\b", 9: b"\\t", 10: b"\\n", 11: b"\\v", 12: b"\\f", 13: b"\\r", 34: b'\\"', 92: b"\\\\"}


def _git_quoted(raw: bytes) -> bytes:
    """``raw``, a path with its ``a/`` or ``b/``, as git's patch writes it with ``core.quotePath`` off: bare, unless it holds
    a byte below a space, a quote, a backslash or DEL; then in double quotes, each such byte escaped (by a letter where git's
    table has one, else in octal) and every other byte, those from 0x80 too, as it is. What ``_named_patch`` checks git's own
    headers against, so a header is the listed file's or the read refuses; never what a reader is shown (``shown_name``)."""
    if not any(byte < 0x20 or byte in (0x22, 0x5C, 0x7F) for byte in raw):
        return raw
    return b'"' + b"".join(_GIT_NAMED.get(byte) or (b"\\%03o" % byte if byte < 0x20 or byte == 0x7F else bytes([byte]))
                           for byte in raw) + b'"'


#: The lines git writes between a file's ``diff --git`` line and its ``---`` line, with ``--no-renames`` and ``--text``.
_EXTENDED = (b"old mode ", b"new mode ", b"deleted file mode ", b"new file mode ", b"index ")


def _named_patch(records: list[tuple[str, str, str, str]], patch: bytes) -> str:
    """git's patch (``_DIFF``) as what the patch is known by: each line as git wrote it, decoded as read (the line naming the
    patch's bytes covers what decoding leaves out), but each header git writes for a file its listing names, which names
    that file from the listing's own bytes through the one rule (``shown_name``), the rule the copy names it by
    (review-maskfu2, finding 1). A header is the file's only where it is, byte for byte, the one git's quoting writes for the
    listed name (``_git_quoted``; its ``---`` and ``+++`` lines next, with git's tab after a name holding a space). A line of
    any other shape is left as it was read, as before: no git writes it for a listed file, and the copy is made from the
    files, never from this text. The headers are lines no hunk holds (each of a hunk's lines starts with its mark), and the
    lines are cut at newlines alone."""
    heads = {b"diff --git " + _git_quoted(b"a/" + raw) + b" " + _git_quoted(b"b/" + raw): (rel, raw)
             for rel, raw in ((rel, rel.encode("utf-8", "surrogateescape")) for rel, *_ in records)}
    lines, out, at = patch.split(b"\n"), [], 0
    while at < len(lines):
        if lines[at] not in heads:
            out.append(lines[at].decode("utf-8", "replace"))
            at += 1
            continue
        rel, raw = heads[lines[at]]
        out.append(f"diff --git {shown_name(rel, 'a/')} {shown_name(rel, 'b/')}")
        at += 1
        while at < len(lines) and lines[at].startswith(_EXTENDED):
            out.append(lines[at].decode("utf-8", "replace"))
            at += 1
        for mark, prefix in ((b"--- ", "a/"), (b"+++ ", "b/")):
            side = _git_quoted(prefix.encode() + raw)
            if at < len(lines) and lines[at] == mark + side + (b"\t" if b" " in side else b""):
                out.append(f"{mark.decode()}{shown_name(rel, prefix)}")
            elif at < len(lines) and lines[at] == mark + b"/dev/null":
                out.append(f"{mark.decode()}/dev/null")
            else:
                break
            at += 1
    return "\n".join(out)


def _left_none_out(diffed: bytes, unseen: bytes, length: int) -> None:
    """A refusal (``G6_E_GIT_COMMAND_FAILED``) where git's diff (``diffed``, ``_DIFF``'s output) left out a tracked file git
    cannot see. ``unseen`` is ``_UNSEEN``'s records, git's ``ls-files -t --stage`` form (``H`` for each entry, then ``R``
    for each one git's ``lstat`` fails on); each file it marks ``R``, but a gitlink (``_DIFF`` enters none), must be in the
    diff's listing (a file gone is, as removed). A record of any other shape refuses too: what git said is not known."""
    listed = {path for path, *_ in _listing(diffed, length)[0]}
    left = []
    for record in unseen.split(b"\0"):
        if not record:
            continue
        found = re.fullmatch(rb"([HR]) ([0-7]{6}) [0-9a-f]+ [0-3]\t(.+)", record, re.S)
        if found is None:
            raise RepoRefused("G6_E_GIT_COMMAND_FAILED", "git ls-files gave a listing G6 cannot read")
        rel = GitName(found[3])
        if found[1] == b"R" and found[2] != b"160000" and rel not in listed:
            left.append(rel)
    if left:
        named = ", ".join(shown_name(rel) for rel in left[:3]) + (" and more" if len(left) > 3 else "")
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", (f"git could not read {named}, so its diff leaves them out (a path too "
                                                      "long for git here, or one it may not enter)")[:200])

def blob_bytes(repo: Path, name: str, *, timeout_s: float = GIT_TIMEOUT_S) -> bytes:
    """The bytes of the blob git names ``name``, read by git from its own store by that name (``cat-file``): no path is
    looked up and no link followed, so a name git listed is read as git keeps it. A reader is shown them through
    ``_decoded``, the one decoder."""
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", name):
        raise RepoRefused("G6_E_GIT_COMMAND_FAILED", "an object name that is not git's")
    return _git(repo, "cat-file", "blob", name, timeout_s=timeout_s)


def _where(fd: int) -> Path:
    """Where the file G6 holds open as ``fd`` is, as the operating system says of the open file itself, not of a path. On
    Windows that is its final path exactly as the system gives it, in the extended form (``\\\\?\\``), which Win32 reads
    literally: a lookup made by it is never normalised (a trailing dot or space dropped, a device name read as the device), so
    it names the folder held and no other (review-maskfu3, finding 1)."""
    if os.name == "nt":
        import ctypes
        import msvcrt

        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetFinalPathNameByHandleW(ctypes.c_void_p(msvcrt.get_osfhandle(fd)), buffer, 32768, 0)
        if not 0 < size < 32768:
            raise OSError("the system could not say where an open file is")
        return Path(buffer.value)
    if os.path.isdir("/proc/self/fd"):
        return Path(os.readlink(f"/proc/self/fd/{fd}"))
    import fcntl

    return Path(fcntl.fcntl(fd, 50, b"\0" * 1024).split(b"\0", 1)[0].decode("utf-8", "surrogateescape"))  # F_GETPATH


_LONG, _LONG_UNC = "\\\\?\\", "\\\\?\\UNC\\"   # the prefixes of the extended form


def _extended(path: Path) -> Path:
    """``path``, a full path, in the form ``_where`` gives one: on Windows the extended form, ``\\\\?\\`` before a drive and
    ``\\\\?\\UNC\\`` before a share (as it stands where it has it already); elsewhere ``path`` itself. The characters of each
    part are kept as they are, so a path and its extended form name the same parts."""
    if os.name != "nt":
        return path
    text = str(path)
    if text.startswith(_LONG):
        return path
    return Path(_LONG_UNC + text[2:] if text.startswith("\\\\") else _LONG + text)


def _within(root: Path, where: Path) -> bool:
    """Whether ``where``, a path the system gave for what G6 looked at or holds, lies in ``root``, the repository's top as the
    system gives it: both in the one form a held file's final path has (``_extended``), part by part, each as the system
    writes it, with no case folded. The system writes a folder's name as it is stored, so one folder is always one text and
    two folders two, under a folder whose names may differ by case alone (a case-sensitive NTFS folder) as under any other.
    ``PurePath.is_relative_to`` folds case on Windows, which let a sibling ``Repo`` pass as inside ``repo`` (review-maskfu2,
    finding 4)."""
    root, where = _extended(root), _extended(where)
    return where.parts[:len(root.parts)] == root.parts


#: A made-up folder on a drive, below which ``_as_named`` asks Windows how it reads a name: no disk is looked at.
_BELOW = "C:\\g6\\"


def _as_named(name: str) -> bool:
    """Whether Windows, given ``name`` (a path of a tree as ``_on_disk`` decodes it, its parts joined by ``/``) below a
    folder, opens what is stored under that name. Win32 rewrites a path before it opens it: it drops the dots and spaces a
    path ends in and the one dot a folder's name ends in, and reads ``nul`` (older versions: every device's name, with an
    extension too) as the device. So a file stored as ``x.py.`` or ``nul`` (made by its literal name, or by another
    system) is listed by git, and a plain open of the listed name reaches ``x.py``, or the device (review-maskfu4, findings
    1 and 2). The question is put to Windows itself (``GetFullPathNameW``, the rewriting every such open makes; Microsoft's
    "File path formats on Windows systems", under path normalization), never to a list kept here, as what it rewrites
    differs between its versions: the name stands below a made-up folder, each part as it is, and is read as itself where
    the rewriting gives that path back (its length counted as Windows counts it, in UTF-16 units, and a buffer it calls
    too small given again at the size it asks for). Where Windows cannot say, it is not. A name Windows cannot store at all (a control
    character, ``< > " | ? *``) is not rewritten: opening it fails, and the caller's own refusal covers it. Elsewhere a
    name is its bytes (True). A limit: a short (8.3) name the volume made for another file is not rewritten either, so a
    listed name that is another file's short name is read as that file."""
    if os.name != "nt":
        return True
    import ctypes

    given = _BELOW + name.replace("/", "\\")
    size = len(given) + sum(ord(char) > 0xFFFF for char in given) + 2      # in UTF-16 units, as Windows counts them
    for _ in range(3):
        buffer = ctypes.create_unicode_buffer(size)
        wrote = ctypes.windll.kernel32.GetFullPathNameW(given, size, buffer, None)
        if wrote < size:      # it fitted (0: Windows could not say)
            return wrote > 0 and buffer.value == given
        size = wrote + 1      # too small: Windows gave the size it needs, which is a question to ask again, never an answer
    return False


def _stored(root: Path, name: str) -> bool:
    """Whether anything is stored in ``root`` under ``name`` itself, a name Windows reads as another's (``_as_named``): looked
    up, never opened, by the literal (extended) form of the path, in which Win32 rewrites nothing, each part as it is.
    False only where Windows says nothing is there; where it cannot say, something may be (True). It is asked of a name
    git lists as removed: git looked for it through the rewriting, so its word says nothing of what is stored under the
    name (review revmaskfu2a, finding 2)."""
    try:
        os.lstat(str(_extended(root)) + "\\" + name.replace("/", "\\"))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def _on_disk(rel: str) -> tuple[str, bool]:
    """The name the system is given for a path git gave (``rel``, git's bytes as ``_listing`` and ``_paths`` read them),
    and whether it names the file git means: git's bytes read back as git encodes a name where it runs (review-maskfu2,
    finding 3). git for Windows writes a file's UTF-16 name as UTF-8 with a lone surrogate kept as its three bytes, so
    those bytes read back that way are the file's name. Bytes that do not read back so name, for git there, another file
    (its own fallback for them, which G6 does not copy): G6 may look at the folders on the way (``exact`` False, ``rel`` as
    given) but never opens such a file. Nor one whose name Windows reads as another's (``_as_named``; review-maskfu4):
    what is stored under it is not what an open of it reaches. Elsewhere a name is its bytes, as the system takes them
    (``os.fsdecode``)."""
    raw = _raw(rel)
    if os.name != "nt":
        return os.fsdecode(raw), True
    try:
        name = raw.decode("utf-8", "surrogatepass")
    except UnicodeDecodeError:
        return str.__str__(rel), False
    return name, _as_named(name)


def _disk(repo: Path, rel: str) -> Path | None:
    """``rel`` in ``repo`` as the system is given it (``_on_disk``), or None where those bytes name, for git there, another
    file, or Windows reads the name as another's, which G6 never opens: every path G6 opens or reads for a name git gave
    is made here (repair 3, item 3)."""
    on_disk, exact = _on_disk(rel)
    return repo / on_disk if exact else None


def _in(rel: str) -> str:
    """The name the system is given for a path of a tree: a name git listed (``GitName``) as ``_on_disk`` gives it, a
    path the scan, a caller or a model gave as it is."""
    return _on_disk(rel)[0] if isinstance(rel, GitName) else rel


def _raw(rel: str) -> bytes:
    """The bytes git is given for a name: a ``GitName``'s own (``GitName.raw``), or a path held as text (a caller's, or a
    name a test made) as the listing's reader would have read it from those bytes."""
    return rel.raw if isinstance(rel, GitName) else rel.encode("utf-8", "surrogateescape")


def _listed_at(repo: Path, commit: str, rels: Iterable[str], *, timeout_s: float = GIT_TIMEOUT_S) -> dict[str, tuple]:
    """Each of ``rels`` ``commit`` holds, name to (mode, kind, object name, size) as ``ls-tree -r -l`` gives them, the name
    matched by git's own bytes: asked by the names themselves where a command line carries them (``_carried``), else read
    from the whole tree. A text at the base is then read by its object name, which any command line carries."""
    wanted = set(rels)
    if not wanted:
        return {}
    spec = ["--", *map(_raw, wanted)] if all(_carried(_raw(rel)) for rel in wanted) else []
    found = {}
    for meta, rel in _entries(_git(repo, "ls-tree", "-r", "-l", "-z", commit, *spec, timeout_s=timeout_s)):
        if rel in wanted:
            found[rel] = tuple(meta.split())
    return found


def _texts_listed(repo: Path, commit: str, rels: Iterable[str]) -> dict[str, str]:
    """The text at ``commit`` of each of ``rels`` it holds as a blob (``_listed_at``), with the line endings
    ``Path.read_text`` gives, read by object name (``blob_bytes``)."""
    return {rel: _text(blob_bytes(repo, entry[2])) for rel, entry in sorted(_listed_at(repo, commit, rels).items())
            if entry[1] == "blob"}


def _read_inside(root: Path, path: Path) -> bytes | None:
    """The bytes of the file ``path`` opens, or None where what was opened is not a regular file inside ``root``: where a
    file is, is decided on the file G6 holds open, after it is opened, so a folder on its way swapped for a link between any
    check and the open cannot make G6 read outside the repository (M1), by the system's own path for it (``_within``)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        try:
            inside = stat.S_ISREG(os.fstat(fd).st_mode) and _within(root, _where(fd))
        except OSError:
            inside = False
        if not inside:
            return None
        with os.fdopen(os.dup(fd), "rb") as handle:
            return handle.read()
    finally:
        os.close(fd)


def _link_inside(root: Path, path: Path) -> bytes | None:
    """The path the link at ``path`` names, as bytes, or None where the link is not inside ``root``. As for a file
    (``_read_inside``), where it is, is decided on what G6 holds open while it reads: the folder the link lies in is held
    open, its own path decides, and the link is read in that folder, so a folder on its way swapped for a link between any
    check and the read cannot make G6 read a link outside the repository. Where ``readlink`` takes a folder (``dir_fd``),
    the link is read in the folder held. Windows takes none, so there it is read by the held folder's own final path, which
    names no link: the folder is held for listing, shared for reading and writing but never for being moved or removed, and
    Windows moves no folder above one held so, so that path is the folder decided on. That path is the one the decision
    compared, in the extended form the system gave (``_where``), with the link's own name after it as given: Win32 reads it
    literally, so neither the folder's name nor the link's can be read as another's (a trailing dot or space dropped, a
    device name; review-maskfu3, finding 1)."""
    if os.name != "nt":
        held = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    else:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
        create.restype = wintypes.HANDLE
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                           wintypes.DWORD, wintypes.HANDLE)
        # FILE_LIST_DIRECTORY; FILE_SHARE_READ | FILE_SHARE_WRITE, never FILE_SHARE_DELETE; OPEN_EXISTING;
        # FILE_FLAG_BACKUP_SEMANTICS, which opens a folder (a link or junction on the way is followed: its final path says)
        handle = create(str(path.parent), 0x1, 0x1 | 0x2, None, 3, 0x02000000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        held = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    try:
        try:
            folder = _where(held)
        except OSError:
            return None
        if not _within(root, folder):
            return None
        return os.fsencode(os.readlink(folder / path.name) if os.name == "nt" else os.readlink(path.name, dir_fd=held))
    finally:
        os.close(held)


def _leads_out(repo: Path, rel: str) -> bool:
    """Whether a folder on ``rel``'s way is a link or junction and the path resolves outside ``repo``, or cannot be resolved:
    what lies there is outside the repository, and G6 does not open it. ``rel`` itself being a link is not this. It is only
    the first look: what a file G6 opens is, is decided on the open file (``_read_inside``). Inside is ``_within``'s."""
    parts = PurePosixPath(rel).parts
    if not any(_linked(repo.joinpath(*parts[:end])) for end in range(1, len(parts))):
        return False
    try:
        return not _within(repo.resolve(), (repo / rel).resolve())
    except (OSError, RuntimeError):
        return True


def diff_read(change: Change) -> DiffRead:
    """The change, read once, its ``files`` exactly the files of that read: the tracked ones git's own listing names, in the
    same run as the patch (``_DIFF``), and the untracked ones the change holds that its listing names. Its ``text``, what the
    patch is known by, is one unified diff: git's own for tracked files (each file's text, never a ``textconv`` filter's),
    which holds each one's mode; then lines for each tracked file, naming the digest of its blob's bytes at the base, as
    this read read them by the object name git listed (``blob_bytes``: what a reader is shown of the base is those bytes, so
    the patch is known by them too, and a base that changes after or during the read is another patch), and the digest of
    its bytes as they stand, or of the path a link names, as git shows a file after its filters (line endings by
    ``core.autocrlf`` or ``.gitattributes``, a ``clean`` driver), so two working files that run differently could give one
    diff, and with these lines cannot; then each untracked file as added lines (at most ``DIFF_FILE_CHARS`` of it, each byte
    that is not UTF-8 one U+FFFD), so a scan of the diff sees a new test file too, each followed by a line naming its bytes,
    or the path a link names, where its text is not them (``now_line``). A link is known by the path it names, as git
    holds a link, never by what it leads to, and read where G6 decides it lies (``_link_inside``). A removed file has no
    bytes now, so no line for them. A file below a link that leads out of the repository is never read (M1): it is named,
    and has no text in ``files``. Each name G6 writes is put into the text as ``shown_name`` quotes it, so a name is one
    line of the text and two names are two texts (review-maskfu1, finding 4), git's own headers too (``_named_patch``,
    review-maskfu2, finding 1). A file is looked for where git looks (``_on_disk``), and only where git's listing says
    something is there. A listing that holds, as there, a name Windows reads as another's (a trailing dot or space,
    ``nul``: ``_as_named``) is refused whole (``READ_AS_ANOTHER``): no open there reaches what is stored under it, and what
    git says of its bytes it read through the same rewriting. One git lists as removed is read as removed, as any is."""
    # a private index built from the base tree (the review of VS-4, finding 1): git's flags, its stat cache and an
    # entry written into the repository's own index hide nothing (review C2 too)
    listed, others = _private(change.repo, change.base_commit, _DIFF, ("ls-files", "--others", "--exclude-standard", "-z"))
    tracked, diffed = _listing(listed, len(change.base_commit))
    shown = _named_patch(tracked, diffed)
    new = set(_paths(others)) | _tracked_new(change.repo, change.base_commit)   # a force-added file too (F4)
    text, files, root = (shown + _bytes_line(diffed, diffed.decode("utf-8", "replace"), "the diff of the tracked files"),
                         [], change.repo.resolve())

    def found(rel: str, added: bool, modes: tuple[str, str]) -> tuple[FileRead, bytes | None, bool]:
        """The file at ``rel`` as it stands, its bytes where G6 read them, and whether it is a link, whose bytes are the
        path it names. A file git lists as removed is never opened. One git lists as there that G6 cannot open by the name
        git gives it is named so in what the patch is known by, and a copy made of it refuses (``refusal``): it is never
        read as removed (review-maskfu2, finding 3); so is whatever git lists as there that is no file, a folder it lists
        whole among them (``NOT_A_FILE``), and a file gone between this read's look and its open: no listed name ends with
        no bytes and nothing said (review revmaskfu2a, finding 1). One listed under a name Windows reads as another's
        (``_as_named``) refuses the read itself, before anything is looked at, where git lists it as there
        (review-maskfu4, findings 1 and 2), and where git lists it as removed and something is stored under the name
        itself (``_stored``)."""
        on_disk, exact = _on_disk(rel)
        gone = not added and modes[1] == NO_MODE     # git's own word that nothing is there now
        if not exact and not _as_named(on_disk) and (not gone or _stored(root, on_disk)):
            raise RepoRefused("G6_E_GIT_COMMAND_FAILED", f"{shown_name(rel)} {READ_AS_ANOTHER}")
        path = change.repo / on_disk     # where a file is comes from this read alone, never an earlier ``Change.outside_links``
        if _leads_out(change.repo, on_disk):
            return FileRead(rel, None, added=added, unread=True, modes=modes), None, False
        if gone:
            return FileRead(rel, None, added=added, modes=modes), None, False
        if exact and path.is_symlink():     # read as the path it names: the link is never followed
            named = _link_inside(root, path)      # its bytes, shown through the one decoder as a file's are
            if named is None:
                return FileRead(rel, None, added=added, unread=True, modes=modes), None, False
            return FileRead(rel, _decoded(named), added=added, modes=modes), named, True
        if not exact or not path.is_file():      # the name whole, so the line the identity holds for it names it whole
            why = CANNOT_OPEN if not exact else NOT_A_FILE if os.path.lexists(path) else NOT_THERE
            return FileRead(rel, None, added=added, modes=modes, refusal=RepoRefused(
                "G6_E_GIT_COMMAND_FAILED", f"{shown_name(rel)} {why}")), None, False
        try:
            raw = _read_inside(root, path)
        except FileNotFoundError:      # gone between the look and the open: named, never shown as nothing
            return FileRead(rel, None, added=added, modes=modes, refusal=RepoRefused(
                "G6_E_GIT_COMMAND_FAILED", f"{shown_name(rel)} {NOT_THERE}")), None, False
        if raw is None:
            return FileRead(rel, None, added=added, unread=True, modes=modes), None, False
        return FileRead(rel, _decoded(raw), added=added, modes=modes), raw, False

    def unread(name: str) -> str:      # a file G6 did not read is still named in what the patch is known by
        return f"\\ G6: {name} is not read, as it leads out of the repository or lies below a link that does\n"

    for rel, old, now, before in sorted(tracked):
        one, raw, link = found(rel, False, (old, now))
        name = shown_name(rel)
        if one.refusal is not None:      # named, never as removed: a copy made of this read refuses
            text += f"\\ G6: {one.refusal}\n"
        if one.unread:
            text += unread(name)
        elif before:    # the base, read once, here: the copy a reader is shown is made from these bytes, never read again
            try:
                base: bytes | RepoRefused = blob_bytes(change.repo, before)
                text += f"\\ G6: the bytes of {name} at the base have SHA-256 {hashlib.sha256(base).hexdigest()}\n"
            except RepoRefused as refused:
                base = refused
                text += f"\\ G6: the bytes of {name} at the base could not be read\n"
            one = replace(one, old=base)
        one = replace(one, base=before)
        if raw is not None:
            text += _link_line(name, hashlib.sha256(raw).hexdigest()) if link else \
                f"\\ G6: the bytes of {name} as they stand have SHA-256 {hashlib.sha256(raw).hexdigest()}\n"
            one = replace(one, digest=_part(raw, _normal(one.new)))
        files.append(one)
    for rel in sorted(new & set(change.files)):
        one, raw, link = found(rel, True, ("", LINK_MODE))
        name = shown_name(rel)
        if raw is None:
            text += unread(name) if one.unread else f"\\ G6: {one.refusal}\n"      # one or the other: ``found`` has no third
            files.append(replace(one, modes=("", "")))
            continue
        shown = _cut_lines(_normal(one.new), DIFF_FILE_CHARS)
        lines = _at_newlines(shown)
        one = replace(one, digest=hashlib.sha256(raw).hexdigest() if link else _part(raw, shown), limit=DIFF_FILE_CHARS,
                      modes=("", LINK_MODE if link else ""))
        text += (f"--- /dev/null\n+++ {shown_name(rel, 'b/')}\n@@ -0,0 +1,{len(lines)} @@\n"
                 + "".join(f"+{line}\n" for line in lines) + now_line(one, name))
        files.append(one)
    return DiffRead(text, tuple(files))


def diff_text(change: Change) -> str:
    """The change as one unified diff, what a patch is known by: ``diff_read``'s ``text``."""
    return diff_read(change).text


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
    """The bytes of a file, read in blocks; a nested repository's own ``snapshot`` (without its untracked files; a folder
    git lists whole never comes here: ``snapshot`` hashes every file in it); "" when nothing is there, or for a plain
    folder, whose files git lists on their own. A file or repository that cannot be read, or a thing that is there and is
    no file, reads "unreadable" before and after alike, so a change to it is not seen."""
    try:
        component_process.checkpoint()
        if path.is_dir():
            if not (path / ".git").exists():  # never ask git from a plain folder: it would answer for the parent
                return ""
            return hashlib.sha256(json.dumps(sorted(snapshot(path, ()).items())).encode("utf-8")).hexdigest()
        if not path.is_file():      # a thing that is there and is no file (a pipe) is not read, and never reads as nothing
            return "unreadable" if os.path.lexists(path) else ""
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
    as git cannot see them, each submodule folder git does not look into (``_gitlinks``), and each folder git lists
    whole as untracked (one holding a repository of its own, or a ``.git`` file), as G6 hashes it
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
    linked: dict[Path, bool] = {}
    held = [_index_path(repo, rel, linked) for tag, rel in _tagged(marks) if tag[:1].islower() or tag[:1] == "S"]
    # The review of VS-4: what differs from HEAD's tree by content, through a private index, so no entry written into
    # the repository's own index keeps a changed file's bytes out of the snapshot (receipts bind the bytes that ran);
    # and each file the index holds that HEAD does not, a staged one, as before.
    diffed, others = _private(repo, head.decode().strip(), ("diff", "--name-only", "-z", "--no-renames",
                                                             "--no-textconv"),
                              ("ls-files", "--others", "--exclude-standard", "-z"))
    staged = (set(_paths(others)) & {rel for _, rel in _entries(index)}) | _tracked_new(
        repo, head.decode().strip())   # a file force-added under an ignored path too (F4)
    budget = {"files": 0, "bytes": 0, "until": time.monotonic() + SUBMODULE_HASH_S}
    for rel in (*_paths(_git(repo, "diff", "--name-only", "-z")), *_paths(diffed), *sorted(staged), *held,
                *(_untracked(repo) if untracked is None else untracked)):
        path = _disk(repo, rel)    # read where git looks (``_on_disk``); one G6 cannot open by its name reads as unread
        if path is not None and rel.endswith("/"):
            # a folder git lists whole and never looks into (a repository of its own, a ``.git`` file): hashed as a
            # submodule's folder is, every file in it, or said to be not hashed (review revmaskfu2a, finding 1)
            found[_SUBMODULE + rel] = _submodule(repo, rel, budget)
            continue
        found[rel] = _digest(path) if path is not None else "unreadable"
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
    bytes, take longer than ``SUBMODULE_HASH_S`` seconds, or cannot be read. The folders are looked at where git looks
    (``_on_disk``), and what the digest is of names the folder by the name git gave."""
    named, parts = PurePosixPath(rel).parts, PurePosixPath(_on_disk(rel)[0]).parts
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
                return hashlib.sha256(json.dumps(["link", "/".join(named[:end]), os.readlink(here)]).encode()).hexdigest()
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
        where = (Path(exc.filename).relative_to(repo).as_posix() if exc.filename and _within(repo, Path(exc.filename))
                 else shown_name(rel))
        return NOT_HASHED + HASH_UNREADABLE.format(path=where)
    return hashlib.sha256(json.dumps(sorted(entries)).encode("utf-8")).hexdigest()


def submodules_not_hashed(found: dict[str, str]) -> dict[str, str]:
    """The submodule folders a ``snapshot`` did not hash (``_submodule``), path to why: a change there is not seen. Each
    path is the name git gave (a ``GitName`` of the bytes the snapshot's key holds)."""
    return {GitName(_raw(key[len(_SUBMODULE):])): value[len(NOT_HASHED):] for key, value in found.items()
            if key.startswith(_SUBMODULE) and value.startswith(NOT_HASHED)}


def _moved(before: dict[str, str], after: dict[str, str]) -> list[str]:
    # PENDING copy: the name for _MARKS is a proposal (the fix pass after the review of Tasks 4-5).
    names = {_HEAD: "the commit checked out (HEAD)", _INDEX: "the staged files (the index)",
             _MARKS: "git's assume-unchanged and skip-worktree marks"}

    def name(key: str) -> str:    # what changed, as text: a name git gave by its one rule
        if key.startswith(_SUBMODULE):
            return SAID["submodule_name"].format(path=shown_name(key[len(_SUBMODULE):]))
        return names.get(key) or shown(key)

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
      would show it;
    * a fatal signal before the tallies were whole is ``crashed``, by the same reasoning: it cannot get a patch accepted. That stays
      the run's verdict even with a failure on the record, since the run did not finish and a verdict of ``failed`` could be discounted
      as a failure the base has too (the failure itself survives, per check, in ``patch_review._verdicts``). A test can choose exit
      139, so ``crashed`` is no proof of a crash, and it
      needs no more: all that test gains is insufficient evidence in place of an integrity alarm, and an exit outside the signals'
      range gains it nothing."""
    # by a signal: the process's own negative code, a shell's 128 + n, or a Windows exception's status
    killed = exit_code is not None and (exit_code < 0 or 128 < exit_code <= 192 or exit_code >= 0xC0000000)
    if moved:
        return CHANGED
    if exit_code is None:
        return "timed_out"
    if counts is None and "No module named 'pytest'" in output:
        return "pytest_not_found"
    if exit_code == 5:
        return "no_tests_collected"
    if counts is None:
        return CRASHED if killed else "collection_error" if exit_code in (2, 3, 4) else "inconsistent_report"
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
    return CRASHED if killed else "inconsistent_report"


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


def _crash_site(output: str) -> str:
    """Where a crash happened, from the interpreter's dump: the signal, and the test that was running."""
    signal = _CRASH_SIGNAL.search(output)
    frame = _CRASH_FRAME.search(output, signal.end() if signal else 0)
    parts = [*([signal[1].strip()] if signal else []), *([f"in {frame[2]} in {frame[1]}"] if frame else [])]
    return f" ({', '.join(parts)})" if parts else ""


def _said(verdict: str, *, seconds: int, python: str, output: str = "") -> str:
    if verdict in RAN:
        return ""
    if verdict == CRASHED:
        return SAID["crashed"].format(where=_crash_site(output))
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


def _test_id(repo: Path, test: str, what: str = "test") -> str:
    """A test (or, ``what``, a module) as pytest, which opens it, is given it: a file git listed (``GitName``) as the system
    is given it (``_on_disk``), its shape judged on the name and inside by ``_within``, the one containment rule (repair 3,
    item 3); a caller's id as ``inside`` accepts it."""
    if not isinstance(test, GitName):
        return inside(repo, test, what)
    problem = shape_problem(test, what)
    if problem:
        raise RepoRefused("G6_E_INPUT_VALIDATION", problem)
    on_disk = _on_disk(test)[0]
    if not _within(repo.resolve(), (repo / on_disk).resolve()):
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["outside"].format(what=what, value=shown_name(test)))
    return on_disk


def _run(repo: Path, tests: list[str], *, roots: tuple[str, ...], timeout_s: int, network: bool,
         mutant: tuple[str, str] | None = None, copied: bool = False, python: str = "",
         scratch_dir: Path | None = None, deadline: float | None = None, receipt: bool = False,
         require_isolation: bool = False, links: dict | None = None, observation: dict | None = None) -> dict:
    """The shared execution path; only old-test replay uses a copy without git metadata. ``require_isolation``: a run
    no backend confines is refused (``G6_E_ISOLATION_UNAVAILABLE``) before it starts, even where the person allowed
    unconfined runs (verification search's proposed checks). ``links``: what the copy (``repository_copy``'s
    ``report``) left out or copied in a link's place, added to the result as it is. ``observation`` selects G6's
    isolated scenario runner before any pytest configuration or repository interpreter is consulted; recorded
    executors at this seam therefore control confirmed checks and behaviour probes too."""
    if observation is not None:
        from g6.harness import behaviour_runner

        if len(tests) != 1 or not copied or network or not require_isolation:
            raise RepoRefused("G6_E_INPUT_VALIDATION", "an observation needs one copied, isolated, offline run")
        return behaviour_runner.run(repo, observation, tests[0], timeout_s=timeout_s,
                                    deadline=deadline if deadline is not None else time.monotonic() + timeout_s)
    if not tests:
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["no_tests"])
    ids = [_test_id(repo, test) for test in tests]
    missing = [test for test, one in zip(tests, ids) if not (repo / one.partition("::")[0]).exists()]
    if missing:
        raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["missing"].format(value=shown(missing[0])))
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
        return {"verdict": verdict, "said": _said(verdict, seconds=bound, python=python, output=output), "passed": passed,
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
        if mutant is not None:   # the module's file where the system holds it (``_test_id``: a name git listed by its rule)
            target, source = str(repo / _test_id(repo, mutant[0], "module")), str(own / "mutant.py")
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
                "no test receipt: git ignores files that could change this run, "
                + ", ".join(shown(rel) for rel in scan.loadable[:10])
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
#: ``.pdm-build``: pdm-backend's editable build output (a ``.pth`` Python never reads from there: worked example 9, F8).
_UNSCANNED = frozenset({".git", ".venv", "venv", "env", ".tox", ".nox", ".direnv", ".eggs", "__pypackages__", ".conda",
                        "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist",
                        ".pdm-build"})
#: An installed package's metadata folder: pytest loads, at start-up, every plugin one declares (an entry point in its
#: ``pytest11`` group) when the folder's parent is on the import path (review C3).
_METADATA = (".egg-info", ".dist-info")
_PLUGIN_GROUP = re.compile(r"^\s*\[\s*pytest11\s*\]\s*$", re.MULTILINE)
#: Compiled extension modules: Python imports one in place of a ``.py`` file of the same name (review C3).
_EXTENSIONS = tuple(suffix.lower() for suffix in importlib.machinery.EXTENSION_SUFFIXES)
#: The most entries the ignored-file scan reads; past it, the scan is incomplete and says so.
IGNORED_SCAN_LIMIT = 20_000


def _unscanned(repo: Path, rel: str) -> bool:
    """Whether ``rel`` is, or is inside, a folder the scan does not enter (``_UNSCANNED``, or a virtual environment). A
    name git listed is looked for where git looks (``_on_disk``)."""
    parts, disk = PurePosixPath(rel).parts, PurePosixPath(_in(rel)).parts
    return any(part in _UNSCANNED for part in parts) or any(
        (repo.joinpath(*disk[:end]) / "pyvenv.cfg").is_file() for end in range(1, len(disk) + 1))


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
    whose name Python could import; a folder git lists whole and never looks into; and a new Python file, not a test file, whose dotted name, with any import root a
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
        if rel.endswith(".py") and os.path.lexists(change.repo / _on_disk(rel)[0]):
            for name in _import_names(rel, import_roots):
                held.setdefault(name, set()).add(rel)
    found: dict[str, str] = {}
    for rel in change.files:     # each looked at where git looks (``_on_disk``), and read only where G6 can open it
        path, full, readable = PurePosixPath(rel), change.repo / _on_disk(rel)[0], _disk(change.repo, rel)
        name = path.name.lower()
        if not os.path.lexists(full):
            continue   # deleted
        inside = next((index for index, part in enumerate(path.parts[:-1]) if part.endswith(_METADATA)), None)
        if inside is not None:
            if inside == len(path.parts) - 2 and name == "entry_points.txt" and (
                    readable is None or _declares_plugin(readable)):   # unread, it may declare one
                found[rel] = "it declares a pytest plugin, which pytest loads at start-up"
        elif _linked(full):
            if not path.suffix or name.endswith((".py", ".pyc", ".pth", *_EXTENSIONS)):
                found[rel] = "a link, which can put any code under a name Python imports"
        elif rel.endswith("/"):      # review revmaskfu2a, finding 1: its files are in no listing and in no patch
            found[rel] = ("a folder git lists whole and does not look into (it holds a repository of its own, or a .git "
                          "file), so the files in it, which Python can import, are in no patch")
        elif name.endswith(_EXTENSIONS):
            found[rel] = "a compiled extension module, which Python imports in place of a .py file of the same name"
        elif name.endswith(".pyc"):
            found[rel] = "bytecode, which Python can import in place of the source"
        elif path.suffix == ".py" and rel not in base and not is_test_related(rel):   # a test: the weakening detector
            clash = sorted(other for module in _import_names(rel, import_roots)
                           for other in held.get(module, ()) if other != rel)
            if clash:
                found[rel] = f"a new module named like {shown(clash[0])}, which it can shadow on the import path"
    return found


def ignored_inputs(repo: Path, *, timeout_s: float = GIT_TIMEOUT_S) -> Ignored:
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
    bound. ``timeout_s`` bounds each of its git calls."""
    listed = _paths(_git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z",
                         timeout_s=timeout_s))
    modules = {_module(PurePosixPath(rel)) for rel in _paths(_git(repo, "ls-files", "-z", timeout_s=timeout_s))
               if rel.endswith(".py")}
    loadable: list[str] = []
    named: list[str] = []
    other: dict[str, str] = {}
    seen = 0

    def looked(rel: str) -> Path:
        """Where a path is looked at: a name git listed where git looks (``_on_disk``), one the scan found as found."""
        return repo / (_on_disk(rel)[0] if isinstance(rel, GitName) else rel)

    def opened(rel: str) -> Path | None:
        """Where a path is read: a name git listed only where G6 can open it by that name (``_disk``)."""
        return _disk(repo, rel) if isinstance(rel, GitName) else repo / rel

    def judge(rel: str) -> None:
        path = PurePosixPath(rel)
        name = path.name.lower()
        inside = next((index for index, part in enumerate(path.parts[:-1]) if part.endswith(_METADATA)), None)
        if inside is not None:   # a metadata folder's files: only its own entry_points.txt can load code
            if inside == len(path.parts) - 2 and name == "entry_points.txt" and (
                    opened(rel) is None or _declares_plugin(opened(rel))):   # unread, it may declare one
                loadable.append(rel)
        elif _linked(looked(rel)) or name in _LOADABLE or name.endswith((".pth", ".pyc", *_EXTENSIONS)) or (
                path.suffix == ".py" and (name.startswith("test_") or name.endswith("_test.py"))):
            loadable.append(rel)
        elif path.suffix == ".py" and _module(path) in modules:
            loadable.append(rel)
            named.append(rel)
        elif path.suffix == ".py":
            other[rel] = _digest(opened(rel)) if opened(rel) is not None else "unreadable"

    def folder(rel: str) -> bool:
        """Judge a folder the scan would enter: True when its files are to be read."""
        if _linked(looked(rel)):
            loadable.append(rel)
        elif rel.endswith(_METADATA):
            if (looked(rel) / "entry_points.txt").is_file():
                judge(GitName(rel.raw + b"/entry_points.txt") if isinstance(rel, GitName) else f"{rel}/entry_points.txt")
        else:
            return True
        return False

    def found(complete: bool) -> Ignored:
        return Ignored(tuple(sorted(loadable)), dict(sorted(other.items())), complete, tuple(sorted(named)))

    for entry in listed:
        seen += 1
        if seen > IGNORED_SCAN_LIMIT:
            return found(False)
        rel = GitName(entry.raw.rstrip(b"/"))     # the name git listed, without the slash it ends a folder's with
        if _unscanned(repo, rel):
            continue
        if not entry.endswith("/"):
            judge(rel)
            continue
        if not folder(rel):
            continue
        for top, dirs, files in os.walk(looked(rel)):   # never through a link: os.walk does not follow one
            here = Path(top).relative_to(repo).as_posix()
            dirs[:] = sorted(name for name in dirs if name not in _UNSCANNED
                             and not (Path(top) / name / "pyvenv.cfg").is_file() and folder(f"{here}/{name}"))
            for name in sorted(files):
                seen += 1
                if seen > IGNORED_SCAN_LIMIT:
                    return found(False)
                judge(f"{here}/{name}")
    return found(True)


def read_at(repo: Path, commit: str, rel: str, *, timeout_s: float = GIT_TIMEOUT_S) -> str | None:
    """``rel``'s text at ``commit`` (None when it was not there), with the line endings ``Path.read_text`` gives,
    read with the hardened git call (``_git``: the repository's own configuration may be the agent's), each git call
    within ``timeout_s``. A name git listed (``GitName``) is given to git as its bytes; any other path is first one
    ``inside`` accepts."""
    if not isinstance(rel, GitName):
        rel = inside(repo, rel, "path")
    entry = _listed_at(repo, commit, [rel], timeout_s=timeout_s).get(rel)
    if entry is None or entry[1] != "blob":
        return None
    return _text(blob_bytes(repo, entry[2], timeout_s=timeout_s))


#: The most paths one ``texts_at`` git call names.
_TEXTS_AT_BATCH = 100


def texts_at(repo: Path, commit: str, rels: list[str], *, limit: int | None) -> dict[str, str]:
    """``read_at`` for many files in a few git calls: each of ``rels`` that ``commit`` holds as a regular file of at
    most ``limit`` bytes (any size when None), by path, with the line endings ``Path.read_text`` gives. The sizes come
    from git's tree before any blob is read; the blobs, from one ``git show`` per batch, are cut by those sizes."""
    found: dict[str, str] = {}
    for start in range(0, len(rels), _TEXTS_AT_BATCH):
        batch = [rel if isinstance(rel, GitName) else inside(repo, rel, "path")
                 for rel in rels[start:start + _TEXTS_AT_BATCH]]
        sizes: dict[str, int] = {}
        listed = _listed_at(repo, commit, batch)
        for rel, (mode, kind, _, size) in listed.items():
            if kind == "blob" and mode in ("100644", "100755") and (limit is None or int(size) <= limit):
                sizes[rel] = int(size)
        wanted = [rel for rel in batch if rel in sizes]
        if not wanted:
            continue
        raw = _git(repo, "show", "--no-textconv", *(listed[rel][2] for rel in wanted))   # by object name
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
    absolute path) or follows more than ``_MAX_HOPS`` links (a loop). Each part of a name git listed is looked for where
    git looks (``_on_disk``, by ``_in``); a link's own text is the system's."""
    named = isinstance(rel, GitName)
    pending = [GitName(_raw(part)) if named else part for part in PurePosixPath(rel).parts]
    done, hops = [], 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not done:
                return None
            done.pop()
            continue
        here, path = "/".join([*done, part]), _joined(root, [*done, part])
        text = virtual[here] if here in virtual else os.readlink(path) if _linked(path) else None
        if text is None:
            done.append(part)
            continue
        hops += 1
        if hops > _MAX_HOPS or _absolute(text):
            return None
        pending[:0] = PurePosixPath(text.replace("\\", "/")).parts
    return done


def _joined(root: Path, parts: list[str]) -> Path:
    """``root`` with ``parts`` below it (``_resolve_inside``'s), each as the system is given it (``_in``)."""
    return root.joinpath(*(_in(part) for part in parts))


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
        (copy / _in(rel)).parent.mkdir(parents=True, exist_ok=True)
        made = text
        if _WINDOWS:   # CI run 40780356106: a Windows link resolves no forward slash, and a folder's link is its own
            made = text.replace("/", "\\")
            resolved = _resolve_inside(copy, rel, texts)
            folder = folder or (resolved is not None and _joined(copy, resolved).is_dir())
        try:
            os.symlink(made, copy / _in(rel), target_is_directory=folder)
        except OSError as exc:
            if getattr(exc, "winerror", None) == _PRIVILEGE_NOT_HELD:
                deferred[rel] = text
            else:
                left[rel] = LINK_NOT_MADE.format(error=type(exc).__name__)
    outside = [rel for rel in sorted(links)
               if rel not in left and rel not in deferred and _resolve_inside(copy, rel, deferred) is None]
    for rel in outside:   # judged all together first: a link may resolve through another
        os.unlink(copy / _in(rel))
        left[rel] = LINK_OUTSIDE
    materialised: dict[str, str] = {}
    for rel in sorted(deferred):
        resolved = _resolve_inside(copy, rel, deferred)
        source = _joined(copy, resolved) if resolved is not None else copy
        if resolved is None:
            left[rel] = LINK_OUTSIDE
        elif tuple(resolved) == PurePosixPath(rel).parts[:len(resolved)]:
            left[rel] = LINK_LOOP
        elif source.is_file() and not _linked(source):
            put(source, copy / _in(rel))
            materialised[rel] = "/".join(resolved)
        elif source.is_dir() and not _linked(source):
            (copy / _in(rel)).mkdir(parents=True, exist_ok=True)
            for folder, dirs, files in os.walk(source):
                here = Path(folder)
                dirs[:] = sorted(name for name in dirs if not _linked(here / name))
                for name in sorted(files):
                    if not _linked(here / name) and (here / name).is_file():
                        put(here / name, copy / _in(rel) / here.relative_to(source) / name)
            materialised[rel] = "/".join(resolved)
        else:
            left[rel] = LINK_NO_TARGET
    return materialised


#: The build configuration files whose text, as committed at the review's base, can name a generated file a copy takes.
_BUILD_CONFIG = ("pyproject.toml", "setup.cfg", "setup.py")
_QUOTED_PY = re.compile(r"""["']([^"'\s]+\.py)["']""")
#: The most bytes of a generated file G6 reads; a version file is a few hundred.
GENERATED_MAX_BYTES = 64 * 1024
#: The double-underscore names a generated file may bind: any other (``__path__``, ``__builtins__``) is import machinery.
_GENERATED_DUNDERS = frozenset({"__all__", "__version__", "__version_tuple__", "__commit_id__"})
# COPY: why a copy left out a git-ignored file the build configuration names (``report``'s ``generated_left_out``).
GENERATED_TOO_BIG = "left out of the copy: it holds more than {n} bytes, the most G6 reads of a generated file"
GENERATED_UNREADABLE = "left out of the copy: G6 could not read it"
GENERATED_NOT_DATA = ("left out of the copy: it holds more than literal data ({what}, line {line}); the patch's author "
                      "can write a git-ignored file, which is in no diff, and both copies would run its code")


def _literal(node: ast.expr) -> bool:
    """A literal string, integer or None, or a tuple or list of them (``ast.literal_eval``: nothing runs)."""
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return all(item is None or type(item) in (str, int, bool) for item in
               (value if type(value) in (tuple, list) else (value,)))


def _type_expression(node: ast.expr) -> bool:
    """A type written with names, subscripts, tuples, constants and ``|`` only: no call, no attribute."""
    return all(isinstance(part, (ast.Name, ast.Subscript, ast.Tuple, ast.Constant, ast.Load, ast.BitOr))
               or isinstance(part, ast.BinOp) and isinstance(part.op, ast.BitOr) for part in ast.walk(node))


def _not_data(data: bytes) -> str:
    """Why ``data`` (Python source, parsed, never run) is more than the literal data a build writes into a version
    file, "" when it is not. Allowed, at the top of the module only: a docstring first; ``from __future__ import``;
    assignments to plain names (each double-underscore one in ``_GENERATED_DUNDERS``) of a literal (``_literal``),
    annotated or not, an annotation being a type (``_type_expression``); and setuptools-scm's typing stub,
    ``TYPE_CHECKING = False`` then ``if TYPE_CHECKING:`` holding only ``from typing import`` and assignments of a type,
    ``else:`` only assignments of ``object``, with ``TYPE_CHECKING`` bound nowhere else."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        return GENERATED_NOT_DATA.format(what="text Python cannot parse", line=getattr(exc, "lineno", None) or 1)

    def names(targets: list[ast.expr]) -> bool:
        return all(isinstance(t, ast.Name) and (not (t.id.startswith("__") and t.id.endswith("__"))
                                                or t.id in _GENERATED_DUNDERS) for t in targets)

    def plain(node: ast.stmt, index: int) -> bool:
        if isinstance(node, ast.Expr):
            return index == 0 and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        if isinstance(node, ast.ImportFrom):
            return node.module == "__future__" and not node.level
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "TYPE_CHECKING" for t in node.targets):
                return (len(node.targets) == 1 and isinstance(node.value, ast.Constant)
                        and node.value.value is False)
            return names(node.targets) and _literal(node.value)
        if isinstance(node, ast.AnnAssign):
            return (names([node.target]) and node.target.id != "TYPE_CHECKING" and _type_expression(node.annotation)
                    and (node.value is None or _literal(node.value)))
        if isinstance(node, ast.If):
            return (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                    and all(isinstance(s, ast.ImportFrom) and s.module == "typing" and not s.level
                            and all(a.name != "TYPE_CHECKING" and a.asname != "TYPE_CHECKING" for a in s.names)
                            or isinstance(s, ast.Assign) and names(s.targets) and _type_expression(s.value)
                            for s in node.body)
                    and all(isinstance(s, ast.Assign) and names(s.targets) and isinstance(s.value, ast.Name)
                            and s.value.id == "object" for s in node.orelse)
                    and not any(isinstance(t, ast.Name) and t.id == "TYPE_CHECKING"
                                for s in (*node.body, *node.orelse) for t in getattr(s, "targets", ())))
        return False

    for index, node in enumerate(tree.body):
        if not plain(node, index):
            return GENERATED_NOT_DATA.format(what=f"a statement of kind {type(node).__name__}", line=node.lineno)
    return ""


def _generated_files(repo: Path, base: str | None = None,
                     deadline: float | None = None) -> tuple[dict[str, bytes], dict[str, str]]:
    """The git-ignored Python files the project's own build writes, which a copy takes, path to the bytes G6 read and
    checked (once: those bytes are what the copy holds and what its SHA-256 is of), and the named ones it left out, path
    to why (worked examples, round 2: black's ``src/_black_version.py``, which hatch-vcs writes at install time and git
    ignores, so no check G6 ran in a copy could import black). A file is taken only when (1) a build configuration file
    at the top (``_BUILD_CONFIG``), as committed at ``base`` (the review's base commit; HEAD when there is none), names
    its path in quotes, inside the repository, so neither the working tree nor a commit after the base can add a name
    (``setup.py`` is no runner file); (2) ``ignored_inputs`` finished its scan and judged the file ``other``: not a
    runner, start-up, path or test file, not named like a tracked module it could shadow, not a link, bytecode or an
    extension; and (3) it holds only literal data (``_not_data``, from the source parsed, never run): the patch's
    author can write the file, it is in no diff, and the base and the patch copies both import it, so code in it could
    forge both halves of a fail-before, pass-after check. Git's calls keep to ``deadline``; a call git refuses takes
    nothing. Not covered: the literal values themselves, the same in both copies.
    SECURITY-SENSITIVE (2026-10-30): reviewed; this is the review's fix."""
    def timeout() -> float:
        return GIT_TIMEOUT_S if deadline is None else min(GIT_TIMEOUT_S, _copy_remaining(deadline))

    named: set[str] = set()
    try:
        for config in _BUILD_CONFIG:
            text = read_at(repo, base or "HEAD", config, timeout_s=timeout())
            for match in _QUOTED_PY.finditer(text or ""):
                rel = match.group(1).removeprefix("./")
                if not _absolute(rel) and ".." not in PurePosixPath(rel).parts and not shape_problem(rel, "path"):
                    named.add(rel)
        if not any((repo / rel).is_file() and not _linked(repo / rel) for rel in named):
            return {}, {}
        scan = ignored_inputs(repo, timeout_s=timeout())
    except RepoRefused:
        return {}, {}
    if not scan.complete:
        return {}, {}
    taken: dict[str, bytes] = {}
    left: dict[str, str] = {}
    for rel in sorted(rel for rel in named if rel in scan.other):   # read by the configuration's own spelling of it
        try:
            if _linked(repo / rel):
                continue
            with (repo / rel).open("rb") as file:
                data = file.read(GENERATED_MAX_BYTES + 1)
        except OSError:
            left[rel] = GENERATED_UNREADABLE
            continue
        if len(data) > GENERATED_MAX_BYTES:
            left[rel] = GENERATED_TOO_BIG.format(n=GENERATED_MAX_BYTES)
        elif why := _not_data(data):
            left[rel] = why
        else:
            taken[rel] = data
    return taken, left


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
                    report: dict | None = None, base: str | None = None):
    """Copy git's tracked and non-ignored untracked files, and the ignored files the project's build writes that
    ``_generated_files`` allows, named at ``base`` (the review's base commit; HEAD when None) (``report``'s
    ``generated_copied``, path to SHA-256, and ``generated_left_out``, path to why), with bounded reads and old-test
    overlays; every file of each
    submodule folder git does not look into (``_gitlinks``); and each tracked link (git's mode 120000) and each link in
    such a folder as ``_copy_links`` allows. ``report``, when given, receives ``links_left_out`` (path to why) and
    ``links_materialised`` (path to the target copied in its place), each only when it names one. A repository
    holding a submodule that was set up is refused (``G6_E_GIT_SUBMODULE_REFUSED``)."""
    before = before or {}
    stage = _git(repo, "ls-files", "--stage", "-z", timeout_s=min(GIT_TIMEOUT_S, _copy_remaining(deadline)))
    gitlinks = _gitlinks(repo, stage)
    tracked_links = {rel for meta, rel in _entries(stage) if meta.startswith("120000 ")}
    root = repo.resolve()

    def source_of(rel: str) -> Path | None:
        """Where a file of the tree is read: a name git listed where git looks (``_on_disk``), inside by ``_within`` (the
        one containment rule), and never where G6 cannot open it by its name; a caller's or a model's path as ``inside``
        accepts it (its shape, then where it resolves)."""
        if not isinstance(rel, GitName):
            return repo / inside(repo, rel, "file")
        source = _disk(repo, rel)
        if source is not None and not _within(root, source.resolve()):
            raise RepoRefused("G6_E_INPUT_VALIDATION", SAID["outside"].format(what="file", value=shown_name(rel)))
        return source
    known = _paths(_git(repo, "ls-files", "-co", "--exclude-standard", "-z",
                        timeout_s=min(GIT_TIMEOUT_S, _copy_remaining(deadline))))
    # git-ignored files the project's build writes, as read and checked once (SECURITY-SENSITIVE, see there)
    generated, generated_left = _generated_files(repo, base=base, deadline=deadline)
    known = [*known, *(rel for rel in generated if rel not in known)]
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
            parts = PurePosixPath(_in(rel)).parts     # the folders looked at, where git looks (``_on_disk``)
            skipped = [part in _copy_ignored(str(repo.joinpath(*parts[:index])), [part])
                       for index, part in enumerate(parts)]
            if (rel in tracked_links and rel not in before and not any(skipped[:-1])
                    and not _SKIPPED(str(repo.joinpath(*parts[:-1])), [parts[-1]]) and _linked(repo / _in(rel))):
                links[rel] = _link_of(repo / _in(rel))
                continue
            if any(skipped) or rel in gitlinks:   # a submodule folder is copied below
                continue
            source = source_of(rel)
            if rel not in before and (source is None or not source.is_file()):
                continue
            put(source, copy / _in(rel), before[rel].encode("utf-8") if rel in before else generated.get(rel))
        for rel in gitlinks:
            parts = PurePosixPath(_in(rel)).parts
            if any(_linked(repo.joinpath(*parts[:end])) for end in range(1, len(parts) + 1)):
                left[rel] = LINK_FOLDER
                continue
            if any(part in _copy_ignored(str(repo.joinpath(*parts[:index])), [part])
                   for index, part in enumerate(parts)) or not (repo / _in(rel)).is_dir():
                continue
            for folder, dirs, files in os.walk(repo / _in(rel), onerror=failed):
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
                                                         ("links_materialised", materialised),
                                                         ("generated_copied", {rel: hashlib.sha256(data).hexdigest()
                                                                               for rel, data in generated.items()}),
                                                         ("generated_left_out", generated_left)) if value})
        _copy_caps(*tally, deadline)
        yield copy


def _copy_snapshot(repo: Path, *, deadline: float | None = None) -> dict[str, str]:
    """A copy's own record of its files, read before and after a run in it: each file's SHA-256, and each link the copy
    holds (``_copy_links``) as its own text, never followed."""
    from g6.integrity import in_cache

    found, size = {}, 0
    for folder, dirs, files in os.walk(repo):
        _copy_caps(len(found), size, deadline)
        for name in dirs:
            rel = (Path(folder) / name / '_').relative_to(repo)
            if in_cache(rel):
                raise CopyNotRun("G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: a cache directory is present in the copy: "
                                 + rel.parent.as_posix())
        ignored = _copy_ignored(folder, dirs + files)
        for name in sorted(ignored):
            if _linked(Path(folder) / name):
                _copy_caps(len(found) + 1, size, deadline)
                found[(Path(folder) / name).relative_to(repo).as_posix()] = "link:" + os.readlink(Path(folder) / name)
            elif not (Path(folder) / name).is_dir():
                rel = (Path(folder) / name).relative_to(repo)
                kind = 'cache' if in_cache(rel) else 'excluded'
                raise CopyNotRun(f"G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: an {kind} input is present in the copy: "
                                 + rel.as_posix())
        # Copy exclusions cannot prune an execution manifest: every ordinary directory can hold importable source.
        # Links retain their own text above and are checked by the existing complete-copy binding.
        dirs[:] = [name for name in dirs if not _linked(Path(folder) / name)]
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
    changed = set(_paths(diffed)) | set(_paths(others)) | _tracked_new(change.repo, change.base_commit)   # F4
    changed.update(change.held)   # changed, though git's index flags hide them from its diff (review C2)
    changed = {rel for rel in changed if not any(rel == kept or rel.startswith(kept + "/") for kept in keep)}
    base = {}
    for meta, rel in _entries(_git(change.repo, "ls-tree", "-r", "-l", "-z", change.base_commit)):
        mode, kind, oid, size = meta.split()
        if rel in changed:
            base[rel] = (mode, kind, oid, size)
    count, size = 0, 0
    copied_in = [path for pair in (materialised or {}).items() for path in pair]
    root = copy.resolve()
    with ExitStack() as restore:
        for rel in sorted(changed):
            if (base.get(rel, ("",))[0] == "120000" or _linked(copy / _in(rel))
                    or any(rel == path or rel.startswith(path + "/") for path in copied_in)):
                raise CopyNotRun(OLD_BASELINE_NOT_RUN)
            parts = PurePosixPath(_in(rel)).parts     # looked at, and written, where git looks (``_on_disk``)
            if any(part in _copy_ignored(str(change.repo.joinpath(*parts[:index])), [part])
                   for index, part in enumerate(parts)):
                continue
            target = _disk(copy, rel) if isinstance(rel, GitName) else copy / inside(copy, rel, "file")
            if target is None or not _within(root, target.resolve()):   # inside the copy, by the one containment rule
                raise CopyNotRun(OLD_BASELINE_NOT_RUN)
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
            with repository_copy(change.repo, deadline=started + bound, before=change.before, report=report,
                                 base=change.base_commit) as copy:
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
        with repository_copy(change.repo, deadline=deadline, before=overlay, report=report,
                             base=change.base_commit) as copy:
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
