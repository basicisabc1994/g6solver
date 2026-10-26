# SPDX-License-Identifier: Apache-2.0
"""Conjugate algebra from Bolstad chapters 8, 10, 11; constants retained for evidence."""
from __future__ import annotations

import math
from statistics import NormalDist, fmean

from g6.ml.adapt_bayesian.models import BetaBinomial, GammaPoisson, ModelSpec
from g6.ml.adapt_bayesian.numerics import beta_quantile, gamma_quantile, log_beta


def _beta_posterior(model: BetaBinomial, y: list[float]) -> tuple[float, float]:
    """Rule beta: a' = a + the successes, b' = b + the failures (Bolstad: a + y and b + n - y)."""
    total = math.fsum(y)
    return model.alpha + total, model.beta + len(y) * model.trials - total


def _beta_evidence(model: BetaBinomial, y: list[float], a: float, b: float) -> float:
    """Rule evidence, beta-binomial: each count's log binomial coefficient, plus log B(a', b') - log B(a, b) (Barber
    12.2.7)."""
    lm = math.fsum(math.lgamma(model.trials + 1) - math.lgamma(v + 1) - math.lgamma(model.trials - v + 1) for v in y)
    return lm + log_beta(a, b) - log_beta(model.alpha, model.beta)


def _gamma_posterior(model: GammaPoisson, y: list[float]) -> tuple[float, float, float, float]:
    """Rule gamma: the prior's shape and rate ((1, 0) for the positive uniform prior), then shape + sum(y) and
    rate + n."""
    a0, b0 = (1., 0.) if model.prior == "positive_uniform" else (model.shape, model.rate)
    return a0, b0, a0 + math.fsum(y), b0 + len(y)


def _normal_posterior(model: ModelSpec, y: list[float]) -> tuple[float, float]:
    """Rule normal: the posterior precision is the prior's plus n / sigma^2, and the mean the precision-weighted
    average of the prior mean and the sample mean."""
    noise = model.sigma**2
    var = 1 / (1 / model.variance + len(y) / noise)
    return var * (model.mean / model.variance + math.fsum(y) / noise), var


def update(model: ModelSpec, y: list[float]) -> dict:
    n = len(y)
    if isinstance(model, BetaBinomial):
        a, b = _beta_posterior(model, y)
        return {"distribution": "beta", "parameters": {"alpha": a, "beta": b},
                "log_marginal_likelihood": _beta_evidence(model, y, a, b)}
    if isinstance(model, GammaPoisson):
        a0, b0, a, b = _gamma_posterior(model, y)
        lm = None if b0 == 0 else (a0 * math.log(b0) - math.lgamma(a0) + math.lgamma(a)
                                  - a * math.log(b) - math.fsum(math.lgamma(v + 1) for v in y))
        return {"distribution": "gamma", "parameters": {"shape": a, "rate": b}, "log_marginal_likelihood": lm}
    noise = model.sigma**2
    mu, var = _normal_posterior(model, y)
    avg = fmean(y)
    # Centre first: avoid subtraction of two large squared sums.
    lm = (-n/2 * math.log(2*math.pi*noise) + .5 * math.log(var / model.variance)
          - math.fsum((v - avg)**2 for v in y) / (2*noise)
          - (avg - model.mean)**2 / (2 * (model.variance + noise/n)))
    return {"distribution": "normal", "parameters": {"mean": mu, "variance": var}, "log_marginal_likelihood": lm}


def quantile(p: dict, q: float) -> float:
    v = p["parameters"]
    if p["distribution"] == "beta":
        return beta_quantile(q, v["alpha"], v["beta"])
    if p["distribution"] == "gamma":
        return gamma_quantile(q, v["shape"]) / v["rate"]
    return NormalDist(v["mean"], math.sqrt(v["variance"])).inv_cdf(q)


def summary(p: dict, mass: float = .95) -> dict:
    v = p["parameters"]
    if p["distribution"] == "beta":
        a, b = v["alpha"], v["beta"]
        mean, var = a / (a+b), a*b / ((a+b)**2 * (a+b+1))
        mode = (a-1)/(a+b-2) if a > 1 and b > 1 else None
    elif p["distribution"] == "gamma":
        a, b = v["shape"], v["rate"]
        mean, var, mode = a/b, a/b**2, max(0., (a-1)/b)
    else:
        mean, var = v["mean"], v["variance"]
        mode = mean
    q = (1-mass)/2
    return {"distribution": p["distribution"], "parameters": v, "mean": mean, "variance": var,
            "sd": math.sqrt(var), "median": quantile(p, .5), "mode": mode,
            "interval": {"mass": mass, "method": "equal-tailed", "low": quantile(p, q), "high": quantile(p, 1-q)}}


def prior_shift(baseline: dict, alternative: dict) -> dict:
    """How far an alternative prior moves the posterior mean, in baseline posterior sds; a shift of more than one sd
    is large (rule prior_shift, a G6 warning, not a validated cutoff)."""
    shift = alternative["mean"] - baseline["mean"]
    return {"signed_shift": shift, "absolute_shift": abs(shift), "shift_in_baseline_sd": abs(shift)/baseline["sd"],
            "large_shift": abs(shift) > baseline["sd"]}


def log_density(p: dict, x: float) -> float:
    v = p["parameters"]
    if p["distribution"] == "beta":
        return (v["alpha"]-1)*math.log(x) + (v["beta"]-1)*math.log1p(-x) if 0 < x < 1 else -math.inf
    if p["distribution"] == "gamma":
        return (v["shape"]-1)*math.log(x) - v["rate"]*x if x > 0 else -math.inf
    return -.5*(x-v["mean"])**2/v["variance"]