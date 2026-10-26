---
name: g6-adapt-automl
description: Use when you want the best of several scikit-learn estimators and their settings for a table, found by a bounded, seeded search (a time budget and a trial cap) that compares settings by cross-validation on training rows only, with the winner measured on rows the tool held out itself against a baseline, and a missing search engine refused rather than silently replaced.
---

# G6 adapt_automl

`g6.ml.adapt_automl` searches estimators and their settings for rows you give, within a time budget and a trial
cap, and measures the choice on rows it held out itself. The family's checks run first (a seed, columns of one kind,
enough held-out rows, every class on both sides, no identical rows across the split, no feature that leaks the
target). A registered search backend, FLAML's cost-frugal search by default, then tries settings of each candidate
inside its ranges, each trial a cross-validation inside the training rows; the best is refitted and G6 scores its
held-out predictions against a baseline on the same rows. It is an extension in the ml family, behind the `automl`
extra. Its best state is `qualified-draft`. No qualified person has reviewed this component; its rules are checked
against their sources by G6's certificate and an independent reader.

## When to use it

- You (a coding agent) need a good model for a table and would otherwise tune settings by hand, trying things until
  a number looks good.
- You need the search to be bounded (seconds and trials you set) and repeatable (a seed, and the evidence says
  what ended the search).
- You need the winner's score to be one the search could not choose: rows held out before the search began.

## When not to use it

- One estimator at chosen settings: use adapt_sklearn's `fit_eval`; a quick comparison at default settings:
  adapt_sklearn's `rank`.
- Gradient-boosting libraries such as XGBoost or LightGBM, neural networks, or engines like MLJAR, H2O,
  AutoGluon and auto-sklearn: not shipped (`backends` says why for each).
- Time series or grouped rows that must not be split; more than 20,000 rows, 200 features or 100 classes, a cell
  over 10,000 characters, 5,000,000 characters of text, or columns besides the target and the features (each is
  refused before anything is prepared).
- Keeping or deploying a model: nothing is saved.

## Ops

- `search`: `rows`, `target`, `features`, `task`, `seed`; optional `backend` (default `flaml`), `candidates`
  (default four per task), `time_budget_s` (default 30, at most 300), `trials` (default 20, at most 200), `folds`
  (2 to 10, default 3), `held_out_fraction` and `predict_rows`.
- `backends`: the backends registered and whether each is installed, the candidates and their ranges, and the
  engines not shipped, with why.

The candidates: `logistic_regression`, `ridge`, `random_forest`, `extra_trees`, `gradient_boosting`
(histogram-based) and `k_neighbors`, each with ranges for its main settings and a cheap corner the search starts
from. The trial cap and the time budget are shared equally among the candidates.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_automl", "search", '{"rows": [...], "target": "churned", ...}')`, then read
`result.chosen`, `result.chosen_config` and `result.headline`. No op is sensitive: a search runs in a child of G6's
own interpreter through `g6.core.sandbox` and keeps no model, only the count of held-out scorings. From Python:
`get_registry().invoke("adapt_automl", op, params)`.

Composed:

- adapt_pandas `profile` first; the search refuses a mixed column.
- For predictions from the chosen settings, pass `predict_rows` to `search` itself: the search's ranges are not
  adapt_sklearn's parameter lists, and a refit on the same rows would only score the same held-out rows again.
- A backend of your own registers in `search_backends`; it is given the training part with its answers and the
  held-out features without theirs, and G6 scores what it returns.

## Failure modes it catches

- `unsystematic_tuning` (models tuned by hand without a systematic search): a seeded search over stated ranges,
  bounded by the trial cap and the time budget, with every trial's estimator, settings and fold scores in the
  leaderboard; the evidence says whether the cap or the budget ended it.
- `silent_fallback` (a tool silently missing or replaced, with the result reported as if it ran): a backend that is
  not registered, or not installed, is refused with `G6_E_ML_BACKEND_NOT_INSTALLED` and the reason; nothing else
  runs in its place, and no baseline is returned as a result.
- `train_test_leakage` (a model scored on data it was trained on, or on a leaky split):
  - settings are compared by cross-validation inside the training rows only, every fitted step (imputation,
    scaling, encoding) refitted inside each fold;
  - a trial that fails is recorded with why, and a candidate none of whose trials finished is excluded, never
    scored on its training rows;
  - the leaderboard's scores are labelled optimistic (the search picked them); the headline is the held-out score,
    which the search never saw the answers for;
  - the held-out rows are drawn from the rows' own hash, not the seed, so trying seed after seed never picks an
    easier held-out set; each scoring of a held-out set is recorded by its hash under `G6_HOME`, and every reply
    says how many times those rows were scored before (`held_out_use`): a caution, never a gate;
  - the family's leakage, identical-row, held-out-size and seed checks refuse before any search starts, and a
    held-out score of 0.99 or more is refused as a leak the features carry together, never reported.

## Reading the result

- `chosen`, `chosen_config` and `leaderboard` (per candidate: trials, failed trials, best mean fold score, its fold
  scores and settings, or why it was excluded).
- `headline` and `scores`: as adapt_sklearn's, measured by G6 on the held-out rows against the baseline, with 95%
  intervals; the card is `G6_E_ML_MEASURED` only when the lift's whole interval is above zero, else
  `G6_E_ML_BELOW_BASELINE`.
- `evidence`: the split, the leakage probes, the class balance, the search (backend, limits, what ended it, versions,
  warnings), the scores and the rules applied with their sources.

## Blind spots

- `unevaluated_model_shipped`: the held-out score is one measurement on one split of these rows; it says nothing of
  drift, other populations or fairness.
- With a time budget that ends the search, the trials run depend on the machine's speed, so the result may differ
  between runs; with the trial cap ending it, it repeats.
- The search compares settings within each candidate's ranges only; a better region outside them is not tried.
- Leakage through an honestly named proxy, and a time order broken by a random split, pass every probe.
- A leak spread over several features (a total and its parts) passes the one-feature probes; only a held-out score
  of 0.99 or more catches it, and a task the features genuinely settle that well is refused too.
- Rows that are nearly identical (differing in case, spaces or float noise) are not identical rows: they can sit on
  both sides of the split and inflate the score; clean them first.
- Small held-out sets give wide intervals; read them before the point score.

## What it needs

- `pip install "g6[automl]"` (FLAML's tuner, MIT, with NumPy; scikit-learn, BSD-3-Clause). Without it `search` is
  refused with `G6_E_ML_BACKEND_NOT_INSTALLED` and the install line; the checks before a search still run and
  `backends` still answers.
- No model, no network access, no outside program; the search runs in a child of G6's own Python behind the
  sandbox's network guard, which covers Python's own sockets only (the search's `limits` say so).

## Examples

```g6-example op=search
{"rows": [{"tenure":5,"plan":"plus","churned":"yes"},{"tenure":28,"plan":"plus","churned":"no"},{"tenure":38,"plan":"plus","churned":"no"},{"tenure":7,"plan":"plus","churned":"yes"},{"tenure":22,"plan":"plus","churned":"no"},{"tenure":10,"plan":"basic","churned":"yes"},{"tenure":17,"plan":"plus","churned":"no"},{"tenure":10,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"plus","churned":"yes"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":37,"plan":"pro","churned":"no"},{"tenure":26,"plan":"plus","churned":"no"},{"tenure":34,"plan":"plus","churned":"no"},{"tenure":14,"plan":"plus","churned":"yes"},{"tenure":35,"plan":"pro","churned":"no"},{"tenure":23,"plan":"pro","churned":"no"},{"tenure":40,"plan":"plus","churned":"no"},{"tenure":23,"plan":"plus","churned":"no"},{"tenure":25,"plan":"plus","churned":"no"},{"tenure":39,"plan":"plus","churned":"no"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":12,"plan":"plus","churned":"yes"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":4,"plan":"plus","churned":"yes"},{"tenure":32,"plan":"plus","churned":"no"},{"tenure":10,"plan":"plus","churned":"yes"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":27,"plan":"pro","churned":"no"},{"tenure":20,"plan":"basic","churned":"yes"},{"tenure":36,"plan":"pro","churned":"no"},{"tenure":31,"plan":"pro","churned":"no"},{"tenure":14,"plan":"basic","churned":"yes"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":36,"plan":"plus","churned":"no"},{"tenure":39,"plan":"pro","churned":"no"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":32,"plan":"plus","churned":"no"},{"tenure":37,"plan":"pro","churned":"no"},{"tenure":29,"plan":"plus","churned":"no"},{"tenure":6,"plan":"plus","churned":"yes"},{"tenure":24,"plan":"pro","churned":"no"},{"tenure":33,"plan":"plus","churned":"no"},{"tenure":30,"plan":"pro","churned":"no"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":22,"plan":"plus","churned":"yes"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":7,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"plus","churned":"no"},{"tenure":26,"plan":"pro","churned":"no"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":20,"plan":"plus","churned":"yes"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":7,"plan":"basic","churned":"yes"},{"tenure":25,"plan":"plus","churned":"no"},{"tenure":14,"plan":"basic","churned":"yes"},{"tenure":17,"plan":"plus","churned":"yes"},{"tenure":3,"plan":"basic","churned":"yes"},{"tenure":28,"plan":"pro","churned":"no"},{"tenure":33,"plan":"pro","churned":"no"},{"tenure":26,"plan":"pro","churned":"no"},{"tenure":7,"plan":"plus","churned":"yes"},{"tenure":31,"plan":"plus","churned":"yes"},{"tenure":31,"plan":"plus","churned":"no"},{"tenure":1,"plan":"plus","churned":"yes"},{"tenure":34,"plan":"plus","churned":"no"},{"tenure":43,"plan":"plus","churned":"no"},{"tenure":13,"plan":"plus","churned":"no"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":21,"plan":"pro","churned":"no"},{"tenure":43,"plan":"plus","churned":"no"},{"tenure":31,"plan":"plus","churned":"no"},{"tenure":22,"plan":"plus","churned":"no"},{"tenure":11,"plan":"plus","churned":"yes"},{"tenure":16,"plan":"plus","churned":"no"},{"tenure":8,"plan":"basic","churned":"yes"},{"tenure":15,"plan":"basic","churned":"yes"},{"tenure":27,"plan":"plus","churned":"no"},{"tenure":41,"plan":"pro","churned":"no"},{"tenure":13,"plan":"plus","churned":"yes"}], "target": "churned", "features": ["tenure", "plan"], "task": "classification", "seed": 7, "candidates": ["logistic_regression", "random_forest"], "time_budget_s": 20, "trials": 8}
```

```g6-example op=backends
{}
```

From Python: an engine G6 does not ship is refused, never replaced; the shipped one is measured on held-out rows.

```python
import random

from g6.core.registry import get_registry

reg = get_registry()
rng = random.Random(3)
rows = []
for _ in range(80):
    size, rooms = rng.uniform(40, 200), rng.randint(1, 6)
    rows.append({"size": round(size), "rooms": rooms, "price": round(2.5 * size + 10 * rooms + rng.gauss(0, 40))})
ask = {"rows": rows, "target": "price", "features": ["size", "rooms"], "task": "regression", "seed": 3,
       "candidates": ["ridge", "random_forest"], "time_budget_s": 20, "trials": 6}
other = reg.invoke("adapt_automl", "search", {**ask, "backend": "mljar"}).unwrap()
assert other["code"] == "G6_E_ML_BACKEND_NOT_INSTALLED" and "no other backend was used" in other["reason"]
found = reg.invoke("adapt_automl", "search", ask).unwrap()
print(found["warning_card"]["message"])
if found["completion_state"] == "qualified-draft":
    assert found["chosen"] in ("ridge", "random_forest") and found["headline"]["metric"] == "r2"
else:
    assert found["code"] == "G6_E_ML_BACKEND_NOT_INSTALLED"   # pip install "g6[automl]"
```

## The four open problems

- **Learning signals**: every result records the rows' hash, the held-out set's hash, the seed, the leaderboard and
  the held-out score with its baseline, and each scoring of a held-out set is counted under `G6_HOME`. Not yet:
  nothing here stores the scores or starts a search from earlier ones.
- **Evaluators the model cannot influence**: the held-out answers never reach the search; G6's own code computes the
  held-out score, the baseline and the intervals, checks the backend's outcome against its own leaderboard, and runs
  the leakage probes before the search; no model is consulted.
- **Transfer by failure mode**: the same checks and codes as adapt_sklearn, for every backend; a missing engine is
  refused the same way for each.
- **Enough held-out cases**: fewer than 10 independent held-out rows (identical rows count once), or a class
  missing from either side, is refused, and every score carries its interval, from resamples by group. Not yet:
  there is no fixed set of datasets with known best settings; the tests use built data with a real signal, pure
  noise and known leaks.
