"""Falsey estimator metadata is still supplied metadata, not an absent field."""

from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import extract_tree
from g6.cognitive_bridge.snapshot import NotPlainData


@pytest.mark.parametrize("names", [False, 0, None, ""])
def test_wrong_kind_of_feature_names_is_not_replaced_with_generated_names(names):
    estimator = SimpleNamespace(
        n_features_in_=1,
        n_outputs_=1,
        feature_names_in_=names,
        tree_=SimpleNamespace(children_left=[-1], children_right=[-1], value=[[[0.5]]]),
    )
    with pytest.raises(NotPlainData):
        extract_tree(estimator)
