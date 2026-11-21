# SPDX-License-Identifier: Apache-2.0
"""Scan skill text without executing it. A clean scan is never a safety certificate."""
from __future__ import annotations

import ast
import re
import textwrap

from g6.core.markdown import parse
_HEADERS = re.compile(r"(?m)^\s*(?:async\s+def|def|class|import|from)\b")
_ALLOWED_CALLS = {"abs", "all", "any", "bool", "enumerate", "float", "int", "len", "list", "max", "min",
                  "range", "reversed", "round", "sorted", "str", "sum", "tuple", "zip"}
_DANGEROUS = re.compile(r"\b(?:eval|exec|compile|open|__import__|getattr|setattr|globals|locals)\s*\(|"
                         r"\b(?:os|subprocess|socket|pickle|ctypes|importlib)\s*\.")


def skill_text_problems(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    if _DANGEROUS.search(text):
        return ("G6_E_META_SKILL_CODE: file, dynamic execution or process capability",)
    document = parse(text)
    if document.truncated:
        return ("G6_E_META_SKILL_CODE: text nested deeper than the reader parses requires review",)
    blocks = document.code_blocks
    regions = []
    for block in blocks:
        if not block.closed:
            return ("G6_E_META_SKILL_CODE: unclosed code fence",)
        if block.info.lower() not in ("", "py", "python"):
            return ("G6_E_META_SKILL_CODE: only Python code examples can be scanned",)
        regions.append(block.content)
    # The whole source is always pre-scanned above; parsed code also gets the
    # capability pre-scan, since CommonMark can join/dedent its source lines.
    if any(_DANGEROUS.search(region) for region in regions):
        return ("G6_E_META_SKILL_CODE: file, dynamic execution or process capability",)
    rest = text
    for block in reversed(blocks):
        rest = rest[:block.start] + "\n" + rest[block.end:]
    if "```" in rest:   # a fence marker the reader did not take as a fenced block (an inline span, a one-line JSON
        return ("G6_E_META_SKILL_CODE: unclosed code fence",)   # string): its code is unscanned, as at c5782df1
    if _HEADERS.search(rest):
        regions.append(rest)
    if not blocks and not regions:
        try:
            tree = ast.parse(textwrap.dedent(text))
        except (SyntaxError, ValueError, RecursionError):
            return ()  # ordinary prose, after the whole-text capability pre-scan
        if any(isinstance(n, (ast.Call, ast.Attribute)) for n in ast.walk(tree)):
            regions.append(text)
    for region in regions:
        try:
            tree = ast.parse(textwrap.dedent(region))
        except (SyntaxError, ValueError, RecursionError):
            return ("G6_E_META_SKILL_CODE: code example could not be parsed",)
        local_functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return ("G6_E_META_SKILL_CODE: imports in candidate text require review",)
            if isinstance(node, ast.Attribute):
                return ("G6_E_META_SKILL_CODE: attribute access in candidate code requires review",)
            if isinstance(node, ast.Name) and node.id.startswith("__"):
                return ("G6_E_META_SKILL_CODE: dynamic object access",)
            if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name)
                                               or node.func.id not in _ALLOWED_CALLS | local_functions):
                return ("G6_E_META_SKILL_CODE: call outside the bounded code-example vocabulary",)
    return ()
