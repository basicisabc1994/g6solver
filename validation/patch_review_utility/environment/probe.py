# SPDX-License-Identifier: Apache-2.0
"""The development check's probe, run by G6's toolchain Python inside a task image's G6 container
(``dev_check.py``): G6's repository check on ``/app``, then one confined run of one unit-test folder through G6 with
the image's own interpreter. Only counts, verdicts and reasons are written, never a test's name or a file's contents.

    /opt/g6/python/bin/python3 -I probe.py <out.json> <test folder> <seconds> [<import roots, JSON> [<control test>]]

``G6_REPO_PYTHON`` names the image's interpreter. Where the G6 under test does not read that setting yet, this probe
supplies it to ``repo_tests.interpreter`` itself, and says so (``g6_reads_repo_python``); once G6 reads it, the probe
leaves it alone. ``G6_REPO_ENV`` carries the repository's test environment; when the G6 under test does not read it
(``g6_applies_repo_env``: it has no ``repo_tests.REPO_ENV``), the probe runs the folder once more with it applied, as
a diagnostic it labels as simulated, and once G6 reads it, the probe simulates nothing.
"""
from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
    out, folder, seconds = argv[1], argv[2], int(argv[3])
    roots = tuple(json.loads(argv[4])) if len(argv) > 4 else ()
    control = argv[5] if len(argv) > 5 else ""
    from g6.workforce import repo_tests

    found: dict = {"repo_python": os.environ.get("G6_REPO_PYTHON", "")}

    def write() -> int:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(found, handle, indent=2)
        return 0

    try:
        root = repo_tests.checked_repo("/app")
        change = repo_tests.change(root, "HEAD")
        scan = repo_tests.ignored_inputs(root)
    except repo_tests.RepoRefused as exc:
        found["repository_check"] = {"result": "refused", "code": exc.code, "reason": str(exc)[:500]}
        return write()
    found["repository_check"] = {"result": "passed", "changed_files": len(change.files),
                                 "ignored_loadable": len(scan.loadable), "ignored_other": len(scan.other),
                                 "scan_complete": scan.complete}
    wanted = found["repo_python"]
    found["g6_reads_repo_python"] = hasattr(repo_tests, "REPO_PYTHON") or repo_tests.interpreter(root) == wanted
    if wanted and not found["g6_reads_repo_python"]:
        repo_tests.interpreter = lambda repo: wanted   # until G6 reads the setting itself
    found["import_roots"] = list(roots)
    try:
        ran = repo_tests.run(root, [folder], timeout_s=seconds, roots=roots)
    except repo_tests.RepoRefused as exc:
        found["confined_run"] = {"result": "refused", "code": exc.code, "reason": str(exc)[:500]}
        return write()
    found["confined_run"] = _summary(ran, folder)
    if control:
        found["control_test"] = _outcome(ran, control)
    extra = json.loads(os.environ.get("G6_REPO_ENV") or "{}")
    # A G6 that reads G6_REPO_ENV (repo_tests.REPO_ENV) sets the variables in the test process itself
    found["g6_applies_repo_env"] = hasattr(repo_tests, "REPO_ENV")
    if extra and not found["g6_applies_repo_env"]:
        # Diagnostic only: what the run gives when G6 applies G6_REPO_ENV, which the G6 under test does not yet.
        # Two layers drop the variables: the tests' environment (repo_tests.child_env) and the confined spawn's own
        # list (confine.child_environment).
        from g6.core import confine

        tests_env, spawn_env = repo_tests.child_env, confine.child_environment
        repo_tests.child_env = lambda scratch, *, network: {**tests_env(scratch, network=network), **extra}
        confine.child_environment = lambda env=None, *, confinement=None: {
            **spawn_env(env, confinement=confinement), **extra}
        simulated = repo_tests.run(root, [folder], timeout_s=seconds, roots=roots)
        found["confined_run_with_test_env_simulated"] = _summary(simulated, folder)
        if control:
            found["control_test_with_test_env_simulated"] = _outcome(simulated, control)
    return write()


def _outcome(ran: dict, test: str) -> str:
    """One test's outcome in a run, its id read against the root folder the run reports."""
    prefix = (ran.get("rootdir") or "").strip("/")
    for node, phases in (ran.get("test_outcomes") or {}).items():
        if (f"{prefix}/{node}" if prefix else node) == test:
            states = set(phases.values())
            if "failed" in states:
                return "failed"
            return "passed" if phases.get("call") == "passed" else "skipped" if "skipped" in states else "missing"
    return "missing"


def _summary(ran: dict, folder: str) -> dict:
    isolation = ran.get("isolation") or {}
    return {"result": "ran", "folder": folder, "verdict": ran.get("verdict"),
            "collected": len(ran.get("collected_ids") or []), "passed": ran.get("passed"), "failed": ran.get("failed"),
            "skipped": ran.get("not_passed"), "deselected": ran.get("deselected"), "seconds": ran.get("seconds"),
            "python": ran.get("python"), "interpreter_source": ran.get("interpreter_source"),
            "backend": isolation.get("backend"), "network": isolation.get("network"),
            "said": (ran.get("said") or "")[:300],
            # why a run did not run: pytest's last error lines (an exception's message, never a test's name)
            "error_tail": [] if ran.get("verdict") in ("passed", "failed") else [
                line.strip()[:200] for line in (ran.get("output") or "").splitlines()
                if "Error" in line or "error:" in line or line.startswith("E ")][-4:]}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
