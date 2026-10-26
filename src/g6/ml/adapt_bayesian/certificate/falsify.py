# SPDX-License-Identifier: Apache-2.0
"""Recorded bounded samples; extremes and support boundaries precede random interiors."""
import math
import random


def generate(rule, seed, count):
    rng = random.Random(seed)
    out = []
    for index in range(count):
        if rule in ("beta_cdf", "barber_example"):
            out.append({"x": str(rng.choice([0, 1, rng.random()])),
                        "a": str(rng.choice([.01, 1, 10000, 10**rng.uniform(-2, 4)])),
                        "b": str(rng.choice([.01, 1, 10000, 10**rng.uniform(-2, 4)]))})
        elif rule == "gamma_cdf":
            a = rng.choice([.01, 1, 10000, 10**rng.uniform(-2, 4)])
            out.append({"a": str(a), "x": str(rng.choice([0, a+1, a*rng.random()*4]))})
        elif rule == "convergence":
            n = rng.choice([8, 9, 32, 64])
            # Roughly normal draws from exact arithmetic only (twelve uniforms summed), so every system makes the same
            # chains: random.gauss calls the platform's log and cos, whose last bit differs between systems.
            chains = [[str(sum(rng.random() for _ in range(12)) - 6 + (k if index % 2 else 0)) for _ in range(n)]
                      for k in range(4)]
            out.append({"chains": chains})
        elif rule == "mh":
            kind = rng.choice(["beta", "gamma", "normal"])
            params = ({"alpha": rng.randrange(1, 8), "beta": rng.randrange(1, 8)} if kind == "beta" else
                      {"shape": rng.randrange(1, 8), "rate": rng.randrange(1, 8)} if kind == "gamma" else
                      {"mean": rng.randrange(-5, 6), "variance": rng.randrange(1, 8)})
            out.append({"posterior": {"distribution": kind, "parameters": params}, "seed": rng.randrange(2**32),
                        "draws": 8, "chains": 2, "warmup": 4, "scale": str(rng.choice([1e-12, .5, 2.4, 100]))})
        elif rule in ("moments", "credible_interval"):
            kind = rng.choice(["beta", "gamma", "normal"])
            params = ({"alpha": str(rng.choice([1.5, 2, 30.8, 500, 10**rng.uniform(.2, 3)])),
                       "beta": str(rng.choice([1.5, 2, 93.2, 500, 10**rng.uniform(.2, 3)]))} if kind == "beta" else
                      {"shape": str(rng.choice([.5, 1, 27, 10**rng.uniform(-1, 3)])),
                       "rate": str(rng.choice([.1, 1, 8, 1000]))} if kind == "gamma" else
                      {"mean": str(rng.randrange(-1000000, 1000001)), "variance": str(rng.choice([.01, 1, 10000]))})
            item = {"posterior": {"distribution": kind, "parameters": params}}
            if rule == "moments":
                item["view"] = rng.choice(["mean", "variance", "sd", "mode"])
            else:
                item["q"] = str(rng.choice([.025, .5, .975, round(rng.uniform(.01, .99), 6)]))
            out.append(item)
        elif rule == "prediction":
            out.append({"model": {"kind": "normal_known", "mean": 0, "variance": 1, "sigma": str(rng.choice([.01, 1, 10]))},
                        "posterior": {"distribution": "normal", "parameters": {"mean": rng.randrange(-10, 11), "variance": 1}},
                        "y": [rng.randrange(-10, 11) for _ in range(8)], "seed": rng.randrange(2**32), "replicates": 50})
        else:
            if rule in ("beta", "evidence"):
                m = {"kind": "beta_binomial", "alpha": str(rng.choice([.01, 1, 10000])),
                     "beta": str(rng.choice([.01, 1, 10000])), "trials": rng.choice([1, 10, 10000])}
                y = [rng.randrange(m["trials"]+1) for _ in range(rng.randrange(1, 12))]
            elif rule == "gamma":
                m = {"kind": "gamma_poisson", "shape": str(rng.choice([.01, 1, 10000])),
                     "rate": str(rng.choice([.01, 1, 10000]))}
                y = [rng.randrange(100) for _ in range(rng.randrange(1, 12))]
            else:
                m = {"kind": "normal_known", "mean": rng.randrange(-1000000, 1000001),
                     "variance": str(rng.choice([.01, 1, 10000])), "sigma": str(rng.choice([.01, 1, 10000]))}
                y = [rng.randrange(-1000000, 1000001) for _ in range(rng.randrange(1, 12))]
            out.append({"model": m, "y": y})
    return out


def invariant(rule, inputs, value):
    if rule in ("beta_cdf", "gamma_cdf", "barber_example", "evidence"):
        return 0 <= value <= 1
    if rule == "convergence":
        if inputs.get("view") == "thresholds":
            return value["rhat_below"] > 1 and value["bulk_ess_above"] > 0 and value["tail_ess_above"] > 0
        return value["rhat"] >= -1 and value["bulk_ess"] >= 0 and value["tail_ess"] >= 0
    if rule == "mh":
        rates = value if isinstance(value, list) else value["acceptance"]
        if not all(0 <= v <= 1 for v in rates):
            return False
        if isinstance(value, dict):
            kind = inputs["posterior"]["distribution"]
            return all((0 < v < 1 if kind == "beta" else v > 0 if kind == "gamma" else math.isfinite(v))
                       for row in value["draws"] for v in row)
        return True
    if rule == "moments":
        return value > 0 if inputs["view"] in ("variance", "sd") else math.isfinite(value)
    if rule == "credible_interval":
        kind = inputs["posterior"]["distribution"]
        return 0 < value < 1 if kind == "beta" else value > 0 if kind == "gamma" else math.isfinite(value)
    if rule == "prediction":
        return len(value) == inputs["replicates"] and all(math.isfinite(v) for v in value)
    if not isinstance(value, dict):
        return math.isfinite(value) and (rule == "normal" or value >= 0)
    if rule == "normal":
        m = inputs["model"]
        prior = float(m["variance"]) if "variance" in m else float(m["sd"])**2
        return value["variance"] > 0 and value["variance"] <= prior
    return all(v > 0 for v in value.values())
