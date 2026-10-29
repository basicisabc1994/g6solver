# SPDX-License-Identifier: Apache-2.0
import pytest
from g6.goal_engine.decomposition import GoalTree, propose_goal_tree
from g6.goal_engine import GoalSpec


def test_bounded_tree_dfs_and_leaf_order():
    tree = GoalTree("ship", branching_factor=2, max_depth=2, max_nodes=5)
    a, b = tree.add_subgoals("root", ["test", "deploy"])
    c, = tree.add_subgoals(a, ["unit"])
    assert tree.depth_first_search("root", c) == ("root", a, c)
    assert tree.generate_goal_stack() == (c, a, b, "root")
    assert tree.get_parent_goal(c).node_id == a
    assert [n.goal for n in tree.get_goals_at_depth(1)] == ["test", "deploy"]
    assert tree.is_terminal_node(c)
    assert [n.node_id for n in tree.nodes] == ["root", a, b, c]


def test_invalid_addition_is_atomic():
    tree = GoalTree("root", branching_factor=2)
    with pytest.raises(ValueError):
        tree.add_subgoals("root", ["same", "same"])
    assert len(tree.nodes) == 1
    with pytest.raises(ValueError):
        tree.add_subgoals("root", ["one", "two", "three"])
    assert len(tree.nodes) == 1


def test_model_proposal_remains_unverified_and_preserves_controls():
    declared = GoalSpec(goal="ship", constraints=("keep API",),
                        success_criteria=("tests pass",))
    result = propose_goal_tree(
        "ship", lambda goal, count, depth: '["write tests", "implement"]',
        max_depth=1, max_model_calls=1, root_spec=declared,
    )
    assert result.completion_state == "qualified-draft"
    assert result.complete
    assert result.spec.constraints == ("keep API",)
    assert result.spec.success_criteria == ("tests pass",)
    assert [n.node_id for n in result.plan.nodes] == ["root", "root.0", "root.1"]


def test_invalid_and_exhausted_proposals_visible():
    invalid = propose_goal_tree("goal", lambda *_: "not JSON", max_depth=2)
    assert invalid.errors and invalid.completion_state == "blocked-escalated"
    limited = propose_goal_tree("goal", lambda *_: '["sub"]',
                                max_depth=2, max_model_calls=1)
    assert limited.unexpanded == ("root.0",)
    assert not limited.complete


def test_node_limit_refuses_without_truncation():
    tree = GoalTree("root", max_nodes=2)
    with pytest.raises(ValueError):
        tree.add_subgoals("root", ["a", "b"])
    assert len(tree.nodes) == 1
