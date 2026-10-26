# SPDX-License-Identifier: Apache-2.0
"""Seeded posterior predictive replication and trusted Python statistic registration.

Each replicate draws one parameter from the exact posterior, then a complete
replicated dataset conditional on that parameter. It never substitutes the
posterior mean for posterior uncertainty. No executable input comes from callers.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable
from statistics import fmean, variance

from g6.ml._types import INPUT_INVALID, TOO_LARGE, MLError
from g6.ml.adapt_bayesian import diagnostics
from g6.ml.adapt_bayesian.models import BetaBinomial, GammaPoisson, ModelSpec

MAX_REPLICATED_VALUES = 2_000_000
MAX_POISSON_EVENTS = 2_000_000
STATISTICS: dict[str, Callable[[list[float]], float]] = {"mean": fmean, "variance": variance, "maximum": max}


def register_statistic(name: str, function: Callable[[list[float]], float]) -> None:
    """Trusted host Python extension point, never a tool input or code loader.

    Functions must be pure and bounded; the host owns installed Python code.
    An existing name cannot be replaced silently.
    """
    if not name.isidentifier() or name in STATISTICS or not callable(function):
        raise ValueError("use a new identifier and a callable statistic")
    STATISTICS[name] = function


def _poisson(rng: random.Random, rate: float, budget: list[int], deadline: float) -> int:
    """Poisson counting process: count exponential arrivals before rate time units.

    The event cap bounds the cost of this deliberately simple exact sampler;
    high-rate checks are refused rather than using a Gaussian approximation.
    """
    elapsed, count = 0., 0
    while True:
        budget[0] -= 1
        if budget[0] < 0:
            raise MLError(TOO_LARGE, "predictive Poisson draws exceeded 2000000 exponential arrivals")
        if budget[0] % 128 == 0:
            diagnostics.deadline_check(deadline)
        elapsed -= math.log(max(rng.random(), 1e-300))
        if elapsed > rate:
            return count
        count += 1


def replicated(model: ModelSpec, y: list[float], posterior: dict, *, seed: int,
               measure: Callable[[list[float]], float], replicates: int, deadline: float) -> list[float]:
    """The statistic of each seeded replicate: one parameter drawn from the exact posterior, then a complete
    replicated dataset of len(y) observations conditional on it (the posterior mixture, rule prediction)."""
    rng = random.Random(seed)
    v = posterior["parameters"]
    budget = [MAX_POISSON_EVENTS]
    statistics = []
    for _ in range(replicates):
        diagnostics.deadline_check(deadline)
        if isinstance(model, BetaBinomial):
            theta = rng.betavariate(v["alpha"], v["beta"])
            draws = [float(rng.binomialvariate(model.trials, theta)) for _ in y]
        elif isinstance(model, GammaPoisson):
            theta = rng.gammavariate(v["shape"], 1/v["rate"])
            draws = [float(_poisson(rng, theta, budget, deadline)) for _ in y]
        else:
            theta = rng.gauss(v["mean"], math.sqrt(v["variance"]))
            draws = [rng.gauss(theta, model.sigma) for _ in y]
        value = float(measure(draws))
        if not math.isfinite(value):
            raise MLError(INPUT_INVALID, "replicated statistic is not finite")
        statistics.append(value)
    return statistics


def check(model: ModelSpec, y: list[float], posterior: dict, *, seed: int, statistic: str,
          replicates: int, mass: float, deadline: float) -> dict:
    if statistic not in STATISTICS:
        raise MLError(INPUT_INVALID, "name a registered predictive statistic: " + ", ".join(STATISTICS))
    if replicates * len(y) > MAX_REPLICATED_VALUES:
        raise MLError(TOO_LARGE, "predictive check exceeds 2000000 replicated observations")
    if statistic == "variance" and len(y) < 2:
        raise MLError(INPUT_INVALID, "variance needs at least two observations")
    measure = STATISTICS[statistic]
    observed = float(measure(list(y)))
    if not math.isfinite(observed):
        raise MLError(INPUT_INVALID, "observed statistic is not finite")
    statistics = replicated(model, y, posterior, seed=seed, measure=measure, replicates=replicates, deadline=deadline)
    q = (1-mass)/2
    low = diagnostics.empirical_quantile(statistics, q)
    high = diagnostics.empirical_quantile(statistics, 1-q)
    diagnostics.deadline_check(deadline)
    return {"statistic": statistic, "observed": observed, "replicates": replicates, "seed": seed,
            "interval": {"mass": mass, "method": "empirical equal-tailed predictive", "low": low, "high": high},
            "upper_tail_probability": sum(v >= observed for v in statistics)/replicates,
            "misfit": observed < low or observed > high,
            "rule": "G6 warning when observed statistic is outside the stated predictive interval; not a model proof",
            "replicated_mean": fmean(statistics)}