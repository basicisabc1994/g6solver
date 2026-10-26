# SPDX-License-Identifier: Apache-2.0
"""What adapt_automl searches: a small named list of scikit-learn estimators, held as data, each with the class for
each task it serves, the ranges a search may try for its settings, the cheap corner a search starts from, and the
values G6 fixes.

A caller names candidates from :data:`SPACES`; a name that is not a plain word is refused as code
(``G6_E_ML_UNTRUSTED_CODE``), never imported. The operator may add one from Python with :func:`register_space`, and
only for a class under ``sklearn.``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from g6.ml._types import UNKNOWN_ESTIMATOR, UNTRUSTED_CODE, MLError

_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_CLASS = re.compile(r"sklearn(\.[a-z_][a-z0-9_]*)+:[A-Z][A-Za-z0-9]*")
#: The kinds of range a search may try: whole numbers on a log or a plain scale, numbers on a log or a plain scale.
KINDS = ("lograndint", "randint", "loguniform", "uniform")


@dataclass(frozen=True)
class Space:
    """One estimator a search may try: its scikit-learn class per task (``"sklearn.module:Class"``), the range of
    each setting (``(kind, low, high)``), the cheap starting corner, the values G6 fixes and whether inputs are
    scaled."""

    name: str
    classes: dict[str, str]
    ranges: dict[str, tuple[str, float, float]]
    cheap: dict[str, Any] = field(default_factory=dict)
    fixed: dict[str, Any] = field(default_factory=dict)
    scale: bool = False

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "tasks": sorted(self.classes), "classes": dict(self.classes),
                "ranges": {k: {"kind": v[0], "low": v[1], "high": v[2]} for k, v in self.ranges.items()},
                "starts_from": dict(self.cheap), "fixed": dict(self.fixed), "inputs_scaled": self.scale}


SPACES: dict[str, Space] = {}


def register_space(space: Space, *, replace: bool = False) -> None:
    """Add an estimator to what a search may try (from Python, by the operator; there is no op for it)."""
    if not _NAME.fullmatch(space.name):
        raise ValueError(f"{space.name!r} is not an estimator name (lower-case letters, digits and '_')")
    if not space.classes or set(space.classes) - {"classification", "regression"}:
        raise ValueError("an estimator serves classification, regression or both")
    bad = [c for c in space.classes.values() if not _CLASS.fullmatch(c)]
    if bad:
        raise ValueError(f"only scikit-learn's own classes are searched (sklearn.<module>:<Class>), not {bad[0]!r}")
    for key, (kind, low, high) in space.ranges.items():
        if kind not in KINDS or not low < high or (kind.startswith("log") and low <= 0):
            raise ValueError(f"the range of {key!r} is not a {', '.join(KINDS)} range with low < high")
    if "random_state" in {*space.ranges, *space.fixed, *space.cheap}:
        raise ValueError("random_state is set by the seed, never searched or fixed by a space")
    if space.name in SPACES and not replace:
        raise ValueError(f"a space named {space.name!r} is already listed")
    SPACES[space.name] = space


def unregister_space(name: str) -> None:
    SPACES.pop(name, None)


for _space in (
    Space("logistic_regression", {"classification": "sklearn.linear_model:LogisticRegression"},
          {"C": ("loguniform", 1e-3, 1e3)}, {"C": 1.0}, {"max_iter": 1000}, scale=True),
    Space("ridge", {"classification": "sklearn.linear_model:RidgeClassifier", "regression": "sklearn.linear_model:Ridge"},
          {"alpha": ("loguniform", 1e-3, 1e3)}, {"alpha": 1.0}, scale=True),
    Space("random_forest", {"classification": "sklearn.ensemble:RandomForestClassifier",
                            "regression": "sklearn.ensemble:RandomForestRegressor"},
          {"n_estimators": ("lograndint", 10, 300), "max_features": ("uniform", 0.1, 1.0),
           "min_samples_leaf": ("lograndint", 1, 30)}, {"n_estimators": 10}, {"n_jobs": 1}),
    Space("extra_trees", {"classification": "sklearn.ensemble:ExtraTreesClassifier",
                          "regression": "sklearn.ensemble:ExtraTreesRegressor"},
          {"n_estimators": ("lograndint", 10, 300), "max_features": ("uniform", 0.1, 1.0),
           "min_samples_leaf": ("lograndint", 1, 30)}, {"n_estimators": 10}, {"n_jobs": 1}),
    Space("gradient_boosting", {"classification": "sklearn.ensemble:HistGradientBoostingClassifier",
                                "regression": "sklearn.ensemble:HistGradientBoostingRegressor"},
          {"max_iter": ("lograndint", 10, 500), "learning_rate": ("loguniform", 0.01, 1.0),
           "max_leaf_nodes": ("lograndint", 4, 128), "min_samples_leaf": ("lograndint", 2, 100),
           "l2_regularization": ("loguniform", 1e-6, 10.0)}, {"max_iter": 10, "max_leaf_nodes": 4}),
    Space("k_neighbors", {"classification": "sklearn.neighbors:KNeighborsClassifier",
                          "regression": "sklearn.neighbors:KNeighborsRegressor"},
          {"n_neighbors": ("lograndint", 1, 50)}, {"n_neighbors": 5}, scale=True),
):
    register_space(_space)

#: What ``search`` tries when no candidates are named, per task.
DEFAULT_CANDIDATES = {
    "classification": ("logistic_regression", "random_forest", "extra_trees", "gradient_boosting"),
    "regression": ("ridge", "random_forest", "extra_trees", "gradient_boosting"),
}


def resolve(names: list[str], task: str) -> list[dict[str, Any]]:
    """The child's spec of each candidate, or :class:`MLError`."""
    out = []
    for name in names:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise MLError(UNTRUSTED_CODE, f"{str(name)[:80]!r} is not an estimator name: G6 searches only estimators "
                                          "from its own list and never imports a class, a module, a pickle or a "
                                          "model file you name")
        found = SPACES.get(name)
        if found is None:
            raise MLError(UNKNOWN_ESTIMATOR, f"no estimator named {name!r} to search; the list: "
                                             f"{', '.join(sorted(SPACES))}")
        if task not in found.classes:
            raise MLError(UNKNOWN_ESTIMATOR, f"{name} does not do {task}")
        module, cls = found.classes[task].split(":")
        out.append({"name": name, "module": module, "class": cls, "ranges": {k: list(v) for k, v in
                                                                            found.ranges.items()},
                    "cheap": dict(found.cheap), "fixed": dict(found.fixed), "scale": found.scale})
    return out


__all__ = ["DEFAULT_CANDIDATES", "KINDS", "SPACES", "Space", "register_space", "resolve", "unregister_space"]
