# SPDX-License-Identifier: Apache-2.0
"""Candidate-only conversion/projection; the harness supplies the mutated function. A guard's observation is 1 when
it fires, 0 when it holds (a removed guard returns None and holds) and 2 when it refuses for another reason."""
import math
import random
import time
from decimal import Decimal
from statistics import fmean

from g6.core.special import NotConverged
from g6.ml._types import INPUT_INVALID, NOT_CONVERGED, MLError
from g6.ml.adapt_bayesian import exact
from g6.ml.adapt_bayesian.models import BetaBinomial, GammaPoisson, NormalKnown


def model(value):
    if "sd" in value:  # a case gives a normal prior's standard deviation, as its source prints it
        value = {**{k: v for k, v in value.items() if k != "sd"}, "variance": str(Decimal(str(value["sd"])) ** 2)}
    types = {"beta_binomial": BetaBinomial, "gamma_poisson": GammaPoisson, "normal_known": NormalKnown}
    return types[value["kind"]].model_validate(value)


def refused(call, code):
    try:
        call()
    except MLError as exc:
        return 1 if exc.code == code else 2
    return 0


def evaluate(function, rule, inputs):
    x = inputs
    posterior = x.get("posterior")
    if posterior:
        posterior = {**posterior, "parameters": {k: float(v) for k, v in posterior["parameters"].items()}}
    if rule in ("beta", "gamma", "normal"):
        # Each rule's own posterior function; a view of the posterior's summary goes through exact.summary.
        out = function(model(x["model"]), [float(v) for v in x["y"]])
        kind, parameters = {"beta": ("beta", lambda o: {"alpha": o[0], "beta": o[1]}),
                            "gamma": ("gamma", lambda o: {"shape": o[2], "rate": o[3]}),
                            "normal": ("normal", lambda o: {"mean": o[0], "variance": o[1]})}[rule]
        parameters = parameters(out)
        view = x.get("view", "parameters")
        if view == "parameters":
            return parameters
        if view in parameters:
            return parameters[view]
        return exact.summary({"distribution": kind, "parameters": parameters})[view]
    if rule == "evidence":
        # The beta-binomial evidence of the posterior G6 forms (exact._beta_posterior, not the function checked).
        m, y = model(x["model"]), [float(v) for v in x["y"]]
        return math.exp(function(m, y, *exact._beta_posterior(m, y)))
    if rule == "moments":
        return function(posterior)[x["view"]]
    if rule == "credible_interval":
        return function(posterior, .5 if x.get("view") == "median" else float(x["q"]))
    if rule == "barber_example" and "heads" in x:
        # The source's flat-prior form, I_x(N_H + 1, N_T + 1) (Barber 9.1.31).
        return function(x=float(x["x"]), a=float(x["heads"] + 1), b=float(x["tails"] + 1))
    if rule in ("beta_cdf", "gamma_cdf", "barber_example"):
        return function(**{key: float(value) for key, value in x.items()})
    if rule == "convergence":
        result = function([[float(v) for v in row] for row in x["chains"]])
        if x.get("view") == "thresholds":
            return result["thresholds"]
        return {k: (-1 if result[k] is None else result[k])
                for k in ("rhat", "bulk_ess", "tail_ess")}
    if rule == "mh":
        result = function(posterior, seed=x["seed"], draws=x["draws"], chains=x["chains"],
                          warmup=x["warmup"], proposal_scale=float(x["scale"]), deadline=time.monotonic() + 10)
        return result["acceptance_rates"] if x.get("view") == "acceptance" else {
            "draws": result["draws"], "acceptance": result["acceptance_rates"]}
    if rule == "prediction":
        # Every replicate's mean, in order: the seeded posterior mixture itself.
        return function(model(x["model"]), [float(v) for v in x["y"]], posterior, seed=x["seed"], measure=fmean,
                        replicates=x["replicates"], deadline=time.monotonic() + 10)
    # --- guards ---
    if rule == "prior_shift":
        out = function({"mean": float(x["base_mean"]), "sd": float(x["base_sd"])}, {"mean": float(x["alt_mean"])})
        return 0 if out is None else int(out["large_shift"])
    if rule == "misfit":
        # The predictive interval does not depend on the observed values (only on their number), so a first call
        # finds its ends and a second sends an observed mean on, just past or far from one of them.
        normal = NormalKnown(kind="normal_known", mean=0, variance=1, sigma=1)
        prior = {"distribution": "normal", "parameters": {"mean": 0.0, "variance": 1.0}}

        def check(y):
            return function(normal, [y], prior, seed=x["seed"], statistic="mean", replicates=x["replicates"],
                            mass=.95, deadline=time.monotonic() + 10)
        first = check(0.0)
        if first is None:
            return 0
        low, high = first["interval"]["low"], first["interval"]["high"]
        at = {"low": low, "below_low": math.nextafter(low, -math.inf), "high": high,
              "above_high": math.nextafter(high, math.inf), "far_below": low - 10, "far_above": high + 10}[x["at"]]
        second = check(at)
        return 0 if second is None else int(second["misfit"])
    if rule == "numerics":
        slope = 2.0 ** x["slope_log2"] if "slope_log2" in x else None
        cdf = (lambda v: v * slope) if slope else (lambda v: 0.0 if v < 0.3 else 1.0)
        try:
            function(float(x["q"]), cdf, 0.0, 1.0)
        except NotConverged:
            return 1
        except ValueError:
            return 2
        return 0
    if rule == "bounds":
        values = [float(v) for v in x["values"]] if "values" in x else [float(x["value"])] * x["count"]
        return refused(lambda: function(model(x["model"]), values, None), INPUT_INVALID)
    if rule == "sampler_metadata":
        rng = random.Random(x["seed"])
        draws = [[rng.gauss(0, 1) for _ in range(x["draws"])] for _ in range(x["chains"])]
        if x.get("fault") == "duplicate":
            draws[1] = list(draws[0])
        elif x.get("fault") == "nonfinite":
            draws[0][0] = math.inf
        result = {"draws": draws, "backend": x["backend"], "acceptance_rates": [0.5] * x["chains"]}
        if "divergences" in x:
            result["divergences"] = x["divergences"]
        return refused(lambda: function(result, chains=x["chains"], draws=x["draws"], mass=.95,
                                        deadline=time.monotonic() + 30, support="normal"), NOT_CONVERGED)
    raise ValueError(rule)
