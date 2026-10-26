# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The test bench: the workforce's one native capability, ``workforce.run_tests`` (workforce OS plan, V1b).

The oracle for "make the failing test in this repository pass". No registry component runs a
repository's tests, so the bench is a capability of the workforce itself: it has a shipped
ParamContract (``params/workforce.run_tests.json``, declared model-free), climbs the readiness ladder
without the registry, and is reached only through the single door (``calls.py`` routes it here).

What it does, and nothing else:

* the repository is a NAME resolved under the calling venture's workspace
  (``<root>/repos/<venture_id>/``, the root the venture was bound under, else ``G6_WORKFORCE_DB_DIR`` or
  ``$G6_HOME/workforce``), never a path the caller chooses; with no venture bound (the registry door, a probe) only
  the probe's own repository runs;
* it is copied into a fresh scratch directory and the patch's files are written THERE (the op is
  declared ``read``); the original is hashed before and after, and a run that changed it is refused;
* a patch that edits a test file (``tests/``, ``test_*.py``, ``*_test.py``, ``conftest.py``, in any
  case), writes runner configuration (``pytest.ini``, ``pyproject.toml``, ``setup.cfg``, ``tox.ini``,
  ``*.pth``) or a module named like pytest, its plugins, a site hook or the standard library, or names
  a path outside the copy is refused, as is a test id outside it; test files must be byte-identical in
  the copy;
* each named test runs through ``venture.truth.run_isolated``: ``python -I``, the real pytest imported
  before the repository joins the path (and ``src/`` after it, for a src-layout project), an empty config
  file from outside it, no plugin autoload, a minimal environment, no shell; ``passed`` is true only when
  every named test passed in the runner's own record, whose positive control was recorded failing (never the
  printed tally or exit code alone, audit 010 WF-003);
* when a patch is applied, the repository's whole ``tests/`` is also run once on the patched copy
  (``truth.run_suite``) and compared with the same run on the unpatched repository: a test that passed
  before and does not pass now (class-based, parametrized and nested tests included) fails the patch, and
  the result's ``suite`` says how many were compared and which broke. A suite that cannot be run on the
  unpatched repository is reported ``not_run`` (unmeasured), never as a pass (ECHA Stage A, 2026-10-09).

Residual (Codex review of V1). Where ``g6.core.confine`` has a backend (a Windows AppContainer, once the container can
read the interpreter), each test process runs confined: it reads the interpreter's folders and the copy (and what
Windows lets every AppContainer read: the system folders, Program Files), writes only the copy, its own run folder and
its run's container folder (deleted after it), and reaches no network, this machine included; the copy holds no link
or junction. Otherwise such a run is refused (``G6_E_ISOLATION_UNAVAILABLE``) unless the person allowed it
(``confine.allowed_by``; ``G6_REQUIRE_ISOLATION=1`` refuses it whatever was allowed); an allowed run uses the owner's
permissions, the result's ``isolation`` says so (``{"backend": null, "why": ..., "allowed_by": ...}``), and its pass can
refute a change but never verify it (``rungs.run_verify``).
Either way the runner and the environment are isolated and a change to the original is detected, and the code under
test runs in the same process as pytest and G6's record of it: a patch that prints a pass and exits, or makes pytest
pass what fails, is caught by the record and its positive control, but code aimed at that record itself, from inside
the process, is not excluded, confined or not.

``run_tests_in_place`` (plan 2026-10-05-verify-task-real-repos), the second op, takes no patch and makes no copy: it
runs the named tests against the venture's repository (a git repository) where it is, through
``g6.workforce.repo_tests.run``, the function the meta-harness's coding oracle (``g6.metaharness.coding``) also calls;
that module says what the run in place does and does not protect (git's view read before and after, not the bytes of
a copy).

The answer is an envelope the single reader judges: a refusal is ``ok: false`` with the reason.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from g6.core import component_process, confine
from g6.core.outside import find_program
from g6.core.storage import state_path
from g6.workforce import truth

COMPONENT = "workforce"
#: ``run_tests_in_place`` (plan 2026-10-05-verify-task-real-repos): the named tests of a repository, run against its
#: working tree where it is by ``g6.workforce.repo_tests.run``, so the census measures that function. The repository is
#: resolved as ``run_tests`` resolves it (a name under the venture's workspace; with no venture, only the probe's, made
#: in a scratch folder).
IN_PLACE = "run_tests_in_place"
OPS = frozenset({"run_tests", IN_PLACE})
#: The parameters ``run_tests`` reads (its ParamContract declares exactly these).
PARAMS = frozenset({"repo", "tests", "files", "regression"})
#: The parameters ``run_tests_in_place`` reads (its ParamContract declares exactly these).
IN_PLACE_PARAMS = frozenset({"repo", "tests", "roots", "timeout_s"})
#: The server environment's workforce db dir (``service.ENV_DB_DIR`` is this name).
ENV_DB_DIR = "G6_WORKFORCE_DB_DIR"
REPOS = "repos"
#: The probe's own repository, made in the scratch directory: a probe needs nothing of the owner's.
PROBE = "__probe__"
PROBE_TEST = "tests/test_probe.py::test_probe"
_PROBE_FILES = {"tests/test_probe.py": "def test_probe():\n    assert 1 + 1 == 2\n"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
#: Test files, matched without regard to case (a case-insensitive filesystem aliases TESTS/ to tests/).
_TEST_FILE = re.compile(r"(?:^|/)(?:tests?/|test_[^/]*\.py$|[^/]*_test\.py$|conftest\.py$)", re.IGNORECASE)
#: What configures, or could stand in for, the runner: never written by a patch.
_RUNNER_CONFIG = frozenset({"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"})
_RUNNER_MODULES = frozenset({"pytest", "_pytest", "pluggy", "py", "sitecustomize", "usercustomize"})
_PATTERNS = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc")
_IGNORED_PART = frozenset({".git", "__pycache__", ".pytest_cache"})


def _linked(path: Path) -> bool:
    """A symbolic link or a junction: what it points at may be anywhere, outside the repository included."""
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _ignored(folder: str, names: list[str]) -> set[str]:
    """What a copy of a repository leaves out: git and caches, and every link or junction (``shutil.copytree`` would
    copy in what it points at, which a confined run could then read)."""
    return set(_PATTERNS(folder, names)) | {name for name in names if _linked(Path(folder) / name)}


_IGNORED = _ignored
# COPY: A runner that never started is unmeasured, never a failed test.
NOT_STARTED = "the test runner could not start, so no test ran: {why}"
# COPY: A candidate's run that started and did not finish: an executed failure, sent to the worker.
TIMED_OUT = "the tests did not finish: timed out after {seconds} s"
# COPY: A candidate's run that started and ended before pytest reported: an executed failure.
EXITED = "the test process exited with code {rc} before reporting"
# COPY: A run whose positive control (a check that always fails) was not recorded failing: an executed failure.
UNCONTROLLED = ("the test run did not report G6's positive control, a check that always fails, as failing, so nothing "
                "it reported can be trusted")
#: ECHA Stage A (2026-10-09, founder decision (a)): a patch is compared with the repository's whole existing suite, not
#: only the top-level tests the frozen standard names. The unpatched repository's suite, run once per distinct content in
#: a scratch copy (never in the owner's repository), held in this process only.
SUITE_BASELINES: dict[tuple[str, str], dict] = {}
MAX_SUITE_BASELINES = 8
#: How long a baseline that started and did not finish (or ended without reporting) is remembered: one transient timeout
#: under load must not make every later patch of that repository "not compared" for the life of the process.
NOT_RAN_TTL_S = 600
_SUITE_META = threading.Lock()  # guards the two dicts; a suite run itself holds only its own repository's lock
_SUITE_KEYLOCKS: dict[tuple[str, str], threading.Lock] = {}
#: The most broken tests a result lists (the count is always whole).
MAX_BROKEN_SHOWN = 20
# COPY: Said when no test passed on the unpatched repository, so there was nothing to compare a patch with.
NOTHING_TO_COMPARE = "no test passed on the unpatched repository, so the patched copy had nothing to be compared with"


def ready() -> bool:
    """Whether the bench can run here: it needs pytest in this interpreter."""
    return importlib.util.find_spec("pytest") is not None


def missing(op: str) -> str:
    """Why the bench cannot run ``op`` here, or ``""``: every op needs pytest, and ``run_tests_in_place`` also needs
    git, which it reads before and after the run (task #151; the words are its own refusal's, ``repo_tests.SAID``)."""
    if not ready():
        return "the bench needs pytest, which is not installed here"
    if op == IN_PLACE and find_program("git") is None:
        from g6.workforce import repo_tests

        return repo_tests.SAID["no_git"]
    return ""


def run(op: str, params: dict, *, venture: str | None = None) -> dict:
    """``run_tests`` on ``{"repo", "tests", "files"?}`` for ``venture`` (its repositories only; with no
    venture only the probe runs): an envelope with ``passed`` per test and overall."""
    if op not in OPS:
        return _refused(f"the bench has no op {op!r} (it has {sorted(OPS)})")
    if op == IN_PLACE:
        return _in_place(params, venture)
    if "regression" in params:
        from g6.workforce import patch_checks

        return patch_checks.run(params, venture=venture)
    try:
        tests = _tests(params.get("tests"))
        files = _files(params.get("files") or {})
        source = _repo(params.get("repo"), venture)
    except ValueError as exc:
        return _refused(str(exc))
    before = _digest(source)
    with confine.run_folder(prefix="g6_bench_") as scratch, confine.recording() as runs:
        copy = Path(scratch) / "repo"
        if source is None:
            copy.mkdir()
            _write(copy, _PROBE_FILES)
        else:
            shutil.copytree(source, copy, ignore=_IGNORED)
        _write(copy, files)
        altered = _tests_altered(source, copy)
        if altered:
            return _refused(f"the patch changed a test file ({altered}): a patch may not edit a test")
        compared = _test_files(source, copy)
        test_snapshot = _test_snapshot(copy)
        results, causes, unstarted = {}, {}, ""
        suite, interruption, phase = None, None, "named_tests"
        try:
            for nodeid in tests:
                truth.LAST_RUN.clear()
                results[nodeid] = truth.run_isolated(nodeid, copy)
                if truth.LAST_RUN.get("refused"):  # no backend, and unconfined runs not allowed: nothing ran
                    return _refused(truth.LAST_RUN["refused"])
                kind, why = _attributed(nodeid)
                if kind == "failed":  # completed local timeout: an executed failure
                    causes[nodeid] = why
                elif kind == "not_started":
                    unstarted = why
                    del results[nodeid]
                    break
            phase = "suite"
            suite = _suite(source, copy) if files and source is not None and not unstarted else None
        except component_process.ExecutionInterrupted as exc:
            # Owned cleanup has completed. A stop, uncertain cleanup (a different
            # exception), or zero completed observations retains the control failure.
            # Never use LAST_RUN to invent an outcome for the interrupted node.
            if exc.stopped or not results:
                raise
            interruption = {"phase": phase, "reason": "The verification allowance expired; remaining checks were not completed."}
            if files and source is not None:
                suite = {"status": "not_run", "reason": interruption["reason"]}
        if _test_snapshot(copy) != test_snapshot:
            return _refused("the scratch test files changed while the checks ran, so no finding can be trusted")
    if _digest(source) != before:
        return _refused("the original repository changed while the tests ran: the patch's code wrote outside its "
                        "copy, so nothing it reported can be trusted")
    if unstarted and all(results.values()):  # nothing that ran failed: unmeasured, never a failed test
        return _refused(NOT_STARTED.format(why=unstarted))
    found = {"passed": not unstarted and all(results.values()) and (suite or {}).get("status") != "fail",
             "tests": results, "applied": sorted(files), "integrity": {"test_files_unchanged": True, "test_files": compared},
             "isolation": confine.summary(runs)}
    if suite is not None:
        found["suite"] = suite
    if causes:
        found["causes"] = causes
    if unstarted or interruption:
        found["not_run"] = [node for node in tests if node not in results]
    if interruption:
        found["interruption"] = interruption
        if any(value is False for value in results.values()):
            found["passed"] = False  # an observed failure suffices to reject, never to certify
        else:
            del found["passed"]  # unknown overall result; missing work is neither a pass nor a failure
    # Named tests can pass while the wider comparison is unavailable. Keep
    # those results, but carry their limited scope through the door, verdict
    # ledger and reuse checks. A successful call is not a complete check.
    from g6.workforce.contracts import suite_limits

    limits = suite_limits(suite)
    if interruption:
        limits = (*limits, interruption["reason"])
    if limits:
        return {"ok": True, "result": found, "degraded": True, "degradation_reason": " ".join(limits)}
    return {"ok": True, "result": found}


def _suite_baseline(source: Path) -> dict:
    """The unpatched repository's whole suite (``truth.run_suite``), run once per distinct content of ``source``. A run
    whose runner never started is not kept, so the next call tries again."""
    key = (str(source), _digest(source))
    with _SUITE_META:
        lock = _SUITE_KEYLOCKS.setdefault(key, threading.Lock())
    with lock:  # the checks and the learning drain share this process: one run per content, not one per caller
        with _SUITE_META:
            held = SUITE_BASELINES.get(key)
        if held is not None and (held["found"]["ran"] or time.monotonic() - held["at"] < NOT_RAN_TTL_S):
            return held["found"]
        with confine.run_folder(prefix="g6_baseline_") as scratch:
            copy = Path(scratch) / "repo"
            shutil.copytree(source, copy, ignore=_IGNORED)
            found = truth.run_suite(copy)
        if not found["ran"] and not found.get("started"):
            return found
        with _SUITE_META:
            while len(SUITE_BASELINES) >= MAX_SUITE_BASELINES and key not in SUITE_BASELINES:
                gone = next(iter(SUITE_BASELINES))
                del SUITE_BASELINES[gone]
                _SUITE_KEYLOCKS.pop(gone, None)
            SUITE_BASELINES[key] = {"found": found, "at": time.monotonic()}
        return found


def _suite(source: Path, copy: Path) -> dict:
    """Whether the patched ``copy`` still passes every test that passed on the unpatched ``source``, across the whole
    suite (class-based, parametrized and nested tests included). ``pass``/``fail`` with how many tests were compared and
    which broke; ``not_run`` with the reason when the comparison could not be made (the baseline would not run, or the
    patched runner never started): unmeasured, never a pass. A patched suite that started and did not finish or report
    is an executed failure. A test that did not pass before (failed, errored, skipped, expected to fail) is not held."""
    baseline = _suite_baseline(source)
    if not baseline["ran"]:
        return {"status": "not_run", "reason": baseline["why"]}
    held = sorted(node for node, outcome in baseline["outcomes"].items() if outcome == "passed")
    if not held:  # compared 0 is not a pass (review I-5)
        return {"status": "not_run", "reason": NOTHING_TO_COMPARE}
    # the patched suite gets the time the unpatched one needed twice over (review I-3), never less than the base limit
    limit = min(max(truth.SUITE_TIMEOUT_S, 2 * float(baseline.get("seconds") or 0)), 3 * truth.SUITE_TIMEOUT_S)
    patched = truth.run_suite(copy, limit)
    if not patched["ran"]:
        return {"status": "fail" if patched.get("started") else "not_run", "reason": patched["why"]}
    broken = [node for node in held if patched["outcomes"].get(node) != "passed"]
    if broken:  # a test that failed once and passes again is not held against the patch (review I-3, flaky tests)
        again = truth.run_suite(copy, limit)
        if again["ran"]:
            broken = [node for node in broken if again["outcomes"].get(node) != "passed"]
    found = {"status": "fail" if broken else "pass", "compared": len(held), "broken": len(broken),
             "tests": broken[:MAX_BROKEN_SHOWN]}
    if baseline.get("collect_errors"):  # files the baseline could not import here: said, never silent (review I-5)
        found["not_collected"] = len(baseline["collect_errors"])
    return found


def _attributed(nodeid: str) -> tuple[str, str]:
    """Lead ruling I-2 (E2E Build 6 review): ("not_started", why) only when the runner never started (it never spawned,
    or never reached its boot's start mark); ("failed", the true cause) when it started and then timed out or ended
    before reporting; ("", "") when it reported, or was never reached (``run_isolated`` refused the node itself)."""
    run = truth.LAST_RUN
    if run.get("nodeid") != nodeid:
        return "", ""
    if not run.get("started", run.get("rc") is not None):
        # the runner's own words, bounded (local only: the archive's hosted redaction is not ported; the isolated
        # runner's environment carries only truth._ISOLATED_KEEP and a dummy paid-model key, no secret of the server's)
        return "not_started", str(run.get("stderr") or "no exit code")[:300]
    if run.get("timed_out"):
        return "failed", TIMED_OUT.format(seconds=run.get("seconds", truth.TIMEOUT_S))
    if run.get("tally") is None:  # audit 010 WF-003: the runner's own record, never the printed tally
        return "failed", UNCONTROLLED if run.get("reported") else EXITED.format(rc=run.get("rc"))
    return "", ""


def _in_place(params: dict, venture: str | None) -> dict:
    """``run_tests_in_place``: pytest's tallies for the named tests (one outcome per test), the verdict, and whose
    Python ran them (``interpreter_source``); or a refusal: a request that cannot run, or a run under which a tracked
    file changed or after which git could not read the repository (nothing it reported can be trusted). Its words are
    ``repo_tests.SAID``'s (PENDING copy: VR-6), and ``run_tests``' own for a repository name it cannot resolve.
    ``passed`` is true only for the verdict ``passed``: a run that did not finish (``timed_out``, ``incomplete``) or
    could not be collected is not a pass."""
    from g6.workforce import repo_tests

    tests, roots = params.get("tests"), params.get("roots") or []
    if not isinstance(tests, list) or not tests:
        return _refused(repo_tests.SAID["no_tests"])
    try:
        source = _repo(params.get("repo"), venture)
    except ValueError as exc:
        return _refused(str(exc))
    with confine.run_folder(prefix="g6_bench_in_place_", ignore_cleanup_errors=True) as scratch:
        try:
            repo = source if source is not None else repo_tests.probe_repo(Path(scratch))
            found = repo_tests.run(repo, list(tests), roots=tuple(roots),
                                   timeout_s=int(params.get("timeout_s") or repo_tests.DEFAULT_TIMEOUT_S))
        except repo_tests.RepoRefused as refused:
            return _refused(f"{refused} (code {refused.code})")
    if found["verdict"] == repo_tests.CHANGED:
        return _refused(f"{found['said']}: {', '.join(found['changed'])}")
    return {"ok": True, "result": {"passed": found["verdict"] == repo_tests.PASSED, "verdict": found["verdict"],
                                   "counts": {key: found[key] for key in ("passed", "failed", "not_passed", "total")},
                                   "said": found["said"], "seconds": found["seconds"],
                                   "interpreter_source": found["interpreter_source"],
                                   "isolation": found.get("isolation") or confine.summary([])}}


def _refused(why: str) -> dict:
    return {"ok": False, "error": why}


def _inside(relative: str) -> PurePosixPath:
    """A relative path that stays inside the repository copy."""
    path = PurePosixPath(str(relative).replace("\\", "/"))
    if (not str(relative) or not path.parts or path.is_absolute() or ".." in path.parts
            or re.match(r"[A-Za-z]:", str(relative))):
        raise ValueError(f"{relative!r} must be a path inside the repository")
    return path


def _tests(tests: Any) -> list[str]:
    if not isinstance(tests, list) or not tests:
        raise ValueError("name at least one test to run ('<file>::<test>')")
    for nodeid in tests:
        if not isinstance(nodeid, str) or "::" not in nodeid:
            raise ValueError(f"a test is '<file>::<test>', got {nodeid!r}")
        _inside(nodeid.split("::")[0])
    return list(dict.fromkeys(tests))


def _files(files: Any) -> dict[str, str]:
    if not isinstance(files, dict):
        raise ValueError("files maps a path inside the repository to its new text")  # noqa: TRY004 - one refusal type
    out = {}
    for relative, text in files.items():
        path = _inside(relative)
        if _TEST_FILE.search(str(path)):
            raise ValueError(f"{relative!r} is a test file: a patch may not edit a test")
        top = path.parts[0].lower().removesuffix(".py")
        if (path.name.lower() in _RUNNER_CONFIG or path.suffix.lower() == ".pth" or top in _RUNNER_MODULES
                or top in sys.stdlib_module_names):
            raise ValueError(f"{relative!r} would configure or replace the runner: a patch may not write it")
        if not isinstance(text, str):
            raise ValueError(f"{relative!r}: a file's new content is text")  # noqa: TRY004 - one refusal type
        out[str(path)] = text
    return out


def workspace_root() -> Path:
    """Where the workforce keeps its stores and the ventures' repositories: ``$G6_WORKFORCE_DB_DIR`` when set (the
    archive's served-process binding), else ``$G6_HOME/workforce``."""
    root = str(os.environ.get(ENV_DB_DIR) or "").strip()
    return Path(root).expanduser() if root else state_path("workforce")


#: The root each venture was bound under (``service.bind``), so binding a root (``api.bind(db_dir=)``) changes nothing
#: for the rest of the process; a venture never bound here resolves under ``workspace_root()``.
_BOUND_ROOTS: dict[str, Path] = {}


def bind_root(venture: str, root: str | os.PathLike) -> None:
    """Resolve ``venture``'s repositories under ``root`` from now on."""
    _BOUND_ROOTS[venture] = Path(root)


def _repo(name: Any, venture: str | None) -> Path | None:
    """The venture's repository to copy (None: the probe's own)."""
    if name == PROBE:
        return None
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError(f"{name!r} is not a repository name (letters, digits, '_', '.', '-')")
    if not (isinstance(venture, str) and _NAME.match(venture)):
        raise ValueError("no venture is bound to this call: only the probe runs")
    repos = (_BOUND_ROOTS.get(venture) or workspace_root()) / REPOS / venture
    repos = repos.resolve()
    repo = (repos / name).resolve()
    if repo.parent != repos or not repo.is_dir():
        raise ValueError(f"no repository {name!r} under the workspace's {REPOS}/")
    return repo


def _digest(repo: Path | None) -> str:
    """Every file's path and bytes (caches aside), to tell whether the original changed under a run."""
    if repo is None:
        return ""
    digest = hashlib.sha256()
    for path in sorted(p for p in repo.rglob("*") if p.is_file() and not _IGNORED_PART.intersection(p.parts)):
        digest.update(path.relative_to(repo).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _tests_altered(source: Path | None, copy: Path) -> str:
    """The first test file whose bytes in the copy are not the original's, or "" (a case alias is caught here)."""
    if source is None:
        return ""
    for path in source.rglob("*"):
        relative = path.relative_to(source).as_posix()
        if (path.is_file() and _TEST_FILE.search(relative) and not _IGNORED_PART.intersection(path.parts)
                and not _through_link(source, path) and (copy / relative).read_bytes() != path.read_bytes()):
            return relative
    return ""


def _through_link(root: Path, path: Path) -> bool:
    """Whether ``path`` is, or lies under, a link or junction inside ``root`` (the copy leaves those out)."""
    return any(_linked(root.joinpath(*path.relative_to(root).parts[:depth]))
               for depth in range(1, len(path.relative_to(root).parts) + 1))


def _test_snapshot(repo: Path) -> dict[str, str]:
    """Freeze scratch test paths/bytes before execution, including additions and deletions."""
    return {p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in repo.rglob("*") if p.is_file() and not _IGNORED_PART.intersection(p.parts)
            and _TEST_FILE.search(p.relative_to(repo).as_posix())}


def _test_files(source: Path | None, copy: Path) -> int:
    """How many test files this run holds exactly as the original has them: every one in the copy (a patch may write
    none, and ``_tests_altered`` compared each byte for byte). A structural protection, reported; not a check of the
    tests' adequacy and never read as one (founder ruling R-1, 2026-10-09). The probe has no original to compare with,
    so it reports none."""
    if source is None:
        return 0
    return sum(1 for path in copy.rglob("*") if path.is_file() and not _IGNORED_PART.intersection(path.parts)
               and _TEST_FILE.search(path.relative_to(copy).as_posix()))


def _write(copy: Path, files: dict[str, str]) -> None:
    base = copy.resolve()
    for relative, text in files.items():
        target = (copy / relative).resolve()
        if base not in target.parents:
            raise ValueError(f"{relative!r} must be a path inside the repository")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


__all__ = ["COMPONENT", "ENV_DB_DIR", "IN_PLACE", "IN_PLACE_PARAMS", "OPS", "PROBE", "PROBE_TEST", "REPOS", "missing", "ready",
           "run"]
