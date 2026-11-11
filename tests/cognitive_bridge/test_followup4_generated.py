# SPDX-License-Identifier: Apache-2.0
"""Generated collections spend the boundary budget before constructing each item."""
import builtins
from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import snapshot, xai
from g6.cognitive_bridge.codes import NotPlainData


@pytest.mark.parametrize('present', [True, False])
@pytest.mark.parametrize('count', [-1, 100_000, 10**30])
def test_estimator_count_is_bounded_before_first_generated_item(monkeypatch, present, count):
    visits = []

    def counted_range(stop):
        visits.append(stop)
        return builtins.range(min(max(stop, 0), 2))

    monkeypatch.setattr(xai, 'range', counted_range, raising=False)
    estimator = SimpleNamespace(n_features_in_=count, tree_=object())
    if present:
        estimator.feature_names_in_ = ['x']
    with pytest.raises(NotPlainData):
        xai._feature_names(estimator, None)
    assert visits == []


def test_generation_uses_shared_visit_and_size_budget(monkeypatch):
    made = []
    generated = getattr(snapshot, 'generated', None)
    assert generated is not None, 'generated collections need the boundary budget'
    monkeypatch.setattr(snapshot, 'MAX_ITEMS', 4)
    with pytest.raises(NotPlainData, match='size'):
        generated(4, lambda i: made.append(i) or str(i), where='names')
    assert made == []
    assert generated(3, lambda i: str(i), where='names') == ('0', '1', '2')
    monkeypatch.setattr(snapshot, 'MAX_SIZE', 3)
    with pytest.raises(NotPlainData, match='size'):
        generated(3, lambda i: made.append(i) or 'long', where='names')
    assert made == [0]


def test_present_names_never_generate_defaults(monkeypatch):
    def unexpected(*args):
        pytest.fail('present feature names must not generate defaults')
    monkeypatch.setattr(xai, 'range', unexpected, raising=False)
    monkeypatch.setattr(xai, 'generated', unexpected, raising=False)
    estimator = SimpleNamespace(n_features_in_=2, tree_=object(), feature_names_in_=['a', 'b'])
    assert xai._feature_names(estimator, None) == ('a', 'b')


def test_absent_names_use_bounded_defaults():
    assert xai._feature_names(SimpleNamespace(n_features_in_=3, tree_=object()), None) == ('x0', 'x1', 'x2')


def test_repeated_estimator_nodes_are_bounded_before_transcription(monkeypatch):
    # A small fitted graph can denote an exponentially large output tree.
    calls = []
    native_init = xai.TreeNode.__init__

    def node(self, *args, **kwargs):
        calls.append(1)
        if len(calls) > 128:
            raise AssertionError('transcription expanded before its visit budget')
        native_init(self, *args, **kwargs)

    monkeypatch.setattr(snapshot, 'MAX_ITEMS', 128)
    monkeypatch.setattr(xai.TreeNode, '__init__', node)
    n = 12
    tree = SimpleNamespace(children_left=list(builtins.range(1, n))+[-1],
                           children_right=list(builtins.range(1, n))+[-1],
                           feature=[0]*n, threshold=[0.5]*n, value=[[[0.0]]]*n)
    estimator = SimpleNamespace(n_features_in_=1, feature_names_in_=['a'], tree_=tree)
    with pytest.raises(NotPlainData, match='size'):
        xai.extract_tree(estimator)
    assert len(calls) <= 128
