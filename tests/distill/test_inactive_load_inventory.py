# SPDX-License-Identifier: Apache-2.0
"""Every registration-loader edge has an inactive-method guard first."""
import ast
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_edges(source):
    """Return direct loader calls and whether a preceding sibling guards them.

    Imports and aliases of the loader are rejected by the repository inventory;
    no alternate entry point may quietly acquire registration custody.
    """
    tree = ast.parse(source)
    result = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for parent in ast.walk(fn):
            for _, value in ast.iter_fields(parent):
                if not isinstance(value, list):
                    continue
                for index, statement in enumerate(value):
                    if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)):
                        continue
                    call = statement.value
                    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != '_load':
                        continue
                    previous = value[max(0, index - 2):index]
                    guards = [stmt.value for stmt in previous if isinstance(stmt, ast.Expr)
                              and isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name)
                              and stmt.value.func.id == '_check_inactive']
                    guarded = any(len(guard.args) >= 2 and len(call.args) >= 3
                                  and ast.dump(guard.args[0]) == ast.dump(call.args[1])
                                  and ast.dump(guard.args[1]) == ast.dump(call.args[2]) for guard in guards)
                    result.append((fn.name, guarded))
    # ast.walk on an enclosing function also sees nested functions. No current
    # loader owner nests a function, and inventory must not count edges twice.
    return sorted(set(result))


def test_all_registration_load_callers_are_guarded():
    source = (ROOT / 'src/g6/distill/acceptance_gate.py').read_text(encoding='utf-8')
    edges = load_edges(source)
    assert {name for name, _ in edges} == {'_decide_batch', 'recheck', 'ablate', 'promote'}
    assert all(guarded for _, guarded in edges), edges
    calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == '_load']
    assert len(calls) == len(edges), 'a loader call escaped the guarded statement inventory'


def test_inventory_rejects_a_new_unguarded_loader():
    source = (ROOT / 'src/g6/distill/acceptance_gate.py').read_text(encoding='utf-8')
    source += '\ndef forgotten(candidate, library, registration):\n return _load(registration, candidate, library)\n'
    assert ('forgotten', False) in load_edges(source)


def test_no_other_module_can_import_or_alias_the_registration_loader():
    paths = subprocess.run(['git', 'ls-files', 'src/**/*.py'], cwd=ROOT, check=True,
                           capture_output=True, text=True).stdout.splitlines()
    assert paths
    # These existing methods load other products' local state. Any new _load
    # attribute owner must be reviewed, including relative module imports and
    # aliases that a simple acceptance_gate-name search would miss.
    unrelated = {
        ('src/g6/mcp/review_loop.py', 'loop'),
        ('src/g6/harness/workflow/adapters.py', 'completion_loop'),
        ('src/g6/harness/completion_hook.py', 'loop'),
        ('src/g6/metaharness/lora.py', 'self'),
        ('src/g6/metaharness/surface.py', 'self'),
    }
    for rel in paths:
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or '').endswith('acceptance_gate'):
                assert not any(alias.name in {'_load', '*'} for alias in node.names), rel
            if rel == 'src/g6/distill/acceptance_gate.py':
                # Calls, rather than first-class aliases, are inventoried above.
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.Name) and child.id == '_load' and isinstance(child.ctx, ast.Load):
                        assert isinstance(node, ast.Call) and node.func is child, rel
            else:
                if isinstance(node, ast.Attribute) and node.attr == '_load' and isinstance(node.ctx, ast.Load):
                    assert (rel, ast.unparse(node.value)) in unrelated, rel


@pytest.mark.parametrize('file,owner,target', [
    ('src/g6/distill/acceptance_gate.py', 'decide', 'decide_batch'),
    ('src/g6/distill/acceptance_gate.py', 'decide_batch', '_decide_batch'),
    ('src/g6/distill/acceptance_gate.py', 'restore', '_decide_batch'),
    ('src/g6/mcp/distill_backend.py', 'run', 'decide_batch'),
])
def test_public_routes_keep_the_guarded_admission_edge(file, owner, target):
    tree = ast.parse((ROOT / file).read_text(encoding='utf-8'))
    owners = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and node.name == owner]
    assert owners
    assert any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == target
               for fn in owners for call in ast.walk(fn))


def test_probation_persistence_only_comes_from_the_guarded_batch():
    tree = ast.parse((ROOT / 'src/g6/distill/acceptance_gate.py').read_text(encoding='utf-8'))
    owners = {fn.name for fn in tree.body if isinstance(fn, ast.FunctionDef)
              and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == '_persist'
                      for call in ast.walk(fn))}
    assert owners == {'_decide_batch'}
