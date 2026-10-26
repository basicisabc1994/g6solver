# SPDX-License-Identifier: Apache-2.0
"""Seeded size, imbalance, grouping, constant-target and held-out perturbation probes. Shared by adapt_sklearn and
adapt_automl."""
import math
import random
from decimal import Decimal


def generate(rule, seed, count):
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        n = rng.choice([10, 50, 150, 1000, 20000])
        if rule == "held_out":
            out.append({"n": n, "task": "regression", "fraction": str(rng.choice([.1, .25, .4, .5])),
                        "seed": rng.randrange(2**32), **({"view": "overlap"} if rng.randrange(2) else {})})
        elif rule == "stratified":
            n = rng.choice([50, 100, 1000])
            small = rng.randrange(2, n//2)
            out.append({"n": n, "labels": [0]*(n-small)+[1]*small, "task": "classification",
                        "fraction": str(rng.choice([.1, .25, .5])), "seed": rng.randrange(2**32)})
        elif rule == "grouped":
            k = rng.randrange(2, 6)
            groups = [g for g in range(k) for _ in range(rng.randrange(1, 8))]
            out.append({"X": list(range(len(groups))), "y": list(range(len(groups))), "groups": groups,
                        "folds": k, "seed": rng.randrange(2**32)})
        elif rule == "grouped_hold_out":
            size = rng.choice([1, 2, 3])
            keys = [k for k in range(rng.randrange(8, 40)) for _ in range(size)]
            out.append({"keys": keys, "fraction": str(rng.choice([.1, .25, .5])),
                        **({"view": "held"} if rng.randrange(2) else {})})
        elif rule == "seed":
            out.append({"seed": rng.choice([0, 2**32-1, rng.randrange(2**32)])})
        elif rule in ("preprocessing_in_pipeline", "cv_training_part", "search_training_part"):
            n = rng.randrange(6, 20)
            out.append({"train": [rng.randrange(-100, 101) for _ in range(n)], "y": [rng.randrange(-10, 11) for _ in range(n)],
                        "test": [rng.randrange(-1000000, 1000001) for _ in range(3)], "folds": 3, "seed": rng.randrange(2**32)})
        elif rule == "baseline":
            out.append({"task": rng.choice(["classification", "regression"]),
                        "train_truth": [rng.randrange(-3, 4) for _ in range(12)], "n": rng.randrange(1, 10)})
        elif rule == "headline_imbalance":
            # Minorities on both sides of the 10% threshold, the held-out tail after the training rows.
            total = rng.choice([20, 50, 100, 1000])
            small = rng.choice([1, max(1, total // 10 - 1), total // 10, total // 10 + 1, total // 3])
            train = rng.randrange(total // 2, total + 1)
            out.append({"counts": [[0, total - small], [1, small], [2, rng.randrange(0, 3)]], "train": train})
        else:
            n = rng.randrange(2, 100)
            if rule in ("accuracy", "balanced_accuracy"):
                truth = [rng.randrange(3) for _ in range(n)]
                pred = [rng.randrange(4) for _ in range(n)]
            elif rule == "r2" and rng.randrange(3) == 0:
                # Constant non-integer targets: R2 is undefined, though their float mean can be off by a rounding.
                truth = [f"{rng.randrange(-1000000, 1000000)}.{rng.randrange(1, 1000):03d}"]*n
                pred = [rng.randrange(-1000, 1001) for _ in range(n)]
            else:
                truth = [rng.randrange(-1000, 1001) for _ in range(n)]
                pred = [rng.randrange(-1000, 1001) for _ in range(n)]
            out.append({"truth": truth, "pred": pred})
    return out


def invariant(rule, inputs, value):
    if rule in ("accuracy", "balanced_accuracy"):
        return 0 <= value <= 1
    if rule in ("mae", "rmse"):
        return value >= 0
    if rule == "r2":
        # The source: undefined exactly when every true value is equal; otherwise at most 1.
        constant = len({float(v) for v in inputs["truth"]}) == 1
        return value == {"defined": 0} if constant else not isinstance(value, dict) and value <= 1
    if rule == "held_out":
        if inputs.get("view") == "overlap":
            return value == 0
        if inputs.get("view") == "groups":
            return 0 < value < inputs["n"]
        return value[0]+value[1] == inputs["n"] and value[0] > 0 and value[1] > 0
    if rule == "stratified":
        n = inputs["n"] if "n" in inputs else sum(count for _, count in inputs["counts"])
        return all(a > 0 and b > 0 for a, b in zip(value[0], value[1])) and sum(value[0])+sum(value[1]) == n
    if rule == "grouped_hold_out":
        if inputs.get("view") in ("held", "groups"):
            return 0 < value < len(inputs["keys"])
        return value == 0
    if rule == "grouped" and inputs.get("view") in ("folds", "order"):
        rows = sorted(int(i) for fold in value for i in fold)
        return rows == list(range(len(inputs["groups"]))) and all(
            len({inputs["groups"][int(i)] for i in fold}) == 1 for fold in value)
    if rule == "grouped":
        return all(v == 0 for v in value["overlap"]) and value["coverage"] == list(range(len(inputs["groups"])))
    if rule == "seed":
        return value[0] == inputs["seed"] and value[1] == 2
    if rule == "baseline":
        return len(value) == inputs["n"] and len(set(value)) == 1
    if rule == "headline_imbalance":
        # The headline is balanced accuracy exactly when the smallest training class holds under a tenth of the rows.
        return 0 < value["share"] <= 1 and value["balanced"] == int(value["share"] < Decimal("0.1"))
    if inputs.get("view") == "leakage":
        return value == 0
    if rule == "search_training_part":
        return (value["trials"] == 1 and sum(value["valid"]) == len(inputs["y"]) and all(v >= 0 for v in value["scores"])
                and len(value["held_out"]) == len(inputs["test"]))
    if rule == "preprocessing_in_pipeline":
        return all(math.isfinite(v) for row in value for v in row)
    return all(v >= 0 for v in value)
