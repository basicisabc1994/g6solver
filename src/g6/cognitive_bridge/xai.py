# SPDX-License-Identifier: Apache-2.0
"""Safe-to-inspect explicit decision trees and bounded black-box surrogate distillation.

This does not import a pickle or reconstruct arbitrary Python source. An exact
structural transcription of a supported scikit-learn tree is different from
an approximate surrogate of an arbitrary black-box model.

Every value the bridge takes from an estimator, a learner or a black box (code
it does not own) is read where it is taken, before anything uses it, by the
reader for its kind: an estimator's value by :func:`_native` (the boundary,
``snapshot.plain``), then a number by the number rule (:func:`_estimated`,
``snapshot.number``), a name as text (``snapshot.text``) and a class label as
a JSON leaf (:func:`_label`); a black box's predictions, training and held-out
alike, the same way (:func:`_predictions`).
``tests/cognitive_bridge/test_read_inventory.py`` lists every such read from
the code.
"""
from __future__ import annotations

import math
import struct
from typing import Any, Mapping, Sequence

from .codes import FrozenMap
from .codes import _has_member, _is_type
from .codes import DEPTH, NotPlainData, _read_as, settled, unclaimed
from .snapshot import publish, record_data, _too_deep, canonical_json, fresh, number, plain, record, returned, snapshot, text


def _native(value: Any, *, where: str) -> Any:
    """A value a fitted estimator holds (an array, or one item of one) as the bridge reads it, the one reader of an
    estimator's values: Python's own values (numpy's ``tolist``, for an array or a numpy scalar), taken by the boundary
    (``snapshot.plain``: a NaN, a set, bytes, a subclass of a JSON type or any other object is refused, never
    converted). Each value is then read by the rule for its kind."""
    return plain(value.tolist() if _has_member(value, "tolist") else value, where=where)


def _estimated(value: Any, *, where: str, index: bool = False) -> int | float:
    """A number a fitted estimator holds, as the bridge takes it: read by :func:`_native`, then by the number rule
    (``snapshot.number``: a flag, text or null is refused, never converted); an index or a count (a child, a feature,
    the number of features or outputs) an integer."""
    taken = number(_native(value, where=where), where=where)
    if index and type(taken) is not int:
        raise NotPlainData(where, f"a {type(taken).__name__} is not an integer")
    return taken


def _label(value: Any, *, where: str) -> Any:
    """A class label (a fitted classifier's class, or a black box's prediction of one) as the bridge takes it: a JSON
    leaf (text, a number, a flag or null, by the boundary's own leaf test, ``codes._read_as``), kept as its kind; an
    object, an array or anything else is refused."""
    taken = plain(value, where=where)
    if _read_as(taken) not in ("text", "leaf"):
        raise NotPlainData(where, f"a {type(taken).__name__} is not a class label: text, a number, a flag or null")
    return taken


def _numbers(value: Any, *, where: str) -> list[int | float]:
    """A list a fitted estimator holds, each item read by the number rule (``snapshot.number``)."""
    if type(value) is not list:
        raise NotPlainData(where, f"a {type(value).__name__} is not a list of numbers")
    return [number(item, where=f"{where}[{index}]") for index, item in enumerate(value)]


def _float32(value: Any) -> float:
    """A tree input, a number (``snapshot.number``: a flag, text or null is refused, never converted), as sklearn
    compares it: a float32."""
    try:
        converted = struct.unpack("f", struct.pack("f", number(value, where="a tree input")))[0]
    except (OverflowError, struct.error) as exc:
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
        if _is_type(type(sample), (dict, FrozenMap,)):
            values = [sample[name] for name in self.features]
        else:
            values = list(sample)
        if len(values) != len(self.features):
            raise ValueError("sample feature count mismatch")
        return [_float32(x) for x in values]

    def explain_one(self, sample: Mapping[str, Any] | Sequence[Any]) -> dict[str, Any]:
        tree = snapshot(self, where="the explicit tree")
        sample = snapshot(sample, where="sample")
        values = tree._row(sample)
        node = tree.root
        conditions = []
        while node.feature is not None:
            assert node.threshold is not None and node.left is not None and node.right is not None
            less = values[node.feature] <= node.threshold
            conditions.append({"feature": tree.features[node.feature], "operator": "<=" if less else ">",
                               "threshold": node.threshold, "observed": values[node.feature]})
            node = node.left if less else node.right
        return publish(unclaimed({"prediction": node.value, "conditions": conditions, "provenance": tree.provenance,
                          "caveat": "explains this explicit model's decision, not causality"}, where="an explanation"), where="ExplicitTree.explain_one")

    def predict(self, samples: Sequence[Mapping[str, Any] | Sequence[Any]]) -> list[Any]:
        samples = snapshot(samples, where="samples")
        return publish(unclaimed([self.explain_one(sample)["prediction"] for sample in samples], where="the predictions"), where="ExplicitTree.predict")

    def to_dict(self) -> dict:
        return publish(unclaimed(record_data(self), where="a tree"), where="ExplicitTree.to_dict")  # inspectable data, not arbitrary executable code


def _distinct_names(names: Sequence[Any], *, where: str = "feature names") -> tuple[str, ...]:
    """The feature names as the tree uses them: a list of text (``snapshot.text``), never a number or a flag made
    text, so ``1`` and ``True`` are refused, not read as ``"1"`` and ``"True"``; and unique. ``names`` is what the
    boundary already took (the caller's snapshot, or an estimator's array read by :func:`_native`)."""
    if not _is_type(type(names), (list, tuple)):
        raise NotPlainData(where, f"a {type(names).__name__} is not a list of names")
    used = tuple(text(name, where=f"{where}: feature name {index}") for index, name in enumerate(names))
    if len(set(used)) != len(used):
        raise NotPlainData(where, "feature names must be unique as the strings the tree reads them by")
    return used


def _feature_names(estimator: Any, names: Sequence[str] | None) -> tuple[str, ...]:
    if not hasattr(estimator, "tree_") or not hasattr(estimator, "n_features_in_"):
        raise TypeError("estimator must be an already fitted, supported sklearn decision tree")
    features = _estimated(estimator.n_features_in_, where="the estimator's n_features_in_", index=True)
    if names is None:
        names = _native(getattr(estimator, "feature_names_in_", [f"x{i}" for i in range(features)]),
                        where="the estimator's feature_names_in_")
        names = _distinct_names(names, where="the estimator's feature_names_in_")
    used = _distinct_names(names)
    if len(used) != features:
        raise NotPlainData("the estimator's feature_names_in_", "feature names must match fitted tree and be unique")
    if _estimated(getattr(estimator, "n_outputs_", 1), where="the estimator's n_outputs_", index=True) != 1:
        raise ValueError("multi-output trees not supported in explicit export")
    return used


def extract_tree(estimator: Any, feature_names: Sequence[str] | None = None,
                 *, provenance: str = "tree_transcription") -> ExplicitTree:
    """Transcribe the splitting logic of a fitted sklearn DecisionTree, without refitting.

    Supported: single-output classification/regression over finite numeric
    features, no preprocessing pipeline, no missing values. Comparisons first
    cast input values to float32, matching sklearn's tree input convention.
    A tree whose nodes would nest deeper than ``codes.DEPTH`` in the record
    made of it (or that loops) is refused, as the boundary refuses any value.
    """
    feature_names = snapshot(feature_names, where="feature_names")
    provenance = text(provenance, where="provenance")
    names = _feature_names(estimator, feature_names)
    tree = estimator.tree_
    is_classifier = hasattr(estimator, "classes_")
    task = "classification" if is_classifier else "regression"
    classes: list[Any] = []
    if is_classifier:
        listed = _native(estimator.classes_, where="the estimator's classes_")
        if type(listed) is not list:
            raise ValueError("unsupported class labels")
        classes = [_label(label, where=f"the estimator's class {index}") for index, label in enumerate(listed)]

    arrays: dict[str, list[Any]] = {}

    def item(name: str, node_id: int) -> Any:
        """Node ``node_id``'s item of the fitted tree's array ``name``: the whole array read once, when first used,
        through :func:`_native` (so a container subclass, a NaN, a set or bytes anywhere in it is refused there), and
        the item taken from what the boundary made of it."""
        if name not in arrays:
            listed = _native(getattr(tree, name), where=f"the estimator's tree_.{name}")
            if type(listed) is not list:
                raise NotPlainData(f"the estimator's tree_.{name}", f"a {type(listed).__name__} is not an array")
            arrays[name] = listed
        if not 0 <= node_id < len(arrays[name]):
            raise ValueError("invalid fitted tree structure")
        return arrays[name][node_id]

    def visit(node_id: int, depth: int) -> TreeNode:
        # ``depth`` is where this node's fields stand in the ExplicitTree made of it: its children stand there too
        if depth > DEPTH:
            raise _too_deep(f"the estimator's tree at node {node_id}")
        at = f"the estimator's tree at node {node_id}"
        left = _estimated(item("children_left", node_id), where=f"{at}: its left child", index=True)
        right = _estimated(item("children_right", node_id), where=f"{at}: its right child", index=True)
        if left == -1 and right == -1:
            # a leaf's value: one row of numbers for the tree's one output, every score read by the number rule
            # whether the tree classifies or regresses (review 3 of PR #8's second follow-up, finding 1)
            row = item("value", node_id)
            if type(row) is not list or len(row) != 1:
                raise ValueError(f"{at}: its value is not one row for one output")
            scores = _numbers(row[0], where=f"{at}: its value[0]")
            if is_classifier:
                if len(scores) != len(classes):
                    raise ValueError(f"{at}: its value has {len(scores)} scores for {len(classes)} classes")
                prediction = classes[max(range(len(scores)), key=scores.__getitem__)]
            else:
                if len(scores) != 1:
                    raise ValueError(f"{at}: a regression leaf holds one value")
                prediction = float(scores[0])
            return TreeNode(value=prediction)
        feature = _estimated(item("feature", node_id), where=f"{at}: its feature", index=True)
        if not 0 <= feature < len(names) or left < 0 or right < 0:
            raise ValueError("invalid fitted tree structure")
        threshold = float(_estimated(item("threshold", node_id), where=f"{at}: its threshold"))
        return TreeNode(feature=feature, threshold=threshold, left=visit(left, depth + 1), right=visit(right, depth + 1))

    # the tree stands at 0, its root node at 1 (its ``root`` field), and the root's own fields at 2
    return publish(ExplicitTree(names, visit(0, 2), task, provenance), where="extract_tree")


def _predictions(rows: Any, count: int, task: str) -> tuple[Any, ...]:
    """A black box's answer as the surrogate uses it (a ``snapshot.returned`` check), its training and its held-out
    predictions alike: one prediction for each of the ``count`` rows it was handed, each read where it is taken, before
    anything fits or scores on it, by the rule for its kind: a regression's a number (``snapshot.number``), a
    classification's a class label (:func:`_label`). Review 3 of PR #8's second follow-up, finding 4: the training
    predictions were counted, not read."""
    if type(rows) is not tuple or len(rows) != count:
        raise ValueError("black-box predictor returned mismatched rows")
    read = number if task == "regression" else _label
    return tuple(read(row, where=f"a black-box prediction [{index}]") for index, row in enumerate(rows))


def distill_blackbox(blackbox: Any, X_train: Sequence[Sequence[float]],
                     X_holdout: Sequence[Sequence[float]], feature_names: Sequence[str],
                     *, task: str = "classification", max_depth: int = 3,
                     min_samples_leaf: int = 2, seed: int = 0) -> tuple[ExplicitTree, dict]:
    """Fit a small CART surrogate to black-box predictions and measure held-out fidelity.

    Supplied holdout rows must be kept untouched during training and selection.
    The returned rule tree describes approximated *model behaviour*, not
    ground truth or causal influence.
    """
    X_train = snapshot(X_train, where="X_train")
    X_holdout = snapshot(X_holdout, where="X_holdout")
    feature_names = snapshot(feature_names, where="feature_names")
    task = text(task, where="task")
    max_depth = snapshot(max_depth, where="max_depth")
    min_samples_leaf = snapshot(min_samples_leaf, where="min_samples_leaf")
    seed = snapshot(seed, where="seed")
    if task not in {"classification", "regression"}:
        raise ValueError("task must be classification or regression")
    number(seed, where="seed")
    if (not 1 <= number(max_depth, where="max_depth") <= 12
            or not 1 <= number(min_samples_leaf, where="min_samples_leaf") <= 1000):
        raise ValueError("surrogate complexity outside supported bounds")
    names = _distinct_names(feature_names)
    if not 1 <= len(names) <= 100:
        raise ValueError("invalid feature names")
    # Tuples: the black box is handed fresh copies, so the rows checked here are the rows fitted and scored.
    train = tuple(tuple(_float32(x) for x in row) for row in X_train)
    holdout = tuple(tuple(_float32(x) for x in row) for row in X_holdout)
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

    observed_train = returned(blackbox.predict(fresh(train)), _predictions, len(train), task)
    observed_holdout = returned(blackbox.predict(fresh(holdout)), _predictions, len(holdout), task)
    cls = DecisionTreeClassifier if task == "classification" else DecisionTreeRegressor
    tree = cls(max_depth=max_depth, min_samples_leaf=min_samples_leaf, random_state=seed)
    tree.fit(train, observed_train)
    explicit = extract_tree(tree, names, provenance="blackbox_surrogate")
    predicted = explicit.predict(holdout)
    report: dict[str, Any] = {
        "method": "held_out_surrogate_fidelity",
        "train_n": len(train), "holdout_n": len(holdout), "max_depth": max_depth,
        "model_origin": "blackbox_predictions", "reference": "blackbox_predictor",
        "caveat": "fidelity measures agreement with model output, not accuracy against real outcomes",
    }
    if task == "classification":
        # two labels agree when the bridge's encoder writes them alike, as an identity is read: true is not 1
        matches = sum(canonical_json(a) == canonical_json(b) for a, b in zip(observed_holdout, predicted))
        report.update({"metric": "agreement", "fidelity": matches / len(holdout),
                       "disagreements": len(holdout) - matches})
    else:  # a regression's predictions are numbers, as the black box and the tree wrote them (snapshot.number)
        errors = [number(a, where="a black-box prediction") - number(b, where="a tree prediction")
                  for a, b in zip(observed_holdout, predicted)]
        if not all(math.isfinite(err) for err in errors):
            raise ValueError("non-finite surrogate predictions")
        report.update({"metric": "rmse", "fidelity": math.sqrt(sum(x*x for x in errors)/len(errors)),
                       "mae": sum(abs(x) for x in errors)/len(errors),
                       "lower_is_better": True})
    return publish((explicit, settled(None, **report)), where="distill_blackbox")
