# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The renderer ``smt.words.v1`` of fragment ``smt.relation.int_bool.v1``: one render case per grammar constructor.

Every compound term is written with its own operator words and, when nested, in brackets; every symbol is written in
single quotes. So the words follow the term's structure exactly, and two different terms never read the same. The
lines name the function and its inputs, what is observed, the inputs covered (the domain), the assumptions, the
rule with its quantifier, when it raises, each named obligation, every guard, and what the statement does not cover.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from g6.harness.formalisation import smt_adapter, smt_ir
from g6.harness.formalisation.render import Rendering, Unrenderable, finished
from g6.harness.formalisation.statement import FormalStatement
from g6.harness.executable_scenario import SETUP

ID = "smt.words.v1"
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SORT = {"Int": "whole number", "Bool": "true or false value"}
_ARITH = {"+": "plus", "-": "minus", "*": "times"}
_COMPARE = {"=": "equals", "distinct": "differs from", "<": "is less than", "<=": "is at most",
            ">": "is greater than", ">=": "is at least"}


def _inner(term: smt_ir.Term) -> str:
    """A sub-term's words: a compound one in brackets, so the structure is read back exactly."""
    text = words_of(term)
    return text if isinstance(term, smt_ir.IntLit | smt_ir.BoolLit | smt_ir.Var) else f"({text})"


def _literal(term: smt_ir.IntLit) -> str:
    return str(term.value) if term.value >= 0 else f"minus {-term.value}"


#: Constructor -> its words. The inventory test holds this to ``smt_ir.CONSTRUCTORS``.
CASES: dict[type, Callable[[smt_ir.Term], str]] = {
    smt_ir.IntLit: _literal,
    smt_ir.BoolLit: lambda t: "true" if t.value else "false",
    smt_ir.Var: lambda t: f"'{t.name}'",
    smt_ir.Neg: lambda t: f"the negative of {_inner(t.arg)}",
    smt_ir.Arith: lambda t: f" {_ARITH[t.op]} ".join(_inner(a) for a in t.args),
    smt_ir.DivMod: lambda t: (f"{_inner(t.left)} divided by {_inner(t.right)}, rounded down" if t.op == "div" else
                              f"the remainder of {_inner(t.left)} divided by {_inner(t.right)}"),
    smt_ir.Compare: lambda t: f"{_inner(t.left)} {_COMPARE[t.op]} {_inner(t.right)}",
    smt_ir.Not: lambda t: f"it is not the case that {_inner(t.arg)}",
    smt_ir.And: lambda t: " and ".join(_inner(a) for a in t.args),
    smt_ir.Or: lambda t: " or ".join(_inner(a) for a in t.args),
    smt_ir.Implies: lambda t: f"if {_inner(t.left)} then {_inner(t.right)}",
    smt_ir.Ite: lambda t: f"{_inner(t.then)} when {_inner(t.cond)}, otherwise {_inner(t.orelse)}",
}


def words_of(term: smt_ir.Term) -> str:
    """One term in words, by its constructor's case; ``Unrenderable`` for anything outside the inventory."""
    case = CASES.get(type(term))
    if case is None:
        raise Unrenderable(f"no render case for a {type(term).__name__}")
    return case(term)


def exclusions(statement: FormalStatement) -> tuple[str, ...]:
    """What a statement of this fragment never covers, in words, from the statement alone."""
    out = ["effects on arguments or on anything outside the returned value",
           "inputs outside 'Inputs covered', and values that are not whole numbers or true or false"]
    if statement.definition(smt_adapter.RAISES_WHEN) is None:
        out.append("errors raised")
    out.append("how the code computes the result, and whether the code does what this rule says")
    return tuple(out)


def render(statement: FormalStatement) -> Rendering:
    why = smt_adapter.ADAPTER.supports(statement)
    if why:
        raise Unrenderable(why)
    seen = statement.observation
    if not _DOTTED.match(seen.entry_point):
        raise Unrenderable("the entry point is not a dotted function name")
    if seen.error and not _PLAIN.match(seen.error):
        raise Unrenderable("the error is not an exception type's plain name")
    if seen.setup not in ("", SETUP):
        raise Unrenderable("the setup is not G6's own executable-scenario setup")
    inputs = dict(seen.inputs)
    sorts = smt_adapter.ADAPTER.sorts(statement)
    relation = smt_ir.read(statement.text, sorts)
    guard = statement.definition(smt_adapter.RAISES_WHEN)
    listed = "; ".join(words_of(smt_ir.read(t, inputs)) for t in statement.domain)
    assumed = "; ".join(words_of(smt_ir.read(t, inputs)) for t in statement.assumptions)
    guards = "; ".join(dict.fromkeys(words_of(g) for g in smt_ir.guards(relation)))
    params = ", ".join(f"'{name}': {_SORT[sort]}" for name, sort in seen.inputs)
    lines = [
        ("Function", f"{seen.entry_point}({params})"),
        ("Observed", f"the value it returns, called 'result', a {_SORT[seen.result_sort]}"),
        ("Setup", seen.setup or "none stated"),
        ("Inputs covered", listed or "every value of the declared inputs"),
        ("Assuming", assumed or "nothing further"),
        ("Rule", ("for every covered input that does not raise, " if guard is not None else "for every covered input, ")
         + f"every returned 'result' satisfies: {words_of(relation)}"),
        ("Raises", f"{seen.error} exactly when {words_of(smt_ir.read(guard, inputs))}" if guard is not None else
         "not covered by this statement"),
    ]
    for name, text in statement.definitions:
        if name.startswith(smt_adapter.ENTAILS):
            lines.append((f"Also required ({name[len(smt_adapter.ENTAILS):]})", words_of(smt_ir.read(text, sorts))))
    lines += [("Guards", guards or "none"), ("Not covered", "; ".join(exclusions(statement)))]
    return finished(statement, lines, ID)


__all__ = ["CASES", "ID", "exclusions", "render", "words_of"]