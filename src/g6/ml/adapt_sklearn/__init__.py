# SPDX-License-Identifier: Apache-2.0
"""g6.ml.adapt_sklearn — fit and score a scikit-learn model on rows it holds out itself, after checking for leakage.

An extension in the ml family, behind the ``ml`` extra (scikit-learn). Without it every fitting op says scikit-learn
is not installed and how to install it; the checks before a fit, and the list of estimators, need nothing.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_sklearn", "fit_eval", {
        "rows": rows, "target": "churned", "features": ["tenure", "plan"], "task": "classification",
        "seed": 7, "estimator": "random_forest"}).unwrap()
    out["headline"]["held_out"], out["headline"]["baseline"]   # measured on rows the fit never saw the answers for

What it does:

* ``fit_eval`` fits one estimator from G6's own list (``estimators``) on the extension's own training split and
  scores it on its held-out rows: the headline metric (accuracy; balanced accuracy when the smallest class holds under
  10% of the rows; R squared for regression) against a baseline that ignores the features, on the same rows, with the
  lift, the train-held-out gap and 95% intervals from seeded resamples; predictions for ``predict_rows`` too;
* ``rank`` (the archive's adapt_learning, merged in) cross-validates candidates inside the training rows only,
  stratified and grouped by identical rows, picks the best mean fold score, and scores that one once on the held-out
  rows, which choose nothing;
* before any fit, the family's checks (``g6.ml._types.prepare``): a seed is required and set once; columns exist and
  are of one kind; every class has rows on both sides of the split and at least 10 distinct rows are held out;
  identical rows never sit on both sides; a feature that is the target, a copy of it or one that alone predicts it is
  refused as leakage.

The fit runs out of G6's process (``g6.ml._fit``: G6's own fixed script in a child with a clean environment, a
deadline, a memory cap and the sandbox's guard on Python's sockets, which lets them reach only this machine and is not
an operating-system sandbox), given the training answers and never the held-out ones; G6 scores what it
predicted. No model is kept: a model exists only for its call, so no model file is written or loaded, and a name that
is not one of G6's estimators (a class, a module, a pickle) is refused, never imported. Results are
``qualified-draft``: a measurement on these rows, not a promise about new data. Its rules rest on the family's source
register (``g6.ml._sources``) and wait for an independent review (``__g6_vetting__``).

Not ported from the archive's adapt_sklearn, and why: ``model_load`` and joblib saving (a pickle runs code when it is
loaded; nothing is kept instead); ``verified`` on every successful op, and the default fit scored on its own training
rows; the plain-English sentence made from a training score; the stateless duplicate block, the experiment and
heuristic stores, text vectorising, ``partial_fit``, the tree-to-formula export, the model planner, the FastMCP
servers and the scaffold. From adapt_learning: the fixed "estimated scores" and the outcome store (a candidate is
ranked only by a score measured on the caller's rows).
"""
from g6.ml.adapt_sklearn.block import AdaptSklearnBlock, SklearnInput
from g6.ml.adapt_sklearn.estimators import CATALOGUE, Estimator, Param, register_estimator, unregister_estimator

__g6_component__ = True
__g6_block__ = "AdaptSklearnBlock"
__g6_family__ = "ml"
# A fit starts a child of G6's own interpreter through g6.core.sandbox (bounded, nothing fetched) and keeps only a
# record of each held-out scoring (hashes and times, under G6_HOME), so no op is sensitive.
__g6_sensitive_ops__: tuple[str, ...] = ()
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("train_test_leakage", "ill_suited_model")
__g6_introduces__: tuple[str, ...] = ("unevaluated_model_shipped",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"ml": ("sklearn", "numpy")}
# Vetting (handoff section 4): statistical rules, built and tested; not listed as in the library until an independent
# pass over each rule against its source exists.
__g6_vetting__ = {
    "kinds": ("scientific_statistical",),
    "register": "g6/ml/_data/source_register.json",
    "independent_pass": "g6/ml/adapt_sklearn/data/rule_review.json",
    "signed_review": None,
}

__all__ = ["CATALOGUE", "AdaptSklearnBlock", "Estimator", "Param", "SklearnInput", "register_estimator",
           "unregister_estimator"]
