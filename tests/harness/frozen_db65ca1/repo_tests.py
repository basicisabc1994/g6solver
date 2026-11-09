"""Frozen db65ca1 reading; stdlib and sibling fixtures only.

Copied source; receipt version/executable/digest metadata use fixed test adapters.
"""
from __future__ import annotations

import os


PASSED, FAILED, CHANGED = "passed", "failed", "changed_tracked_files"


RAN = frozenset({PASSED, FAILED})


RUN_INCONSISTENT = "run_inconsistent"


CRASHED = "crashed"


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


def _judge(exit_code: int | None, counts: dict[str, int] | None, output: str, moved: list[str]) -> str:
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
