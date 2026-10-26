# SPDX-License-Identifier: Apache-2.0
"""Verification gate and replay evaluation (framework sections 4.iii and 5).

Stage-1 gate: before an abstraction earns any evaluation budget, discharge
its proof obligations empirically with property-based testing:

  extensivity   every visited config is covered by the abstraction fitted
                on a set containing it              X subset gamma(alpha(X))
  monotonicity  growing the fitted set never uncovers a config
  bound-sound   no observed score inside a cell exceeds the cell's bound
                (violations are soundness counterexamples, handed to CEGAR)

Replay evaluation: because sound abstractions only prune and reorder, a
candidate can be scored *counterfactually* against the recorded trace at
zero fresh-evaluation cost. We replay the trace in order; configs the
abstraction would have pruned are skipped (their budget refunded); the
refunded budget extends the horizon. Reported:

  best_with / best_without   incumbent score at budget B with vs without
  evals_saved                pruned evaluations (refunded budget)
  regret_events              prunes that discarded a config better than the
                             incumbent at that moment -> soundness cexs
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hypothesis import given, settings, strategies as st, HealthCheck

from .abstractions import Abstraction, Sufficient
from .domain import ConcreteDomain, Record


# ------------------------------------------------------------------ #
# Stage 1: property-based soundness gate
# ------------------------------------------------------------------ #
@dataclass
class GateReport:
    passed: bool
    checks: dict[str, bool]
    counterexamples: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    calibration: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checks": self.checks,
                "n_counterexamples": len(self.counterexamples),
                "counterexamples": self.counterexamples[:10],
                "notes": self.notes, "calibration": self.calibration}


def run_gate(abstraction: Abstraction, domain: ConcreteDomain,
             max_examples: int = 50, tolerance: float = 1e-9) -> GateReport:
    n = len(domain.records)
    if n < 4:
        return GateReport(False, {}, notes=["need >= 4 records to gate"])

    checks: dict[str, bool] = {}
    cexs: list[dict[str, Any]] = []
    notes: list[str] = []

    idx = st.lists(st.integers(min_value=0, max_value=n - 1),
                   min_size=2, max_size=min(n, 30), unique=True)

    # -- extensivity: X subset gamma(alpha(X)) ------------------------- #
    ext_fail: list[int] = []

    @settings(max_examples=max_examples, deadline=None,
              derandomize=True,  # verdicts must be reproducible to
              # debug the debugger: same inputs -> same counterexamples
              suppress_health_check=list(HealthCheck))
    @given(idx)
    def _extensivity(indices: list[int]) -> None:
        sub = _subdomain(domain, indices)
        fitted = _clone(abstraction).fit(sub)
        for rec in sub.records:
            if not fitted.covers(rec.config):
                ext_fail.append(rec.index)

    ext_ok = _run(_extensivity, notes, "extensivity")
    checks["extensivity"] = ext_ok and not ext_fail
    if ext_fail:
        cexs.append({"kind": "extensivity",
                     "detail": f"fitted-on configs not covered: {sorted(set(ext_fail))[:5]}"})

    # -- monotonicity: cover(alpha(X)) subset cover(alpha(X u Y)) ------- #
    mono_fail: list[int] = []

    @settings(max_examples=max_examples, deadline=None,
              derandomize=True,  # verdicts must be reproducible to
              # debug the debugger: same inputs -> same counterexamples
              suppress_health_check=list(HealthCheck))
    @given(idx, idx)
    def _monotonicity(a: list[int], b: list[int]) -> None:
        small = _subdomain(domain, a)
        big = _subdomain(domain, sorted(set(a) | set(b)))
        f_small = _clone(abstraction).fit(small)
        f_big = _clone(abstraction).fit(big)
        for rec in domain.records:
            if f_small.covers(rec.config) and not f_big.covers(rec.config):
                mono_fail.append(rec.index)

    mono_ok = _run(_monotonicity, notes, "monotonicity")
    checks["monotonicity"] = mono_ok and not mono_fail
    if mono_fail:
        cexs.append({"kind": "monotonicity",
                     "detail": f"coverage lost when fitting set grew: {sorted(set(mono_fail))[:5]}"})

    # -- prospective bound soundness + calibration ----------------------- #
    # Fit only on the prefix BEFORE each record (mirroring how pruning is
    # actually used); a record covered by the prefix-fitted abstraction must
    # not exceed its cell bound. Retrospective checks are trivially
    # satisfied once a record is inside its own fit, so they test nothing.
    # Calibration (self-debugging input): how often was a finite bound even
    # issued (binding opportunity), how often violated, and mean slack —
    # bounds that never bind make a mechanism inert; bounds that violate
    # make it unsound; both are diagnosable states, not silent ones.
    bound_viol = []
    fitted = _clone(abstraction)
    warmup = 3
    n_covered = n_finite = 0
    slack_sum = 0.0
    for i in range(warmup, n):
        fitted.fit(domain, upto=i)  # incremental: O(1) amortised per step
        rec = domain.records[i]
        if fitted.covers(rec.config):
            n_covered += 1
            b = fitted.bound(rec.config)
            s = domain.score(rec.objective)
            if b != float("inf"):
                n_finite += 1
                slack_sum += b - s
            if s > b + tolerance:
                bound_viol.append({"kind": "bound", "index": rec.index,
                                   "score": s, "bound": b,
                                   "config": rec.config})
    checks["bound_soundness"] = not bound_viol
    cexs.extend(bound_viol)
    n_prospective = max(1, n - warmup)
    calibration = {
        "coverage_rate": round(n_covered / n_prospective, 4),
        "finite_bound_rate": round(n_finite / n_prospective, 4),
        "violation_rate": round(len(bound_viol) / n_prospective, 4),
        "mean_slack": round(slack_sum / n_finite, 6) if n_finite else None,
    }

    passed = all(checks.values())
    return GateReport(passed, checks, cexs, notes, calibration)


def _run(fn: Any, notes: list[str], name: str) -> bool:
    """Run one mandatory property check. Returns False if it aborted.

    An aborted check found no violation only because it did not finish, so it is reported
    as inconclusive and the caller records it as not passed (audit 001, F-02: fail closed).
    """
    try:
        fn()
        return True
    except Exception as exc:  # hypothesis raises on flaky strategies etc.
        notes.append(f"{name} check aborted (inconclusive, counted as not passed): {type(exc).__name__}: {exc}")
        return False


def _subdomain(domain: ConcreteDomain, indices: list[int]) -> ConcreteDomain:
    recs = [Record(domain.records[i].config, domain.records[i].objective, j)
            for j, i in enumerate(sorted(indices))]
    return ConcreteDomain(params=domain.params, records=recs,
                          objective_name=domain.objective_name,
                          maximise=domain.maximise, name=domain.name + "/sub")


def _clone(abstraction: Abstraction) -> Abstraction:
    from .abstractions import from_spec
    return from_spec(abstraction.spec())


# ------------------------------------------------------------------ #
# Stage 2 input: counterfactual replay evaluation
# ------------------------------------------------------------------ #
@dataclass
class ReplayReport:
    budget: int
    best_without: float
    best_with: float
    evals_used: int
    evals_saved: int
    horizon_reached: int
    regret_events: list[dict[str, Any]] = field(default_factory=list)
    reorder_gain: float | None = None
    evaluated_indices: list[int] = field(default_factory=list)
    skipped_indices: list[int] = field(default_factory=list)

    @property
    def value_gain(self) -> float:
        return self.best_with - self.best_without

    def to_dict(self) -> dict[str, Any]:
        return {"budget": self.budget, "best_without": self.best_without,
                "best_with": self.best_with, "value_gain": self.value_gain,
                "evals_used": self.evals_used, "evals_saved": self.evals_saved,
                "horizon_reached": self.horizon_reached,
                "n_regret_events": len(self.regret_events),
                "regret_events": self.regret_events[:10],
                "reorder_gain": self.reorder_gain,
                "n_evaluated": len(self.evaluated_indices),
                "n_skipped": len(self.skipped_indices)}


def replay(abstraction: Abstraction, domain: ConcreteDomain,
           budget: int | None = None, warmup: int = 3) -> ReplayReport:
    """Counterfactual replay of the recorded trace under `abstraction`.

    The abstraction is fitted only on records the counterfactual agent actually
    evaluated (no lookahead, and no learning from skipped records: audit 001, F-01 —
    the archive refitted on the whole prefix, so pruning decisions used scores of
    points it had pruned). Skipped scores are read only to audit regret. Pruned
    records refund budget, extending the horizon into later trace records — the
    mechanism's value is measured as incumbent improvement at equal evaluation
    cost, per (BLP)'s V_B with f evaluated concretely (axiom A0).
    """
    n = len(domain.records)
    B = budget if budget is not None else max(1, n // 2)
    B = min(B, n)

    # Baseline: first B records in trace order.
    best_without = max(domain.score(r.objective) for r in domain.records[:B])

    fitted = _clone(abstraction).fit(domain, upto=0)  # empty model bound to this domain
    incumbent = -float("inf")
    used = saved = 0
    i = 0
    regrets: list[dict[str, Any]] = []
    evaluated: list[int] = []
    skipped: list[int] = []
    while i < n and used < B:
        rec = domain.records[i]
        if i >= warmup and fitted.prune(rec.config, incumbent):
            saved += 1
            skipped.append(i)
            s = domain.score(rec.objective)  # oracle read for the regret audit only
            if s > incumbent:  # pruned something better: soundness regret
                regrets.append({"index": rec.index, "score": s,
                                "incumbent": incumbent,
                                "bound": fitted.bound(rec.config),
                                "config": rec.config})
            i += 1
            continue
        used += 1
        evaluated.append(i)
        incumbent = max(incumbent, domain.score(rec.objective))
        fitted._absorb(rec)  # the model learns only what was evaluated
        i += 1
    fitted._fitted_n = None  # model no longer equals a trace prefix; force refit on reuse

    # Reorder gain for guidance abstractions (Sufficient): rank remaining
    # trace by UCB and measure incumbent at the same budget.
    reorder_gain = None
    if isinstance(abstraction, Sufficient) and n > warmup:
        fitted2 = _clone(abstraction).fit(domain, upto=warmup)
        assert isinstance(fitted2, Sufficient)
        rest = domain.records[warmup:]
        ranked = sorted(rest, key=lambda r: -fitted2.ucb(r.config))
        take = max(0, B - warmup)
        pool = domain.records[:warmup] + ranked[:take]
        reorder_best = max(domain.score(r.objective) for r in pool)
        reorder_gain = reorder_best - best_without

    return ReplayReport(budget=B, best_without=best_without,
                        best_with=max(incumbent, -float("inf")),
                        evals_used=used, evals_saved=saved,
                        horizon_reached=i, regret_events=regrets,
                        reorder_gain=reorder_gain, evaluated_indices=evaluated,
                        skipped_indices=skipped)
