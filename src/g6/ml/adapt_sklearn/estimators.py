# SPDX-License-Identifier: Apache-2.0
"""The estimators adapt_sklearn fits: a small named list, held as data, each with the scikit-learn class for each
task it serves, the parameters a caller may set with their ranges, the ones G6 fixes, and whether its inputs are
scaled.

A caller names an estimator from :data:`CATALOGUE` and sets only the parameters it lists; a name that is not a plain
word (a dotted path, a call, a file) is refused as code (``G6_E_ML_UNTRUSTED_CODE``), never imported. The operator
may add an estimator from Python with :func:`register_estimator`, and only a class under ``sklearn.``: G6 never runs
a class, a pickle or a model file of a caller's.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from g6.ml._types import _SEED_PARAMS, INPUT_INVALID, SEED_MISSING, UNKNOWN_ESTIMATOR, UNTRUSTED_CODE, MLError

_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_CLASS = re.compile(r"sklearn(\.[a-z_][a-z0-9_]*)+:[A-Z][A-Za-z0-9]*")


@dataclass(frozen=True)
class Param:
    """One parameter a caller may set: an integer or number within bounds, a choice, or true/false; ``none_ok`` lets
    it be left unset (scikit-learn's ``None``)."""

    kind: Literal["int", "float", "choice", "bool"]
    low: float | None = None
    high: float | None = None
    choices: tuple[Any, ...] = ()
    none_ok: bool = False
    tasks: tuple[str, ...] = ("classification", "regression")

    def check(self, name: str, value: Any) -> Any:
        if value is None:
            if self.none_ok or (self.kind == "choice" and None in self.choices):
                return None
            raise MLError(INPUT_INVALID, f"parameter {name!r} cannot be left unset")
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise MLError(INPUT_INVALID, f"parameter {name!r} is true or false")
            return value
        if self.kind == "choice":
            # 1 and 1.0 (or true) are different parameters to scikit-learn: the type must match too
            if not any(c == value and type(c) is type(value) for c in self.choices if c is not None):
                shown = ", ".join(repr(c) for c in self.choices)
                raise MLError(INPUT_INVALID, f"parameter {name!r} is one of {shown}, not {value!r}")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise MLError(INPUT_INVALID, f"parameter {name!r} is a number, not {value!r}")
        if self.kind == "int" and not float(value).is_integer():
            raise MLError(INPUT_INVALID, f"parameter {name!r} is a whole number, not {value!r}")
        if (self.low is not None and value < self.low) or (self.high is not None and value > self.high):
            raise MLError(INPUT_INVALID, f"parameter {name!r} is between {self.low:g} and {self.high:g}, not {value!r}")
        return int(value) if self.kind == "int" else float(value)

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.kind in ("int", "float"):
            out.update(low=self.low, high=self.high)
        if self.choices:
            out["choices"] = list(self.choices)
        if self.none_ok:
            out["may_be_unset"] = True
        if self.tasks != ("classification", "regression"):
            out["tasks"] = list(self.tasks)
        return out


@dataclass(frozen=True)
class Estimator:
    """One estimator: its scikit-learn class per task (``"sklearn.module:Class"``), its parameters, the values G6
    fixes, whether its inputs are scaled, and a line on when it suits."""

    name: str
    classes: dict[str, str]
    summary: str
    params: dict[str, Param] = field(default_factory=dict)
    fixed: dict[str, Any] = field(default_factory=dict)
    scale: bool = False

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "tasks": sorted(self.classes), "summary": self.summary,
                "classes": dict(self.classes), "params": {k: p.describe() for k, p in self.params.items()},
                "fixed": dict(self.fixed), "inputs_scaled": self.scale}


_DEPTH = Param("int", 1, 100, none_ok=True)
_LEAF = Param("int", 1, 1000)
_WEIGHT = Param("choice", choices=(None, "balanced"), tasks=("classification",))
_TREES = Param("int", 1, 500)

CATALOGUE: dict[str, Estimator] = {}


def register_estimator(estimator: Estimator, *, replace: bool = False) -> None:
    """Add an estimator to the list (from Python, by the operator; there is no op for it). Its classes must be
    scikit-learn's own (``sklearn.<module>:<Class>``) and its name a plain word."""
    if not _NAME.fullmatch(estimator.name):
        raise ValueError(f"{estimator.name!r} is not an estimator name (lower-case letters, digits and '_')")
    if not estimator.classes or set(estimator.classes) - {"classification", "regression"}:
        raise ValueError("an estimator serves classification, regression or both")
    bad = [c for c in estimator.classes.values() if not _CLASS.fullmatch(c)]
    if bad:
        raise ValueError(f"only scikit-learn's own classes are fitted (sklearn.<module>:<Class>), not {bad[0]!r}")
    if "random_state" in estimator.params or "random_state" in estimator.fixed:
        raise ValueError("random_state is set by the seed, never by an estimator's parameters")
    if estimator.name in CATALOGUE and not replace:
        raise ValueError(f"an estimator named {estimator.name!r} is already listed")
    CATALOGUE[estimator.name] = estimator


def unregister_estimator(name: str) -> None:
    CATALOGUE.pop(name, None)


for _estimator in (
    Estimator("logistic_regression", {"classification": "sklearn.linear_model:LogisticRegression"},
              "a linear model of the log-odds; a strong first baseline, needs scaled inputs",
              {"C": Param("float", 1e-4, 1e4), "class_weight": _WEIGHT, "max_iter": Param("int", 50, 5000)},
              {"max_iter": 1000}, scale=True),
    Estimator("linear_regression", {"regression": "sklearn.linear_model:LinearRegression"},
              "ordinary least squares; the plainest regression",
              {"fit_intercept": Param("bool")}, scale=True),
    Estimator("ridge", {"classification": "sklearn.linear_model:RidgeClassifier",
                        "regression": "sklearn.linear_model:Ridge"},
              "a linear model with an L2 penalty; steadier than plain least squares with correlated features",
              {"alpha": Param("float", 0.0, 1e6), "class_weight": _WEIGHT}, scale=True),
    Estimator("random_forest", {"classification": "sklearn.ensemble:RandomForestClassifier",
                                "regression": "sklearn.ensemble:RandomForestRegressor"},
              "many decision trees on bootstrap samples, averaged; little tuning, handles interactions",
              {"n_estimators": _TREES, "max_depth": _DEPTH, "min_samples_leaf": _LEAF,
               "max_features": Param("choice", choices=("sqrt", "log2", None, 1.0)),
               "class_weight": Param("choice", choices=(None, "balanced", "balanced_subsample"),
                                     tasks=("classification",))},
              {"n_estimators": 200, "n_jobs": 1}),
    Estimator("extra_trees", {"classification": "sklearn.ensemble:ExtraTreesClassifier",
                              "regression": "sklearn.ensemble:ExtraTreesRegressor"},
              "like a random forest, with split points drawn at random; faster, often as good",
              {"n_estimators": _TREES, "max_depth": _DEPTH, "min_samples_leaf": _LEAF, "class_weight": _WEIGHT},
              {"n_estimators": 200, "n_jobs": 1}),
    Estimator("gradient_boosting", {"classification": "sklearn.ensemble:HistGradientBoostingClassifier",
                                    "regression": "sklearn.ensemble:HistGradientBoostingRegressor"},
              "trees added one at a time to correct the last ones' errors (histogram-based); strong on tables",
              {"max_iter": Param("int", 10, 1000), "learning_rate": Param("float", 0.001, 1.0),
               "max_depth": _DEPTH, "min_samples_leaf": _LEAF, "l2_regularization": Param("float", 0.0, 1e4),
               "class_weight": _WEIGHT}),
    Estimator("decision_tree", {"classification": "sklearn.tree:DecisionTreeClassifier",
                                "regression": "sklearn.tree:DecisionTreeRegressor"},
              "one tree of if-then splits; readable, and overfits without a depth limit",
              {"max_depth": _DEPTH, "min_samples_leaf": _LEAF, "class_weight": _WEIGHT}),
    Estimator("k_neighbors", {"classification": "sklearn.neighbors:KNeighborsClassifier",
                              "regression": "sklearn.neighbors:KNeighborsRegressor"},
              "the k most similar training rows vote; needs scaled inputs and few irrelevant features",
              {"n_neighbors": Param("int", 1, 200), "weights": Param("choice", choices=("uniform", "distance"))},
              scale=True),
    Estimator("naive_bayes", {"classification": "sklearn.naive_bayes:GaussianNB"},
              "each feature a normal distribution per class, independent of the others; fast, a sanity check",
              {"var_smoothing": Param("float", 1e-12, 1.0)}),
):
    register_estimator(_estimator)

#: What ``rank`` tries when no candidates are named, per task.
DEFAULT_CANDIDATES = {
    "classification": ("logistic_regression", "random_forest", "gradient_boosting", "k_neighbors", "naive_bayes"),
    "regression": ("ridge", "random_forest", "gradient_boosting", "k_neighbors"),
}


def resolve(name: str, task: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """The child's spec for ``name`` on ``task`` with ``params`` checked against its list, or :class:`MLError`; a
    seed among the params (``random_state``, ``seed``, ``random_seed``) is ``G6_E_ML_SEED_MISSING``."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise MLError(UNTRUSTED_CODE, f"{str(name)[:80]!r} is not an estimator name: G6 fits only estimators from its "
                                      "own list and never imports a class, a module, a pickle or a model file you "
                                      "name")
    found = CATALOGUE.get(name)
    if found is None:
        raise MLError(UNKNOWN_ESTIMATOR, f"no estimator named {name!r}; the list: {', '.join(sorted(CATALOGUE))}")
    if task not in found.classes:
        raise MLError(UNKNOWN_ESTIMATOR, f"{name} does not do {task}; it does {', '.join(sorted(found.classes))}")
    seeded = [key for key in (params or {}) if key in _SEED_PARAMS]
    if seeded:
        raise MLError(SEED_MISSING, f"{', '.join(seeded)} is set among the parameters; the block sets the seed itself, "
                                    "from seed, for every step that takes one, so the evidence names the one seed used")
    checked: dict[str, Any] = {}
    for key, value in (params or {}).items():
        param = found.params.get(key)
        if param is None or task not in param.tasks:
            allowed = ", ".join(k for k, p in found.params.items() if task in p.tasks) or "none"
            raise MLError(UNKNOWN_ESTIMATOR, f"{name} takes no parameter {key!r} for {task} (it takes: {allowed})")
        checked[key] = param.check(key, value)
    module, cls = found.classes[task].split(":")
    return {"name": name, "module": module, "class": cls, "params": {**found.fixed, **checked}, "scale": found.scale,
            "set_by_caller": sorted(checked)}


__all__ = ["CATALOGUE", "DEFAULT_CANDIDATES", "Estimator", "Param", "register_estimator", "resolve",
           "unregister_estimator"]
