# SPDX-License-Identifier: Apache-2.0
"""Discover scalar-driven generation sites and inherited metadata from code."""
import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from g6.cognitive_bridge import CognitiveBridgeBlock, snapshot
from g6.core.ai_block import AIBlock
from tests.cognitive_bridge.test_read_inventory import _functions


def scalar_sites():
    for path in Path(snapshot.__file__).parent.glob('*.py'):
        for name, fn in _functions(ast.parse(path.read_text(encoding='utf-8'))):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'range':
                    yield path.name+':'+name, ast.unparse(node)
                if isinstance(node, ast.While):
                    yield path.name+':'+name, 'while '+ast.unparse(node.test)
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult) and (
                    isinstance(node.left, (ast.List, ast.Tuple)) or isinstance(node.right, (ast.List, ast.Tuple))
                ):
                    yield path.name+':'+name, ast.unparse(node)


def classify(site, expression):
    if (site, expression) == ('block.py:_admit_input', 'while pending'):
        return 'fixed model topology; native collection counts admitted before pending receives their entries'
    if (site, expression) == ('engine.py:CognitiveAgent.run', 'range(max_steps)'):
        source = inspect.getsource(__import__('g6.cognitive_bridge.engine', fromlist=['CognitiveAgent']).CognitiveAgent.run)
        assert source.index('<= 20') < source.index('range(max_steps)')
        return 'snapshot and explicit 1..20 limit before first step'
    if (site, expression) == ('xai.py:extract_tree', 'range(len(scores))'):
        return 'scores read from _native-bounded fitted arrays by _numbers before argmax'
    if (site, expression) == ('xai.py:ExplicitTree.explain_one', 'while node.feature is not None'):
        return 'tree snapshotted and depth/item bounded before node traversal'
    if (site, expression) == ('snapshot.py:generated', 'while index < count'):
        return 'count preflight and shared visit/size budget before make(index)'
    raise AssertionError(f'unclassified scalar-driven collection: {site}: {expression}')


def test_every_scalar_driven_range_and_collection_is_bounded():
    seen = list(scalar_sites())
    assert seen
    for site, expression in seen:
        assert classify(site, expression)
    with pytest.raises(AssertionError, match='unclassified'):
        classify('xai.py:new', 'range(estimator.size)')


def test_default_generation_is_inside_the_boundary_budget():
    generated = getattr(snapshot, 'generated', None)
    assert generated is not None
    tree = ast.parse(inspect.getsource(generated))
    guard = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and 'count < MAX_ITEMS' in ast.unparse(n.test))
    make = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'make')
    assert guard.lineno < make.lineno
    assert 'budget[0] >= MAX_ITEMS' in inspect.getsource(generated)
    assert 'budget=budget' in inspect.getsource(generated)


def inherited_members():
    # Resolve the effective descriptor using the bridge's MRO, including properties.
    result = {}
    for name, descriptor in inspect.getmembers_static(CognitiveBridgeBlock):
        if name.startswith('_'):
            continue
        owner = next(base for base in inspect.getmro(CognitiveBridgeBlock) if name in vars(base))
        if owner is not CognitiveBridgeBlock:
            result[name] = (owner, descriptor)
    for field in dataclasses.fields(AIBlock):
        result.setdefault(field.name, (AIBlock, None))
    return result


def test_inherited_inventory_covers_the_actual_base_schema_and_properties():
    members = inherited_members()
    assert set(inspect.signature(AIBlock).parameters) == {f.name for f in dataclasses.fields(AIBlock)}
    for base in inspect.getmro(CognitiveBridgeBlock)[1:]:
        for name, descriptor in vars(base).items():
            if not name.startswith('_') and isinstance(descriptor, property):
                assert name in members
    assert all(owner in inspect.getmro(CognitiveBridgeBlock) for owner, _ in members.values())
