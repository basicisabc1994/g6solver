# SPDX-License-Identifier: Apache-2.0
"""Seeded bounded scalar random-walk Metropolis-Hastings; fixed step after warmup.

Barber 27.4.2, symmetric Gaussian proposal: accept with min(1,p(new)/p(old)).
Initial states are dispersed posterior quantiles, not repeated identical states.
The user chooses the step in posterior standard-deviation units; no adaptation.
"""
from __future__ import annotations

import math
import random
from statistics import fmean, variance

from g6.ml._types import MLError, NOT_CONVERGED
from g6.ml.adapt_bayesian import diagnostics, exact


def mh(posterior: dict, *, seed: int, draws: int, chains: int, warmup: int,
       proposal_scale: float, deadline: float) -> dict:
    sd = exact.summary(posterior)["sd"]
    all_draws, rates = [], []
    seeds = random.Random(seed)
    for k in range(chains):
        rng = random.Random(seeds.randrange(2**63))
        x = exact.quantile(posterior, (k+.5)/chains)
        lp = exact.log_density(posterior, x)
        kept, accepted = [], 0
        for i in range(warmup + draws):
            if i % 32 == 0:
                diagnostics.deadline_check(deadline)
            candidate = x + rng.gauss(0, sd * proposal_scale)
            proposed = exact.log_density(posterior, candidate)
            if math.log(max(rng.random(), 1e-300)) < proposed - lp:
                x, lp = candidate, proposed
                accepted += i >= warmup
            if i >= warmup:
                kept.append(x)
        all_draws.append(kept)
        rates.append(accepted/draws)
    return {"draws": all_draws, "acceptance_rates": rates, "backend": "mh"}


def checked(result: dict, *, chains: int, draws: int, mass: float, deadline: float, support: str | None = None) -> tuple[dict, dict]:
    values = result.get("draws")
    if not isinstance(values, list) or len(values) != chains or any(not isinstance(c, list) or len(c) != draws
                                                                  for c in values):
        raise MLError(NOT_CONVERGED, "sampler returned missing or incomplete chains")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for c in values for v in c):
        raise MLError(NOT_CONVERGED, "sampler returned non-finite or non-numeric draws")
    if support == "beta" and any(not 0 < v < 1 for c in values for v in c):
        raise MLError(NOT_CONVERGED, "beta sampler returned draws outside (0,1)")
    if support == "gamma" and any(v <= 0 for c in values for v in c):
        raise MLError(NOT_CONVERGED, "gamma sampler returned nonpositive draws")
    if result.get("backend") == "pymc":
        divergences = result.get("divergences")
        if type(divergences) is not int or divergences != 0:
            raise MLError(NOT_CONVERGED, "PyMC divergences must be measured and zero")
    if any(values[i] == values[j] for i in range(chains) for j in range(i)):
        raise MLError(NOT_CONVERGED, "sampler returned duplicated complete chains; draws are not independent")
    found = diagnostics.assess(values, deadline)
    if not found["converged"] or result.get("divergences", 0) != 0:
        raise MLError(NOT_CONVERGED, "R-hat must be below 1.01 and bulk and tail ESS above 400; "
                      "PyMC must also have no divergences", [{"kind": "mcmc_diagnostics", **found,
                                                            "divergences": result.get("divergences")}])
    flat = [x for c in values for x in c]
    var, q = variance(flat), (1-mass)/2
    summary = {"mean": fmean(flat), "variance": var, "sd": math.sqrt(var),
               "interval": {"method": "empirical equal-tailed", "mass": mass,
                            "low": diagnostics.empirical_quantile(flat, q),
                            "high": diagnostics.empirical_quantile(flat, 1-q)}}
    return summary, found