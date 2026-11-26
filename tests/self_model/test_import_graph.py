# SPDX-License-Identifier: Apache-2.0
"""Breaks caught: importing a writer, an executor or predictions into a judging gate."""
import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2] / 'src'


def source_for(name):
    path = ROOT.joinpath(*name.split('.'))
    if path.with_suffix('.py').is_file():
        return path.with_suffix('.py')
    if (path / '__init__.py').is_file():
        return path / '__init__.py'
    return None


def imports(name, path):
    package = name if path.name == '__init__.py' else name.rpartition('.')[0]
    # Deferred imports in unrelated functions (e.g. g6.invoke) are not import
    # edges. Include module/class bodies and every eager parent initializer.
    def eager(node):
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for child in ast.iter_child_nodes(node):
                yield from eager(child)

    for node in eager(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module
            if target:
                yield target
                for item in node.names:
                    if source_for(f'{target}.{item.name}'):
                        yield f'{target}.{item.name}'


def graph(roots):
    seen, todo = set(), list(roots)
    while todo:
        name = todo.pop()
        if name in seen or not name.startswith('g6'):
            continue
        path = source_for(name)
        if path is None:
            continue
        seen.add(name)
        todo.extend(imports(name, path))
        # Python imports every parent initializer, even for a direct submodule import.
        todo.extend('.'.join(name.split('.')[:i]) for i in range(1, len(name.split('.'))))
    return seen


def test_read_only_import_graph_includes_package_initializers():
    assert source_for('g6.self_model'), 'self-model package is missing'
    reached = graph(['g6.self_model.model', 'g6.self_model.monitor', 'g6.self_model.simulate', 'g6.self_model.risk_gate'])
    writers = {'g6.core.ledger', 'g6.core.storage', 'g6.metaharness.api', 'g6.metaharness.store',
               'g6.action_gating.decision_engine', 'g6.navigator.plan', 'g6.core.llm', 'g6.core.registry'}
    assert not (reached & writers), f'writer import path: {sorted(reached & writers)}'
    for name in reached:
        tree = ast.parse(source_for(name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {'write_text', 'write_bytes', 'mkdir', 'unlink', 'rename',
                                              'chmod', 'symlink_to', 'Popen', 'run_script'}, (name, node.lineno)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {'open', 'exec', 'eval', '__import__'}, (name, node.lineno)
            if name.startswith('g6.self_model') and isinstance(node, ast.Import):
                assert not any(item.name in {'os', 'subprocess', 'importlib'} for item in node.names)


def test_judging_gate_apis_do_not_import_self_predictions():
    # Wave 3 adds these gates. This guard becomes active when either lands.
    for name in ('g6.self_training.merge_gate', 'g6.distill.acceptance_gate'):
        if source_for(name):
            assert not any(item.startswith('g6.self_model') for item in graph([name]))


def test_pure_lessons_reuse_does_not_load_storage():
    reached = graph(['g6.core.lessons'])
    assert 'g6.metaharness.api' not in reached, 'pure lessons import reaches writer API'
