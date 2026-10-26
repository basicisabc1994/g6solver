# SPDX-License-Identifier: Apache-2.0
"""Your tests, run on your code in ``g6.core.sandbox``: a bounded child process, never G6's own.

:func:`run_tests` writes the code as ``<module>.py`` and the tests beside it in a fresh folder and runs a small runner
under ``g6.core.sandbox.run_script`` (``python -I``, a time and memory bound, an environment with no secret, network
connections to other machines refused by the interpreter's socket guard). The runner imports the tests and calls each
``test*`` function once, and reports each one's outcome on a line marked with a number drawn for this run. The tests
and the code are yours, and they run in the same process as the runner, so what they report is your tests' claim
about your code, not a check G6 made; the sandbox bounds the run, it is not an operating-system sandbox.
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from g6.core.sandbox import run_script

TESTS_FILE = "_g6_tests.py"
_RUNNER = '''
import importlib, inspect, json, sys
sys.path.insert(0, ".")
MARK = {mark!r}
try:
    tests = importlib.import_module("_g6_tests")
except BaseException as exc:
    print(MARK + json.dumps({{"collected": None, "error": (type(exc).__name__ + ": " + str(exc))[:500]}}))
    raise SystemExit(3)
names = sorted(n for n in dir(tests) if n.startswith("test") and callable(getattr(tests, n)))
results = []
for name in names:
    try:
        test = getattr(tests, name)
        if inspect.iscoroutinefunction(test) or inspect.isgeneratorfunction(test) or inspect.isasyncgenfunction(test):
            results.append([name, "unsupported", "async and generator tests have no execution semantics here"])
            continue
        value = test()
        if inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
            results.append([name, "unsupported", "test returned deferred work that was not executed"])
            continue
        results.append([name, "passed", ""])
    except BaseException as exc:
        results.append([name, "failed", (type(exc).__name__ + ": " + str(exc))[:300]])
print(MARK + json.dumps({{"collected": len(names), "results": results}}))
'''


def run_tests(source: str, tests: str, module: str, *, timeout_s: float) -> dict[str, Any]:
    """The outcome of each of ``tests``' test functions on ``source``, as the runner in the child reported them."""
    mark = f"G6-RESULT-{secrets.token_hex(8)}:"
    ran = run_script(_RUNNER.format(mark=mark), {f"{module}.py": source, TESTS_FILE: tests}, timeout_s=timeout_s,
                     mem_mb=512)
    report = None
    for line in reversed(ran.stdout.splitlines()):
        if line.startswith(mark):
            try:
                report = json.loads(line[len(mark):])
            except json.JSONDecodeError:
                report = None
            break
    results = []
    if isinstance(report, dict) and isinstance(report.get("results"), list):
        results = [{"test": str(r[0])[:200], "outcome": str(r[1]), "said": str(r[2])[:300]}
                   for r in report["results"] if isinstance(r, list) and len(r) == 3]
    return {"status": ran.status, "returncode": ran.returncode, "collected": report.get("collected")
            if isinstance(report, dict) else None, "error": report.get("error", "") if isinstance(report, dict) else "",
            "results": results, "stderr": ran.stderr[-1500:], "seconds": round(ran.seconds, 3),
            "limits": ran.limits}


__all__ = ["TESTS_FILE", "run_tests"]
