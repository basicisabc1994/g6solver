# SPDX-License-Identifier: Apache-2.0
"""The AST pre-filter for small generated functions run out of process, shared by ``g6.cegis`` and
``g6.distillation`` (audit 025 HD-010: one copy, not two).

``validate_source`` refuses imports, the builtins that reach the interpreter (``exec``, ``eval``, ``open``,
``getattr``, ``type``, ``object`` …), ``global`` / ``nonlocal``, any attribute starting with ``_``, and the attributes
that reach other attributes at run time (``"{0.__class__}".format(x)`` gets past the underscore rule, so ``format``
and ``format_map`` are refused too). ``SAFE_BUILTIN_NAMES`` is the small builtins table the code then runs with.

A pre-filter, not a sandbox: the runner's process bounds (``g6.core.sandbox``, ``g6.cegis.runner``) are what limit
memory, time and the network. Standard library only, so a child process can import it cheaply.
"""
from __future__ import annotations

import ast

FORBIDDEN = frozenset({"exec", "eval", "open", "compile", "__import__", "getattr", "setattr", "delattr", "vars",
                       "globals", "locals", "breakpoint", "input", "help", "memoryview", "type", "object", "super"})
#: Attributes that reach other attributes at run time ("{0.__class__}".format(x) gets past the underscore rule).
FORBIDDEN_ATTRS = FORBIDDEN | {"format", "format_map", "mro"}
SAFE_BUILTIN_NAMES = ("abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "int",
                      "isinstance", "len", "list", "map", "max", "min", "pow", "range", "reversed", "round", "set",
                      "sorted", "str", "sum", "tuple", "zip", "ValueError", "ZeroDivisionError")


def validate_source(src: str) -> str:
    """Why ``src`` is refused ("" when it passes the pre-filter)."""
    if not src.strip():
        return "the source is empty"
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return f"SyntaxError: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            return "imports are not allowed"
        if isinstance(node, ast.Name) and node.id in FORBIDDEN:
            return f"{node.id!r} is not allowed"
        if isinstance(node, ast.Attribute) and (node.attr in FORBIDDEN_ATTRS or node.attr.startswith("_")):
            return f"attribute {node.attr!r} is not allowed"
        if isinstance(node, ast.Global | ast.Nonlocal):
            return "global and nonlocal are not allowed"
    return ""


__all__ = ["FORBIDDEN", "FORBIDDEN_ATTRS", "SAFE_BUILTIN_NAMES", "validate_source"]
