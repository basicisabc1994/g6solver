# SPDX-License-Identifier: Apache-2.0
"""Independent conjugate algebra, SciPy special functions and direct autocovariances."""
import math
import random
import statistics

import numpy as np
from scipy import special, stats


def diagnostic(chains):
    halves = [row[part] for row in chains for part in (slice(0, len(row)//2), slice(-(len(row)//2), None))]
    a = np.asarray(halves, dtype=float)

    def ranked(values):
        ranks = stats.rankdata(values.ravel(), method="average")
        return stats.norm.ppf((ranks - .375)/(values.size + .25)).reshape(values.shape)

    def rhat(values):
        m, n = values.shape
        w = np.var(values, axis=1, ddof=1).mean()
        if w == 0:
            return -1
        return float(np.sqrt(((n-1)*w/n + np.var(values.mean(axis=1), ddof=1))/w))

    def ess(values):
        m, n = values.shape
        centred = values - values.mean(axis=1, keepdims=True)
        ac = np.asarray([[np.dot(row[:n-k], row[k:])/n for k in range(n)] for row in centred])
        s2 = ac[:, 0]*n/(n-1)
        w = s2.mean()
        total = w*(n-1)/n + np.var(values.mean(axis=1), ddof=1)
        if w <= 0 or total <= 0:
            return 0.0
        # Equation 10 with each chain's own autocorrelation ac_t/ac_0 (zero for a constant chain).
        own = np.divide(ac, ac[:, :1], out=np.zeros_like(ac), where=ac[:, :1] > 0)
        rho = 1 - (w - (s2[:, None]*own).mean(axis=0))/total
        pairs = [float(rho[0]+rho[1])]
        for k in range(2, n-1, 2):
            pair = float(rho[k]+rho[k+1])
            if pair <= 0:
                break
            pairs.append(min(pair, pairs[-1]))
        # Page 8: the sums truncated at the odd lag and at the next even lag, averaged.
        last = 2*len(pairs)
        tau = 2*sum(pairs) - 1 + (float(rho[last]) if last < n else 0.0)
        return float(m*n/max(tau, 1/math.log10(m*n)))

    z = ranked(a)
    folded = ranked(np.abs(a-np.median(a)))
    rhats = [rhat(z), rhat(folded)]
    return {"rhat": -1 if min(rhats) < 0 else max(rhats), "bulk_ess": ess(z),
            "tail_ess": min(ess((a <= np.quantile(a, q)).astype(float)) for q in (.05, .95))}


def distribution(p):
    v = p["parameters"]
    if p["distribution"] == "beta":
        return stats.beta(float(v["alpha"]), float(v["beta"]))
    if p["distribution"] == "gamma":
        return stats.gamma(float(v["shape"]), scale=1/float(v["rate"]))
    return stats.norm(float(v["mean"]), math.sqrt(float(v["variance"])))


def evaluate(rule, inputs):
    x = inputs
    if rule == "barber_example" and "heads" in x:
        return float(special.betainc(x["heads"] + 1, x["tails"] + 1, float(x["x"])))
    if rule in ("beta_cdf", "barber_example"):
        return float(special.betainc(float(x["a"]), float(x["b"]), float(x["x"])))
    if rule == "credible_interval":
        return float(distribution(x["posterior"]).ppf(.5 if x.get("view") == "median" else float(x["q"])))
    if rule == "moments":
        d, p = distribution(x["posterior"]), x["posterior"]
        v = {k: float(value) for k, value in p["parameters"].items()}
        mode = ((v["alpha"]-1)/(v["alpha"]+v["beta"]-2) if p["distribution"] == "beta" else
                max(0., (v["shape"]-1)/v["rate"]) if p["distribution"] == "gamma" else v["mean"])
        return {"mean": float(d.mean()), "variance": float(d.var()), "sd": float(d.std()), "mode": mode}[x["view"]]
    if rule == "gamma_cdf":
        return float(special.gammainc(float(x["a"]), float(x["x"])))
    if rule == "convergence":
        if x.get("view") == "thresholds":
            return {"rhat_below": 1.01, "bulk_ess_above": 400, "tail_ess_above": 400}
        return diagnostic([[float(v) for v in row] for row in x["chains"]])
    if rule == "mh":
        d = distribution(x["posterior"])
        seeds = random.Random(x["seed"])
        chains, rates = [], []
        for k in range(x["chains"]):
            rng = random.Random(seeds.randrange(2**63))
            state = float(d.ppf((k+.5)/x["chains"]))
            values, accepted = [], 0
            for i in range(x["warmup"] + x["draws"]):
                proposal = state + rng.gauss(0, float(d.std())*float(x["scale"]))
                u = max(rng.random(), 1e-300)
                ratio = float(d.logpdf(proposal) - d.logpdf(state))
                if u < math.exp(min(0.0, ratio)):
                    state = proposal
                    accepted += int(i >= x["warmup"])
                if i >= x["warmup"]:
                    values.append(state)
            chains.append(values)
            rates.append(accepted/x["draws"])
        return rates if x.get("view") == "acceptance" else {"draws": chains, "acceptance": rates}
    if rule == "prediction":
        # One parameter from the posterior, then len(y) observations given it, on one seeded standard-library stream:
        # binomial counts, Poisson counts as exponential arrivals within the rate's time, or normal measurements.
        rng = random.Random(x["seed"])
        p, kind = x["posterior"]["parameters"], x["posterior"]["distribution"]

        def arrivals(rate):
            elapsed, count = 0., 0
            while True:
                elapsed -= math.log(max(rng.random(), 1e-300))
                if elapsed > rate:
                    return count
                count += 1
        means = []
        for _ in range(x["replicates"]):
            if kind == "beta":
                theta = rng.betavariate(float(p["alpha"]), float(p["beta"]))
                draws = [float(rng.binomialvariate(int(x["model"]["trials"]), theta)) for _ in x["y"]]
            elif kind == "gamma":
                theta = rng.gammavariate(float(p["shape"]), 1/float(p["rate"]))
                draws = [float(arrivals(theta)) for _ in x["y"]]
            else:
                theta = rng.gauss(float(p["mean"]), math.sqrt(float(p["variance"])))
                draws = [rng.gauss(theta, float(x["model"]["sigma"])) for _ in x["y"]]
            means.append(statistics.fmean(draws))
        return means
    m, y = x["model"], [float(v) for v in x["y"]]
    n, total = len(y), math.fsum(y)
    if m["kind"] == "beta_binomial":
        a0, b0 = float(m["alpha"]), float(m["beta"])
        a, b = a0+total, b0+n*m.get("trials", 1)-total
        parameters = {"alpha": a, "beta": b}
        d = stats.beta(a, b)
        log_evidence = (sum(math.log(math.comb(m.get("trials", 1), int(v))) for v in y)
                        + special.betaln(a, b) - special.betaln(a0, b0))
    elif m["kind"] == "gamma_poisson":
        a0, b0 = (1., 0.) if m.get("prior") == "positive_uniform" else (float(m["shape"]), float(m["rate"]))
        a, b = a0+total, b0+n
        parameters = {"shape": a, "rate": b}
        d = stats.gamma(a, scale=1/b)
        log_evidence = (None if b0 == 0 else a0*math.log(b0) - special.gammaln(a0)
                        + special.gammaln(a) - a*math.log(b) - sum(special.gammaln(v+1) for v in y))
    else:
        # Gaussian conditioning as a two-observation weighted least squares problem.
        prior = float(m["variance"]) if "variance" in m else float(m["sd"])**2
        precision = np.asarray([1/prior, n/float(m["sigma"])**2])
        variance = 1/precision.sum()
        mean = np.dot(precision, [float(m["mean"]), statistics.fmean(y)])/precision.sum()
        parameters = {"mean": float(mean), "variance": float(variance)}
        d = stats.norm(mean, math.sqrt(variance))
        covariance = np.eye(n)*float(m["sigma"])**2 + np.ones((n,n))*prior
        log_evidence = stats.multivariate_normal.logpdf(y, mean=np.full(n, float(m["mean"])), cov=covariance)
    if rule == "evidence":
        return float(math.exp(log_evidence))
    view = x.get("view", "parameters")
    if view == "parameters":
        return parameters
    if view in parameters:
        return parameters[view]
    if view == "mean":
        return float(d.mean())
    if view == "sd":
        return float(d.std())
    if view == "median":
        return float(d.median())
    raise ValueError(view)
