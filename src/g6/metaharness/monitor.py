# SPDX-License-Identifier: Apache-2.0
"""The monitor: an audit stream and a drift test that may only demote (META_HARNESS_PLAN.md Phase 3, MH-8, MH-12).

After a person activates a version, a share of live runs (at least the workflow's ``audit_floor``) is also run under
the previous approved version and judged by the same checks. Each audited run gives a paired difference in
``[-1, 1]``: the previous version's outcome minus the current one's (1 = the current version failed where the previous
passed). The monitor demotes when the mean difference over the ``n`` audited pairs since activation exceeds

    t_n = sqrt(2 * ln(n * (n + 1) / alpha) / n)

which is Hoeffding's bound for differences of range 2 at level ``alpha / (n (n + 1))``. The levels sum to ``alpha``
over every ``n``, so the chance of a false demotion stays below ``alpha`` however often the monitor is consulted
(anytime-valid by a union bound). A demotion moves the head to the version approved before the current one, never
further, and opens a re-evaluation ticket.

What an audit compares is the standard's checks on live tasks, which carry no held-out answers: a version that keeps
passing its checks but answers worse is not caught here (that is what the held-out gate is for).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_ALPHA = 0.05
MIN_PAIRS = 5


def threshold(n: int, alpha: float = DEFAULT_ALPHA) -> float:
    if n < 1:
        return math.inf
    return math.sqrt(2 * math.log(n * (n + 1) / alpha) / n)


def pairs_to_detect(drift: float, alpha: float = DEFAULT_ALPHA, *, limit: int = 100_000) -> int:
    """The smallest number of audited pairs at which a constant drift of ``drift`` (a mean difference) is over the
    threshold: the bound n* a test can check demotion against (CR Theorem D.2, for this test)."""
    for n in range(MIN_PAIRS, limit):
        if drift > threshold(n, alpha):
            return n
    return limit


@dataclass(frozen=True)
class DriftVerdict:
    pairs: int
    mean_difference: float
    threshold: float
    demote: bool


def assess(differences: list[float], alpha: float = DEFAULT_ALPHA) -> DriftVerdict:
    n = len(differences)
    mean = sum(differences) / n if n else 0.0
    t = threshold(n, alpha)
    return DriftVerdict(n, round(mean, 6), round(t, 6) if math.isfinite(t) else t,
                        n >= MIN_PAIRS and mean > t)


def due(live_count: int, rate: float) -> bool:
    """Whether live run number ``live_count`` (1, 2, ...) is audited: every ``ceil(1 / rate)``-th run, deterministic."""
    every = max(1, math.ceil(1 / rate))
    return live_count % every == 0


@dataclass(frozen=True)
class ObservationWarning:
    kind: str
    fired: bool
    p_value: float
    samples: int
    detail: str


def detect_anchor_decoupling(internal, anchor, *, alpha: float = 0.05) -> ObservationWarning:
    """Sign test on successive paired dev-minus-anchor changes; a diagnostic, not a causal conclusion."""
    from g6.reasoning.compare import sign_test_p

    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    if len(internal) != len(anchor) or len(internal) < 3:
        return ObservationWarning("anchor_decoupling", False, 1.0, 0, "insufficient aligned version scores")
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in (*internal, *anchor)):
        raise ValueError("scores must be finite shares")
    deltas = [(internal[i] - internal[i - 1]) - (anchor[i] - anchor[i - 1]) for i in range(1, len(internal))]
    wins, losses = sum(d > 1e-12 for d in deltas), sum(d < -1e-12 for d in deltas)
    p = sign_test_p(wins, losses)
    fired = internal[-1] > internal[0] and wins > losses and p <= alpha
    return ObservationWarning("anchor_decoupling", fired, p, len(deltas),
                              "paired version changes; dependent tuning can invalidate the sign-test calibration")


def detect_agreement_collapse(baseline: tuple[int, int], recent: tuple[int, int], *,
                              judges: tuple[str, str] = ("standard", "oracle"),
                              alpha: float = 0.05, min_samples: int = 20) -> ObservationWarning:
    """One-sided exact two-proportion test; windows contain (agree, disagree) counts for any judge pair."""
    if (not 0 < alpha < 1 or min_samples < 1 or len(judges) != 2 or judges[0] == judges[1]
            or len(baseline) != 2 or len(recent) != 2
            or any(type(n) is not int or n < 0 for n in (*baseline, *recent))):
        raise ValueError("invalid judge pair, counts or policy")
    n1, n2 = sum(baseline), sum(recent)
    if min(n1, n2) < min_samples:
        return ObservationWarning("agreement_collapse", False, 1.0, n1 + n2, "insufficient judge pairs")
    # Conditional exact test: recent disagreements no greater than observed, holding the margins fixed.
    disagreements = baseline[1] + recent[1]
    denominator = math.comb(n1 + n2, n2)
    p = min(1.0, sum(math.comb(disagreements, k) * math.comb(n1 + n2 - disagreements, n2 - k)
                     / denominator for k in range(max(0, n2 - (n1 + n2 - disagreements)), recent[1] + 1)))
    fired = recent[1] / n2 < baseline[1] / n1 and p <= alpha
    return ObservationWarning("agreement_collapse", fired, p, n1 + n2,
                              f"{judges[0]} / {judges[1]}: falling disagreement is a warning, not proof of shared error")


def version_observations(rows: list[dict], workflow_digest: str, *, alpha: float) -> list[dict]:
    """Latest complete baseline per approved version, on this frozen workflow; no caller score input."""
    from dataclasses import asdict

    versions: dict[int, dict] = {}
    for row in rows:
        if (row.get("purpose") != "baseline" or row.get("partial") or row.get("workflow_digest") != workflow_digest
                or "head_version" not in row or not row.get("n")):
            continue
        versions.setdefault(row["head_version"], {})[row["split"]] = row
    pairs = [(v, r) for v, r in sorted(versions.items()) if "dev" in r and "held_out" in r]
    if len(pairs) < 2:
        return []
    # Spending across version counts and the two detectors limits repeated consultations; rows are deduplicated.
    level = alpha / (2 * len(pairs) * (len(pairs) + 1))
    dev = [r["dev"]["estimates"]["success"]["base_mean"] for _, r in pairs]
    held = [r["held_out"]["estimates"]["success"]["base_mean"] for _, r in pairs]
    warnings = [detect_anchor_decoupling(dev, held, alpha=level)]
    first, last = pairs[0][1]["held_out"], pairs[-1][1]["held_out"]
    if "judge_counts" in first and "judge_counts" in last:
        warnings.append(detect_agreement_collapse(tuple(first["judge_counts"]), tuple(last["judge_counts"]), alpha=level))
    return [{**asdict(w), "versions": [v for v, _ in pairs], "workflow_digest": workflow_digest,
             "failure_mode": "unseen_early_warning"} for w in warnings if w.fired]
