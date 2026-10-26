---
name: g6-adapt-sklearn
description: Use when you need to fit a scikit-learn model to rows of data, or compare a few, and need its score measured on rows held out by the tool itself, against a baseline, with an interval, after checks for target leakage, repeated rows, class imbalance and a fixed seed, so a training score or a leaky feature never passes for a result.
---

# G6 adapt_sklearn

`g6.ml.adapt_sklearn` fits a scikit-learn estimator from G6's own list to rows you give and measures it on rows it
held out itself. Before anything is fitted it checks the data: a seed, columns of one kind, enough held-out rows,
every class on both sides of the split, no identical rows across it, and no feature that leaks the target. The fit
runs in a bounded child process (a deadline, a memory cap except on macOS, a guard letting Python's sockets reach
only this machine), confined by the operating system where G6 can (then no network, and only its own folders writable), else
running as you, as the result's `limits` say; it is given the training answers, never the held-out ones; G6 scores what
comes back, against a baseline that ignores the features, on the same rows. It is an extension in the ml
family, behind the `ml` extra. Its best state is `qualified-draft`. Its rules rest on a committed source register.
No qualified person has reviewed this component; its rules are checked against their sources by G6's certificate and an
independent reader.

## When to use it

- You (a coding agent) are asked whether a model can predict something from a table, and need a number you can
  defend rather than one from code you wrote and scored yourself.
- You need to know whether a model beats doing nothing clever: the baseline is on every result.
- You suspect a column carries the answer (a field filled in after the outcome, a re-coded label) and want it found
  before a score is believed.
- You want to compare a few standard estimators fairly (`rank`), without letting the test rows choose between them.

## When not to use it

- Large data or deep learning: at most 20,000 rows, 200 features, 100 classes, 10,000 characters a cell and
  5,000,000 of text; rows hold only the target and the features; no neural networks.
- Time series, or rows grouped by a person, a shop or a day that must not be split: the split is random (stratified
  by class) and keeps only identical rows together.
- Tuning many settings at once: use adapt_automl's search.
- Keeping or shipping a model: nothing is saved; a model exists only for its call.
- Cleaning the table: profile it with adapt_pandas first.

## Ops

- `fit_eval`: `rows`, `target`, `features`, `task`, `seed`, `estimator` and its `params`; optional `predict_rows`,
  `held_out_fraction` (0.1 to 0.5, default 0.25) and `timeout_s` (default 120, at most 600).
- `rank`: the same rows, plus `candidates` (default a small set per task) and `folds` (2 to 10, default 5).
- `estimators`: the estimators G6 fits, for a `task` or all, with each parameter's range and what G6 fixes.

The estimators: `logistic_regression`, `linear_regression`, `ridge`, `random_forest`, `extra_trees`,
`gradient_boosting` (histogram-based), `decision_tree`, `k_neighbors` and `naive_bayes`, each for the tasks it
serves. Numbers are imputed by their training median with a missing-value indicator (scaled for the linear models
and neighbours); text is one-hot encoded with a missing value as its own category; every step is fitted on training
rows only, inside one pipeline, inside each fold too.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_sklearn", "fit_eval", '{"rows": [...], "target": "churned", ...}')`, then read
`result.headline`. No op is sensitive: a fit starts a child of G6's own interpreter through `g6.core.sandbox` and
keeps no model, only the count of held-out scorings. From Python: `get_registry().invoke("adapt_sklearn", op, params)`.

Composed:

- adapt_pandas `load` gives rows typed by one rule; its `profile` names the mixed columns this refuses.
- `rank` first to choose an estimator on the training rows, then `fit_eval` with settings, never the other way.
- The evidence carries the rows' hash and the held-out set's hash, so a later call can show it used the same rows
  and the same held-out rows.

## Failure modes it catches

- `train_test_leakage` (a model scored on data it was trained on, or on a leaky split):
  - the split is the extension's own, drawn from the rows' own hash, so the same rows give the same held-out rows
    whatever the seed (which drives the fits, folds and resamples); stratified by class, identical rows on one side;
    a caller cannot hand in a split, a score, a baseline or a test set;
  - each scoring of a held-out set is recorded by its hash under `G6_HOME`, and every reply counts the earlier ones:
    a caution against retrying until a score looks good, never a gate;
  - the headline is always held out; the training score is reported beside it as the train score, with the gap;
  - a feature equal to the target, one with Pearson or Spearman |r| of 0.99 or more against a numeric target, or
    one that alone predicts the target for every row from the others (leave-one-out) at 0.99 or better, is refused
    with `G6_E_ML_LEAKAGE` before anything is fitted; a held-out score of 0.99 or more, which features can reach
    together though none does alone, is refused with `G6_E_ML_LEAKAGE` too, never reported as a measurement;
  - identical rows across the split are refused with `G6_E_ML_DUPLICATES_ACROSS_SPLIT`; fewer than 10 independent
    held-out rows (identical rows count once) or a class not on both sides with `G6_E_ML_TOO_FEW_HELD_OUT`; a
    missing seed, or a `random_state` among the params, with `G6_E_ML_SEED_MISSING`;
  - the fit is never given the held-out answers, and G6, not the fit, scores the predictions.
- `ill_suited_model` (a model ill-suited to the data it is used on):
  - every result sets the model against a baseline on the same rows; the card is `G6_E_ML_MEASURED` only when the
    lift's whole 95% interval is above zero, `G6_E_ML_BELOW_BASELINE` otherwise, and the message says so plainly;
  - `rank` compares candidates by cross-validation on the training rows only and scores the best once on the held-out
    rows; a candidate that cannot be validated is excluded with why, never scored on its training rows;
  - with the smallest class under 10% of the training rows the headline is balanced accuracy, and the message says why.

## Reading the result

- `headline`: the metric, `held_out`, `baseline`, `lift`, `train`, `gap`, `held_out_rows`, `held_out_groups` (the
  independent ones), `interval` (95%, from 1,000 seeded resamples by group, fewer on a large held-out set as
  `lowered` says) and `clearly_better`.
- `scores`: every metric for the held-out rows, the training rows and the baseline (accuracy and balanced accuracy,
  or R squared, mean absolute error and root mean squared error).
- `held_out_use`: how many times these held-out rows were scored before, by op.
- `evidence`: the split, the leakage probes, the class balance, the fit (estimator, parameters, pipeline, versions,
  warnings, limits), the scores and the rules applied with their sources.

## Blind spots

- `unevaluated_model_shipped`: a held-out score is one measurement on one split of these rows; it says nothing of
  drift, of other populations or of fairness, so a model chosen here is still unevaluated for production use.
- Leakage through an honestly named proxy (a feature known only after the outcome that is not a near-copy) passes
  every probe; so does a time order broken by a random split.
- A leak spread over several features (a total and its parts) passes the one-feature probes; only a held-out score
  of 0.99 or more catches it, so a smaller one goes unseen.
- Nearly identical rows (differing in case, spaces or float noise) are not identical: they can sit on both sides of
  the split and inflate the score; clean them first.
- A near-deterministic feature, or features that settle the task at 0.99 or more, are refused as a leak.
- Grouped data (several rows per person) is split row by row unless the rows are identical.
- Small held-out sets give wide intervals; read them before the point score.

## What it needs

- `pip install "g6[ml]"` (scikit-learn, BSD-3-Clause, with NumPy and SciPy). Without it `fit_eval` and `rank` are
  refused with `G6_E_ML_BACKEND_NOT_INSTALLED` and the install line; the checks before a fit still run and
  `estimators` still answers.
- No model, no network access, no outside program; the fit runs in a child of G6's own Python, and the fit's
  `limits` say what that child's sandbox does and does not hold back.

## Examples

```g6-example op=fit_eval
{"rows": [{"tenure":5,"plan":"plus","churned":"yes"},{"tenure":28,"plan":"plus","churned":"no"},{"tenure":38,"plan":"plus","churned":"no"},{"tenure":7,"plan":"plus","churned":"yes"},{"tenure":22,"plan":"plus","churned":"no"},{"tenure":10,"plan":"basic","churned":"yes"},{"tenure":17,"plan":"plus","churned":"no"},{"tenure":10,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"plus","churned":"yes"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":37,"plan":"pro","churned":"no"},{"tenure":26,"plan":"plus","churned":"no"},{"tenure":34,"plan":"plus","churned":"no"},{"tenure":14,"plan":"plus","churned":"yes"},{"tenure":35,"plan":"pro","churned":"no"},{"tenure":23,"plan":"pro","churned":"no"},{"tenure":40,"plan":"plus","churned":"no"},{"tenure":23,"plan":"plus","churned":"no"},{"tenure":25,"plan":"plus","churned":"no"},{"tenure":39,"plan":"plus","churned":"no"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":12,"plan":"plus","churned":"yes"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":4,"plan":"plus","churned":"yes"},{"tenure":32,"plan":"plus","churned":"no"},{"tenure":10,"plan":"plus","churned":"yes"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":27,"plan":"pro","churned":"no"},{"tenure":20,"plan":"basic","churned":"yes"},{"tenure":36,"plan":"pro","churned":"no"},{"tenure":31,"plan":"pro","churned":"no"},{"tenure":14,"plan":"basic","churned":"yes"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":39,"plan":"pro","churned":"no"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":32,"plan":"plus","churned":"no"},{"tenure":37,"plan":"pro","churned":"no"},{"tenure":29,"plan":"plus","churned":"no"},{"tenure":6,"plan":"plus","churned":"yes"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":33,"plan":"plus","churned":"no"},{"tenure":30,"plan":"pro","churned":"no"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":22,"plan":"plus","churned":"yes"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":7,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"plus","churned":"no"},{"tenure":26,"plan":"pro","churned":"no"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":20,"plan":"plus","churned":"yes"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":7,"plan":"basic","churned":"yes"},{"tenure":25,"plan":"plus","churned":"no"},{"tenure":14,"plan":"basic","churned":"yes"},{"tenure":17,"plan":"plus","churned":"yes"},{"tenure":3,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"pro","churned":"no"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":26,"plan":"pro","churned":"no"},{"tenure":7,"plan":"plus","churned":"yes"},{"tenure":31,"plan":"plus","churned":"yes"},{"tenure":31,"plan":"plus","churned":"no"},{"tenure":1,"plan":"plus","churned":"yes"},{"tenure":34,"plan":"plus","churned":"no"},{"tenure":43,"plan":"plus","churned":"no"},{"tenure":13,"plan":"plus","churned":"no"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":21,"plan":"pro","churned":"no"},{"tenure":43,"plan":"plus","churned":"no"},{"tenure":31,"plan":"plus","churned":"no"},{"tenure":22,"plan":"plus","churned":"no"},{"tenure":11,"plan":"plus","churned":"yes"},{"tenure":16,"plan":"plus","churned":"no"},{"tenure":8,"plan":"basic","churned":"yes"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":41,"plan":"pro","churned":"no"},{"tenure":13,"plan":"plus","churned":"yes"}], "target": "churned", "features": ["tenure", "plan"], "task": "classification", "seed": 7, "estimator": "logistic_regression", "predict_rows": [{"tenure": 3, "plan": "basic"}]}
```

```g6-example op=rank
{"rows": [{"tenure":5,"plan":"plus","churned":"yes"},{"tenure":28,"plan":"plus","churned":"no"},{"tenure":38,"plan":"plus","churned":"no"},{"tenure":7,"plan":"plus","churned":"yes"},{"tenure":22,"plan":"plus","churned":"no"},{"tenure":10,"plan":"basic","churned":"yes"},{"tenure":17,"plan":"plus","churned":"no"},{"tenure":10,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"plus","churned":"yes"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":37,"plan":"pro","churned":"no"},{"tenure":26,"plan":"plus","churned":"no"},{"tenure":34,"plan":"plus","churned":"no"},{"tenure":14,"plan":"plus","churned":"yes"},{"tenure":35,"plan":"pro","churned":"no"},{"tenure":23,"plan":"pro","churned":"no"},{"tenure":40,"plan":"plus","churned":"no"},{"tenure":23,"plan":"plus","churned":"no"},{"tenure":25,"plan":"plus","churned":"no"},{"tenure":39,"plan":"plus","churned":"no"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":12,"plan":"plus","churned":"yes"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":4,"plan":"plus","churned":"yes"},{"tenure":32,"plan":"plus","churned":"no"},{"tenure":10,"plan":"plus","churned":"yes"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":27,"plan":"pro","churned":"no"},{"tenure":20,"plan":"basic","churned":"yes"},{"tenure":36,"plan":"pro","churned":"no"},{"tenure":31,"plan":"pro","churned":"no"},{"tenure":14,"plan":"basic","churned":"yes"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":39,"plan":"pro","churned":"no"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":32,"plan":"plus","churned":"no"},{"tenure":37,"plan":"pro","churned":"no"},{"tenure":29,"plan":"plus","churned":"no"},{"tenure":6,"plan":"plus","churned":"yes"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":33,"plan":"plus","churned":"no"},{"tenure":30,"plan":"pro","churned":"no"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":22,"plan":"plus","churned":"yes"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":7,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"plus","churned":"no"},{"tenure":26,"plan":"pro","churned":"no"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":20,"plan":"plus","churned":"yes"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":7,"plan":"basic","churned":"yes"},{"tenure":25,"plan":"plus","churned":"no"},{"tenure":14,"plan":"basic","churned":"yes"},{"tenure":17,"plan":"plus","churned":"yes"},{"tenure":3,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"pro","churned":"no"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":26,"plan":"pro","churned":"no"},{"tenure":7,"plan":"plus","churned":"yes"},{"tenure":31,"plan":"plus","churned":"yes"},{"tenure":31,"plan":"plus","churned":"no"},{"tenure":1,"plan":"plus","churned":"yes"},{"tenure":34,"plan":"plus","churned":"no"},{"tenure":43,"plan":"plus","churned":"no"},{"tenure":13,"plan":"plus","churned":"no"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":21,"plan":"pro","churned":"no"},{"tenure":43,"plan":"plus","churned":"no"},{"tenure":31,"plan":"plus","churned":"no"},{"tenure":22,"plan":"plus","churned":"no"},{"tenure":11,"plan":"plus","churned":"yes"},{"tenure":16,"plan":"plus","churned":"no"},{"tenure":8,"plan":"basic","churned":"yes"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":41,"plan":"pro","churned":"no"},{"tenure":13,"plan":"plus","churned":"yes"}], "target": "churned", "features": ["tenure", "plan"], "task": "classification", "seed": 7, "candidates": ["logistic_regression", "random_forest", "naive_bayes"], "folds": 3}
```

```g6-example op=estimators
{"task": "regression"}
```

From Python: a column filled in after the sale is refused as a leak; without it (rows hold only the declared
columns) the model is measured.

```python
import random

from g6.core.registry import get_registry

reg = get_registry()
rng = random.Random(3)
rows = []
for _ in range(80):
    size, rooms = rng.uniform(40, 200), rng.randint(1, 6)
    price = round(2.5 * size + 10 * rooms + rng.gauss(0, 40))
    rows.append({"size": round(size), "rooms": rooms, "price": price, "sold_for": price + 25})
ask = {"target": "price", "task": "regression", "seed": 3, "estimator": "ridge"}
leak = reg.invoke("adapt_sklearn", "fit_eval", {**ask, "rows": rows, "features": ["size", "rooms", "sold_for"]}).unwrap()
assert leak["code"] == "G6_E_ML_LEAKAGE" and "sold_for" in leak["reason"]
honest = [{k: row[k] for k in ("size", "rooms", "price")} for row in rows]
fit = reg.invoke("adapt_sklearn", "fit_eval", {**ask, "rows": honest, "features": ["size", "rooms"]}).unwrap()
print(fit["warning_card"]["message"])
if fit["completion_state"] == "qualified-draft":
    assert fit["headline"]["metric"] == "r2" and fit["headline"]["held_out_rows"] >= 10
else:
    assert fit["code"] == "G6_E_ML_BACKEND_NOT_INSTALLED"   # pip install "g6[ml]"
```

## The four open problems

- **Learning signals**: every result records the rows' hash, the held-out set's hash, the seed and the held-out score
  with its baseline, and each scoring of a held-out set is counted under `G6_HOME`. Not yet: nothing here stores the
  scores or learns from them.
- **Evaluators the model cannot influence**: the held-out answers never reach the fitting process; G6's own code
  computes every score, the baseline and the intervals, and the leakage probes run before any fit; no model is
  consulted and a caller cannot hand in a split or a score.
- **Transfer by failure mode**: the same checks (leakage, identical rows, imbalance, seed, held-out size) run for
  every estimator and for adapt_automl, with the same codes.
- **Enough held-out cases**: fewer than 10 independent held-out rows, or a class missing from either side, is
  refused, and every score carries its interval. Not yet: there is no fixed set of datasets with known scores; the
  tests use built data with known leaks, noise that nothing can learn and imbalance.
