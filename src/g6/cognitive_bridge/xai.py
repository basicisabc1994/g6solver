# SPDX-License-Identifier: Apache-2.0
"""Safe-to-inspect explicit decision trees and bounded black-box surrogate distillation.

This does not import a pickle or reconstruct arbitrary Python source. An exact
structural transcription of a supported scikit-learn tree is different from
an approximate surrogate of an arbitrary black-box model.
"""
from __future__ import annotations

import math
import struct
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .snapshot import record


def _native(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def _float32(value: Any) -> float:
    try:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("explicit tree only handles finite numerical inputs")
        converted = struct.unpack("f", struct.pack("f", number))[0]
    except (OverflowError, struct.error, TypeError) as exc:
        raise ValueError("invalid or out-of-range numeric tree input") from exc
    if not math.isfinite(converted):
        raise ValueError("tree input overflows float32")
    return converted


@record
class TreeNode:
    value: Any = None
    feature: int | None = None
    threshold: float | None = None
    left: "TreeNode | None" = None
    right: "TreeNode | None" = None


@record
class ExplicitTree:
    features: tuple[str, ...]
    root: TreeNode
    task: str  # classification | regression
    provenance: str  # tree_transcription | blackbox_surrogate

    def _row(self, sample: Mapping[str, Any] | Sequence[Any]) -> list[float]:
        if isinstance(sample, Mapping):
            values = [sample[name] for name in self.features]
        else:
            values = list(sample)
        if len(values) != len(self.features):
            raise ValueError("sample feature count mismatch")
        return [_float32(x) for x in values]

    def explain_one(self, sample: Mapping[str, Any] | Sequence[Any]) -> dict[str, Any]:
        values = self._row(sample)
        node = self.root
        conditions = []
        while node.feature is not None:
            assert node.threshold is not None and node.left is not None and node.right is not None
            less = values[node.feature] <= node.threshold
            conditions.append({"feature": self.features[node.feature], "operator": "<=" if less else ">",
                               "threshold": node.threshold, "observed": values[node.feature]})
            node = node.left if less else node.right
        return {"prediction": node.value, "conditions": conditions, "provenance": self.provenance,
                "caveat": "explains this explicit model's decision, not causality"}

    def predict(self, samples: Sequence[Mapping[str, Any] | Sequence[Any]]) -> list[Any]:
        return [self.explain_one(sample)["prediction"] for sample in samples]

    def to_dict(self) -> dict:
        return asdict(self)  # inspectable data, not arbitrary executable code


def _distinct_names(names: Sequence[Any]) -> tuple[str, ...]:
    """The feature names as the tree uses them (strings), compared as they will be used: two names that read the
    same once made strings (``1`` and ``"1"``) are one name, so they are refused rather than merged."""
    used = tuple(str(_native(name)) for name in names)
    if len(set(used)) != len(used):
        raise ValueError("feature names must be unique as the strings the tree reads them by")
    return used


def _feature_names(estimator: Any, names: Sequence[str] | None) -> tuple[str, ...]:
    if not hasattr(estimator, "tree_") or not hasattr(estimator, "n_features_in_"):
        raise TypeError("estimator must be an already fitted, supported sklearn decision tree")
    if names is None:
        names = list(getattr(estimator, "feature_names_in_", [])) or [
            f"x{i}" for i in range(estimator.n_features_in_)]
    used = _distinct_names(names)
    if len(used) != estimator.n_features_in_:
        raise ValueError("feature names must match fitted tree and be unique")
    if getattr(estimator, "n_outputs_", 1) != 1:
        raise ValueError("multi-output trees not supported in explicit export")
    return used


def extract_tree(estimator: Any, feature_names: Sequence[str] | None = None,
                 *, provenance: str = "tree_transcription") -> ExplicitTree:
    """Transcribe the splitting logic of a fitted sklearn DecisionTree, without refitting.

    Supported: single-output classification/regression over finite numeric
    features, no preprocessing pipeline, no missing values. Comparisons first
    cast input values to float32, matching sklearn's tree input convention.
    """
    names = _feature_names(estimator, feature_names)
    tree = estimator.tree_
    is_classifier = hasattr(estimator, "classes_")
    task = "classification" if is_classifier else "regression"
    if is_classifier and not hasattr(estimator.classes_, "__len__"):
        raise ValueError("unsupported class labels")

    def visit(node_id: int) -> TreeNode:
        left = int(tree.children_left[node_id])
        right = int(tree.children_right[node_id])
        if left == -1 and right == -1:
            if is_classifier:
                proportions = tree.value[node_id][0]
                class_index = int(max(range(len(proportions)), key=lambda i: proportions[i]))
                prediction = _native(estimator.classes_[class_index])
            else:
                prediction = float(tree.value[node_id][0][0])
            return TreeNode(value=prediction)
        feature = int(tree.feature[node_id])
        if not 0 <= feature < len(names) or left < 0 or right < 0:
            raise ValueError("invalid fitted tree structure")
        return TreeNode(feature=feature, threshold=float(tree.threshold[node_id]),
                        left=visit(left), right=visit(right))

    return ExplicitTree(names, visit(0), task, provenance)


def distill_blackbox(blackbox: Any, X_train: Sequence[Sequence[float]],
                     X_holdout: Sequence[Sequence[float]], feature_names: Sequence[str],
                     *, task: str = "classification", max_depth: int = 3,
                     min_samples_leaf: int = 2, seed: int = 0) -> tuple[ExplicitTree, dict]:
    """Fit a small CART surrogate to black-box predictions and measure held-out fidelity.

    Supplied holdout rows must be kept untouched during training and selection.
    The returned rule tree describes approximated *model behaviour*, not
    ground truth or causal influence.
    """
    if task not in {"classification", "regression"}:
        raise ValueError("task must be classification or regression")
    if not 1 <= max_depth <= 12 or not 1 <= min_samples_leaf <= 1000:
        raise ValueError("surrogate complexity outside supported bounds")
    names = _distinct_names(feature_names)
    if not 1 <= len(names) <= 100:
        raise ValueError("invalid feature names")
    train = [tuple(_float32(x) for x in row) for row in X_train]
    holdout = [tuple(_float32(x) for x in row) for row in X_holdout]
    if len(train) < 10 or len(holdout) < 5:
        raise ValueError("need at least 10 training and 5 independent held-out rows")
    if any(len(row) != len(names) for row in train + holdout):
        raise ValueError("wrong number of features")
    if set(train) & set(holdout):
        raise ValueError("identical feature rows cross the training/holdout boundary")
    try:
        from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor  # optional g6[ml] dependency
    except ImportError as exc:
        raise ImportError("scikit-learn is not installed: pip install 'g6[ml] @ "
                          "git+https://github.com/basicisabc1994/g6solver'") from exc

    observed_train = list(blackbox.predict(train))
    observed_holdout = [_native(x) for x in blackbox.predict(holdout)]
    if len(observed_train) != len(train) or len(observed_holdout) != len(holdout):
        raise ValueError("black-box predictor returned mismatched rows")
    cls = DecisionTreeClassifier if task == "classification" else DecisionTreeRegressor
    tree = cls(max_depth=max_depth, min_samples_leaf=min_samples_leaf, random_state=seed)
    tree.fit(train, observed_train)
    explicit = extract_tree(tree, names, provenance="blackbox_surrogate")
    predicted = explicit.predict(holdout)
    report: dict[str, Any] = {
        "method": "held_out_surrogate_fidelity", "completion_state": "qualified-draft",
        "train_n": len(train), "holdout_n": len(holdout), "max_depth": max_depth,
        "model_origin": "blackbox_predictions", "reference": "blackbox_predictor",
        "caveat": "fidelity measures agreement with model output, not accuracy against real outcomes",
    }
    if task == "classification":
        matches = sum(a == b for a, b in zip(observed_holdout, predicted))
        report.update({"metric": "agreement", "fidelity": matches / len(holdout),
                       "disagreements": len(holdout) - matches})
    else:
        errors = [float(a) - float(b) for a, b in zip(observed_holdout, predicted)]
        if not all(math.isfinite(err) for err in errors):
            raise ValueError("non-finite surrogate predictions")
        report.update({"metric": "rmse", "fidelity": math.sqrt(sum(x*x for x in errors)/len(errors)),
                       "mae": sum(abs(x) for x in errors)/len(errors),
                       "lower_is_better": True})
    return explicit, report
