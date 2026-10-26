# SPDX-License-Identifier: Apache-2.0
"""Small case runner feeding regression_delta; no executors, gates or report store."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from g6.self_training.regression_delta import RunSummary

MAX_CASES = 1000
MAX_SUITE_BYTES = 1024 * 1024
_UNSET = object()


@dataclass(frozen=True)
class BenchmarkCase:
    """One named input and an equality assertion. Explicit None is an expected value."""
    name: str
    input_data: Any
    expected_output: Any = _UNSET


def run_cases(executor: Callable[[Any], Any], cases: Sequence[BenchmarkCase], *,
              max_cases: int = MAX_CASES, max_suite_bytes: int = MAX_SUITE_BYTES) -> RunSummary:
    """Call trusted application code on each asserted case and compare by identity downstream.

    Missing assertions are skipped and make the run unevaluable, even if an output would claim success.
    This is a Python composition seam, not a sandbox. Never pass source text or model-written functions;
    use a caller-owned adapter to core.sandbox for untrusted execution. No labels are issued here.
    """
    # Imported here so that importing the package's pure modules loads no other component (audit 024 REL-012).
    from g6.core import stop
    from g6.core.resources import check_case_bounds

    stopped = stop.engaged()
    if stopped is not None:
        return RunSummary(evaluable=False, diagnostics=(f"{stop.STOPPED}: G6 is stopped: {stopped}",))
    check_case_bounds(
        len(cases), lambda: ((c.name, c.input_data, None if c.expected_output is _UNSET else c.expected_output)
                            for c in cases), max_cases=max_cases, max_suite_bytes=max_suite_bytes)
    cases = tuple(cases)
    names = [c.name for c in cases]
    if len(names) != len(set(names)) or any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError("G6_E_BENCHMARK_CASES: case ids must be non-empty and unique")
    if not callable(executor):
        raise ValueError("G6_E_BENCHMARK_EXECUTOR: supply a callable, never a named echo or source text")
    failing, skipped = set(), set()
    diagnostics = []
    for index, case in enumerate(cases):
        stopped = stop.engaged()
        if stopped is not None:
            diagnostics.append(f"{stop.STOPPED}: G6 is stopped: {stopped}")
            skipped.update(c.name for c in cases[index:])
            break
        if case.expected_output is _UNSET:
            skipped.add(case.name)
            continue
        try:
            actual = executor(case.input_data)
        except (OSError, ImportError) as exc:
            diagnostics.append(f"{case.name}: {type(exc).__name__}: {exc}")
            skipped.add(case.name)
            continue
        except Exception:  # noqa: BLE001 - other executor exceptions are measured case failures
            failing.add(case.name)
            continue
        try:
            if (actual == case.expected_output) is not True:
                failing.add(case.name)
        except Exception:  # noqa: BLE001 - an assertion error is a case failure
            failing.add(case.name)
    return RunSummary(failing=frozenset(failing), collected=frozenset(names), skipped=frozenset(skipped),
                      evaluable=bool(cases) and not skipped and not diagnostics, diagnostics=tuple(diagnostics))
