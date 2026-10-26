# SPDX-License-Identifier: Apache-2.0
"""Opt-in histories of checks actually observed by solve; never caller quality scores."""
from __future__ import annotations

import hashlib
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

from g6.core.binomial import lower_bound, upper_bound
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.storage import open_db, state_path
from g6.solver.schema import SolverOutput

# Process-local provenance, not a durable credential. Untrusted Python must never
# run in this process. Copies, deserialised results and edited outputs are refused.
_RECEIPT = object()

#: The interval every trend reports, stated in its evidence (SOLVER_ACCURACY review: no normal approximation).
METHOD = "Clopper-Pearson exact two-sided 95% interval (2.5% in each tail) on checked outcomes"
#: The fewest checked outcomes a regression check accepts; a caller may raise it, never lower it.
MIN_SAMPLES = 10


def _interval(passed: int, n: int) -> tuple[float, float] | None:
    return (lower_bound(passed, n, 0.025), upper_bound(passed, n, 0.025)) if n else None


def _digest(output: SolverOutput) -> str:
    return hashlib.sha256(output.model_dump_json().encode()).hexdigest()


def _observe(output: SolverOutput) -> SolverOutput:
    output._history_receipt = (_RECEIPT, id(output), _digest(output))
    return output


def _receipt_matches(output) -> bool:
    receipt = getattr(output, "_history_receipt", None)
    return (isinstance(output, SolverOutput) and isinstance(receipt, tuple) and len(receipt) == 3
            and receipt[0] is _RECEIPT and receipt[1] == id(output) and receipt[2] == _digest(output))


@dataclass(frozen=True)
class HistoryReport:
    decision: str
    code: str
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    run_id: str = ""

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.decision in ("recorded", "dry_run", "trend", "pass") else "blocked-escalated"

    def to_envelope(self):
        return {"decision": self.decision, "metrics": self.metrics, **envelope_fields(
            reliability_label=self.completion_state, warning_card=warning_card(self.code, self.reason),
            evidence=[{"kind": "solver_history", **self.metrics}], request_id=self.request_id, run_id=self.run_id)}


def _refuse(exc, *, request_id="", run_id=""):
    return HistoryReport("refused", "G6_E_SOLVER_HISTORY_FAILED", f"{type(exc).__name__}: {exc}"[:500],
                         request_id=request_id, run_id=run_id)


def _series(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError("series needs a non-empty name of at most 200 characters")


class SolverHistory:
    """One state_path database; construction and reads create nothing.

    Named series measure G6's checked outcomes; a model check can lower, never raise them.
    This is compliance with those checks, not ground-truth answer accuracy. A
    window is a count of recent runs, including unchecked runs, not a day count.
    A write is a local state write, done only with an explicit ``dry_run=False``; it
    is not an outward action, so it does not go through the action gate.
    """

    def record(self, output: SolverOutput, *, series: str = "checked_outcome", dry_run: bool = True) -> HistoryReport:
        ids = {"request_id": getattr(output, "request_id", ""), "run_id": getattr(output, "run_id", "")}
        try:
            _series(series)
            if not _receipt_matches(output):
                raise ValueError("only an unchanged output just observed by g6.solver.solve may be recorded")
            if not output.run_id:
                raise ValueError("an observed run needs its run_id")
            metrics = {"series": series, "outcome": output.outcome, "observed_by": "g6.solver.solve"}
            if dry_run:
                return HistoryReport("dry_run", "G6_E_SOLVER_HISTORY_DRY_RUN", "no history written", metrics, **ids)
            path = state_path("solver", "history.db")
            with closing(open_db(path)) as conn, conn:
                conn.execute("CREATE TABLE IF NOT EXISTS checked_history (id INTEGER PRIMARY KEY, series TEXT NOT NULL, run_id TEXT NOT NULL, outcome TEXT NOT NULL CHECK(outcome IN ('passed','failed','unchecked')), observed_by TEXT NOT NULL, UNIQUE(series, run_id))")
                conn.execute("INSERT INTO checked_history(series, run_id, outcome, observed_by) VALUES (?, ?, ?, ?)",
                             (series, output.run_id, output.outcome, "g6.solver.solve"))
                found = conn.execute("SELECT outcome, observed_by FROM checked_history WHERE series=? AND run_id=?",
                                     (series, output.run_id)).fetchone()
                if tuple(found) != (output.outcome, "g6.solver.solve"):
                    raise ValueError("history read-back differs from observed outcome")
            return HistoryReport("recorded", "G6_E_SOLVER_HISTORY_RECORDED", "checked outcome recorded", metrics, **ids)
        except Exception as exc:  # noqa: BLE001 -- provenance and storage failures are refusals
            return _refuse(exc, **ids)

    def trend(self, *, series: str = "checked_outcome", window: int = 30) -> HistoryReport:
        try:
            _series(series)
            if type(window) is not int or not 1 <= window <= 10000:
                raise ValueError("window is 1..10000 recent runs")
            path = state_path("solver", "history.db")
            rows = []
            if path.exists():
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
                    rows = conn.execute("SELECT outcome, observed_by FROM checked_history WHERE series=? ORDER BY id DESC LIMIT ?",
                                        (series, 2 * window)).fetchall()
            if any(r[0] not in ("passed", "failed", "unchecked") or r[1] != "g6.solver.solve" for r in rows):
                raise ValueError("history contains an invalid observation")
            current, prior = rows[:window], rows[window:]
            values = [int(row[0] == "passed") for row in current if row[0] != "unchecked"]
            before = [int(row[0] == "passed") for row in prior if row[0] != "unchecked"]
            n, passed = len(values), sum(values)
            mean = passed / n if n else None
            interval = _interval(passed, n)
            earlier = _interval(sum(before), len(before))
            # A decline is significant when this window's exact upper bound is below the previous window's exact
            # lower bound: with no real decline, each bound misses with chance at most 2.5%, so a false flag <= 5%.
            metrics = {"series": series, "window": window, "n": n, "mean": mean, "interval": interval,
                       "method": METHOD, "unchecked": len(current)-n,
                       "delta": mean-sum(before)/len(before) if n and before else None,
                       "decline_significant": interval[1] < earlier[0] if interval and earlier else None,
                       "observed_by": "g6.solver.solve"}
            return HistoryReport("trend" if n else "review_required", "G6_E_SOLVER_HISTORY_TREND",
                                 "exact interval on checked outcomes; no ground-truth accuracy claim", metrics)
        except Exception as exc:  # noqa: BLE001 -- a missing/corrupt store is never a passing trend
            return _refuse(exc)

    def check_regression(self, *, threshold: float, series: str = "checked_outcome", window: int = 30,
                         min_samples: int = 10, max_ci_width: float = 0.30) -> HistoryReport:
        """``pass`` only when the exact lower bound reaches ``threshold``; ``regression`` when the exact upper bound
        is below it or the pass rate fell significantly from the previous window; otherwise ``review_required``."""
        try:
            if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                    or not math.isfinite(threshold) or not 0 <= threshold <= 1
                    or type(min_samples) is not int or min_samples < MIN_SAMPLES
                    or isinstance(max_ci_width, bool) or not isinstance(max_ci_width, (int, float))
                    or not math.isfinite(max_ci_width) or not 0 <= max_ci_width <= 1):
                raise ValueError(f"threshold/width must be finite probabilities and min_samples at least {MIN_SAMPLES}")
            found = self.trend(series=series, window=window)
            if found.decision == "refused":
                return found
            n, interval = found.metrics["n"], found.metrics["interval"]
            if n < min_samples or interval is None:
                return HistoryReport("review_required", "G6_E_SOLVER_HISTORY_INSUFFICIENT",
                                     "too few checked samples", found.metrics)
            lower, upper = interval
            if upper < threshold:
                return HistoryReport("regression", "G6_E_SOLVER_HISTORY_REGRESSION",
                                     "the exact upper bound of the checked pass rate is below the threshold", found.metrics)
            if found.metrics["decline_significant"]:
                return HistoryReport("regression", "G6_E_SOLVER_HISTORY_DECLINE",
                                     "the checked pass rate fell significantly from the previous window", found.metrics)
            if upper - lower > max_ci_width:
                return HistoryReport("review_required", "G6_E_SOLVER_HISTORY_INSUFFICIENT",
                                     "the exact interval is too wide", found.metrics)
            if lower < threshold:
                return HistoryReport("review_required", "G6_E_SOLVER_HISTORY_INSUFFICIENT",
                                     "the exact interval reaches below the threshold; more checked runs are needed",
                                     found.metrics)
            return HistoryReport("pass", "G6_E_SOLVER_HISTORY_ABOVE_THRESHOLD",
                                 "the exact lower bound of the checked pass rate meets the threshold; draft only",
                                 found.metrics)
        except Exception as exc:  # noqa: BLE001 -- no malformed statistical input opens a gate
            return _refuse(exc)
