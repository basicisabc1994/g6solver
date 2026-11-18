# SPDX-License-Identifier: Apache-2.0
"""Trusted static observations from explicitly supplied non-held-out source mappings.

No repository walks, imports, test execution or probe execution. Parse errors remain visible.
These summaries describe syntax, not test strength; the existing detector owns that judgment.
"""
from __future__ import annotations

import ast
import difflib
from collections import Counter
from collections.abc import Mapping

from .records import FACT_NAMES, PROBE_NAMES, Split

CATALOGUE = FACT_NAMES


def check_split(split: str) -> None:
    if split not in {"training", "dev", "monitor", "field"}:
        raise ValueError("split must be training, dev, monitor or field; held-out content is refused")


def _summary(fn, changed):
    # A nested helper has its own assertions, never the enclosing test's assertions.
    nodes = []
    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)
    for stmt in fn.body:
        visit(stmt)
    assertions = Counter()
    for node in nodes:
        if isinstance(node, ast.Assert):
            assertions["assert"] += 1
            for comparison in ast.walk(node.test):
                if isinstance(comparison, ast.Compare):
                    assertions.update(type(op).__name__ for op in comparison.ops)
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func).rsplit(".", 1)[-1]
            if name.startswith("assert") or name in {"raises", "warns", "fail", "deprecated_call"}:
                assertions[name] += 1
    literals = []
    def constants(node):
        if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, (str, int, float, bool))):
            literals.append(node.value)
        for child in ast.iter_child_nodes(node):
            constants(child)
    for node in nodes:
        if isinstance(node, ast.Compare):
            constants(node)
    calls = sorted({ast.unparse(node.func) for node in nodes if isinstance(node, ast.Call)})
    markers = sorted(ast.unparse(d) for d in fn.decorator_list)
    parametrizations = []
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and ast.unparse(d.func).endswith("parametrize"):
            values = d.args[1] if len(d.args) > 1 else next((k.value for k in d.keywords if k.arg == "argvalues"), None)
            parametrizations.append(len(values.elts) if isinstance(values, (ast.List, ast.Tuple)) else None)
    return dict(assertions=dict(assertions), compared_literals=literals, markers=markers,
                mocks=[c for c in calls if c.rsplit(".", 1)[-1] in {"patch", "Mock", "MagicMock", "AsyncMock", "setattr"}],
                try_blocks=sum(isinstance(n, (ast.Try, ast.TryStar)) for n in nodes),
                parametrize_counts=parametrizations, calls=calls,
                calls_into_changed=[c for c in calls if c in changed or c.rsplit(".", 1)[-1] in changed])


def _functions(tree, changed):
    result = {}
    def walk(body, prefix=""):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result[prefix + node.name] = _summary(node, changed)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, prefix + node.name + "::")
    walk(tree.body)
    return result


def source_mapping(source):
    if not isinstance(source, Mapping):
        raise ValueError("source must be an explicit mapping, never a directory")
    for path, text in source.items():
        if not isinstance(path, str) or not path or not isinstance(text, str):
            raise ValueError("source maps non-empty paths to text")
    return dict(source)


def extract_facts(before, after, *, split: Split, changed_functions=(), probe_outcomes=None):
    check_split(split)  # Always before even iterating over a source mapping.
    return _extract_facts(before, after, changed_functions=changed_functions, probe_outcomes=probe_outcomes)


def _extract_facts(before, after, *, changed_functions=(), probe_outcomes=None):
    """Shared syntax extractor; protected evaluators supply their own explicit case mappings.

    This has no storage reader and cannot obtain held-out content. The public
    proposer interface above still rejects held-out split labels before access.
    """
    before, after = source_mapping(before), source_mapping(after)
    probes = dict(probe_outcomes or {})
    if set(probes) - PROBE_NAMES:
        raise ValueError("unknown probe catalogue entry")
    files, diffs, errors = {}, {}, {}
    for path in sorted(before.keys() | after.keys()):
        sides = {}
        for label, sources in (("before", before), ("after", after)):
            text = sources.get(path)
            if text is None:
                sides[label] = {}
            else:
                try:
                    sides[label] = _functions(ast.parse(text), set(changed_functions))
                except SyntaxError as error:
                    sides[label] = {}
                    errors[f"{path}:{label}"] = dict(line=error.lineno, reason=error.msg)
        files[path] = sides
        diffs[path] = "\n".join(difflib.unified_diff(before.get(path, "").splitlines(), after.get(path, "").splitlines(),
                                                   fromfile=path, tofile=path, lineterm=""))
    return dict(files=files, diff_hunks=diffs, probe_outcomes=probes, parse_errors=errors)
