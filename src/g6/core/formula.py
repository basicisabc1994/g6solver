# SPDX-License-Identifier: Apache-2.0
"""G6's own expression parser, read into a tree of its own under a :class:`Dialect`. Nothing in it is evaluated.

Caller text reaches nothing but this parser: a family builds what it needs from the tree (``g6.algebra`` builds SymPy
objects through SymPy's constructors; ``g6.jobs`` evaluates a formula entry's steps with plain arithmetic). It imports
only the standard library, so reading a formula loads neither SymPy nor any family. A dialect fixes its functions and
their numbers of arguments, its constants, whether comparisons, ``min`` and ``max``, ``piecewise`` and the declared
rounding functions are allowed, the names it refuses outright, and the prefix of its codes. The language, strictly:

* numbers: integers (``42``) and decimals (``2.5``, read as the exact rational 5/2); a rational is a division
  (``3/4``). At most 200 digits each; no scientific notation (write ``1.5*10^3``);
* symbols the caller declares (:func:`declare`): letters and digits, single underscores between them, at most 31
  characters, each with optional assumptions from ``real``, ``positive`` and ``integer``. A name that is a Python
  keyword or built-in, a function or a constant cannot be declared;
* ``+ - * / ^`` (``**`` is the same as ``^``), unary ``+`` and ``-``, and parentheses. ``^`` binds tighter than unary
  minus (``-x^2`` is ``-(x^2)``) and groups to the right (``2^3^2`` is ``2^9``). Every product is written (``2*x``);
* the dialect's functions, called by name with their number of arguments, and its constants; the dialect's infinity
  only where an op takes a point or a bound;
* in an equation, one ``=`` (or ``==``) between two expressions, at the top level (a dialect without comparisons);
* with comparisons: ``<``, ``<=``, ``>``, ``>=`` and ``==``, one per condition, and only as a condition of
  ``piecewise((value, condition), ..., (value, otherwise))``, whose last pair, and only its last, is ``otherwise``.

Everything else is refused, with its code, the field, the line and the column: strings, attribute access (``.``),
brackets, braces, keywords (``lambda``, ``import``), names with leading, trailing or doubled underscores
(``__import__``), comments, semicolons, keyword arguments, ``!``, comparisons outside a condition, and any character
outside printable ASCII (``<prefix>_CONSTRUCT_REFUSED``); an unknown function (``_UNKNOWN_FUNCTION``), an undeclared
name (``_UNKNOWN_SYMBOL``), a wrong number of arguments (``_WRONG_ARGUMENTS``) and a malformed expression
(``_PARSE_ERROR``).

Limits, checked before anything is built, so nothing is ever asked to compute a number without end: at most 2,000
characters, 500 nodes and 40 levels of nesting (``_INPUT_TOO_LARGE``); every exponent at most 1,000 in size, judged on
each part of the exponent that has no symbol, so ``2^(10^10)``, ``x^(10^10)`` and ``2^(x + 10^10)`` are all refused,
and an exponent G6 cannot bound (``2^(1/sin(1))``) is refused too (``_EXPONENT_TOO_LARGE``); factorial and binomial
arguments at most 1,000 (``_FACTORIAL_TOO_LARGE``); exact numbers of at most 10,000 digits, and ``floor`` and
``ceiling`` only of numbers G6 can bound to that size (``_NUMBER_TOO_LARGE``). The bounds are worked out on the tree
from upper bounds of each part's size, never by evaluating it.
"""
from __future__ import annotations

import builtins
import keyword
import math
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

MAX_CHARS = 2000
MAX_NODES = 500
MAX_DEPTH = 40
MAX_LITERAL_DIGITS = 200
MAX_EXPONENT = 1000
MAX_FACTORIAL = 1000
MAX_DIGITS = 10_000
MAX_BITS = math.ceil(MAX_DIGITS * math.log2(10))
MAX_SYMBOLS = 20
MAX_NAME = 31

ASSUMPTIONS = ("integer", "positive", "real")
#: ``min`` and ``max`` (of two to eight values), added by a dialect with ``min_max``.
MIN_MAX: dict[str, tuple[int, ...]] = {"min": (2, 3, 4, 5, 6, 7, 8), "max": (2, 3, 4, 5, 6, 7, 8)}
#: The declared rounding functions, added by a dialect with ``rounding``: ``round_half_up(x, digits)`` rounds to
#: ``digits`` decimal places, a half going away from zero; ``ceil_to(x, step)`` is the smallest multiple of ``step``
#: not below ``x``.
ROUNDING: dict[str, tuple[int, ...]] = {"round_half_up": (2,), "ceil_to": (2,)}
PIECEWISE = "piecewise"
OTHERWISE = "otherwise"
COMPARISONS = ("<", "<=", ">", ">=", "==")
#: The reasons every code is built from: ``<prefix>_<REASON>``.
REASONS = ("PARSE_ERROR", "CONSTRUCT_REFUSED", "UNKNOWN_FUNCTION", "UNKNOWN_SYMBOL", "WRONG_ARGUMENTS",
           "SYMBOL_INVALID", "EXPONENT_TOO_LARGE", "FACTORIAL_TOO_LARGE", "NUMBER_TOO_LARGE", "INPUT_INVALID",
           "INPUT_TOO_LARGE")


class FormulaRefused(Exception):
    """Text the parser will not read, with its stable code, the reason in words and any fields to keep."""

    def __init__(self, code: str, reason: str, **keep: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.keep = keep


@dataclass(frozen=True)
class Dialect:
    """What one language allows. ``functions`` maps each name to the numbers of arguments it takes; ``constants`` are
    names read as constants; ``infinity`` is the name read as infinity where an op allows it (``None``: none);
    ``reserved`` are further names no symbol may take; ``refused_names`` are names refused outright, each with its
    reason; ``refusal`` is the exception raised (it takes the code, the reason and the fields to keep)."""

    name: str
    prefix: str
    functions: Mapping[str, tuple[int, ...]]
    constants: tuple[str, ...] = ()
    infinity: str | None = None
    comparisons: bool = False
    min_max: bool = False
    piecewise: bool = False
    rounding: bool = False
    reserved: frozenset[str] = frozenset()
    refused_names: Mapping[str, str] = field(default_factory=dict)
    refusal: type[Exception] = FormulaRefused

    def code(self, reason: str) -> str:
        return f"{self.prefix}_{reason}"

    def all_functions(self) -> dict[str, tuple[int, ...]]:
        """Every function a call may name, the dialect's own and those its switches add."""
        found = dict(self.functions)
        if self.min_max:
            found.update(MIN_MAX)
        if self.rounding:
            found.update(ROUNDING)
        if self.piecewise:
            found[PIECEWISE] = (2,)  # read by the pairs' own grammar, never by this arity
        return found

    def names_reserved(self) -> frozenset[str]:
        extra = ({self.infinity} if self.infinity else set()) | ({OTHERWISE} if self.piecewise else set())
        return (frozenset(keyword.kwlist) | frozenset(keyword.softkwlist) | frozenset(dir(builtins))
                | frozenset(self.all_functions()) | frozenset(self.constants) | frozenset(self.refused_names)
                | frozenset(extra) | self.reserved)


_GOOD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*\Z")
_NAME_RUN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_SCIENTIFIC = re.compile(r"[eE][0-9]+\Z")
_PUNCT = {"+": "+", "-": "-", "*": "*", "/": "/", "^": "^", "(": "(", ")": ")", ",": ","}
_REFUSED_CHARS = {
    "'": "strings are not part of the language", '"': "strings are not part of the language",
    "[": "square brackets (lists and subscripts) are not part of the language",
    "]": "square brackets (lists and subscripts) are not part of the language",
    "{": "braces (sets and dictionaries) are not part of the language",
    "}": "braces (sets and dictionaries) are not part of the language",
    ".": "a point outside a number would be attribute access, which is not part of the language (write decimals as "
         "0.5)",
    ";": "a semicolon would start another statement: write one expression",
    "#": "comments are not part of the language",
    "!": "write factorial(n), not n!",
    "<": "comparisons are not part of the language", ">": "comparisons are not part of the language",
    ":": "colons (lambda, slices, annotations) are not part of the language",
    "\\": "backslashes are not part of the language",
}
_LOG2E = math.log2(math.e)
_LOG2_EXPONENT = math.log2(MAX_EXPONENT)
_LOG2_FACTORIAL = math.log2(MAX_FACTORIAL)
_INF = math.inf


# --- the tree ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Num:
    """An exact number (an integer, or a decimal read as a rational)."""

    value: Fraction
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Sym:
    """A declared symbol."""

    name: str
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Const:
    """A dialect's constant (``pi``, ``E``, ``I``), or its infinity."""

    name: str
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Neg:
    operand: Node
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class BinOp:
    """``op`` is one of ``+ - * / ^``; the position is the operator's."""

    op: str
    left: Node
    right: Node
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[Node, ...]
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Equation:
    left: Node
    right: Node
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Compare:
    """One comparison (``op`` one of :data:`COMPARISONS`), only ever a piecewise pair's condition."""

    op: str
    left: Node
    right: Node
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Pair:
    """One ``(value, condition)`` of a piecewise; ``cond`` is ``None`` for ``otherwise``."""

    value: Node
    cond: Compare | None
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


Node = Num | Sym | Const | Neg | BinOp | Call | Equation | Compare | Pair


def children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, Neg):
        return (node.operand,)
    if isinstance(node, BinOp | Equation | Compare):
        return (node.left, node.right)
    if isinstance(node, Call):
        return node.args
    if isinstance(node, Pair):
        return (node.value,) if node.cond is None else (node.value, node.cond)
    return ()


def free_symbols(node: Node) -> frozenset[str]:
    """The names of the symbols ``node`` uses."""
    if isinstance(node, Sym):
        return frozenset({node.name})
    found: set[str] = set()
    for child in children(node):
        found |= free_symbols(child)
    return frozenset(found)


# --- declaring symbols ---------------------------------------------------------------------------------------------

def _symbol_name(name: Any, dialect: Dialect) -> str:
    invalid = dialect.code("SYMBOL_INVALID")
    if not isinstance(name, str) or not name:
        raise dialect.refusal(invalid, f"a symbol is named by a non-empty string, not {name!r}")
    if len(name) > MAX_NAME or not _GOOD_NAME.match(name):
        raise dialect.refusal(invalid, f"{name[:40]!r} is not a symbol name: letters and digits, single underscores "
                                       f"between them, starting with a letter, at most {MAX_NAME} characters")
    if name in dialect.names_reserved():
        raise dialect.refusal(invalid, f"{name} cannot name a symbol: it is a function, a constant, or a Python "
                                       "keyword or built-in")
    return name


def declare(symbols: Any, dialect: Dialect, implicit: Iterable[str] = ()) -> dict[str, tuple[str, ...]]:
    """The symbols an op may use, each with its assumptions: ``symbols`` is a list of names or a mapping from each name
    to a list drawn from ``real``, ``positive`` and ``integer``; ``implicit`` are names the op itself declares (its
    variable or unknowns), added without assumptions when ``symbols`` does not list them."""
    invalid = dialect.code("SYMBOL_INVALID")
    if symbols is None:
        items: list[tuple[Any, Any]] = []
    elif isinstance(symbols, Mapping):
        items = list(symbols.items())
    elif isinstance(symbols, list | tuple):
        items = [(name, ()) for name in symbols]
    else:
        raise dialect.refusal(invalid, "symbols is a list of names, or a mapping from each name to its assumptions")
    declared: dict[str, tuple[str, ...]] = {}
    for name, assumed in items:
        name = _symbol_name(name, dialect)
        if not isinstance(assumed, list | tuple) or not all(isinstance(a, str) for a in assumed):
            raise dialect.refusal(invalid, f"the assumptions of {name} are a list drawn from {', '.join(ASSUMPTIONS)}")
        unknown = sorted(set(assumed) - set(ASSUMPTIONS))
        if unknown:
            raise dialect.refusal(invalid, f"{name}: {', '.join(unknown)[:80]} is not an assumption G6 takes; use "
                                           f"{', '.join(ASSUMPTIONS)}")
        declared[name] = tuple(sorted(set(assumed)))
    for name in implicit:
        declared.setdefault(_symbol_name(name, dialect), ())
    if len(declared) > MAX_SYMBOLS:
        raise dialect.refusal(dialect.code("INPUT_TOO_LARGE"),
                              f"{len(declared)} symbols are declared; the limit is {MAX_SYMBOLS}")
    return declared


# --- reading -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Token:
    kind: str          # "num", "name", "op", "cmp", "end"
    text: str
    line: int
    column: int


def _refuse(dialect: Dialect, reason_code: str, reason: str, where: str, line: int, column: int) -> Exception:
    code = dialect.code(reason_code)
    said = f"{where}, line {line}, column {column}: {reason}"
    return dialect.refusal(code, said, refusals=[{"code": code, "reason": said, "field": where, "line": line,
                                                  "column": column}])


def _tokens(text: str, where: str, dialect: Dialect) -> list[_Token]:
    found: list[_Token] = []
    i, line, start = 0, 1, 0
    while i < len(text):
        ch, column = text[i], i - start + 1
        if ch == "\n":
            i, line, start = i + 1, line + 1, i + 1
        elif ch in " \t\r":
            i += 1
        elif ch.isascii() and ch.isdigit():
            match = _NUMBER.match(text, i)
            digits = match.group(0).replace(".", "")
            if len(digits) > MAX_LITERAL_DIGITS:
                raise _refuse(dialect, "INPUT_TOO_LARGE",
                              f"a number has {len(digits)} digits; the limit is {MAX_LITERAL_DIGITS}", where, line,
                              column)
            found.append(_Token("num", match.group(0), line, column))
            i = match.end()
        elif ch.isascii() and (ch.isalpha() or ch == "_"):
            run = _NAME_RUN.match(text, i).group(0)
            if keyword.iskeyword(run) or keyword.issoftkeyword(run):
                raise _refuse(dialect, "CONSTRUCT_REFUSED", f"{run} is a Python keyword, not part of the language",
                              where, line, column)
            if not _GOOD_NAME.match(run):
                raise _refuse(dialect, "CONSTRUCT_REFUSED",
                              f"{run[:40]} is not a name the language reads: letters and digits, with single "
                              "underscores between them, never at the start or the end", where, line, column)
            found.append(_Token("name", run, line, column))
            i += len(run)
        elif ch == "*" and text.startswith("**", i):
            found.append(_Token("op", "^", line, column))
            i += 2
        elif dialect.comparisons and ch in "<>":
            wide = text.startswith("=", i + 1)
            found.append(_Token("cmp", ch + ("=" if wide else ""), line, column))
            i += 2 if wide else 1
        elif dialect.comparisons and ch == "=":
            if not text.startswith("==", i):
                raise _refuse(dialect, "CONSTRUCT_REFUSED", "an equation or an assignment is not part of this "
                                                            "language: write == inside a piecewise condition", where,
                              line, column)
            found.append(_Token("cmp", "==", line, column))
            i += 2
        elif ch == "=":
            found.append(_Token("op", "=", line, column))
            i += 2 if text.startswith("==", i) else 1
        elif ch in _PUNCT:
            found.append(_Token("op", ch, line, column))
            i += 1
        elif ch in _REFUSED_CHARS:
            raise _refuse(dialect, "CONSTRUCT_REFUSED", _REFUSED_CHARS[ch], where, line, column)
        elif not ch.isascii() or not ch.isprintable():
            raise _refuse(dialect, "CONSTRUCT_REFUSED", f"the character U+{ord(ch):04X} is not part of the language: "
                                                        "write the expression in plain ASCII", where, line, column)
        else:
            raise _refuse(dialect, "CONSTRUCT_REFUSED", f"{ch!r} is not an operator of the language: use + - * / ^",
                          where, line, column)
    found.append(_Token("end", "", line, len(text) - start + 1))
    return found


class _Parser:
    def __init__(self, tokens: list[_Token], symbols: Mapping[str, tuple[str, ...]], where: str, equation: bool,
                 infinity: bool, dialect: Dialect) -> None:
        self.tokens, self.i, self.depth, self.nodes, self.calls = tokens, 0, 0, 0, 0
        self.symbols, self.where, self.equation, self.infinity = symbols, where, equation, infinity
        self.dialect, self.functions = dialect, dialect.all_functions()

    @property
    def peek(self) -> _Token:
        return self.tokens[self.i]

    def take(self) -> _Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def error(self, code: str, reason: str, token: _Token) -> Exception:
        return _refuse(self.dialect, code, reason, self.where, token.line, token.column)

    def node(self, made: Node) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise self.error("INPUT_TOO_LARGE", f"the expression has more than {MAX_NODES} parts", self.peek)
        return made

    def deeper(self, token: _Token) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self.error("INPUT_TOO_LARGE", f"the expression nests more than {MAX_DEPTH} levels deep", token)

    def is_op(self, *ops: str) -> bool:
        return self.peek.kind == "op" and self.peek.text in ops

    def parse(self) -> Node:
        tree = self.expr()
        if self.is_op("=") and self.equation:
            sign = self.take()
            right = self.expr()
            tree = self.node(Equation(tree, right, sign.line, sign.column))
        self.after_operand("the end")
        return tree

    def after_operand(self, wanted: str) -> None:
        """The token after a complete operand must be what the caller expects; say what is wrong otherwise."""
        token = self.peek
        if token.kind == "end" and wanted == "the end":
            return
        if token.kind == "op" and token.text == "=":
            why = ("keyword arguments are not part of the language" if wanted.startswith("',' or ')'") else
                   "an equation has one = at its top level" if self.equation else
                   "an equation is accepted only where the op takes equations")
            raise self.error("CONSTRUCT_REFUSED", why, token)
        if token.kind == "cmp":
            raise self.error("CONSTRUCT_REFUSED", "a comparison is accepted only as the condition of a piecewise pair, "
                                                  "one comparison per condition", token)
        if token.kind in ("name", "num") or self.is_op("("):
            previous = self.tokens[self.i - 1]
            if previous.kind == "num" and token.kind == "name" and _SCIENTIFIC.match(token.text):
                raise self.error("PARSE_ERROR", "scientific notation is not part of the language: write 1*10^10, "
                                                "not 1e10", token)
            raise self.error("PARSE_ERROR", f"expected an operator (+ - * / ^) or {wanted} before "
                                            f"{token.text or 'this'}: every product is written, such as 2*x", token)
        if token.kind == "end":
            raise self.error("PARSE_ERROR", f"the expression ends where {wanted} was expected", token)
        raise self.error("PARSE_ERROR", f"expected {wanted} here, not {token.text!r}", token)

    def expr(self) -> Node:
        tree = self.term()
        while self.is_op("+", "-"):
            sign = self.take()
            tree = self.node(BinOp(sign.text, tree, self.term(), sign.line, sign.column))
        return tree

    def term(self) -> Node:
        tree = self.unary()
        while self.is_op("*", "/"):
            sign = self.take()
            tree = self.node(BinOp(sign.text, tree, self.unary(), sign.line, sign.column))
        return tree

    def unary(self) -> Node:
        if self.is_op("+", "-"):
            sign = self.take()
            self.deeper(sign)
            operand = self.unary()
            self.depth -= 1
            return operand if sign.text == "+" else self.node(Neg(operand, sign.line, sign.column))
        return self.power()

    def power(self) -> Node:
        base = self.atom()
        if self.is_op("^"):
            sign = self.take()
            self.deeper(sign)
            exponent = self.unary()
            self.depth -= 1
            return self.node(BinOp("^", base, exponent, sign.line, sign.column))
        return base

    def atom(self) -> Node:
        token = self.peek
        if token.kind == "num":
            self.take()
            return self.node(Num(Fraction(token.text), token.line, token.column))
        if token.kind == "name":
            self.take()
            return self.name(token)
        if self.is_op("("):
            self.take()
            self.deeper(token)
            inner = self.expr()
            if not self.is_op(")"):
                self.after_operand("')'")
            self.take()
            self.depth -= 1
            return inner
        if token.kind == "end":
            raise self.error("PARSE_ERROR", "the expression ends where a number, a name or '(' was expected", token)
        raise self.error("PARSE_ERROR", f"expected a number, a name or '(' here, not {token.text!r}", token)

    def name(self, token: _Token) -> Node:
        name, dialect = token.text, self.dialect
        if self.calls and self.is_op("="):
            raise self.error("CONSTRUCT_REFUSED", "keyword arguments are not part of the language", self.peek)
        if name in dialect.refused_names:
            raise self.error("CONSTRUCT_REFUSED", f"{name}: {dialect.refused_names[name]}", token)
        if dialect.piecewise and name == OTHERWISE:
            raise self.error("CONSTRUCT_REFUSED", f"{OTHERWISE} is accepted only as the condition of a "
                                                  f"{PIECEWISE}'s last pair", token)
        if self.is_op("("):
            if name not in self.functions:
                if name in self.symbols or name in dialect.constants or name == dialect.infinity:
                    raise self.error("PARSE_ERROR", f"{name} is not a function: write {name}*(...) for a product",
                                     self.peek)
                raise self.error("UNKNOWN_FUNCTION", f"{name} is not a function the language has; it has "
                                                     f"{', '.join(sorted(self.functions))}", token)
            if name == PIECEWISE and dialect.piecewise:
                return self.piecewise(token)
            return self.call(token)
        if name in self.functions:
            raise self.error("PARSE_ERROR", f"{name} is a function: call it, such as {name}(x)", token)
        if name in dialect.constants:
            return self.node(Const(name, token.line, token.column))
        if dialect.infinity and name == dialect.infinity:
            if not self.infinity:
                raise self.error("CONSTRUCT_REFUSED", f"{name} (infinity) is accepted only as a limit point or an "
                                                      "integration bound", token)
            return self.node(Const(name, token.line, token.column))
        if name in self.symbols:
            return self.node(Sym(name, token.line, token.column))
        raise self.error("UNKNOWN_SYMBOL", f"{name} is not declared: list it in symbols (G6 reads only the symbols "
                                           "you declare)", token)

    def call(self, token: _Token) -> Node:
        opening = self.take()
        self.deeper(opening)
        self.calls += 1
        args: list[Node] = []
        if not self.is_op(")"):
            while True:
                args.append(self.expr())
                if self.is_op(","):
                    self.take()
                    continue
                if not self.is_op(")"):
                    self.after_operand("',' or ')'")
                break
        self.take()
        self.depth -= 1
        self.calls -= 1
        wanted = self.functions[token.text]
        if len(args) not in wanted:
            counts = " or ".join(str(n) for n in wanted)
            raise self.error("WRONG_ARGUMENTS", f"{token.text} takes {counts} argument{'s' if wanted != (1,) else ''}, "
                                                f"not {len(args)}", token)
        return self.node(Call(token.text, tuple(args), token.line, token.column))

    def piecewise(self, token: _Token) -> Node:
        """``piecewise((value, condition), ..., (value, otherwise))``: at least two pairs, ``otherwise`` last."""
        opening = self.take()
        self.deeper(opening)
        self.calls += 1
        pairs: list[Node] = []
        shape = f"{PIECEWISE} takes pairs (value, condition), the last of them (value, {OTHERWISE})"
        while True:
            if not self.is_op("("):
                raise self.error("PARSE_ERROR", shape, self.peek)
            start = self.take()
            self.deeper(start)
            value = self.expr()
            if not self.is_op(","):
                self.after_operand("','")
            self.take()
            cond: Compare | None = None
            if self.peek.kind == "name" and self.peek.text == OTHERWISE:
                self.take()
            else:
                cond = self.condition()
            if not self.is_op(")"):
                self.after_operand("')'")
            self.take()
            self.depth -= 1
            pairs.append(self.node(Pair(value, cond, start.line, start.column)))
            if cond is None:
                break
            if not self.is_op(","):
                raise self.error("PARSE_ERROR", shape, self.peek)
            self.take()
        if not self.is_op(")"):
            raise self.error("PARSE_ERROR", f"nothing follows (value, {OTHERWISE}) in a {PIECEWISE}", self.peek)
        self.take()
        self.depth -= 1
        self.calls -= 1
        if len(pairs) < 2:
            raise self.error("WRONG_ARGUMENTS", f"{PIECEWISE} needs at least one (value, condition) before "
                                                f"(value, {OTHERWISE})", token)
        return self.node(Call(PIECEWISE, tuple(pairs), token.line, token.column))

    def condition(self) -> Compare:
        left = self.expr()
        if self.peek.kind != "cmp":
            raise self.error("PARSE_ERROR", f"a condition compares two values with {', '.join(COMPARISONS)}, or is "
                                            f"{OTHERWISE}", self.peek)
        sign = self.take()
        right = self.expr()
        if self.peek.kind == "cmp":
            raise self.error("CONSTRUCT_REFUSED", "one comparison per condition", self.peek)
        made = Compare(sign.text, left, right, sign.line, sign.column)
        self.node(made)
        return made


def parse(text: Any, symbols: Mapping[str, tuple[str, ...]], dialect: Dialect, *, field: str = "expression",
          equation: bool = False, infinity: bool = False) -> Node:
    """``text`` read as one expression (or, with ``equation``, an equation or an expression meaning ``= 0``) of
    ``dialect`` over the declared ``symbols``; refused with a code, ``field``, the line and the column otherwise.
    ``infinity`` lets the dialect's infinity stand as a point or a bound. Its size limits are checked before it is
    returned."""
    if not isinstance(text, str):
        raise _refuse(dialect, "INPUT_INVALID", "an expression is text, such as x^2 - 1", field, 1, 1)
    if len(text) > MAX_CHARS:
        raise _refuse(dialect, "INPUT_TOO_LARGE", f"the expression is {len(text)} characters; the limit is "
                                                  f"{MAX_CHARS}", field, 1, 1)
    if not text.strip():
        raise _refuse(dialect, "INPUT_INVALID", "the expression is empty", field, 1, 1)
    tree = _Parser(_tokens(text, field, dialect), symbols, field, equation, infinity, dialect).parse()
    _Bounds(field, dialect).info(tree)
    return tree


# --- bounds: how large each part can be, from the tree alone --------------------------------------------------------

@dataclass(frozen=True)
class _Info:
    """What G6 knows of a part's value without evaluating it. ``lmag`` and ``llow`` bound log2 of its size from above
    and below (``inf`` and ``-inf`` when unknown); ``exact`` is its value when it is plain arithmetic on numbers;
    ``real`` is whether it is surely real. A part with a symbol is ``symbolic``, and its size is not bounded."""

    symbolic: bool
    lmag: float = _INF
    llow: float = -_INF
    exact: Fraction | None = None
    real: bool = False


_SYMBOLIC = _Info(True)
_CONSTANTS = {"pi": _Info(False, math.log2(math.pi) + 1e-9, math.log2(math.pi) - 1e-9, None, True),
              "E": _Info(False, _LOG2E + 1e-9, _LOG2E - 1e-9, None, True),
              "I": _Info(False, 0.0, 0.0, None, False)}


def _log2(value: Fraction) -> float:
    if value == 0:
        return -_INF
    return math.log2(abs(value.numerator)) - math.log2(value.denominator)


def _bits(value: Fraction) -> int:
    return value.numerator.bit_length() + value.denominator.bit_length()


def _size(lmag: float) -> float:
    """``2 ** lmag``, without overflow."""
    return _INF if lmag > 1000 else 2.0 ** lmag


class _Bounds:
    def __init__(self, where: str, dialect: Dialect) -> None:
        self.where, self.dialect = where, dialect
        self.known: dict[int, _Info] = {}

    def refuse(self, code: str, reason: str, node: Node) -> Exception:
        return _refuse(self.dialect, code, reason, self.where, node.line, node.column)

    def info(self, node: Node) -> _Info:
        for child in children(node):
            self.info(child)
        found = self._info(node)
        self.known[id(node)] = found
        return found

    def of(self, node: Node) -> _Info:
        return self.known[id(node)]

    def maximal_free(self, node: Node) -> Iterator[Node]:
        """The largest parts of ``node`` with no symbol in them."""
        if not self.of(node).symbolic:
            yield node
            return
        for child in children(node):
            yield from self.maximal_free(child)

    def bounded_by(self, node: Node, limit: int, log2_limit: float, code: str, what: str) -> None:
        for part in self.maximal_free(node):
            known = self.of(part)
            too_big = (abs(known.exact) > limit if known.exact is not None else not known.lmag <= log2_limit + 1e-12)
            if too_big:
                shown = render(part)
                shown = shown if len(shown) <= 60 else shown[:57] + "..."
                size = ("cannot be bounded by G6" if math.isinf(known.lmag) else
                        f"may be as large as {_size(known.lmag):.3g}")
                raise self.refuse(code, f"{what} {shown} {size}; the limit is {limit}", part)

    def exact_or_refuse(self, value: Fraction, node: Node) -> Fraction:
        if _bits(value) > MAX_BITS:
            raise self.refuse("NUMBER_TOO_LARGE", f"a number here has more than {MAX_DIGITS} digits", node)
        return value

    def _info(self, node: Node) -> _Info:
        if isinstance(node, Num):
            return _Info(False, _log2(node.value), _log2(node.value), node.value, True)
        if isinstance(node, Sym):
            return _SYMBOLIC
        if isinstance(node, Const):
            return _CONSTANTS.get(node.name, _Info(False, _INF, _INF, None, True))
        if isinstance(node, Equation | Compare):
            return _Info(self.of(node.left).symbolic or self.of(node.right).symbolic)
        if isinstance(node, Pair):
            return _Info(any(self.of(child).symbolic for child in children(node)))
        if isinstance(node, Neg):
            inner = self.of(node.operand)
            return inner if inner.exact is None else _Info(False, inner.lmag, inner.llow, -inner.exact, True)
        if isinstance(node, BinOp):
            return self._binop(node)
        return self._call(node)

    def _binop(self, node: BinOp) -> _Info:
        a, b = self.of(node.left), self.of(node.right)
        if node.op == "^":
            self.bounded_by(node.right, MAX_EXPONENT, _LOG2_EXPONENT, "EXPONENT_TOO_LARGE", "the exponent")
        if a.symbolic or b.symbolic:
            return _SYMBOLIC
        real = a.real and b.real
        if node.op in "+-":
            if a.exact is not None and b.exact is not None:
                value = self.exact_or_refuse(a.exact + b.exact if node.op == "+" else a.exact - b.exact, node)
                return _Info(False, _log2(value), _log2(value), value, True)
            return _Info(False, max(a.lmag, b.lmag) + 1, -_INF, None, real)
        if node.op == "*":
            if a.exact is not None and b.exact is not None:
                value = self.exact_or_refuse(a.exact * b.exact, node)
                return _Info(False, _log2(value), _log2(value), value, True)
            return _Info(False, a.lmag + b.lmag, a.llow + b.llow, None, real)
        if node.op == "/":
            if b.exact == 0:
                return _Info(False)
            if a.exact is not None and b.exact is not None:
                value = self.exact_or_refuse(a.exact / b.exact, node)
                return _Info(False, _log2(value), _log2(value), value, True)
            return _Info(False, a.lmag - b.llow, a.llow - b.lmag, None, real)
        return self._power(node, a, b)

    def _power(self, node: BinOp, a: _Info, b: _Info) -> _Info:
        if a.exact is not None and b.exact is not None:
            if a.exact == 0 and b.exact < 0:
                return _Info(False)
            if _bits(a.exact) * abs(b.exact) > MAX_BITS:
                raise self.refuse("NUMBER_TOO_LARGE", f"this power would have more than {MAX_DIGITS} digits", node)
            if b.exact.denominator == 1:
                value = self.exact_or_refuse(a.exact ** b.exact.numerator, node)
                return _Info(False, _log2(value), _log2(value), value, True)
        spread = max(a.lmag, -a.llow, 0.0)
        lmag = 0.0 if b.exact == 0 else _size(b.lmag) * (spread + _LOG2E * math.pi)
        lmag = _INF if math.isnan(lmag) else lmag
        whole = b.exact is not None and b.exact.denominator == 1
        positive = a.exact is not None and a.exact > 0
        return _Info(False, lmag, -lmag, None, (a.real and whole) or (positive and b.real))

    def _call(self, node: Call) -> _Info:
        name, args = node.name, [self.of(a) for a in node.args]
        if name in ("factorial", "binomial"):
            for arg in node.args:
                self.bounded_by(arg, MAX_FACTORIAL, _LOG2_FACTORIAL, "FACTORIAL_TOO_LARGE", f"the argument of {name},")
        if name in ("floor", "ceiling", "ceil") and not args[0].symbolic and not args[0].lmag <= MAX_BITS:
            raise self.refuse("NUMBER_TOO_LARGE", f"{name} of a number G6 cannot bound to {MAX_DIGITS} digits", node)
        if any(a.symbolic for a in args):
            return _SYMBOLIC
        x = args[0]
        real = all(a.real for a in args)
        grows = _size(x.lmag) * _LOG2E          # log2 of e^|x|, which bounds sin, cos, sinh, cosh and exp
        if name in ("sin", "cos"):
            return _Info(False, 0.0 if x.real else grows, -_INF, None, x.real)
        if name in ("sinh", "cosh"):
            return _Info(False, grows, -_INF, None, x.real)
        if name == "exp":
            return _Info(False, grows, -grows, None, x.real)
        if name in ("tanh", "atan"):
            return _Info(False, 1.0 if x.real else _INF, -_INF, None, x.real)
        if name in ("log", "ln", "log10"):
            spread = max(x.lmag, -x.llow)
            return _Info(False, math.log2(spread * math.log(2) + math.pi) if spread > 0 else 2.0, -_INF)
        if name == "sqrt":
            return _Info(False, x.lmag / 2, x.llow / 2)
        if name == "root":
            return _Info(False, max(x.lmag, -x.llow, 0.0) + _LOG2E * math.pi, -_INF)
        if name in ("abs", "conjugate"):
            return _Info(False, x.lmag, x.llow, None, name == "abs" or x.real)
        if name in ("re", "im", "real_part", "imag_part"):
            return _Info(False, x.lmag, -_INF, None, True)
        if name == "sign":
            return _Info(False, 0.0, -_INF, None, x.real)
        if name in ("floor", "ceiling", "ceil"):
            return _Info(False, max(x.lmag, 0.0) + 1, -_INF, None, True)
        if name in ("min", "max"):
            return _Info(False, max(a.lmag for a in args), -_INF, None, real)
        if name == "factorial":
            n = _size(x.lmag) + 1
            return _Info(False, n * math.log2(n) + 1, -_INF, None, real)
        if name == "binomial":
            n = _size(x.lmag) + _size(args[1].lmag) + 2
            return _Info(False, n * math.log2(n), -_INF, None, real)
        if name in ("tan", "cot", "sec", "csc"):
            return _Info(False, _INF, -_INF, None, x.real)
        return _Info(False)


# --- writing it back -----------------------------------------------------------------------------------------------

_PREC = {"+": 1, "-": 1, "*": 2, "/": 2}


def _decimal(value: Fraction) -> str:
    """An integer or a terminating decimal, exactly (the parser makes no other number)."""
    if value.denominator == 1:
        return str(value.numerator)
    places = 0
    while (value * 10 ** places).denominator != 1:
        places += 1
        if places > MAX_LITERAL_DIGITS:
            return f"({value.numerator}/{value.denominator})"
    digits = str(abs(value.numerator * 10 ** places // value.denominator)).rjust(places + 1, "0")
    return f"{'-' if value < 0 else ''}{digits[:-places]}.{digits[-places:]}"


def _prec(node: Node) -> int:
    if isinstance(node, BinOp):
        return 4 if node.op == "^" else _PREC[node.op]
    if isinstance(node, Neg):
        return 3
    if isinstance(node, Equation | Compare):
        return 0
    return 5


def render(node: Node) -> str:
    """``node`` as text in G6's canonical form, which :func:`parse` reads back to the same tree."""
    if isinstance(node, Num):
        return _decimal(node.value)
    if isinstance(node, Sym | Const):
        return node.name
    if isinstance(node, Equation):
        return f"{render(node.left)} = {render(node.right)}"
    if isinstance(node, Compare):
        return f"{render(node.left)} {node.op} {render(node.right)}"
    if isinstance(node, Pair):
        return f"({render(node.value)}, {OTHERWISE if node.cond is None else render(node.cond)})"
    if isinstance(node, Call):
        return f"{node.name}({', '.join(render(a) for a in node.args)})"
    if isinstance(node, Neg):
        inner = render(node.operand)
        return f"-{inner}" if _prec(node.operand) >= 3 else f"-({inner})"
    if node.op == "^":
        base, exponent = render(node.left), render(node.right)
        base = base if _prec(node.left) == 5 and not (isinstance(node.left, Num) and node.left.value.denominator != 1
                                                      ) else f"({base})"
        exponent = exponent if _prec(node.right) >= 3 else f"({exponent})"
        return f"{base}^{exponent}"
    level = _PREC[node.op]
    left, right = render(node.left), render(node.right)
    left = left if _prec(node.left) >= level else f"({left})"
    right = right if _prec(node.right) > level else f"({right})"
    return f"{left} {node.op} {right}" if level == 1 else f"{left}{node.op}{right}"


__all__ = ["ASSUMPTIONS", "BinOp", "COMPARISONS", "Call", "Compare", "Const", "Dialect", "Equation", "FormulaRefused",
           "MAX_BITS", "MAX_CHARS", "MAX_DEPTH", "MAX_DIGITS", "MAX_EXPONENT", "MAX_FACTORIAL", "MAX_LITERAL_DIGITS",
           "MAX_NAME", "MAX_NODES", "MAX_SYMBOLS", "MIN_MAX", "Neg", "Node", "Num", "OTHERWISE", "PIECEWISE", "Pair",
           "REASONS", "ROUNDING", "Sym", "children", "declare", "free_symbols", "parse", "render"]
