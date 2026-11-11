"""An oversized fitted array must be refused before a native list is made."""
from array import array
import sys
from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import extract_tree, snapshot
from g6.cognitive_bridge.codes import INPUT, NotPlainData


def test_fitted_array_is_bounded_before_tolist():
    # The input is a real built-in numeric array, not an expensive user-defined
    # protocol. Its length alone exceeds the walker's entire visit budget.
    children = array("i", [-1]) * snapshot.MAX_ITEMS
    estimator = SimpleNamespace(
        n_features_in_=1,
        feature_names_in_=["x"],
        tree_=SimpleNamespace(children_left=children),
    )
    expanded = []

    def profile(frame, event, function):
        if (event == "c_call" and getattr(function, "__self__", None) is children
                and getattr(function, "__name__", "") == "tolist"):
            expanded.append(len(children))

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        with pytest.raises(NotPlainData) as caught:
            extract_tree(estimator)
    finally:
        sys.setprofile(previous)

    assert caught.value.code == INPUT
    assert "children_left" in str(caught.value)
    assert expanded == [], f"tolist expanded {expanded} items before the visit bound"
