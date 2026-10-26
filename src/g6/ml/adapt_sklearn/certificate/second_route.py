# SPDX-License-Identifier: Apache-2.0
"""Independent counts, Decimal arithmetic and array algebra; no estimator library. Shared by adapt_sklearn and
adapt_automl; guards have no second route."""
from decimal import Decimal

import numpy as np


def fold_mae(x):
    """Each fold's mean absolute error of the mean of the other folds' targets, sorted: one fold per array_split
    part."""
    y = np.asarray(x["y"], dtype=float)
    result = []
    for group in np.array_split(np.arange(len(y)), x["folds"]):
        outside = np.ones(len(y), dtype=bool)
        outside[group] = False
        prediction = y[outside].mean()
        result.append(round(float(np.abs(y[group]-prediction).mean()), 6))
    return sorted(result)


def evaluate(rule, inputs):
    x = inputs
    fraction = 1/x["folds"] if "folds" in x else float(x.get("fraction", 0))
    if rule == "held_out":
        if x.get("view") == "overlap":
            return 0
        held = round(x["n"]*fraction)
        if x.get("view") == "groups":  # distinct rows: each held-out row is its own group
            return held
        return [x["n"]-held, held]
    if rule == "stratified":
        labels = [c for c, count in x["counts"] for _ in range(count)] if "counts" in x else x["labels"]
        totals = [labels.count(c) for c in sorted(set(labels))]
        held = [min(n-1, max(1, round(n*fraction))) for n in totals]
        return [[n-h for n, h in zip(totals, held)], held]
    if rule == "grouped_hold_out":
        # Whole groups of equal size g are held out until the share is reached: g * ceil(round(n * f) / g) rows,
        # none of whose keys stays in training.
        n, size = len(x["keys"]), x["keys"].count(x["keys"][0])
        want = round(n*float(x["fraction"]))
        groups = -(-want//size)
        return {"held": groups*size, "groups": groups}.get(x.get("view"), 0)
    if rule == "grouped" and x.get("view") == "order":
        # The source's GroupKFold deals the largest group first (here a tie goes to the larger label, as it prints);
        # G6's folds are dealt in a seeded shuffle's order, which only a case's seed fixes.
        order = sorted(set(x["groups"]), key=lambda g: (-x["groups"].count(g), -g))
        return [[i for i, g in enumerate(x["groups"]) if g == group] for group in order]
    if rule == "grouped" and x.get("view") == "folds":
        # One group per fold: each test fold is one group's rows.
        return sorted([i for i, g in enumerate(x["groups"]) if g == group] for group in set(x["groups"]))
    if rule == "grouped":
        # Cases use one group per fold: ordering is immaterial, coverage is exact.
        groups = sorted(set(x["groups"]))
        return {"sizes": sorted(x["groups"].count(g) for g in groups),
                "overlap": [0]*x["folds"], "coverage": list(range(len(x["groups"])))}
    if rule == "seed":
        return [x["seed"], 2]
    if rule == "preprocessing_in_pipeline":
        if x.get("view") == "leakage":
            return 0
        train = np.asarray(x["train"], dtype=float).reshape(-1, 1)
        test = np.asarray(x["test"], dtype=float).reshape(-1, 1)
        missing = np.isnan(train)
        median = np.nanmedian(train, axis=0)
        fit = np.where(missing, median, train)
        held = np.where(np.isnan(test), median, test)
        if missing.any():
            fit = np.column_stack([fit, missing.astype(float)])
            held = np.column_stack([held, np.isnan(test).astype(float)])
        if not x.get("scale", True):  # an estimator not flagged scale gets the imputed values unscaled
            return held.tolist()
        sd = fit.std(axis=0)
        sd[sd == 0] = 1
        return ((held-fit.mean(axis=0))/sd).tolist()
    if rule in ("cv_training_part", "search_training_part"):
        if x.get("view") == "leakage":
            return 0
        if rule == "cv_training_part":
            return fold_mae(x)
        # One trial of the one candidate; the held-out rows predicted by the mean of every training target.
        mean = float(np.asarray(x["y"], dtype=float).mean())
        return {"scores": fold_mae(x), "trials": 1,
                "valid": sorted(len(g) for g in np.array_split(np.arange(len(x["y"])), x["folds"])),
                "held_out": [mean] * len(x["test"])}
    if rule == "baseline":
        y = [Decimal(str(v)) for v in x["train_truth"]]
        if x["task"] == "regression":
            mean = sum(y)/len(y)
        else:
            # G6's public tie convention is lexicographic after float conversion.
            mean = max(sorted(set(y), key=lambda v: str(float(v))), key=y.count)
        return [mean]*x["n"]
    if rule == "headline_imbalance":
        labels = [label for label, count in x["counts"] for _ in range(count)][:x["train"]]
        counts = [labels.count(c) for c in set(labels)]
        share = Decimal(min(counts)) / Decimal(len(labels))
        # The share is reported to 6 places; the threshold is tested on the share itself.
        return {"share": share.quantize(Decimal("0.000001")), "balanced": int(share < Decimal("0.1"))}
    y, p = [Decimal(str(v)) for v in x["truth"]], [Decimal(str(v)) for v in x["pred"]]
    n = len(y)
    if rule == "accuracy":
        return Decimal(sum(a == b for a, b in zip(y, p)))/n
    if rule == "balanced_accuracy":
        classes = set(y)
        return sum(Decimal(sum(a == b == c for a, b in zip(y, p)))/y.count(c) for c in classes)/len(classes)
    if rule == "mae":
        return sum(abs(a-b) for a, b in zip(y, p))/n
    squares = sum((a-b)**2 for a, b in zip(y, p))
    if rule == "rmse":
        return squares/n if x.get("view") == "squared" else (squares/n).sqrt()
    if rule == "r2":
        # Exact decimal arithmetic: equal targets give a total of exactly zero, so R2 is undefined.
        total = sum((a-sum(y)/n)**2 for a in y)
        return {"defined": 0} if total == 0 else 1-squares/total
    raise ValueError(rule)
