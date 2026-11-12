# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The restricted SMT relation grammar of fragment ``smt.relation.int_bool.v1``, and its reader.

One frozen dataclass per constructor (``CONSTRUCTORS``): whole numbers and truth values, declared symbols of sort
``Int`` or ``Bool``, linear and non-linear integer arithmetic, ``div`` and ``mod`` by a positive whole-number
literal, comparisons, the Boolean connectives and ``ite``. Nothing else is in the fragment: a quantifier, a ``let``,
a string, an uninterpreted function or any other operator raises ``OutsideFragment`` naming the construct.

The reader is stricter than Z3 on purpose. Z3 coerces a Bool used as an Int; here every operator's argument sorts
are checked, so text the reader takes means to Z3 what it means here. ``write`` gives the one canonical text of a
term; a statement's text is that canonical text, so what is shown, digested and given to the engine are the same
bytes. ``div`` and ``mod`` take only a positive literal divisor because SMT-LIB and Python agree there and nowhere
else (a negative divisor rounds differently, a zero divisor has no agreed value).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SORTS = ("Int", "Bool")
MAX_CHARS = 10_000
MAX_DEPTH = 100


class OutsideFragment(ValueError):
    """The text is outside the supported grammar; the message names the construct."""


@dataclass(frozen=True)
class IntLit:
    value: int


@dataclass(frozen=True)
class BoolLit:
    value: bool


@dataclass(frozen=True)
class Var:
    name: str
    sort: str


@dataclass(frozen=True)
class Neg:
    arg: Any


@dataclass(frozen=True)
class Arith:
    op: str            # "+", "-", "*"
    args: tuple


@dataclass(frozen=True)
class DivMod:
    op: str            # "div", "mod"
    left: Any
    right: IntLit      # a positive literal


@dataclass(frozen=True)
class Compare:
    op: str            # "=", "distinct", "<", "<=", ">", ">="
    left: Any
    right: Any


@dataclass(frozen=True)
class Not:
    arg: Any


@dataclass(frozen=True)
class And:
    args: tuple


@dataclass(frozen=True)
class Or:
    args: tuple


@dataclass(frozen=True)
class Implies:
    left: Any
    right: Any


@dataclass(frozen=True)
class Ite:
    cond: Any
    then: Any
    orelse: Any


Term = IntLit | BoolLit | Var | Neg | Arith | DivMod | Compare | Not | And | Or | Implies | Ite
#: Every constructor of the grammar: the inventory a renderer, an evaluator or a mutator must cover.
CONSTRUCTORS: tuple[type, ...] = (IntLit, BoolLit, Var, Neg, Arith, DivMod, Compare, Not, And, Or, Implies, Ite)

_ARITH = ("+", "-", "*")
_ORDER = ("<", "<=", ">", ">=")
_OPERATORS = frozenset({*_ARITH, *_ORDER, "=", "distinct", "div", "mod", "not", "and", "or", "=>", "ite"})
_RESERVED = _OPERATORS | {"true", "false", "assert", "let", "forall", "exists", "xor", "result__other"}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_NUMERAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_ALLOWED = re.compile(r"[A-Za-z0-9_+\-*<>=()\s]")
_TOKEN = re.compile(r"\(|\)|[^\s()]+")


def check_sorts(sorts: Mapping[str, str]) -> dict[str, str]:
    """The declared symbols, each a plain name with sort ``Int`` or ``Bool``; anything else is outside the fragment."""
    out = {}
    for name, sort in sorts.items():
        if type(name) is not str or not _NAME.match(name) or name in _RESERVED:
            raise OutsideFragment(f"the symbol name {name!r} (a plain name that is not an operator)")
        if sort not in SORTS:
            raise OutsideFragment(f"the sort {sort!r} of {name!r} (this fragment has Int and Bool)")
        out[name] = sort
    return out


def sort_of(term: Term) -> str:
    if isinstance(term, IntLit | Neg | Arith | DivMod):
        return "Int"
    if isinstance(term, Var):
        return term.sort
    if isinstance(term, Ite):
        return sort_of(term.then)
    return "Bool"


def _tree(text: str) -> Any:
    if type(text) is not str or not text.strip():
        raise OutsideFragment("an empty statement")
    if len(text) > MAX_CHARS:
        raise OutsideFragment(f"a statement longer than {MAX_CHARS} characters")
    for ch in text:
        if not _ALLOWED.match(ch):
            raise OutsideFragment("a string literal" if ch == '"' else f"the character {ch!r}")
    stack: list[list] = [[]]
    for token in _TOKEN.findall(text):
        if token == "(":
            if len(stack) > MAX_DEPTH:
                raise OutsideFragment(f"nesting deeper than {MAX_DEPTH}")
            stack.append([])
        elif token == ")":
            if len(stack) == 1:
                raise OutsideFragment("an unmatched ')'")
            done = stack.pop()
            stack[-1].append(tuple(done))
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise OutsideFragment("an unclosed '('")
    if len(stack[0]) != 1:
        raise OutsideFragment(f"{len(stack[0])} top-level items where one term is needed")
    return stack[0][0]


def _need(term: Term, sort: str, op: str) -> Term:
    if sort_of(term) != sort:
        raise OutsideFragment(f"{op!r} applied to a {sort_of(term)} where an {sort} is needed" if sort == "Int" else
                              f"{op!r} applied to an {sort_of(term)} where a {sort} is needed")
    return term


def _build(node: Any, sorts: Mapping[str, str]) -> Term:
    if isinstance(node, str):
        if node in ("true", "false"):
            return BoolLit(node == "true")
        if _NUMERAL.match(node):
            return IntLit(int(node))
        if node in sorts:
            return Var(node, sorts[node])
        if _NAME.match(node) and node not in _RESERVED:
            raise OutsideFragment(f"the undeclared symbol {node!r}")
        raise OutsideFragment(f"the token {node[:40]!r}")
    if not node:
        raise OutsideFragment("an empty application '()'")
    head = node[0]
    if not isinstance(head, str):
        raise OutsideFragment("an application whose head is not an operator")
    if head in ("forall", "exists"):
        raise OutsideFragment(f"the quantifier {head!r}")
    if head == "let":
        raise OutsideFragment("a let binding")
    if head not in _OPERATORS:
        raise OutsideFragment(f"the uninterpreted function or unsupported operator {head!r}")
    args = [_build(child, sorts) for child in node[1:]]
    count = len(args)

    def arity(low: int, high: int | None = None) -> None:
        if count < low or (count > (low if high is None else high)):
            raise OutsideFragment(f"{head!r} with {count} argument(s)")

    if head == "-" and count == 1:
        return Neg(_need(args[0], "Int", head))
    if head in _ARITH:
        arity(2, 64)
        return Arith(head, tuple(_need(a, "Int", head) for a in args))
    if head in ("div", "mod"):
        arity(2)
        if not isinstance(args[1], IntLit) or args[1].value <= 0:
            raise OutsideFragment(f"{head!r} by anything but a positive whole-number literal")
        return DivMod(head, _need(args[0], "Int", head), args[1])
    if head in _ORDER:
        arity(2)
        return Compare(head, _need(args[0], "Int", head), _need(args[1], "Int", head))
    if head in ("=", "distinct"):
        arity(2)
        if sort_of(args[0]) != sort_of(args[1]):
            raise OutsideFragment(f"{head!r} between an Int and a Bool")
        return Compare(head, args[0], args[1])
    if head == "not":
        arity(1)
        return Not(_need(args[0], "Bool", head))
    if head in ("and", "or"):
        arity(2, 64)
        made = tuple(_need(a, "Bool", head) for a in args)
        return And(made) if head == "and" else Or(made)
    if head == "=>":
        arity(2)
        return Implies(_need(args[0], "Bool", head), _need(args[1], "Bool", head))
    arity(3)   # ite
    if sort_of(args[1]) != sort_of(args[2]):
        raise OutsideFragment("'ite' whose branches are an Int and a Bool")
    return Ite(_need(args[0], "Bool", head), args[1], args[2])


def read(text: str, sorts: Mapping[str, str]) -> Term:
    """The term ``text`` holds over the declared ``sorts``, or ``OutsideFragment`` naming the construct. A bare
    ``(assert t)`` is read as ``t``. Reading decides nothing: only an engine query is a result."""
    declared = check_sorts(sorts)
    node = _tree(text)
    if isinstance(node, tuple) and len(node) == 2 and node[0] == "assert":
        node = node[1]
    return _build(node, declared)


def write(term: Term) -> str:
    """The canonical SMT-LIB text of a term."""
    if isinstance(term, IntLit):
        return str(term.value) if term.value >= 0 else f"(- {-term.value})"
    if isinstance(term, BoolLit):
        return "true" if term.value else "false"
    if isinstance(term, Var):
        return term.name
    if isinstance(term, Neg):
        return f"(- {write(term.arg)})"
    if isinstance(term, Arith):
        return f"({term.op} " + " ".join(write(a) for a in term.args) + ")"
    if isinstance(term, DivMod | Compare):
        return f"({term.op} {write(term.left)} {write(term.right)})"
    if isinstance(term, Not):
        return f"(not {write(term.arg)})"
    if isinstance(term, And | Or):
        return ("(and " if isinstance(term, And) else "(or ") + " ".join(write(a) for a in term.args) + ")"
    if isinstance(term, Implies):
        return f"(=> {write(term.left)} {write(term.right)})"
    if isinstance(term, Ite):
        return f"(ite {write(term.cond)} {write(term.then)} {write(term.orelse)})"
    raise OutsideFragment(f"a {type(term).__name__} is not a term of this grammar")


def canonical(text: str, sorts: Mapping[str, str]) -> str:
    return write(read(text, sorts))


def children(term: Term) -> tuple:
    if isinstance(term, IntLit | BoolLit | Var):
        return ()
    if isinstance(term, Neg | Not):
        return (term.arg,)
    if isinstance(term, Arith | And | Or):
        return term.args
    if isinstance(term, DivMod | Compare | Implies):
        return (term.left, term.right)
    return (term.cond, term.then, term.orelse)


def rebuilt(term: Term, parts: tuple) -> Term:
    """``term`` with its children replaced by ``parts``, in ``children`` order."""
    if isinstance(term, IntLit | BoolLit | Var):
        return term
    if isinstance(term, Neg | Not):
        return type(term)(parts[0])
    if isinstance(term, Arith):
        return Arith(term.op, tuple(parts))
    if isinstance(term, And | Or):
        return type(term)(tuple(parts))
    if isinstance(term, DivMod | Compare):
        return type(term)(term.op, parts[0], parts[1])
    if isinstance(term, Implies):
        return Implies(parts[0], parts[1])
    return Ite(parts[0], parts[1], parts[2])


def substitute(term: Term, name: str, replacement: Term) -> Term:
    """``term`` with every occurrence of the symbol ``name`` replaced; the replacement has the symbol's sort."""
    if isinstance(term, Var):
        if term.name != name:
            return term
        if sort_of(replacement) != term.sort:
            raise OutsideFragment(f"a {sort_of(replacement)} put where {name!r} is a {term.sort}")
        return replacement
    return rebuilt(term, tuple(substitute(child, name, replacement) for child in children(term)))


def symbols(term: Term) -> frozenset[str]:
    if isinstance(term, Var):
        return frozenset({term.name})
    return frozenset().union(*(symbols(child) for child in children(term))) if children(term) else frozenset()


def guards(term: Term) -> tuple[Term, ...]:
    """Every guard of the term, outermost first: each antecedent of ``=>`` and each condition of ``ite``."""
    own = (term.left,) if isinstance(term, Implies) else (term.cond,) if isinstance(term, Ite) else ()
    return own + tuple(g for child in children(term) for g in guards(child))


def evaluate(term: Term, values: Mapping[str, Any]) -> Any:
    """The term's value under ``values`` (every symbol given, an Int as ``int``, a Bool as ``bool``), with SMT-LIB's
    meaning; ``div`` and ``mod`` have a positive divisor, where Python's ``//`` and ``%`` agree with it."""
    if isinstance(term, IntLit | BoolLit):
        return term.value
    if isinstance(term, Var):
        value = values[term.name]
        if type(value) is not (int if term.sort == "Int" else bool):
            raise OutsideFragment(f"the value {value!r} given for {term.name!r}, which is a {term.sort}")
        return value
    parts = [evaluate(child, values) for child in children(term)]
    if isinstance(term, Neg):
        return -parts[0]
    if isinstance(term, Arith):
        out = parts[0]
        for value in parts[1:]:
            out = out + value if term.op == "+" else out - value if term.op == "-" else out * value
        return out
    if isinstance(term, DivMod):
        return parts[0] // parts[1] if term.op == "div" else parts[0] % parts[1]
    if isinstance(term, Compare):
        left, right = parts
        return {"=": left == right, "distinct": left != right, "<": left < right, "<=": left <= right,
                ">": left > right, ">=": left >= right}[term.op]
    if isinstance(term, Not):
        return not parts[0]
    if isinstance(term, And):
        return all(parts)
    if isinstance(term, Or):
        return any(parts)
    if isinstance(term, Implies):
        return (not parts[0]) or parts[1]
    return parts[1] if parts[0] else parts[2]


__all__ = ["CONSTRUCTORS", "SORTS", "And", "Arith", "BoolLit", "Compare", "DivMod", "Implies", "IntLit", "Ite", "Neg",
           "Not", "Or", "OutsideFragment", "Term", "Var", "canonical", "check_sorts", "children", "evaluate", "guards",
           "read", "rebuilt", "sort_of", "substitute", "symbols", "write"]
