# SPDX-License-Identifier: Apache-2.0
"""Readiness from this harness's observations, never caller-declared rates or approvals."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from g6.core.resources import check_case_bounds
from g6.harness import Memory, Standard, _report, run

MAX_CASES = 1000
MAX_SUITE_BYTES = 1024 * 1024


def evaluate_cases(standard: Standard, cases: Mapping[str, str], executor: Callable[[Any, str], str], *,
                   baseline_executor: Callable[[Any, str], str] | None = None,
                   memory: Memory | None = None, max_cases: int = MAX_CASES,
                   max_suite_bytes: int = MAX_SUITE_BYTES) -> dict[str, Any]:
    """Reserve cases after freezing checks, then run candidate and baseline against the same cases.

    Inputs must differ from the check controls. This observes a local hold-out only: it cannot
    establish that a model never saw the cases in training. Callables are trusted application code.
    """
    from g6.core import stop
    from g6.harness import _standard_sha, _try

    if stop.engaged() is not None:
        return run(standard, None, memory=memory)

    check_case_bounds(len(cases), lambda: cases.items(), max_cases=max_cases, max_suite_bytes=max_suite_bytes)
    cases = dict(cases)
    controls = {o for check in standard.checks for o in (*check.known_bad, check.known_good) if o is not None}
    if (not cases or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) for k, v in cases.items())
            or len(set(cases.values())) != len(cases) or any(value in controls for value in cases.values())):
        raise ValueError("G6_E_HARNESS_CASES: supply distinct named inputs, separate from the controls")
    frozen = _standard_sha(standard)
    own = memory is None
    memory = memory if memory is not None else Memory()
    candidate, baseline = {}, {}
    try:
        for name, value in cases.items():
            candidate[name] = run(standard, lambda contract, value=value: executor(contract, value), memory=memory)
            if candidate[name].get("stage") == "stop":
                return candidate[name]
            if baseline_executor is not None:
                baseline[name] = run(standard, lambda contract, value=value: baseline_executor(contract, value),
                                     memory=memory)
                if baseline[name].get("stage") == "stop":
                    return baseline[name]
        # Recheck every control after both executors, even when an earlier control no longer fails.
        controls_still_fail = all([_try(check, bad) == "fail"
                                   for check in standard.checks for bad in check.known_bad])
        stable = frozen == _standard_sha(standard)
        stable = stable and controls_still_fail
        stable = stable and all(not r.get("plan", {}).get("changed_checks")
                                for r in (*candidate.values(), *baseline.values()))
        memory._remember_evaluation(standard.family_key, frozen, candidate, baseline, stable)
        return readiness_report(standard.family_key, memory=memory)
    finally:
        if own:
            memory.close()


def _unevaluable(record: dict) -> str | None:
    results = record.get("results", {})
    if record.get("stage") != "done" or not results:
        return (record.get("warning_card") or {}).get("message") or "evaluation did not complete"
    if set(results) != set(record.get("plan", {}).get("checks", {})) or any(
            value not in {"pass", "fail"} for value in results.values()):
        return "one or more check outcomes are unknown"
    return None


def _passed(record: dict) -> bool:
    results = record.get("results", {})
    return (record.get("stage") == "done" and record["completion_state"] != "blocked-escalated"
            and bool(results) and all(v == "pass" for v in results.values()))


def readiness_report(family: str, *, memory: Memory | None = None) -> dict[str, Any]:
    """Latest standard and latest paired case batch only; ordinary runs never become held-out evidence."""
    own = memory is None
    memory = memory if memory is not None else Memory()
    family = " ".join(family.lower().split())
    try:
        rows = memory.runs(family)
        latest = rows[-1] if rows else {}
        batch = memory._evaluation(family)
        if batch and batch["standard_sha"] != latest.get("standard_sha"):
            batch["stable"] = False
        if batch:
            batch_ids = set(batch["candidate"].values()) | set(batch["baseline"].values())
            if latest.get("run_id") not in batch_ids:
                batch = None
        by_id = {r["run_id"]: r for r in rows}
        candidate = [by_id[r] for r in batch["candidate"].values()] if batch else []
        baseline = [by_id[r] for r in batch["baseline"].values()] if batch else []
        observed = candidate if batch else ([latest] if latest else [])
        frozen = bool(observed) and all(r.get("plan", {}).get("checks") and not
                                       r["plan"].get("changed_checks") for r in observed)
        if batch:
            frozen = frozen and batch["stable"]
        tested = frozen and all(
            all(r["plan"].get("known_bad_results", {}).get(k) and
                all(r["plan"]["known_bad_results"][k]) for k in r["plan"]["checks"])
            and not r["plan"].get("broken_checks") for r in observed)
        unavailable = [f"{side} case {name!r}: {reason}"
                       for side in ("candidate", "baseline")
                       for name, run_id in (batch[side].items() if batch else ())
                       if (reason := _unevaluable(by_id[run_id])) is not None]
        rate = (sum(_passed(r) for r in candidate) / len(candidate)
                if candidate and all(_unevaluable(r) is None for r in candidate) else None)
        base_rate = (sum(_passed(r) for r in baseline) / len(baseline)
                     if baseline and all(_unevaluable(r) is None for r in baseline) else None)
        compared = bool(batch and baseline and batch["candidate"].keys() == batch["baseline"].keys()
                        and not unavailable)
        beaten = rate > base_rate if compared else None
        blocked = sum(r["completion_state"] == "blocked-escalated" for r in observed)
        tier = "prototype" if tested else "none"
        if tested and rate == 1.0 and blocked == 0:
            tier = "publishable" if beaten else "reusable"
        return _report("local readiness observations; no claim of independent validation or training-data isolation",
                       family=family, tier=tier, checks_frozen=frozen, known_bad_tested=tested,
                       held_out_pass_rate=rate, held_out_runs=len(candidate), baseline_pass_rate=base_rate,
                       baseline_compared=compared, outperforms_baseline=beaten,
                       comparison_unavailable=unavailable,
                       blocking_failures_observed=blocked if observed else None,
                       evidence=[{"kind": "run", "run_id": r["run_id"]} for r in observed + baseline])
    finally:
        if own:
            memory.close()
