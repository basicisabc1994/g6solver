# SPDX-License-Identifier: Apache-2.0
"""A paired baseline gate that cannot promote on noise or on extra compute (audit 023 RH-005).

Each arm is run on the same cases. A candidate is promoted only if:

* there are at least ``min_cases`` scored cases (default 30);
* it wins more cases than it loses with a one-sided exact sign test p < ``alpha`` (default 0.05);
* its mean score improvement exceeds ``min_margin``; and
* it used no more model calls per case than the baseline (times ``call_tolerance``), unless the
  caller explicitly sets ``matched=False`` (then the verdict says the comparison is not compute-matched).
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    problem: str
    score: Callable[[str], float]    # maps an answer to [0, 1]; written by the case author, not the arms


@dataclass(frozen=True)
class Arm:
    """``run(problem)`` returns ``(answer, model_calls_used)``."""

    name: str
    run: Callable[[str], tuple[str, int]]


@dataclass
class Verdict:
    promoted: bool
    reason: str
    n: int
    wins: int = 0
    losses: int = 0
    ties: int = 0
    p_value: float = 1.0
    mean_baseline: float = 0.0
    mean_candidate: float = 0.0
    calls_baseline: float = 0.0
    calls_candidate: float = 0.0
    compute_matched: bool = True
    failures: dict[str, int] = field(default_factory=dict)


def _clip(x: float) -> float:
    return min(1.0, max(0.0, x)) if isinstance(x, (int, float)) and math.isfinite(x) else 0.0


def sign_test_p(wins: int, losses: int) -> float:
    """One-sided exact binomial p-value for at least ``wins`` successes out of ``wins + losses``."""
    n = wins + losses
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def _run(arm: Arm, problem: str) -> tuple[str, int, bool]:
    try:
        answer, calls = arm.run(problem)
        return str(answer or ""), max(0, int(calls)), False
    except Exception:  # noqa: BLE001 - a crashing arm produced no answer; counted, never hidden
        return "", 0, True


def compare(baseline: Arm, candidate: Arm, cases: list[Case], *, min_cases: int = 30, alpha: float = 0.05,
            min_margin: float = 0.0, matched: bool = True, call_tolerance: float = 1.0) -> Verdict:
    if min_cases < 1 or not 0 < alpha < 1:
        raise ValueError("min_cases must be >= 1 and 0 < alpha < 1")
    rows, failures = [], {baseline.name: 0, candidate.name: 0}
    for case in cases:
        b_ans, b_calls, b_fail = _run(baseline, case.problem)
        c_ans, c_calls, c_fail = _run(candidate, case.problem)
        failures[baseline.name] += b_fail
        failures[candidate.name] += c_fail
        try:
            b, c = _clip(case.score(b_ans)), _clip(case.score(c_ans))
        except Exception:  # noqa: BLE001 - an unscorable case is dropped, and counted
            failures["scorer"] = failures.get("scorer", 0) + 1
            continue
        rows.append((b, c, b_calls, c_calls))
    n = len(rows)
    if n < min_cases:
        return Verdict(False, "insufficient_cases", n, failures=failures)
    wins = sum(c > b for b, c, *_ in rows)
    losses = sum(c < b for b, c, *_ in rows)
    mean_b = statistics.fmean(r[0] for r in rows)
    mean_c = statistics.fmean(r[1] for r in rows)
    calls_b = statistics.fmean(r[2] for r in rows)
    calls_c = statistics.fmean(r[3] for r in rows)
    p = sign_test_p(wins, losses)
    over_budget = calls_c > calls_b * call_tolerance
    v = Verdict(False, "", n, wins, losses, n - wins - losses, round(p, 6), round(mean_b, 4), round(mean_c, 4),
                round(calls_b, 3), round(calls_c, 3), not over_budget, failures)
    if mean_c - mean_b <= min_margin or wins <= losses:
        v.reason = "no_improvement"
    elif p >= alpha:
        v.reason = "within_noise"
    elif over_budget and matched:
        v.reason = "not_compute_matched"
    else:
        v.promoted, v.reason = True, "beats_baseline" if not over_budget else "beats_baseline_with_more_compute"
    return v
