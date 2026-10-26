# SPDX-License-Identifier: Apache-2.0
"""g6.ml.adapt_automl — search estimators and settings within a budget, and score the choice on rows it held out.

An extension in the ml family, behind the ``automl`` extra (FLAML's tuner and scikit-learn). Without it a search says
the backend is not installed and how to install it; the checks before a search, and the list of backends, need
nothing.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_automl", "search", {
        "rows": rows, "target": "churned", "features": ["tenure", "plan"], "task": "classification",
        "seed": 7, "time_budget_s": 20, "trials": 16}).unwrap()
    out["chosen"], out["chosen_config"], out["headline"]["held_out"]

What it does:

* ``search``: the family's checks first (``g6.ml._types.prepare``: the seed, the columns, the split's classes and
  size, identical rows across the split, target leakage, class imbalance); then a registered search backend (FLAML's
  cost-frugal search, seeded, by default) tries settings of each candidate estimator within its ranges, inside a time
  budget and a trial cap, each trial a cross-validation inside the training rows with the family's pipeline and
  folds; the best mean fold score is refitted on all the training rows; G6 scores its held-out predictions against
  a baseline on the same rows, with the lift, the gap and 95% intervals. The leaderboard is the search's own
  cross-validated scores, reported as optimistic, never as the result;
* ``backends``: the search backends registered and whether each is installed, the candidates and their ranges, and
  the engines the archive offered that G6 does not ship, with why.

The search runs out of G6's process (``g6.ml._fit.run``: G6's own fixed script in a child with a clean environment,
a deadline of 1.5 times the budget plus 60 s, a memory cap and the sandbox's guard on Python's sockets, which lets them
reach only this machine and is not an operating-system sandbox), given the training answers and never the held-out
ones. A backend named but not registered (MLJAR, auto-sklearn, H2O, AutoGluon) or not installed is refused
with ``G6_E_ML_BACKEND_NOT_INSTALLED``, never swapped for another. No model is kept. Results are ``qualified-draft``.
Its rules rest on the family's source register (``g6.ml._sources``) and wait for an independent review.

Not ported from the archive's adapt_automl, and why: ``verified`` decided before the held-out score existed; the
stateless block that fitted SMOTE and feature selection on every row before cross-validating; the training-set
score used when cross-validation failed, ranked with the honest ones; ``import_model`` and joblib export (a pickle
runs code when loaded); the H2O, AutoGluon and MLJAR backends; the guide and its model planner, partial fit,
ensembles, threshold tuning and prediction intervals, the CSV training, the stores, the servers and the scaffold.
From adapt_autosklearn: the MLJAR and auto-sklearn paths, the baseline returned as a success when no engine was
installed, and the planner that chose whether to refuse.
"""
from g6.ml.adapt_automl.backends import (
    FlamlBackend,
    SearchBackend,
    SearchBackends,
    SearchJob,
    SearchOutcome,
    search_backends,
)
from g6.ml.adapt_automl.block import AdaptAutoMLBlock, SearchInput
from g6.ml.adapt_automl.spaces import SPACES, Space, register_space, unregister_space

__g6_component__ = True
__g6_block__ = "AdaptAutoMLBlock"
__g6_family__ = "ml"
# A search starts a child of G6's own interpreter through g6.core.sandbox (bounded, nothing fetched) and keeps only a
# record of each held-out scoring (hashes and times, under G6_HOME), so no op is sensitive.
__g6_sensitive_ops__: tuple[str, ...] = ()
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unsystematic_tuning", "silent_fallback", "train_test_leakage")
__g6_introduces__: tuple[str, ...] = ("unevaluated_model_shipped",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"automl": ("flaml", "sklearn", "numpy")}
# Vetting (handoff section 4): statistical rules, built and tested; not listed as in the library until an independent
# pass over each rule against its source exists.
__g6_vetting__ = {
    "kinds": ("scientific_statistical",),
    "register": "g6/ml/_data/source_register.json",
    "independent_pass": "g6/ml/adapt_automl/data/rule_review.json",
    "signed_review": None,
}

__all__ = ["SPACES", "AdaptAutoMLBlock", "FlamlBackend", "SearchBackend", "SearchBackends", "SearchInput", "SearchJob",
           "SearchOutcome", "Space", "register_space", "search_backends", "unregister_space"]
