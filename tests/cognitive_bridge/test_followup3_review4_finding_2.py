"""A record composed by hand still needs its bound at publication."""

import pytest

from g6.cognitive_bridge import ExplicitTree, TreeNode
from g6.cognitive_bridge.snapshot import NotPlainData


def test_deep_tree_publication_refuses_before_recursive_asdict():
    try:
        leaf = TreeNode(value=0)
        root = leaf
        # Construction may compose records without walking them. This changes no
        # record in place and uses no low-level mutation escape hatch.
        for _ in range(1200):
            root = TreeNode(feature=0, threshold=0.5, left=root, right=leaf)
        tree = ExplicitTree(("x",), root, "regression", "tree_transcription")
        tree.to_dict()
    except Exception as exc:
        assert isinstance(exc, NotPlainData), f"expected the depth refusal, got {type(exc).__name__}"
        return
    pytest.fail("the tree exceeded the depth limit without a refusal")
