# SPDX-License-Identifier: Apache-2.0
"""What the child process runs for adapt_automl's FLAML search; only ever started by ``backends.FlamlBackend``
through ``g6.ml._fit.run``.

For each candidate in turn, FLAML's cost-frugal search (CFO, with the job's seed) proposes settings within the
candidate's ranges, from its cheap corner, for an equal share of the trial cap and the time budget; each trial is a
cross-validation inside the training rows with the extension's own pipeline and folds (``g6.ml._fit``), scored by
G6's metric. A trial that fails is recorded with why and never scored on training rows; a candidate none of whose
trials finished is excluded. The best mean fold score across candidates is refitted on all the training rows, and
the child predicts the held-out rows, whose answers it never had.
"""
from __future__ import annotations

import json
import math
import numbers
import time
import warnings
from pathlib import Path
from typing import Any

from g6.ml import _fit, _metrics


def _domain(kind: str, low: float, high: float) -> Any:
    from flaml import tune

    return {"lograndint": tune.lograndint, "randint": tune.randint, "loguniform": tune.loguniform,
            "uniform": tune.uniform}[kind](low, high)


def _settings(config: dict[str, Any]) -> dict[str, Any]:
    """A configuration FLAML proposed as plain numbers (whole numbers stay whole)."""
    return {k: int(v) if isinstance(v, numbers.Integral) else float(v) for k, v in config.items()}


def search(job: dict[str, Any]) -> dict[str, Any]:
    """The search, in this process (the child's)."""
    from flaml import tune
    from flaml.tune.searcher import CFO

    X, y_fit, splits = _fit.folds(job)
    better = _metrics.HIGHER_IS_BETTER[job["metric"]]
    mode = "max" if better else "min"
    worst = -math.inf if better else math.inf
    candidates = job["candidates"]
    shares = [job["trials"] // len(candidates) + (i < job["trials"] % len(candidates)) for i in range(len(candidates))]
    budget = job["time_budget_s"] / len(candidates)
    board: list[dict[str, Any]] = []
    ended: list[str] = []
    for spec, share in zip(candidates, shares):
        if share < 1:
            board.append({"name": spec["name"], "trials": 0, "excluded": "no trial left in the cap for it"})
            continue
        failures: list[str] = []

        def evaluate(config: dict[str, Any], spec: dict[str, Any] = spec, failures: list[str] = failures) -> dict:
            trial = {**spec, "params": {**spec["fixed"], **_settings(config)}}
            try:
                scores = _fit.cross_validate(trial, job, X, y_fit, splits)
            except Exception as exc:  # noqa: BLE001 - a failed trial is recorded with why, never scored otherwise
                failures.append(f"{type(exc).__name__}: {exc}"[:200])
                return {"score": worst, "failed": 1}
            return {"score": sum(scores) / len(scores), "failed": 0, "fold_scores": scores}

        started = time.monotonic()
        space = {k: _domain(*v) for k, v in spec["ranges"].items()}
        searcher = CFO(metric="score", mode=mode, space=space, low_cost_partial_config=spec["cheap"] or None,
                       seed=job["seed"])
        analysis = tune.run(evaluate, search_alg=searcher, metric="score", mode=mode, num_samples=share,
                            time_budget_s=budget, verbose=0, use_ray=False)
        results = [(t.config, t.last_result) for t in analysis.trials if t.last_result]
        done = [(c, r) for c, r in results if not r.get("failed")]
        ended.append("trial cap" if len(results) >= share else "time budget")
        row: dict[str, Any] = {"name": spec["name"], "trials": len(results), "failed": len(results) - len(done),
                               "seconds": round(time.monotonic() - started, 3)}
        if not done:
            row["excluded"] = failures[0] if failures else "no trial finished within its share of the time budget"
        else:
            config, result = (max if better else min)(done, key=lambda cr: cr[1]["score"])
            row.update(best_score=round(float(result["score"]), 6), fold_scores=result["fold_scores"],
                       best_config=_settings({k: config[k] for k in spec["ranges"]}))
        if failures:
            row["first_failure"] = failures[0]
        board.append(row)
    out: dict[str, Any] = {"leaderboard": board, "folds": [{"train": len(a), "valid": len(b)} for a, b in splits],
                           "ended_by": "time budget" if "time budget" in ended else "trial cap"}
    scored = [r for r in board if "excluded" not in r]
    if not scored:
        return out
    best = (max if better else min)(scored, key=lambda r: r["best_score"])
    spec = next(c for c in candidates if c["name"] == best["name"])
    final = {**spec, "params": {**spec["fixed"], **best["best_config"]}}
    out.update(chosen=best["name"], chosen_config=best["best_config"], **_fit.fit_predict(job, final))
    return out


def child_main() -> None:
    """Read the job, search, and write the result (an error is written as one)."""
    job = json.loads(Path(_fit.JOB).read_text(encoding="utf-8"))
    started = time.monotonic()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = search(job)
        except Exception as exc:  # noqa: BLE001 - every failure goes back to the parent as a reason
            result = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    import flaml
    import sklearn

    result["seconds"] = round(time.monotonic() - started, 3)
    result["versions"] = {"flaml": flaml.__version__, "scikit-learn": sklearn.__version__}
    result["warnings"] = sorted({f"{w.category.__name__}: {w.message}"[:200] for w in caught
                                 if "flaml.automl is not available" not in str(w.message)})[:10]
    Path(_fit.RESULT).write_text(json.dumps(result), encoding="utf-8")


__all__ = ["child_main", "search"]
