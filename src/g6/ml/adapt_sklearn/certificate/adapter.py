# SPDX-License-Identifier: Apache-2.0
"""Invoke the actual shared implementation; project numeric observations only. A guard's observation is 1 when it
fires and 0 when it holds; a guard whose function was removed (it returns None) holds. Shared by adapt_sklearn and
adapt_automl, whose checks.json inherits this folder."""
from types import SimpleNamespace

import numpy as np

from g6.ml import _fit
from g6.ml._types import INPUT_INVALID, LEAKAGE, TOO_FEW_HELD_OUT, TOO_LARGE, DatasetSpec, MLError, make_split


def estimator(scale=True):
    return {"module": "sklearn.dummy", "class": "DummyRegressor", "params": {}, "scale": scale}


def column(values):
    return [[None if v is None else float(v)] for v in values]


def fitted_preparation(function, x, held_out):
    """The preparation step fit_predict fitted, the code that chooses which rows the pipeline learns from; the
    pipeline is the one this function built (fit_predict calls it by name)."""
    built = []

    def recorded(spec, job):
        built.append(function(spec, job))
        return built[-1]

    job = {"task": "regression", "numeric": [0], "categorical": [], "max_categories": 50, "seed": x["seed"],
           "X_train": column(x["train"]), "y_train": [float(i) for i in range(len(x["train"]))],
           "X_held_out": column(held_out)}
    _fit._pipeline = recorded
    try:
        _fit.fit_predict(job, estimator(x.get("scale", True)))
    finally:
        _fit._pipeline = function
    return built[-1].named_steps["prepare"]


def fold_groups(x):
    """One group per array_split part, so each fold of the family's grouped folds is one part."""
    groups = [0] * len(x["y"])
    for group, members in enumerate(np.array_split(np.arange(len(x["y"])), x["folds"])):
        for i in members:
            groups[int(i)] = group
    return groups


def ranked(x, held_out):
    """Sorted fold scores from the rank path, which cross-validates with this function (by name) inside the training
    rows and reads the held-out rows only to predict them; one fold per array_split group."""
    job = {"task": "regression", "numeric": [0], "categorical": [], "max_categories": 50, "seed": x["seed"],
           "metric": "mae", "X_train": column(x["train"]), "y_train": [float(v) for v in x["y"]],
           "groups_train": fold_groups(x), "folds": x["folds"], "X_held_out": column(held_out),
           "candidates": [{**estimator(), "name": "mean"}]}
    return sorted(_fit._rank(job)["cv"][0]["fold_scores"])


def folded(function, x):
    """Sorted fold scores from this function itself on the family's grouped folds, one dummy-regressor candidate."""
    job = {"task": "regression", "numeric": [0], "categorical": [], "max_categories": 50, "seed": x["seed"],
           "metric": "mae", "X_train": column(x["train"]), "y_train": [float(v) for v in x["y"]],
           "groups_train": fold_groups(x), "folds": x["folds"]}
    X, y_fit, splits = _fit.folds(job)
    return sorted(function({**estimator(), "name": "mean"}, job, X, y_fit, splits))


def searched(function, x, held_out, whole=False):
    """FLAML's search with one fixed dummy candidate, one trial and no search dimensions: its sorted fold scores; or,
    whole, also the trials it ran, its sorted validation fold sizes and its predictions for the held-out rows."""
    spec = {**estimator(), "name": "mean", "fixed": {}, "ranges": {}, "cheap": {}}
    job = {"numeric": [0], "categorical": [], "max_categories": 50, "seed": x["seed"], "metric": "mae",
           "task": "regression", "X_train": column(x["train"]), "y_train": [float(v) for v in x["y"]],
           "groups_train": fold_groups(x), "folds": x["folds"], "X_held_out": column(held_out), "trials": 1,
           "time_budget_s": 5, "candidates": [spec]}
    out = function(job)
    row = out["leaderboard"][0]
    scores = sorted(row["fold_scores"])
    if not whole:
        return scores
    return {"scores": scores, "trials": row["trials"], "valid": sorted(f["valid"] for f in out["folds"]),
            "held_out": out["held_out"]}


def expand(x):
    """Rows from (feature, target, count) triples, in order."""
    return [{"f": f, "y": y} for f, y, count in x["pairs"] for _ in range(count)]


def refused(function, codes, *args, said=""):
    """1 when the function refuses with one of the codes (and says ``said``), 0 when it answers, 2 when it refuses
    for another reason (which no guard case expects)."""
    try:
        function(*args)
    except MLError as exc:
        return 1 if exc.code in codes and said in exc.reason else 2
    return 0


def evaluate(function, rule, inputs):
    x = inputs
    if rule in ("held_out", "stratified"):
        # A case gives its labels as (label, count) pairs and its hold-out as one fold's share (1 / folds), as the
        # source prints them.
        labels = ([label for label, count in x["counts"] for _ in range(count)] if "counts" in x
                  else x.get("labels", list(range(x.get("n", 0)))))
        fraction = 1 / x["folds"] if "folds" in x else float(x["fraction"])
        spec = DatasetSpec(rows=[{"x": i, "y": label} for i, label in enumerate(labels)],
                           target="y", features=["x"], task=x.get("task", "classification"), seed=x.get("seed", 0),
                           held_out_fraction=fraction)
        split = function(spec)
        if rule == "held_out":
            if x.get("view") == "overlap":
                return len(set(split.train) & set(split.held_out))
            if x.get("view") == "groups":  # the held-out groups the split counts (rows with identical features: one)
                return split.held_out_groups
            return [len(split.train), len(split.held_out)]
        classes = sorted(set(labels))
        return [[sum(labels[i] == c for i in indices) for c in classes] for indices in (split.train, split.held_out)]
    if rule == "grouped":
        codes = sorted(set(x["y"]), key=str)  # a case's labels are the source's letters
        job = {"X_train": [[float(v)] for v in x["X"]], "y_train": [codes.index(v) for v in x["y"]],
               "groups_train": x["groups"], "folds": x["folds"], "seed": x.get("seed", 0), "task": "regression"}
        _, _, splits = function(job)
        if x.get("view") == "folds":
            return sorted(sorted(int(i) for i in test) for _, test in splits)
        if x.get("view") == "order":  # the test folds in the order the seeded shuffle deals them
            return [sorted(int(i) for i in test) for _, test in splits]
        overlaps = [len({x["groups"][int(i)] for i in train} & {x["groups"][int(i)] for i in test})
                    for train, test in splits]
        return {"sizes": sorted(len(test) for _, test in splits), "overlap": overlaps,
                "coverage": sorted(int(i) for _, test in splits for i in test)}
    if rule == "grouped_hold_out":
        # Rows whose only feature is a key: rows sharing a key are identical. The held-out rows that repeat a
        # training row's key, or (view held) how many rows are held out.
        rows = [{"x": key, "y": float(i)} for i, key in enumerate(x["keys"])]
        spec = DatasetSpec(rows=rows, target="y", features=["x"], task="regression", seed=0,
                           held_out_fraction=float(x["fraction"]))
        split = function(spec)
        if x.get("view") == "held":
            return len(split.held_out)
        if x.get("view") == "groups":
            return split.held_out_groups
        trained = {x["keys"][i] for i in split.train}
        return sum(x["keys"][i] in trained for i in split.held_out)
    if rule == "seed":
        # The seed set on an actual G6-created tree, and the spec's own parameter kept beside it (-1: it was lost).
        spec = {"module": "sklearn.tree", "class": "DecisionTreeClassifier", "params": {"max_depth": 2}}
        params = function(spec, x["seed"]).get_params()
        return [params["random_state"], -1 if params["max_depth"] is None else params["max_depth"]]
    if rule == "preprocessing_in_pipeline":
        prepare = fitted_preparation(function, x, x["test"])
        if x.get("view") == "leakage":
            # Different held-out rows must leave the fitted transformation of the training rows unchanged.
            other = fitted_preparation(function, x, [-1000000])
            train = _fit._array(column(x["train"]))
            return float(np.max(np.abs(prepare.transform(train)-other.transform(train))))
        return prepare.transform(_fit._array(column(x["test"]))).tolist()
    if rule in ("cv_training_part", "search_training_part"):
        if x.get("view") == "leakage":
            # The held-out rows reach the rank path (which cross-validates with this function by name) and the
            # search; they must change no fold score.
            run = ranked if rule == "cv_training_part" else (lambda x, held: searched(function, x, held))
            try:
                first, second = run(x, x["test"]), run(x, [-1000000])
            except Exception:  # noqa: BLE001 - the path around the function failed: not 0 (an error of the
                return -1      # function itself still makes this row an error, as the harness records it)
            return max(abs(a-b) for a, b in zip(first, second))
        return folded(function, x) if rule == "cv_training_part" else searched(function, x, x["test"], whole=True)
    if rule == "baseline":
        return function(x["task"], [float(v) for v in x["train_truth"]], x["n"])
    if rule == "headline_imbalance":
        labels = [label for label, count in x["counts"] for _ in range(count)]
        spec = DatasetSpec(rows=[{"x": i, "y": label} for i, label in enumerate(labels)], target="y", features=["x"],
                           task="classification", seed=0)
        balance = function(spec, tuple(range(x["train"])))
        return {"share": balance["minority_share"], "balanced": int(balance["headline_metric"] == "balanced_accuracy")}
    # --- guards ---
    if rule == "held_out_from_rows":
        n = x["scored_before"]
        text = function({"scored_before": n, "earlier_ops": {"fit_eval": n} if n else {}})
        return int(isinstance(text, str) and "optimistic" in text)
    if rule == "leakage_probes":
        spec = DatasetSpec(rows=expand(x), target="y", features=["f"], task=x["task"], seed=0)
        return int(bool(function(spec, make_split(spec))))
    if rule == "too_good":
        prepared = SimpleNamespace(spec=SimpleNamespace(features=["x"]), evidence=[])
        measured = {"headline": {"held_out": float(x["held_out"]), "higher_is_better": True, "metric": "accuracy"},
                    "held_out_use": {"scored_before": 0, "earlier_ops": {}}}
        return refused(function, (LEAKAGE,), prepared, measured)
    if rule == "min_held_out":
        if x.get("thin_class"):
            rows = [{"x": i, "y": i % 2} for i in range(40)] + [{"x": 1000, "y": 2}]
            spec = DatasetSpec(rows=rows, target="y", features=["x"], task="classification", seed=0)
        else:
            rows = [{"x": i, "y": (i * 37) % 11} for i in range(x["n"])]
            spec = DatasetSpec(rows=rows, target="y", features=["x"], task="regression", seed=0,
                               held_out_fraction=float(x["fraction"]))
        try:
            function(spec)
        except MLError as exc:
            mine = exc.code == TOO_FEW_HELD_OUT or (exc.code == INPUT_INVALID and "fewer than 2 distinct rows"
                                                    in exc.reason)
            return 1 if mine else 2
        return 0
    if rule == "interval":
        # n held-out rows of class 1; the model is right on its first model_right rows, the baseline on its first
        # base_right rows, and wrong elsewhere.
        n = x["n"]
        truth = [1] * n
        pred = [1] * x["model_right"] + [0] * (n - x["model_right"])
        base = [1] * x["base_right"] + [0] * (n - x["base_right"])
        out = function("classification", "accuracy", truth, pred, base, seed=x["seed"])
        return 0 if out is None else int(not out["clearly_better"])
    if rule == "overfit_gap":
        held, gap = float(x["held_out"]), float(x["gap"])
        out = function({"metric": "accuracy", "held_out": held, "train": held + gap, "gap": gap,
                        "higher_is_better": True})
        return 0 if out is None else int("far better than new ones" in out)
    if rule == "bounds":
        rows = [{"y": i % 2, "x": "a" * x.get("cell", 1)} for i in range(x.get("cells", 2))]
        if x.get("classes"):
            rows = [{"y": i, "x": "a"} for i in range(x["classes"])]
        if x.get("undeclared"):
            rows[0] = {**rows[0], "z": 1}
        spec = DatasetSpec(rows=rows, target="y", features=["x"], task="classification", seed=0)
        predict = [{"x": "a" * x["predict_cell"]}] if "predict_cell" in x else None
        return refused(function, (TOO_LARGE, INPUT_INVALID), spec, predict)
    truth, pred = [float(v) for v in x["truth"]], [float(v) for v in x["pred"]]
    result = function(truth, pred)
    if rule == "r2":
        return {"defined": 0} if result is None else result
    if rule == "rmse" and x.get("view") == "squared":
        return result**2
    return result
