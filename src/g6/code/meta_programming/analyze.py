# SPDX-License-Identifier: Apache-2.0
"""What a Python module holds, read from its syntax tree with the standard library (nothing in it runs).

:func:`parse` says whether the source parses, and where it does not (Python's own parser decides). :func:`analyze`
lists its functions (with their qualified names, parameters, decorators, return annotations, whether each has a
docstring, and its decision count: one for each ``if``, loop, ``except``, ``with``, conditional expression,
comprehension condition, ``match`` case and extra ``and`` or ``or`` operand, the count McCabe's complexity adds one
to), its classes, its imports, its ``__all__`` when that is a literal, and the names it binds at module level.
"""
from __future__ import annotations

import ast
from typing import Any

from g6.code._types import CodeRefused

_DECISIONS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.With, ast.AsyncWith, ast.IfExp,
              ast.match_case)


def parse(source: str) -> dict[str, Any]:
    """Whether ``source`` parses, with the parser's message and place when it does not."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return {"parses": False, "error": {"line": getattr(exc, "lineno", None), "column": getattr(exc, "offset", None),
                                           "message": str(getattr(exc, "msg", exc))[:300]}}
    return {"parses": True, "lines": source.count("\n") + (0 if source.endswith("\n") or not source else 1),
            "statements": len(tree.body), "nodes": sum(1 for _ in ast.walk(tree))}


def _decisions(node: ast.AST) -> int:
    """Decisions inside ``node``'s own body, not counting nested functions and classes."""
    count = 0
    todo = list(ast.iter_child_nodes(node))
    while todo:
        child = todo.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, _DECISIONS):
            count += 1
        elif isinstance(child, ast.BoolOp):
            count += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            count += len(child.ifs)
        todo.extend(ast.iter_child_nodes(child))
    return count


def _short(node: ast.AST) -> str:
    return ast.unparse(node)[:100]


def analyze(tree: ast.Module) -> dict[str, Any]:
    """Functions, classes, imports, ``__all__`` and module-level bindings of a parsed module."""
    functions, classes, imports = [], [], []

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                names = [a.arg for a in [*args.posonlyargs, *args.args]]
                names += [f"*{args.vararg.arg}"] if args.vararg else (["*"] if args.kwonlyargs else [])
                names += [a.arg for a in args.kwonlyargs] + ([f"**{args.kwarg.arg}"] if args.kwarg else [])
                decisions = _decisions(node)
                functions.append({"name": node.name, "qualname": prefix + node.name, "line": node.lineno,
                                  "end_line": node.end_lineno, "async": isinstance(node, ast.AsyncFunctionDef),
                                  "params": names, "decorators": [_short(d) for d in node.decorator_list],
                                  "returns": _short(node.returns) if node.returns else "",
                                  "docstring": ast.get_docstring(node) is not None, "decisions": decisions,
                                  "complexity": decisions + 1})
                visit(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                classes.append({"name": node.name, "qualname": prefix + node.name, "line": node.lineno,
                                "end_line": node.end_lineno, "bases": [_short(b) for b in node.bases],
                                "methods": methods, "docstring": ast.get_docstring(node) is not None})
                visit(node.body, f"{prefix}{node.name}.")
            else:
                for field in ("body", "orelse", "finalbody", "handlers"):
                    inner = getattr(node, field, None)
                    if isinstance(inner, list):
                        visit([n for n in inner if isinstance(n, ast.stmt)] if field != "handlers"
                              else [s for h in inner for s in h.body], prefix)

    visit(tree.body, "")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [{"module": a.name, "as": a.asname or "", "line": node.lineno} for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append({"module": "." * node.level + (node.module or ""), "names": [a.name for a in node.names],
                            "line": node.lineno})
    listed = None
    bound: list[str] = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, (ast.AnnAssign, ast.AugAssign)) else []
        for target in targets:
            for name in (n.id for n in ast.walk(target) if isinstance(n, ast.Name)):
                bound.append(name)
                if name == "__all__" and isinstance(node, ast.Assign):
                    try:
                        value = ast.literal_eval(node.value)
                        listed = [str(v) for v in value] if isinstance(value, (list, tuple)) else None
                    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
                        raise CodeRefused("G6_E_SYNTAX_DOES_NOT_PARSE",
                                          f"__all__ cannot be read as a literal: {type(exc).__name__}") from None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.append(node.name)
    return {"functions": functions, "classes": classes, "imports": imports, "all": listed,
            "module_names": sorted(set(bound)), "docstring": ast.get_docstring(tree) is not None}


__all__ = ["analyze", "parse"]
