# SPDX-License-Identifier: Apache-2.0
"""Arm G's check (PROTOCOL.md §4): G6 checks the stage-1 patch with its own shipped functions, unchanged.

It runs inside a throwaway container made from stage 1's finished state, under the standalone Python with G6
installed from the wheel built from the release ref, and prints one JSON object: ``flag``, the ``findings`` (each a
``check`` and what G6 ``said``), and every reply as G6 returned it (``replies``). It sees only the repository as stage 1
left it. The benchmark's hidden tests are not in that container (the runner asserts it), and no grading has happened.

The checks, in order:

* ``g6.workforce.repo_tests.change`` and ``diff_text``: no file changed is a finding;
* ``g6.coding_agent_gate.scan_diff``: a test shortcut in the diff;
* ``g6.safety_engine.detect_test_weakening``: the change's test files before and after;
* the changed Python files that do not parse (``swe_diagnostics.patch_check``'s rule);
* ``g6.workforce.repo_tests.derive_tests``, then ``repo_tests.run`` on those visible tests: a ``failed`` verdict is a
  finding. Any other verdict (a collection error G6 cannot place in the repository, no tests, a timeout) is recorded
  and is not a finding. ``repo_tests`` runs pytest only, so for Go and JavaScript repositories G6 has no test run.

``repo_tests.run`` uses the repository's own ``.venv`` or ``venv`` when there is one, else the Python running G6, which
lacks the project's packages. Given ``--python`` (the container's own ``python3``), the check points a ``.venv`` at it
for the run and removes it after, in a container that is thrown away in any case.

    /opt/g6py/bin/python3 -I g6check.py --repo /app [--python /usr/bin/python3]
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import json
import shutil
import sys
from collections.abc import Callable, Iterator
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

TEST_TIMEOUT_S = 600


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return value


@contextlib.contextmanager
def _venv_link(repo: Path, python: str | None) -> Iterator[None]:
    made = None
    if python and not (repo / ".venv").exists() and not (repo / "venv").exists():
        made = repo / ".venv"
        (made / "bin").mkdir(parents=True)
        (made / "bin" / "python").symlink_to(python)
    try:
        yield
    finally:
        if made is not None:
            shutil.rmtree(made, ignore_errors=True)


def check(repo: str | Path, *, python: str | None = None, run_tests: Callable[..., dict] | None = None,
          timeout_s: int = TEST_TIMEOUT_S) -> dict[str, Any]:
    import g6
    from g6.coding_agent_gate import scan_diff
    from g6.safety_engine import detect_test_weakening
    from g6.workforce import repo_tests

    root = repo_tests.checked_repo(repo)
    change = repo_tests.change(root, "HEAD")
    diff = repo_tests.diff_text(change)
    findings: list[dict[str, Any]] = []
    replies: dict[str, Any] = {"change": {"files": list(change.files), "sources": list(change.sources),
                                          "tests_before": sorted(change.before), "tests_after": sorted(change.after),
                                          "runner_files": list(change.runner_files)}}
    if not change.files:
        findings.append({"check": "change", "said": "no file is changed: the task is not done"})
    hits = scan_diff(diff)
    replies["coding_agent_gate.scan_diff"] = list(hits)
    if hits:
        findings.append({"check": "shortcut", "said": "the diff takes a test shortcut (coding_agent_gate.scan_diff: "
                                                      + ", ".join(sorted(hits)) + ")"})
    weakening = detect_test_weakening(dict(change.before), dict(change.after))
    replies["safety_engine.detect_test_weakening"] = _plain(weakening)
    if weakening.weakened:
        findings.append({"check": "weakening", "said": "the change weakens the tests (safety_engine."
                                                       "detect_test_weakening: " + "; ".join(weakening.reasons) + ")"})
    broken = []
    for rel in (*change.sources, *change.after):
        try:
            ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError) as exc:
            broken.append({"file": rel, "line": getattr(exc, "lineno", None), "message": str(getattr(exc, "msg", exc))})
    replies["does_not_parse"] = broken
    for item in broken:
        findings.append({"check": "parse", "said": f"{item['file']} does not parse (line {item['line']}: "
                                                   f"{item['message']})"})
    derived, more = repo_tests.derive_tests(change)
    replies["workforce.repo_tests.derive_tests"] = {"tests": derived, "more": more}
    if derived:
        runner = run_tests or repo_tests.run
        with _venv_link(root, python):
            try:
                result = runner(root, list(derived), timeout_s=timeout_s)
            except repo_tests.RepoRefused as exc:
                result = {"verdict": "refused", "code": exc.code, "said": str(exc)}
        replies["workforce.repo_tests.run"] = _plain(result)
        if result.get("verdict") == repo_tests.FAILED:
            tail = str(result.get("output") or "")[-4000:]
            findings.append({"check": "visible_tests", "said": f"the repository's own tests that the change touches "
                                                               f"fail ({', '.join(derived)}): "
                                                               f"{result.get('said', '')}", "output_tail": tail})
    return {"flag": bool(findings), "findings": findings, "tests": list(derived), "replies": replies,
            "g6_version": getattr(g6, "__version__", "")}


def findings_text(findings: list[dict[str, Any]]) -> str:
    """The findings as Claude Code is given them: each check's words, and a failing run's output tail."""
    parts = []
    for f in findings:
        parts.append(f"- {f['said']}")
        if f.get("output_tail"):
            parts.append("  Output (end):\n" + "\n".join("    " + line for line in f["output_tail"].splitlines()))
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--python", default="")
    args = ap.parse_args()
    out = check(args.repo, python=args.python or None)
    out["findings_text"] = findings_text(out["findings"])
    sys.stdout.write(json.dumps(out, default=str) + "\n")


if __name__ == "__main__":
    main()
