# SPDX-License-Identifier: Apache-2.0
"""Scope-aware edits that change only the characters they must, each checked before it is returned.

An edit is a list of spans of the original text and what replaces each; everything else, comments and layout
included, is the original byte for byte. Every edit is then re-parsed and compiled (``compile`` builds bytecode and
runs nothing), and checked against the tree it should give:

* :func:`rename` changes every identifier that means one binding (:mod:`.scopes`) and nothing else: not another
  scope's name that is spelt the same, not an attribute, a keyword argument, a string or a comment. Its check: the new
  text's tree equals the old tree with exactly those identifiers renamed, and the comments are the same.
* :func:`extract_function` moves whole statements of a function body into a new module-level function, passing what
  they read from the function and returning what the rest of it reads. Its checks: the new function's statements are
  the moved ones, and the enclosing statement list is the old one with the moved statements replaced by one call.

Neither check says the code behaves the same: names reached through strings (``getattr``, ``__all__``), other
modules, and what the moved statements do at run time are not seen. The result is a draft; ``run_tests`` runs your
tests on it.
"""
from __future__ import annotations

import ast
import io
import keyword
import tokenize
from dataclasses import dataclass, field
from typing import Any

from g6.code._types import CodeRefused
from g6.code.meta_programming.extract import EDIT_UNCHECKED, EXTRACT_REFUSED, extract
from g6.code.meta_programming.scopes import Occurrence, Scope, Scopes, Text

DOES_NOT_PARSE = "G6_E_SYNTAX_DOES_NOT_PARSE"
RENAME_REFUSED = "G6_E_SYNTAX_RENAME_REFUSED"


@dataclass
class Edit:
    """An edited source, its changed spans and the checks it passed, with what was left alone and why."""

    source: str
    changes: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    left_alone: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def parse(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None)
        raise CodeRefused(DOES_NOT_PARSE, f"the source does not parse: {getattr(exc, 'msg', exc)}"
                                          + (f" (line {line})" if line else "")) from None


def _identifier(name: str, what: str) -> None:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise CodeRefused(RENAME_REFUSED, f"{what} {name!r} is not a Python identifier")


def _comments(source: str) -> list[str]:
    try:
        return [t.string for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError):
        return []


def _apply(source: str, spans: list[tuple[int, int, str]]) -> str:
    out, at = [], 0
    for start, end, text in sorted(spans):
        if start < at:
            raise CodeRefused(EDIT_UNCHECKED, "two changes overlap, so the edit is not made")
        out += [source[at:start], text]
        at = end
    return "".join([*out, source[at:]])


def _changes(text: Text, spans: list[tuple[int, int, str]]) -> list[dict[str, Any]]:
    out = []
    for start, end, after in sorted(spans):
        line, column = text.column(start)
        out.append({"line": line, "column": column, "end_column": column + (end - start),
                    "before": text.source[start:end], "after": after})
    return out


def _compiled(source: str, label: str) -> ast.Module:
    try:
        tree = ast.parse(source)
        compile(source, f"<{label}>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        raise CodeRefused(EDIT_UNCHECKED, f"the edited text does not compile ({exc}), so it is not returned") from None
    return tree


# --- rename -----------------------------------------------------------------------------------------------------------

def _target(scopes: Scopes, old: str, line: int | None) -> Scope:
    named = [o for o in scopes.occurrences if o.name == old]
    if not named:
        raise CodeRefused(RENAME_REFUSED, f"{old!r} does not appear as a name in the source")
    if line is not None:
        here = [o for o in named if o.start is not None and scopes.text.column(o.start)[0] == line]
        if not here:
            raise CodeRefused(RENAME_REFUSED, f"{old!r} does not appear as a name on line {line}")
        found = {id(s): s for s in (scopes.resolve(o) for o in here) if s is not None}
        if len(found) != 1:
            raise CodeRefused(RENAME_REFUSED, f"on line {line}, {old!r} is not bound in this source (a builtin or a "
                                              "name from elsewhere)" if not found else
                              f"on line {line}, {old!r} means more than one binding; give a line where it means one")
        return next(iter(found.values()))
    found = {id(s): s for s in (scopes.resolve(o) for o in named) if s is not None}
    if not found:
        raise CodeRefused(RENAME_REFUSED, f"{old!r} is never bound in this source (a builtin or a name from "
                                          "elsewhere), so there is no binding to rename")
    if len(found) > 1:
        where = "; ".join(sorted(s.describe() for s in found.values()))
        raise CodeRefused(RENAME_REFUSED, f"{old!r} is bound in {len(found)} scopes ({where}); give line, a line where "
                                          "the one you mean appears")
    return next(iter(found.values()))


def rename(source: str, old: str, new: str, line: int | None = None) -> Edit:
    """Every identifier that means the binding of ``old`` that ``line`` names (or the only binding of ``old``)
    renamed to ``new``."""
    _identifier(old, "old")
    _identifier(new, "new")
    if old == new:
        raise CodeRefused(RENAME_REFUSED, "old and new are the same name")
    tree = parse(source)
    scopes = Scopes(tree, Text(source))
    target = _target(scopes, old, line)
    if target.kind == "class":
        raise CodeRefused(RENAME_REFUSED, f"{old!r} is bound in a class body ({target.describe()}): it is reached as "
                                          "an attribute, which a scope-aware rename cannot follow")
    if target.star_import or (target.kind == "module" and scopes.module.star_import):
        raise CodeRefused(RENAME_REFUSED, "the scope has a star import, which may bind either name")
    hits = [o for o in scopes.occurrences if o.name == old and scopes.resolve(o) is target]
    blocked = [o for o in hits if o.start is None or o.import_form == "dotted"]
    if blocked:
        first = blocked[0]
        what = {"capture": "a match pattern", "import": "a dotted import (import a.b binds a)"}.get(first.kind,
                                                                                                    first.kind)
        raise CodeRefused(RENAME_REFUSED, f"{old!r} is bound by {what} at line {getattr(first.node, 'lineno', '?')}, "
                                          "which the rename does not edit")
    if scopes.unsupported:
        what, at = scopes.unsupported[0]
        raise CodeRefused(RENAME_REFUSED, f"the source has {what} (line {at}), whose scopes the rename does not read")
    clash = [o for o in scopes.occurrences if o.name == new and (o.scope.within(target) or o.kind in ("global",
                                                                                                       "nonlocal"))]
    if clash or new in target.bindings:
        at = getattr(clash[0].node, "lineno", "?") if clash else target.line
        raise CodeRefused(RENAME_REFUSED, f"{new!r} is already used where the renamed name is seen (line {at}); "
                                          "renaming would change what one of them means")
    params = [o for o in hits if o.kind == "param"]
    if params:
        keywords = [k for k in ast.walk(tree) if isinstance(k, ast.keyword) and k.arg == old]
        if keywords:
            raise CodeRefused(RENAME_REFUSED, f"{old!r} is a parameter, and a call passes {old}= by keyword (line "
                                              f"{keywords[0].lineno}); rename the parameter and its callers together")
    spans = []
    for o in hits:
        replacement = f"{old} as {new}" if o.import_form == "bare" else new
        spans.append((o.start, o.end, replacement))
    edited = _apply(source, spans)
    expected = _renamed_tree(tree, hits, new)
    got = _compiled(edited, "rename")
    checks = [{"check": "parses_and_compiles", "passed": True,
               "would_have_caught": "a change that breaks the syntax"}]
    if ast.dump(got) != ast.dump(expected):
        raise CodeRefused(EDIT_UNCHECKED, "the edited text's tree is not the old tree with the binding renamed, so "
                                          "the edit is not returned")
    checks.append({"check": "tree_is_the_rename", "passed": True,
                   "would_have_caught": "a span that changed the wrong identifier, or missed one"})
    if _comments(edited) != _comments(source):
        raise CodeRefused(EDIT_UNCHECKED, "the edit changed a comment, so it is not returned")
    checks.append({"check": "comments_kept", "passed": True, "would_have_caught": "a comment changed or lost"})
    left = _left_alone(tree, source, old, target)
    return Edit(edited, _changes(scopes.text, spans), checks, left,
                {"binding": target.describe(), "occurrences": len(hits)})


def _renamed_tree(tree: ast.Module, hits: list[Occurrence], new: str) -> ast.Module:
    """The old tree (changed in place: the caller is done with it) with exactly ``hits`` renamed."""
    for o in hits:
        node = o.node
        if isinstance(node, ast.Name):
            node.id = new
        elif isinstance(node, ast.arg):
            node.arg = new
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.name = new
        elif isinstance(node, ast.alias):
            node.asname = new
        elif isinstance(node, ast.ExceptHandler):
            node.name = new
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            node.names = [new if n == o.name else n for n in node.names]
    return tree


def _left_alone(tree: ast.Module, source: str, old: str, target: Scope) -> list[str]:
    said = []
    attributes = sorted({n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == old})
    strings = sorted({n.lineno for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == old})
    keywords = sorted({n.lineno for n in ast.walk(tree) if isinstance(n, ast.keyword) and n.arg == old})
    comments = sum(1 for c in _comments(source) if old in c)
    if attributes:
        said.append(f"attributes named {old} (lines {', '.join(map(str, attributes[:10]))}) are not this binding")
    if strings:
        said.append(f"strings that spell {old} (lines {', '.join(map(str, strings[:10]))}), such as __all__ or a "
                    "getattr, were left as they are")
    if keywords:
        said.append(f"keyword arguments {old}= (lines {', '.join(map(str, keywords[:10]))}) name another function's "
                    "parameter and were left as they are")
    if comments:
        said.append(f"{comments} comment(s) mention {old} and were left as they are")
    if target.kind == "module":
        said.append("other modules that import this name are not seen")
    return said


# --- extract_function -------------------------------------------------------------------------------------------------

def extract_function(source: str, start_line: int, end_line: int, name: str) -> Edit:
    """The whole statements on lines ``start_line`` to ``end_line`` of one function body moved into a new module-level
    function ``name``, called where they were (:mod:`.extract`)."""
    _identifier(name, "name")
    if start_line < 1 or end_line < start_line:
        raise CodeRefused(EXTRACT_REFUSED, "start_line and end_line are the first and last lines, 1 or more")
    tree = parse(source)
    scopes = Scopes(tree, Text(source))
    edited, changes, checks, detail = extract(source, tree, scopes, start_line, end_line, name)
    left = ["whether the moved statements behave the same in a function of their own is not checked: run your tests "
            "on the edit"]
    return Edit(edited, changes, checks, left, detail)


__all__ = ["DOES_NOT_PARSE", "EDIT_UNCHECKED", "EXTRACT_REFUSED", "RENAME_REFUSED", "Edit", "extract_function", "parse",
           "rename"]