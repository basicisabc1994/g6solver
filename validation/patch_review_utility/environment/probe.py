# SPDX-License-Identifier: Apache-2.0
"""The development check's probe, run by G6's toolchain Python inside a task image's G6 container
(``dev_check.py``): G6's repository check on ``/app``, then one confined run of one unit-test folder through G6 with
the image's own interpreter. Only counts, verdicts and reasons are written, never a test's name or a file's contents.

    /opt/g6/python/bin/python3 -I probe.py <out.json> <test folder> <seconds>

``G6_REPO_PYTHON`` names the image's interpreter. Where the G6 under test does not read that setting yet, this probe
supplies it to ``repo_tests.interpreter`` itself, and says so (``g6_reads_repo_python``).
"""
from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
    out, folder, seconds = argv[1], argv[2], int(argv[3])
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
    found["g6_reads_repo_python"] = repo_tests.interpreter(root) == wanted
    if wanted and not found["g6_reads_repo_python"]:
        repo_tests.interpreter = lambda repo: wanted   # until G6 reads the setting itself
    try:
        ran = repo_tests.run(root, [folder], timeout_s=seconds)
    except repo_tests.RepoRefused as exc:
        found["confined_run"] = {"result": "refused", "code": exc.code, "reason": str(exc)[:500]}
        return write()
    isolation = ran.get("isolation") or {}
    found["confined_run"] = {
        "result": "ran", "folder": folder, "verdict": ran.get("verdict"), "collected": len(ran.get("collected_ids") or []),
        "passed": ran.get("passed"), "failed": ran.get("failed"), "skipped": ran.get("not_passed"),
        "deselected": ran.get("deselected"), "seconds": ran.get("seconds"), "python": ran.get("python"),
        "interpreter_source": ran.get("interpreter_source"), "backend": isolation.get("backend"),
        "network": isolation.get("network"), "said": (ran.get("said") or "")[:300]}
    return write()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
