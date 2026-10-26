# SPDX-License-Identifier: Apache-2.0
"""extract_function: whole statements of a function body moved into a new module-level function, called where they
were (see :mod:`.edits` for how every edit is checked).

The statements must be whole, consecutive statements of one block inside one function, on lines of their own. What
crosses the boundary is read from the scopes (:mod:`.scopes`), in the order the statements run:

* a parameter of the new function: a local of the enclosing function (or of a function around it) that the statements
  read before they bind it, or that they may bind on some paths only;
* a value it returns: a local the statements bind that the rest of the function reads (later, or on the next turn of
  a loop), which the statements bind on every path or which was passed in.

It refuses statements that cannot leave their function: a ``return``, ``yield`` or ``await``; a ``break`` or
``continue`` of a loop outside them; ``global`` or ``nonlocal``; ``super()`` with no arguments or ``__class__``; a
walrus inside a comprehension; a local they bind that a nested function outside them reads; and indentation by tabs.
"""
from __future__ import annotations

import ast
import io
import tokenize
from typing import Any

from g6.code._types import CodeRefused
from g6.code.meta_programming.scopes import Scope, Scopes, Text

EXTRACT_REFUSED = "G6_E_SYNTAX_EXTRACT_REFUSED"
EDIT_UNCHECKED = "G6_E_SYNTAX_EDIT_UNCHECKED"
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_BINDS = ("store", "del", "def", "class", "import", "except")


def _first_line(stmt: ast.stmt) -> int:
    return min([stmt.lineno, *(d.lineno for d in getattr(stmt, "decorator_list", None) or [])])


def _statement_lists(node: ast.AST, path: tuple = ()) -> list[tuple[tuple, list[ast.stmt]]]:
    """Every statement list under ``node``, outermost first, with its path of (field, index) steps."""
    found = []
    for name, value in ast.iter_fields(node):
        if isinstance(value, list) and value and all(isinstance(v, ast.stmt) for v in value):
            found.append(((*path, name), value))
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, ast.AST):
                    found += _statement_lists(item, (*path, name, index))
        elif isinstance(value, ast.AST):
            found += _statement_lists(value, (*path, name))
    return found


def follow(tree: ast.AST, path: tuple) -> Any:
    found: Any = tree
    for step in path:
        found = found[step] if isinstance(step, int) else getattr(found, step)
    return found


def _why_not_movable(statements: list[ast.stmt]) -> str:
    """Why the statements cannot leave their function, or ``""``."""
    def walk(node: ast.AST, loop: bool, nested: bool) -> str:
        if isinstance(node, ast.Name) and node.id == "__class__":
            return "__class__"
        if isinstance(node, _COMPREHENSIONS) and any(isinstance(n, ast.NamedExpr) for n in ast.walk(node)):
            return "a walrus inside a comprehension"
        if not nested:
            if isinstance(node, ast.Return):
                return "a return"
            if isinstance(node, (ast.Yield, ast.YieldFrom)):
                return "a yield"
            if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
                return "an await"
            if isinstance(node, (ast.Break, ast.Continue)) and not loop:
                return "a break or continue of a loop outside the lines"
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                return "a global or nonlocal statement"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super" \
                    and not node.args:
                return "super() with no arguments"
        if isinstance(node, _DEFS):  # what a nested function or class does stays inside it
            child_loop, child_nested = False, True
        else:
            child_loop, child_nested = loop or isinstance(node, _LOOPS), nested
        for child in ast.iter_child_nodes(node):
            said = walk(child, child_loop, child_nested)
            if said:
                return said
        return ""

    for stmt in statements:
        said = walk(stmt, False, False)
        if said:
            return said
    return ""


def _held_lines(source: str) -> set[int]:
    """Lines inside a string that spans lines (after its first): their text is the string's and stays as it is."""
    held: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)) and token.end[0] > token.start[0]:
                held.update(range(token.start[0] + 1, token.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        pass
    return held


def _nested_touch(stmt: ast.stmt, start_line: int, end_line: int) -> bool:
    """Whether a statement nested in ``stmt`` (its body, else, finally, handlers or cases) touches the lines: then the
    lines lie inside it and the statements to move are deeper, rather than cutting it."""
    lists = [v for _, v in ast.iter_fields(stmt) if isinstance(v, list) and v and isinstance(v[0], ast.stmt)]
    lists += [h.body for h in getattr(stmt, "handlers", None) or []] + [c.body for c in getattr(stmt, "cases", None) or []]
    return any(_first_line(s) <= end_line and s.end_lineno >= start_line for body in lists for s in body)


def _select(tree: ast.Module, text: Text, start_line: int, end_line: int) -> tuple[tuple, list[ast.stmt], int, int]:
    """The statement list and the indices of the whole statements on the lines asked for."""
    cut_at = None
    for path, statements in _statement_lists(tree):
        touching = [i for i, s in enumerate(statements) if _first_line(s) <= end_line and s.end_lineno >= start_line]
        if not touching:
            continue
        inside = [i for i in touching if _first_line(statements[i]) >= start_line and statements[i].end_lineno <= end_line]
        if inside == touching:
            first, last = inside[0], inside[-1]
            begins = statements[first]
            decorators = getattr(begins, "decorator_list", None) or []
            opener = min(decorators, key=lambda d: d.lineno) if decorators else begins
            head = text.line(opener.lineno).encode("utf-8")
            lead = head[:opener.col_offset].decode("utf-8", "replace")
            if lead.strip() not in ("", "@"):
                raise CodeRefused(EXTRACT_REFUSED, f"line {opener.lineno} holds more than the statements asked for")
            tail = text.line(statements[last].end_lineno)
            rest = tail.encode("utf-8")[statements[last].end_col_offset:].decode("utf-8", "replace").strip()
            if rest and not rest.startswith("#"):
                raise CodeRefused(EXTRACT_REFUSED, f"line {statements[last].end_lineno} holds more than the statements "
                                                   "asked for")
            return path, statements, first, last
        for i in touching:
            if i in inside:
                continue
            around = _first_line(statements[i]) <= start_line and statements[i].end_lineno >= end_line
            if not (around and _nested_touch(statements[i], start_line, end_line)):
                cut_at = cut_at or _first_line(statements[i])
    if cut_at is not None:
        raise CodeRefused(EXTRACT_REFUSED, f"the lines cut the statement at line {cut_at}; give the lines of whole "
                                           "statements")
    raise CodeRefused(EXTRACT_REFUSED, f"lines {start_line} to {end_line} hold no whole statement")


def extract(source: str, tree: ast.Module, scopes: Scopes, start_line: int, end_line: int,
            name: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """The edited source, its changes, the checks it passed, and the parameters and returns it chose."""
    text = scopes.text
    if any(o.name == name for o in scopes.occurrences) or name in scopes.module.bindings:
        raise CodeRefused(EXTRACT_REFUSED, f"{name!r} is already a name in the source; choose another")
    if scopes.unsupported:
        what, at = scopes.unsupported[0]
        raise CodeRefused(EXTRACT_REFUSED, f"the source has {what} (line {at}), whose scopes this does not read")
    path, statements, first, last = _select(tree, text, start_line, end_line)
    moved = statements[first:last + 1]
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    owner: ast.AST | None = parents.get(id(moved[0]))
    while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        owner = parents.get(id(owner))
    if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise CodeRefused(EXTRACT_REFUSED, "extract works on statements inside a function body; these are not in one")
    function = next(s for s in scopes.scopes() if s.kind == "function" and s.node is owner)
    said = _why_not_movable(moved)
    if said:
        raise CodeRefused(EXTRACT_REFUSED, f"the lines hold {said}, which cannot leave their function")
    params, returns = _boundary(scopes, function, moved)
    return _edit(source, text, tree, function, path, statements, first, last, name, params, returns)


def _boundary(scopes: Scopes, function: Scope, moved: list[ast.stmt]) -> tuple[list[str], list[str]]:
    """The new function's parameters and the values it returns (see the module docstring)."""
    moved_ids = {id(n) for s in moved for n in ast.walk(s)}
    external = function.declared_global | function.declared_nonlocal
    writes = {o.name for o in scopes.occurrences if id(o.node) in moved_ids
              and o.scope is function and o.kind in _BINDS and o.name in external}
    if writes:
        raise CodeRefused(EXTRACT_REFUSED, f"{', '.join(sorted(writes))} is written using a global/nonlocal "
                                           "declaration outside the selection")
    outer = {s for s in scopes.scopes() if s.kind in ("function", "lambda") and function.within(s)}
    by_statement: list[tuple[ast.stmt, set[int]]] = [(s, {id(n) for n in ast.walk(s)}) for s in moved]
    inside, outside = [], []
    for o in scopes.occurrences:
        found = scopes.resolve(o)
        if id(o.node) in moved_ids:
            if found in outer:
                inside.append((o, found))
        elif o.scope.within(function) and found is function:
            outside.append(o)
    bound_elsewhere = {o.name for o in outside if o.kind in (*_BINDS, "param")}
    captured = {o.name for o in outside if o.scope is not function}
    bound_here = {o.name for o, found in inside if o.kind in _BINDS and o.scope is function}
    if bound_here & captured:
        raise CodeRefused(EXTRACT_REFUSED, f"{', '.join(sorted(bound_here & captured))} is bound in the lines and read "
                                           "by a nested function outside them, which would then see the old value")
    decided: dict[str, str] = {}
    definite: set[str] = set()
    for statement, ids in by_statement:
        here = [(o, found) for o, found in inside if id(o.node) in ids]
        loads = {o.name for o, _ in here if o.kind == "load"}
        loads |= {n.target.id for n in ast.walk(statement)  # x += 1 reads x first
                  if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)}
        stores = {o.name for o, _ in here if o.kind in _BINDS and o.scope is function}
        whole = isinstance(statement, (ast.Assign, ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)) or (isinstance(statement, ast.AnnAssign)
                                                          and statement.value is not None)
        for o, found in here:
            n = o.name
            if n in decided:
                continue
            if n in loads:
                decided[n] = "param" if (found is not function or n in bound_elsewhere) else "unbound"
            elif n in stores and whole:
                decided[n] = "internal"
            elif n in stores:
                decided[n] = "param" if n in bound_elsewhere else "internal"
        if whole:
            definite |= stores
    params = sorted(n for n, how in decided.items() if how == "param")
    read_after = {o.name for o in outside if o.kind == "load"}
    returns = sorted(bound_here & read_after)
    partial = [n for n in returns if n not in definite and n not in params]
    if partial:
        raise CodeRefused(EXTRACT_REFUSED, f"{', '.join(partial)} is bound only on some paths through the lines and read "
                                           "after them, so the new function could not always return it")
    return params, returns


def _edit(source: str, text: Text, tree: ast.Module, function: Scope, path: tuple, statements: list[ast.stmt],
          first: int, last: int, name: str, params: list[str],
          returns: list[str]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    moved = statements[first:last + 1]
    start_line, end_line = _first_line(moved[0]), moved[-1].end_lineno
    head = text.line(start_line)
    indent = head[:len(head) - len(head.lstrip())]
    if "\t" in indent:
        raise CodeRefused(EXTRACT_REFUSED, "the lines are indented with tabs; this works with spaces")
    held = _held_lines(source)
    body = []
    for lineno in range(start_line, end_line + 1):
        line = text.line(lineno)
        if lineno in held:
            body.append(line)
        elif not line.strip():
            body.append("")
        elif not line.startswith(indent):
            raise CodeRefused(EXTRACT_REFUSED, f"line {lineno} is indented less than the first line")
        else:
            body.append("    " + line[len(indent):])
    call = f"{name}({', '.join(params)})"
    if returns:
        body.append(f"    return {', '.join(returns)}")
        call = f"{', '.join(returns)} = {call}"
    newline = "\r\n" if "\r\n" in source else "\n"
    new_function = newline.join([f"def {name}({', '.join(params)}):", *body]) + newline
    top = next(s for s in tree.body if any(n is function.node for n in ast.walk(s)))
    start = text.starts[start_line - 1]
    end = text.starts[end_line] if end_line < len(text.starts) else len(source)
    region = source[start:end]
    ending = region[len(region.rstrip("\r\n")):] or newline
    after_top = text.starts[top.end_lineno] if top.end_lineno < len(text.starts) else len(source)
    lead = "" if source[:after_top].endswith(("\n", "\r")) else newline
    spans = [(start, end, f"{indent}{call}{ending}"), (after_top, after_top, f"{lead}{newline}{newline}{new_function}")]
    out, at = [], 0
    for begin, finish, replacement in sorted(spans):
        out += [source[at:begin], replacement]
        at = finish
    edited = "".join([*out, source[at:]])
    try:
        got = ast.parse(edited)
        compile(edited, "<extract_function>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        raise CodeRefused(EDIT_UNCHECKED, f"the edited text does not compile ({exc}), so it is not returned") from None
    checks = [{"check": "parses_and_compiles", "passed": True, "would_have_caught": "a change that breaks the syntax"}]
    made = next((s for s in got.body if isinstance(s, ast.FunctionDef) and s.name == name), None)
    kept = made.body[:len(moved)] if made is not None else []
    if made is None or [ast.dump(s) for s in kept] != [ast.dump(s) for s in moved]:
        raise CodeRefused(EDIT_UNCHECKED, "the new function's statements are not the moved ones, so the edit is not "
                                          "returned")
    checks.append({"check": "moved_statements_unchanged", "passed": True,
                   "would_have_caught": "a statement changed or lost on the way into the new function"})
    expected = [*statements[:first], ast.parse(call).body[0], *statements[last + 1:]]
    if [ast.dump(s) for s in follow(got, path)] != [ast.dump(s) for s in expected]:
        raise CodeRefused(EDIT_UNCHECKED, "the enclosing block is not the old one with the call in place of the moved "
                                          "statements, so the edit is not returned")
    checks.append({"check": "call_replaces_the_statements", "passed": True,
                   "would_have_caught": "the call in the wrong place, or other statements changed"})
    changes = [{"lines": [start_line, end_line], "replaced_by": f"{indent}{call}"},
               {"inserted_after_line": top.end_lineno, "function": new_function.rstrip()}]
    return edited, changes, checks, {"params": params, "returns": returns, "from_function": function.describe()}


__all__ = ["EDIT_UNCHECKED", "EXTRACT_REFUSED", "extract", "follow"]
