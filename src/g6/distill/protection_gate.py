# SPDX-License-Identifier: Apache-2.0
"""Compute gate suppliers and verdict consumers from repository-internal imports.

No module is imported to discover its dependencies. Unknown internal targets and
runtime imports close the gate's package to autonomous changes.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Closure:
    files: tuple[str, ...]
    unresolved: tuple[str, ...]
    closed_packages: tuple[str, ...]


def package_sources():
    package = Path(__file__).resolve().parents[1]
    return {'src/' + path.relative_to(package.parent).as_posix(): path.read_text(encoding='utf-8')
            for path in package.rglob('*.py')}


def _module(path):
    return path.removeprefix('src/').removesuffix('.py').removesuffix('/__init__').replace('/', '.')


@lru_cache(maxsize=16384)
def _imports(path, source):
    module = _module(path)
    package = module if path.endswith('/__init__.py') else module.rsplit('.', 1)[0]
    targets, possible, problems = set(), set(), set()
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError):
        return (), (), ('unreadable syntax',)
    for node in ast.walk(tree):
        if ((isinstance(node, ast.Name) and node.id in {'__import__', 'exec', 'eval'})
                or (isinstance(node, ast.alias) and node.name in {'__import__', 'exec', 'eval'})):
            problems.add('runtime import or execution')
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'g6' or alias.name.startswith('g6.'):
                    targets.add(alias.name)
                if alias.name == 'importlib' or alias.name.startswith('importlib.'):
                    problems.add('runtime import machinery')
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split('.')
                if node.level > len(parts):
                    problems.add('unresolved relative import')
                    continue
                base = '.'.join(parts[:len(parts) - node.level + 1])
                if node.module:
                    base += '.' + node.module
            else:
                base = node.module or ''
            if base == 'g6' or base.startswith('g6.'):
                targets.add(base)
                possible.update(base + '.' + alias.name for alias in node.names if alias.name != '*')
            if base == 'importlib' or base.startswith('importlib.'):
                problems.add('runtime import machinery')
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else '')
            if name in {'__import__', 'import_module', 'exec', 'eval'}:
                problems.add('runtime import or execution')
    return tuple(targets), tuple(possible), tuple(problems)


def import_closure(sources, seeds, *, consumers=False):
    modules = {_module(path): path for path in sources}
    graph, unknown = {}, {}
    for path, source in sources.items():
        targets, possible, problems = _imports(path, source)
        dependencies = set()
        errors = set(problems)
        for target in targets:
            if target not in modules:
                errors.add('unresolved internal import: ' + target)
            else:
                dependencies.add(modules[target])
        dependencies.update(modules[target] for target in possible if target in modules)
        own = _module(path).split('.')
        dependencies.update(modules[parent] for i in range(1, len(own))
                            if (parent := '.'.join(own[:i])) in modules)
        # Python executes each parent package's initializer before its module.
        for target in tuple(dependencies):
            name = _module(target)
            dependencies.update(modules[parent] for i in range(1, len(name.split('.')))
                                if (parent := '.'.join(name.split('.')[:i])) in modules)
        graph[path], unknown[path] = dependencies, errors
    roots = set(seeds)
    if consumers:
        handoffs = set(roots)
        while True:
            more = {path for path, deps in graph.items() if deps & handoffs}
            if more <= handoffs:
                break
            handoffs.update(more)
        roots.update(handoffs)
    found, unresolved, closed = set(), set(), set()
    for root in roots:
        pending, visited, failed = [root], set(), False
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            if path not in graph or unknown[path]:
                failed = True
                unresolved.add(path)
            pending.extend(graph.get(path, set()) - visited)
        found.update(visited)
        if failed:
            closed.add(root.removeprefix('src/').rsplit('/', 1)[0] + '/')
    return Closure(tuple(sorted(found)), tuple(sorted(unresolved)), tuple(sorted(closed)))


def complete_patterns(base):
    """Use the existing protection policy to discover roots; never maintain a supplier list."""
    from g6.self_training.merge_gate import FileChange
    sources = package_sources()
    seeds = tuple(path for path in sources
                  if base.hits((FileChange(path=path, before='before', after='after'),)))
    # Treat every protected module as a possible verdict source. Consumers need
    # protection even when their filenames do not advertise a gate or judge.
    closure = import_closure(sources, seeds, consumers=True)
    return tuple(sorted({*(path.removeprefix('src/') for path in closure.files), *closure.closed_packages}))
