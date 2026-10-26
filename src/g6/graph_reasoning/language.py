# SPDX-License-Identifier: Apache-2.0
"""The annotated logic over graphs that g6.graph_reasoning accepts, read by its own parser and checked before anything
is evaluated.

The language, strictly::

    # a graph of nodes and directed edges, each with labels
    node alice, bob, carol, dave, eve.        # nodes (facts and edges declare their nodes too)
    edge alice -> bob, bob -> carol.          // directed edges (an edge fact declares its edge too)
    popular(dave).                            # a fact on a node: true, [1, 1]
    friend(carol, dave) : [0.9, 1].           # a fact on an edge, with its interval
    popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].   # a node rule
    trusts(x, y) <- friend(x, y), popular(y) : [0.7, 1].                     # an edge rule
    score(x) : average <- review(x, y) : [0.1, 1].                            # an annotation function
    hub(x) : [0.6, 1] <- friend(x, y), popular(y) at least 2.                 # a threshold: a count
    wide(x) <- friend(x, y), popular(y) at least 50%.                         # a threshold: a percentage

* Every label carries a truth interval ``[lower, upper]`` within ``[0, 1]``: ``[1, 1]`` is true, ``[0, 0]`` false and
  ``[0, 1]`` unknown. A label is a node label (one argument) or an edge label (two arguments, source then target), and
  keeps one arity everywhere. Numbers are exact decimals (``0.7`` is seven tenths, never a float).
* A fact names nodes; a rule's arguments are all variables. A name is ``[A-Za-z_][A-Za-z0-9_]*``; a node may also be
  a double-quoted string (escapes ``\\"`` and ``\\\\``). ``node`` and ``edge`` are keywords. Comments run from ``#``
  or ``//`` to the end of the line.
* A fact with no interval is true. Facts on one atom are intersected.
* A rule is ``head <- clause, ..., clause.`` The head gives a fixed interval (``[1, 1]`` when none is written) or one
  annotation function from a fixed set (:data:`FUNCTIONS`), never caller code. A clause holds for an atom whose current
  interval lies within the clause's interval (``[1, 1]`` when none is written), and a clause may carry one threshold:
  ``at least N`` (N a whole number) or ``at least P%`` (0 < P <= 100).

Facts and the graph may also come as declarative JSON: ``graph = {"nodes": [...], "edges": [[source, target], ...]}``
and ``facts = [{"label": ..., "node": ...} | {"label": ..., "edge": [source, target]}, optional "bound": [l, u]]``.
Rules stay text.

The semantics (what :mod:`g6.graph_reasoning.engine` computes and :mod:`g6.graph_reasoning.checks` confirms) is one
static fixpoint, with no timesteps: every atom starts at its facts (or ``[0, 1]``), and a rule that fires intersects
its head's interval with the rule's interval until nothing changes. A grounding of a rule binds every variable to a
node so that every edge clause, and an edge rule's head, is an edge of the graph. For one head grounding and one
clause, the *qualified* atoms are that clause's atoms under the groundings where every clause holds, and the
*candidates* are its atoms under the groundings where every other clause holds. The rule fires when every clause has at
least one qualified atom, or as many as its threshold asks (a percentage counts qualified against candidates). An
annotation function reads the intervals of every qualified atom of every clause.

A program is refused, with its own code and the line and column found, when it does not parse
(``G6_E_GRAPH_REASONING_PARSE_ERROR``), when a label is used with two arities (``_ARITY_CLASH``), when a clause reads a
label nothing gives (``_UNKNOWN_LABEL``), when an interval has its lower bound above its upper bound or leaves
``[0, 1]`` (``_BAD_INTERVAL``), when a clause's interval is ``[0, 1]`` (``_VACUOUS_CLAUSE``: every atom, known or not,
is within it), when a function is not one of the set (``_UNKNOWN_FUNCTION``), when a head variable is in no clause
(``_UNSAFE_RULE``), when facts on one atom have nothing in common (``_CONFLICT``) and when an annotation function, or
the other clauses of a rule with a percentage threshold, read a label that depends on the rule's own head
(``_NOT_STRATIFIABLE``, the cycle named): those must be final before the rule is used, or the answer would depend on
the order of evaluation. Nothing in the text is ever run as code.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from g6.graph_reasoning.envelope import (
    ARITY_CLASH,
    BAD_INTERVAL,
    CONFLICT,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    NOT_STRATIFIABLE,
    PARSE_ERROR,
    UNKNOWN_EDGE,
    UNKNOWN_FUNCTION,
    UNKNOWN_LABEL,
    UNKNOWN_NODE,
    UNSAFE_RULE,
    VACUOUS_CLAUSE,
    Refusal,
)

Interval = tuple[Fraction, Fraction]
#: An atom: a label and its component, ``(node,)`` or ``(source, target)``.
Atom = tuple[str, tuple[str, ...]]

ZERO, ONE = Fraction(0), Fraction(1)
UNKNOWN: Interval = (ZERO, ONE)
TRUE: Interval = (ONE, ONE)
#: The annotation functions a head may name, each over the intervals of every qualified atom of every clause.
FUNCTIONS = ("average", "average_lower", "maximum", "minimum", "product")
FUNCTION_MEANING = {
    "average": "the mean of the lower bounds and the mean of the upper bounds",
    "average_lower": "the mean of the lower bounds and the largest upper bound",
    "maximum": "the largest lower bound and the largest upper bound",
    "minimum": "the smallest lower bound and the smallest upper bound",
    "product": "the product of the lower bounds and the product of the upper bounds",
}
KEYWORDS = ("node", "edge")

#: Limits on what is read, each refused with ``G6_E_GRAPH_REASONING_INPUT_TOO_LARGE``. Read at call time, so a test can
#: lower them.
MAX_TEXT = 1_000_000
MAX_NODES = 10_000
MAX_EDGES = 50_000
MAX_FACTS = 50_000
MAX_RULES = 500
MAX_CLAUSES = 16
MAX_NAME = 200
_MAX_DIGITS = 30
_SHOWN = 20

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_TOKEN = re.compile(r"""
    (?P<ws>[ \t\r\f\v]+)
  | (?P<nl>\n)
  | (?P<comment>\#[^\n]*|//[^\n]*)
  | (?P<punct><-|->|[(),.:\[\]%])
  | (?P<number>-?[0-9]+(?:\.[0-9]+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<string>")
""", re.VERBOSE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# --- the model, as data -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Clause:
    """One clause of a rule body: a label over one or two variables, the interval it must lie within, and an optional
    threshold (``at_least`` qualified atoms, or ``at_least`` per cent of the candidates when ``percent``)."""

    label: str
    variables: tuple[str, ...]
    bound: Interval = TRUE
    at_least: Fraction | None = None
    percent: bool = False
    line: int = 0
    column: int = 0


@dataclass(frozen=True)
class Rule:
    """A rule: its head label and variables, its clauses, and either a fixed ``bound`` or a ``function``."""

    head: str
    variables: tuple[str, ...]
    clauses: tuple[Clause, ...]
    bound: Interval | None = TRUE
    function: str | None = None
    line: int = 0
    column: int = 0

    @property
    def kind(self) -> str:
        """``node`` for a node rule (one head variable), ``edge`` for an edge rule (two)."""
        return "node" if len(self.variables) == 1 else "edge"

    @property
    def body_variables(self) -> tuple[str, ...]:
        """Every variable of the body, in the order written."""
        return tuple(dict.fromkeys(v for c in self.clauses for v in c.variables))


@dataclass(frozen=True)
class Model:
    """A read and checked model: its nodes and edges (sorted), its facts (one interval per atom, intersected), where
    each fact was written, its rules in order, each label's arity and stratum, the strata in evaluation order, and the
    labels that depend on themselves."""

    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    facts: dict[Atom, Interval]
    fact_where: dict[Atom, tuple[str, ...]]
    rules: tuple[Rule, ...]
    arities: dict[str, int]
    strata: dict[str, int]
    order: tuple[tuple[str, ...], ...]
    recursive: frozenset[str]
    fact_count: int = 0

    @property
    def derived(self) -> set[str]:
        """The labels a rule gives."""
        return {r.head for r in self.rules}


# --- numbers, names and rendering ---------------------------------------------------------------------------------------

def show(value: Fraction) -> str:
    """A number as written: an exact decimal when it has one (``0.7``), else a fraction (``1/3``)."""
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    d, twos, fives = value.denominator, 0, 0
    while d % 2 == 0:
        d //= 2
        twos += 1
    while d % 5 == 0:
        d //= 5
        fives += 1
    if d != 1:
        return f"{value.numerator}/{value.denominator}"
    places = max(twos, fives)
    scaled = value * 10 ** places
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled.numerator)).rjust(places + 1, "0")
    return f"{sign}{digits[:-places]}.{digits[-places:]}".rstrip("0").rstrip(".")


def show_interval(interval: Interval) -> str:
    return f"[{show(interval[0])}, {show(interval[1])}]"


def as_floats(interval: Interval) -> list[float]:
    return [float(interval[0]), float(interval[1])]


def render_name(name: str) -> str:
    if _NAME.match(name) and name not in KEYWORDS:
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_atom(atom: Atom) -> str:
    label, component = atom
    return f"{label}({', '.join(render_name(n) for n in component)})"


def render_clause(clause: Clause) -> str:
    text = f"{clause.label}({', '.join(clause.variables)})"
    if clause.bound != TRUE:
        text += f" : {show_interval(clause.bound)}"
    if clause.at_least is not None:
        text += f" at least {show(clause.at_least)}{'%' if clause.percent else ''}"
    return text


def render_rule(rule: Rule) -> str:
    head = f"{rule.head}({', '.join(rule.variables)})"
    if rule.function is not None:
        head += f" : {rule.function}"
    elif rule.bound != TRUE:
        head += f" : {show_interval(rule.bound)}"
    return f"{head} <- {', '.join(render_clause(c) for c in rule.clauses)}."


# --- the tokenizer ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Token:
    kind: str       # name, number, string, eof, or the punctuation itself
    text: str
    value: Any
    line: int
    column: int


def _problem(line: int | None, column: int | None, message: str) -> dict[str, Any]:
    return {"line": line, "column": column, "message": message}


def _at(line: int | None, column: int | None, message: str) -> str:
    return f"line {line}, column {column}: {message}" if line is not None else message


def _refuse(code: str, line: int | None, column: int | None, message: str, **keep: Any) -> Refusal:
    return Refusal(code, _at(line, column, message), problems=[_problem(line, column, message)], **keep)


def _tokens(text: str) -> Iterator[_Token]:
    pos, line, line_start = 0, 1, 0
    while pos < len(text):
        column = pos - line_start + 1
        found = _TOKEN.match(text, pos)
        if found is None:
            raise _refuse(PARSE_ERROR, line, column, f"unexpected character {text[pos]!r}")
        kind, chunk = found.lastgroup, found.group()
        if kind == "nl":
            pos, line, line_start = found.end(), line + 1, found.end()
            continue
        if kind in ("ws", "comment"):
            pos = found.end()
            continue
        if kind == "string":
            value, end = _string(text, pos, line, column)
            yield _Token("string", text[pos:end], value, line, column)
            pos = end
            continue
        if kind == "number":
            if len(chunk.lstrip("-").replace(".", "")) > _MAX_DIGITS:
                raise _refuse(PARSE_ERROR, line, column, f"the number has more than {_MAX_DIGITS} digits")
            yield _Token("number", chunk, Fraction(chunk), line, column)
        elif kind == "name" and len(chunk) > MAX_NAME:
            raise _refuse(PARSE_ERROR, line, column, f"the name is longer than {MAX_NAME} characters")
        else:
            yield _Token(chunk if kind == "punct" else kind, chunk, chunk, line, column)
        pos = found.end()
    yield _Token("eof", "", None, line, len(text) - line_start + 1)


def _string(text: str, start: int, line: int, column: int) -> tuple[str, int]:
    out, pos = [], start + 1
    while pos < len(text) and text[pos] != "\n":
        ch = text[pos]
        if ch == '"':
            name = "".join(out)
            if not name or len(name) > MAX_NAME or _CONTROL.search(name):
                raise _refuse(PARSE_ERROR, line, column, f"a quoted node name has 1 to {MAX_NAME} characters and no "
                                                         "control characters")
            return name, pos + 1
        if ch == "\\":
            nxt = text[pos + 1:pos + 2]
            if nxt not in ('"', "\\"):
                raise _refuse(PARSE_ERROR, line, column + pos - start, f"unknown escape '\\{nxt}' in a string (use "
                                                                       '\\" or \\\\)')
            out.append(nxt)
            pos += 2
            continue
        out.append(ch)
        pos += 1
    raise _refuse(PARSE_ERROR, line, column, 'unterminated string: a string ends with " on the same line')


def _show(tok: _Token) -> str:
    if tok.kind == "eof":
        return "the end of the program"
    if tok.kind == "string":
        return "a quoted name"
    if tok.kind == "number":
        return f"the number {tok.text}"
    if tok.kind == "name":
        return f"the name {tok.text}"
    return f"'{tok.text}'"


# --- the parser ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Atom:
    label: str
    args: tuple[tuple[str, str], ...]   # (kind, text): kind is name or string
    line: int
    column: int


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = list(_tokens(text))
        self.i = 0

    def peek(self, ahead: int = 0) -> _Token:
        return self.tokens[min(self.i + ahead, len(self.tokens) - 1)]

    def take(self) -> _Token:
        tok = self.peek()
        self.i += 1
        return tok

    def fail(self, tok: _Token, message: str, code: str = PARSE_ERROR) -> Refusal:
        return _refuse(code, tok.line, tok.column, message)

    def expect(self, kind: str, where: str) -> _Token:
        tok = self.take()
        if tok.kind != kind:
            raise self.fail(tok, f"expected '{kind}' {where}, found {_show(tok)}")
        return tok

    def statements(self) -> Iterator[tuple[str, Any]]:
        while self.peek().kind != "eof":
            yield self.statement()

    def node_name(self, where: str) -> tuple[str, _Token]:
        tok = self.take()
        if tok.kind == "string":
            return tok.value, tok
        if tok.kind == "name":
            if tok.text in KEYWORDS:
                raise self.fail(tok, f'{tok.text} is a keyword: quote it ("{tok.text}") to use it as a node name')
            return tok.text, tok
        raise self.fail(tok, f"expected a node name {where}, found {_show(tok)}")

    def statement(self) -> tuple[str, Any]:
        first = self.peek()
        if first.kind == "name" and first.text == "node":
            self.take()
            names = [self.node_name("after node")]
            while (sep := self.take()).kind == ",":
                names.append(self.node_name("after ','"))
            if sep.kind != ".":
                raise self.fail(sep, f"expected ',' or '.' after a node name, found {_show(sep)}")
            return "nodes", names
        if first.kind == "name" and first.text == "edge":
            self.take()
            edges = [self.edge()]
            while (sep := self.take()).kind == ",":
                edges.append(self.edge())
            if sep.kind != ".":
                raise self.fail(sep, f"expected ',' or '.' after an edge, found {_show(sep)}")
            return "edges", edges
        atom = self.atom("at the start of a statement")
        bound: tuple[Interval, _Token] | None = None
        function: tuple[str, _Token] | None = None
        if self.peek().kind == ":":
            self.take()
            nxt = self.peek()
            if nxt.kind == "[":
                bound = (self.bound(), nxt)
            elif nxt.kind == "name":
                self.take()
                function = (nxt.text, nxt)
            else:
                raise self.fail(nxt, f"expected an interval [lower, upper] or a function name after ':', found "
                                     f"{_show(nxt)}")
        after = self.take()
        if after.kind == ".":
            if function is not None:
                raise self.fail(function[1], "a fact takes an interval, not an annotation function")
            for kind, text in atom.args:
                if kind == "name" and text in KEYWORDS:
                    raise self.fail(self.tokens[self.i - 2], f'{text} is a keyword: quote it ("{text}") to use it as '
                                                             "a node name")
            return "fact", (atom, bound[0] if bound else TRUE, after)
        if after.kind != "<-":
            raise self.fail(after, f"expected '.', ':' or '<-' after the atom, found {_show(after)}")
        if self.peek().kind == "number":
            raise self.fail(self.peek(), "rule delays (<-1) and timesteps are not supported: G6 computes one static "
                                         "fixpoint, so write the rule with <- alone")
        for kind, text in atom.args:
            if kind != "name":
                raise self.fail(first, "a rule's arguments are variables: write a name, not a quoted string")
        if function is not None and function[0] not in FUNCTIONS:
            raise self.fail(function[1], f"{function[0]} is not an annotation function: use one of "
                                         f"{', '.join(FUNCTIONS)}", code=UNKNOWN_FUNCTION)
        clauses = [self.clause()]
        while (sep := self.take()).kind == ",":
            clauses.append(self.clause())
            if len(clauses) > MAX_CLAUSES:
                raise Refusal(INPUT_TOO_LARGE, _at(first.line, first.column, f"the rule has more than {MAX_CLAUSES} "
                                                                             "clauses"))
        if sep.kind != ".":
            raise self.fail(sep, f"expected ',' or '.' after a clause, found {_show(sep)}")
        rule = Rule(atom.label, tuple(text for _, text in atom.args), tuple(clauses),
                    None if function else (bound[0] if bound else TRUE), function[0] if function else None,
                    first.line, first.column)
        return "rule", (rule, atom)

    def edge(self) -> tuple[str, str]:
        source, _ = self.node_name("in an edge")
        self.expect("->", "between an edge's source and target")
        target, _ = self.node_name("after '->'")
        return source, target

    def atom(self, where: str) -> _Atom:
        tok = self.take()
        if tok.kind != "name":
            raise self.fail(tok, f"expected a label {where}, found {_show(tok)}")
        if tok.text in KEYWORDS:
            raise self.fail(tok, f"{tok.text} is a keyword, not a label")
        opened = self.take()
        if opened.kind != "(":
            raise self.fail(opened, f"expected '(' after the label {tok.text}, found {_show(opened)}")
        args: list[tuple[str, str]] = []
        if self.peek().kind != ")":
            while True:
                arg = self.take()
                if arg.kind not in ("name", "string"):
                    raise self.fail(arg, f"expected a name in {tok.text}(...), found {_show(arg)}")
                args.append((arg.kind, arg.value if arg.kind == "string" else arg.text))
                sep = self.take()
                if sep.kind == ")":
                    break
                if sep.kind != ",":
                    raise self.fail(sep, f"expected ',' or ')' after an argument, found {_show(sep)}")
        else:
            self.take()
        if len(args) not in (1, 2):
            raise self.fail(tok, f"{tok.text} has {len(args)} arguments: a label takes one or two arguments, one for "
                                 "a node label and two (source, target) for an edge label")
        return _Atom(tok.text, tuple(args), tok.line, tok.column)

    def number(self, where: str) -> Fraction:
        tok = self.take()
        if tok.kind != "number":
            raise self.fail(tok, f"expected a number {where}, found {_show(tok)}")
        return tok.value

    def bound(self) -> Interval:
        opened = self.expect("[", "to start an interval")
        lower = self.number("as the interval's lower bound")
        self.expect(",", "between the interval's bounds")
        upper = self.number("as the interval's upper bound")
        self.expect("]", "to close the interval")
        if not (ZERO <= lower <= ONE and ZERO <= upper <= ONE):
            raise self.fail(opened, f"the interval [{show(lower)}, {show(upper)}] is outside [0, 1]", BAD_INTERVAL)
        if lower > upper:
            raise self.fail(opened, f"the lower bound {show(lower)} is above the upper bound {show(upper)}",
                            BAD_INTERVAL)
        return lower, upper

    def clause(self) -> Clause:
        start = self.peek()
        atom = self.atom("in the body")
        for kind, _ in atom.args:
            if kind != "name":
                raise self.fail(start, "a rule's arguments are variables: write a name, not a quoted string")
        bound = TRUE
        if self.peek().kind == ":":
            self.take()
            bound_tok = self.peek()
            bound = self.bound()
            if bound == UNKNOWN:
                raise self.fail(bound_tok, f"the clause {atom.label}(...) : [0, 1] holds for every atom, known or "
                                           "not, so it says nothing", VACUOUS_CLAUSE)
        at_least, percent = None, False
        if self.peek().kind == "name" and self.peek().text == "at":
            at = self.take()
            least = self.take()
            if least.kind != "name" or least.text != "least":
                raise self.fail(at, "write a threshold as at least N or at least P%")
            count_tok = self.peek()
            at_least = self.number("after at least")
            if self.peek().kind == "%":
                self.take()
                percent = True
                if not ZERO < at_least <= 100:
                    raise self.fail(count_tok, "a percentage threshold is above 0 and at most 100")
            elif at_least.denominator != 1:
                raise self.fail(count_tok, "a count threshold is a whole number, such as at least 2")
            elif at_least < 1:
                raise self.fail(count_tok, "a count threshold is at least 1")
        return Clause(atom.label, tuple(text for _, text in atom.args), bound, at_least, percent, start.line,
                      start.column)


# --- reading and checking a model ---------------------------------------------------------------------------------------

def _plural(n: int) -> str:
    return f"{n} argument" if n == 1 else f"{n} arguments"


class _Arities:
    def __init__(self) -> None:
        self.seen: dict[str, tuple[int, str]] = {}

    def use(self, label: str, arity: int, label_where: str, line: int | None = None, column: int | None = None,
            path: str = "") -> None:
        first = self.seen.setdefault(label, (arity, label_where))
        if first[0] != arity:
            message = f"{label} is used with {_plural(arity)} here but with {first[0]} {first[1]}"
            raise Refusal(ARITY_CLASH, _at(line, column, message) if line is not None else f"{path}: {message}",
                          problems=[_problem(line, column, message)])


def _json_name(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_NAME or _CONTROL.search(value):
        raise Refusal(INPUT_INVALID, f"{where}: a node name is a string of 1 to {MAX_NAME} characters with no control "
                                     "characters")
    return value


def _json_number(value: Any, where: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Refusal(INPUT_INVALID, f"{where}: {value!r} is not a number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Refusal(INPUT_INVALID, f"{where}: {value!r} is not a finite number")
        return Fraction(repr(value))       # the decimal the caller wrote, 0.9 read as 9/10
    return Fraction(value)


def _json_bound(value: Any, where: str) -> Interval:
    if not isinstance(value, list) or len(value) != 2:
        raise Refusal(INPUT_INVALID, f"{where}: an interval is a list [lower, upper]")
    lower, upper = (_json_number(v, where) for v in value)
    if not (ZERO <= lower <= ONE and ZERO <= upper <= ONE):
        raise Refusal(BAD_INTERVAL, f"{where}: the interval [{show(lower)}, {show(upper)}] is outside [0, 1]")
    if lower > upper:
        raise Refusal(BAD_INTERVAL, f"{where}: the lower bound {show(lower)} is above the upper bound {show(upper)}")
    return lower, upper


def _json_edge(value: Any, where: str) -> tuple[str, str]:
    if not isinstance(value, list) or len(value) != 2:
        raise Refusal(INPUT_INVALID, f"{where}: an edge is a list [source, target]")
    return _json_name(value[0], where), _json_name(value[1], where)


def _json_graph(graph: Any) -> tuple[list[str], list[tuple[str, str]]]:
    if not graph:
        return [], []
    if not isinstance(graph, Mapping) or set(graph) - {"nodes", "edges"}:
        raise Refusal(INPUT_INVALID, "graph is an object with only nodes (a list of names) and edges (a list of "
                                     "[source, target])")
    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise Refusal(INPUT_INVALID, "graph.nodes and graph.edges are lists")
    return ([_json_name(n, f"graph.nodes[{i}]") for i, n in enumerate(nodes)],
            [_json_edge(e, f"graph.edges[{i}]") for i, e in enumerate(edges)])


def _json_facts(facts: Any) -> Iterator[tuple[Atom, Interval, str]]:
    if not facts:
        return
    if not isinstance(facts, list):
        raise Refusal(INPUT_INVALID, "facts is a list of objects, each with a label and a node or an edge")
    for i, fact in enumerate(facts):
        where = f"facts[{i}]"
        if not isinstance(fact, Mapping) or set(fact) - {"label", "node", "edge", "bound"}:
            raise Refusal(INPUT_INVALID, f"{where}: a fact is an object with label, node or edge, and an optional "
                                         "bound")
        label = fact.get("label")
        if not isinstance(label, str) or not _NAME.match(label) or label in KEYWORDS or len(label) > MAX_NAME:
            raise Refusal(INPUT_INVALID, f"{where}: {label!r} is not a label: a label is a name such as popular")
        if ("node" in fact) == ("edge" in fact):
            raise Refusal(INPUT_INVALID, f"{where}: give exactly one of node or edge")
        component = ((_json_name(fact["node"], where),) if "node" in fact else _json_edge(fact["edge"], where))
        bound = _json_bound(fact["bound"], where) if "bound" in fact else TRUE
        yield (label, component), bound, where


def parse_model(text: str = "", *, graph: Mapping[str, Any] | None = None,
                facts: list[Mapping[str, Any]] | None = None) -> Model:
    """``text`` (and any JSON ``graph`` and ``facts``) read and checked: one arity per label, intervals within
    ``[0, 1]``, no vacuous clause, known labels and functions, safe and stratified rules, consistent facts. A
    :class:`~g6.graph_reasoning.envelope.Refusal` with its code, the reason and ``problems`` (line, column, message)
    when not."""
    if not isinstance(text, str):
        raise Refusal(INPUT_INVALID, "the program must be text")
    if len(text) > MAX_TEXT:
        raise Refusal(INPUT_TOO_LARGE, f"the program is {len(text)} characters, more than the {MAX_TEXT} a program "
                                       "may have")
    arities = _Arities()
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    found: dict[Atom, Interval] = {}
    where: dict[Atom, list[str]] = {}
    count = 0

    def add_node(name: str) -> None:
        nodes.add(name)
        if len(nodes) > MAX_NODES:
            raise Refusal(INPUT_TOO_LARGE, f"more than the {MAX_NODES} nodes a model may have")

    def add_edge(edge: tuple[str, str]) -> None:
        add_node(edge[0])
        add_node(edge[1])
        edges.add(edge)
        if len(edges) > MAX_EDGES:
            raise Refusal(INPUT_TOO_LARGE, f"more than the {MAX_EDGES} edges a model may have")

    def add_fact(atom: Atom, bound: Interval, at: str, line: int | None = None, column: int | None = None) -> None:
        nonlocal count
        count += 1
        if count > MAX_FACTS:
            raise Refusal(INPUT_TOO_LARGE, f"more than the {MAX_FACTS} facts a model may have")
        if len(atom[1]) == 1:
            add_node(atom[1][0])
        else:
            add_edge((atom[1][0], atom[1][1]))
        before = found.get(atom, UNKNOWN)
        merged = (max(before[0], bound[0]), min(before[1], bound[1]))
        where.setdefault(atom, []).append(at)
        if merged[0] > merged[1]:
            shown = render_atom(atom)
            message = (f"the facts on {shown} have nothing in common: {show_interval(before)} "
                       f"({', '.join(where[atom][:-1])}) and {show_interval(bound)} ({at})")
            raise Refusal(CONFLICT, _at(line, column, message), problems=[_problem(line, column, message)],
                          conflict={"atom": shown, "between": [
                              {"by": "fact", "where": w, "bounds": as_floats(found_bound)}
                              for w, found_bound in ((", ".join(where[atom][:-1]), before), (at, bound))]})
        found[atom] = merged

    json_nodes, json_edges = _json_graph(graph)
    for name in json_nodes:
        add_node(name)
    for edge in json_edges:
        add_edge(edge)
    for atom, bound, at in _json_facts(facts):
        arities.use(atom[0], len(atom[1]), f"in {at}", path=at)
        add_fact(atom, bound, at)
    rules: list[tuple[Rule, _Atom]] = []
    for kind, item in _Parser(text).statements():
        if kind == "nodes":
            for name, _ in item:
                add_node(name)
        elif kind == "edges":
            for edge in item:
                add_edge(edge)
        elif kind == "fact":
            atom, bound, _ = item
            arities.use(atom.label, len(atom.args), f"at line {atom.line}, column {atom.column}", atom.line,
                        atom.column)
            add_fact((atom.label, tuple(t for _, t in atom.args)), bound, f"line {atom.line}", atom.line,
                     atom.column)
        else:
            rule, head = item
            arities.use(rule.head, len(rule.variables), f"at line {head.line}, column {head.column}", head.line,
                        head.column)
            for clause in rule.clauses:
                arities.use(clause.label, len(clause.variables), f"at line {clause.line}, column {clause.column}",
                            clause.line, clause.column)
            rules.append((rule, head))
            if len(rules) > MAX_RULES:
                raise Refusal(INPUT_TOO_LARGE, f"more than the {MAX_RULES} rules a model may have")
    given = {atom[0] for atom in found} | {rule.head for rule, _ in rules}
    for rule, _ in rules:
        for clause in rule.clauses:
            if clause.label not in given:
                raise _refuse(UNKNOWN_LABEL, clause.line, clause.column,
                              f"no fact and no rule gives {clause.label}, so this clause can never hold: check the "
                              "spelling")
        unbound = [v for v in rule.variables if v not in rule.body_variables]
        if unbound:
            raise _refuse(UNSAFE_RULE, rule.line, rule.column,
                          f"the rule {render_rule(rule)} is unsafe: {', '.join(unbound)} "
                          f"{'is' if len(unbound) == 1 else 'are'} in the head but in no clause of the body")
    labels = {name: arity for name, (arity, _) in arities.seen.items()}
    strata, order, recursive = _stratify(labels, [rule for rule, _ in rules])
    return Model(tuple(sorted(nodes)), tuple(sorted(edges)), dict(sorted(found.items())),
                 {atom: tuple(w) for atom, w in sorted(where.items())}, tuple(rule for rule, _ in rules), labels,
                 strata, order, recursive, count)


def _final_first(rule: Rule, index: int) -> bool:
    """Whether clause ``index`` of ``rule`` must be final before the rule is used: an annotation function reads every
    clause's intervals, and a percentage threshold's candidates come from the other clauses."""
    return rule.function is not None or any(c.percent for j, c in enumerate(rule.clauses) if j != index)


def _stratify(labels: dict[str, int], rules: list[Rule]
              ) -> tuple[dict[str, int], tuple[tuple[str, ...], ...], frozenset[str]]:
    """Each label's stratum (a label that must be final first strictly below the rule's head), the strata in order, and
    the labels that depend on themselves. Refused when a must-be-final label depends on the head."""
    edges: dict[str, dict[str, Rule | None]] = {name: {} for name in labels}   # body -> head -> a rule needing it final
    for rule in rules:
        for i, clause in enumerate(rule.clauses):
            if _final_first(rule, i):
                edges[clause.label][rule.head] = rule
            else:
                edges[clause.label].setdefault(rule.head, None)
    components = _components(edges)
    component_of = {name: i for i, members in enumerate(components) for name in members}
    for members in components:
        for source in members:
            for target, needing in edges[source].items():
                if needing is not None and component_of[target] == component_of[source]:
                    raise _not_stratifiable(edges, target, source, needing)
    stratum = dict.fromkeys(range(len(components)), 0)
    for i in reversed(range(len(components))):  # Tarjan lists a component after those it reaches: walk sources first
        for source in components[i]:
            for target, needing in edges[source].items():
                j = component_of[target]
                if j != i:
                    stratum[j] = max(stratum[j], stratum[i] + (1 if needing is not None else 0))
    strata = {name: stratum[component_of[name]] for name in labels}
    levels = sorted(set(strata.values()))
    order = tuple(tuple(sorted(n for n in labels if strata[n] == level)) for level in levels)
    recursive = frozenset(name for members in components for name in members
                          if len(members) > 1 or name in edges[name])
    return strata, order, recursive


def _components(edges: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Strongly connected components (Tarjan's algorithm, without recursion), each listed after every component it
    reaches."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = 0
    for root in edges:
        if root in index:
            continue
        work = [(root, iter(edges[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(edges[child])))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                members = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    members.append(member)
                    if member == node:
                        break
                found.append(sorted(members))
    return found


def _not_stratifiable(edges: dict[str, dict[str, Any]], head: str, read: str, rule: Rule) -> Refusal:
    # ``head`` reads ``read``, which must be final first; find how ``read`` depends on ``head`` (a path head -> read).
    previous: dict[str, str | None] = {head: None}
    frontier = [head]
    while frontier and read not in previous:
        nxt = []
        for node in frontier:
            for target in edges[node]:
                if target not in previous:
                    previous[target] = node
                    nxt.append(target)
        frontier = nxt
    path = [read]
    while previous.get(path[-1]) is not None:
        path.append(previous[path[-1]])
    path.reverse()  # head, ..., read
    why = f"the annotation function {rule.function}" if rule.function else "a percentage threshold"
    if read == head:
        message = (f"recursion through {why}: the rule for {head} reads {head} itself, so {head} is not final when "
                   "the rule needs it")
    else:
        through = f" through {', '.join(path[1:-1])}" if len(path) > 2 else ""
        message = (f"recursion through {why}: the rule for {head} reads {read}, and {read} depends on "
                   f"{head}{through}, so {read} is not final when the rule needs it")
    return Refusal(NOT_STRATIFIABLE, _at(rule.line, rule.column, message), cycle=path,
                   problems=[_problem(rule.line, rule.column, message)])


def parse_atom(text: str, model: Model) -> Atom:
    """One ground atom (``popular(alice)`` or ``friend(alice, bob)``) read against ``model``: its label must be the
    model's, with the same arity, and its node or edge must be in the graph."""
    if not isinstance(text, str) or not text.strip():
        raise Refusal(INPUT_INVALID, "give an atom, such as popular(alice) or friend(alice, bob)")
    if len(text) > MAX_TEXT:
        raise Refusal(INPUT_TOO_LARGE, f"the atom is longer than {MAX_TEXT} characters")
    parser = _Parser(text)
    atom = parser.atom("at the start of the atom")
    if parser.peek().kind in (".", "?"):
        parser.take()
    if (rest := parser.peek()).kind != "eof":
        raise parser.fail(rest, f"an atom is one label with its node or edge; found {_show(rest)} after it")
    if atom.label not in model.arities:
        raise _refuse(UNKNOWN_LABEL, atom.line, atom.column, f"{atom.label} is not a label of the model: no fact or "
                                                             "rule gives or reads it")
    if len(atom.args) != model.arities[atom.label]:
        raise _refuse(ARITY_CLASH, atom.line, atom.column,
                      f"{atom.label} is used with {_plural(len(atom.args))} here but has "
                      f"{_plural(model.arities[atom.label])} in the model")
    component = tuple(t for _, t in atom.args)
    nodes = set(model.nodes)
    for name in component:
        if name not in nodes:
            raise _refuse(UNKNOWN_NODE, atom.line, atom.column, f"{render_name(name)} is not a node of the graph")
    if len(component) == 2 and component not in set(model.edges):
        raise _refuse(UNKNOWN_EDGE, atom.line, atom.column,
                      f"{render_name(component[0])} -> {render_name(component[1])} is not an edge of the graph")
    return atom.label, component


__all__ = ["FUNCTIONS", "FUNCTION_MEANING", "KEYWORDS", "MAX_CLAUSES", "MAX_EDGES", "MAX_FACTS", "MAX_NAME",
           "MAX_NODES", "MAX_RULES", "MAX_TEXT", "TRUE", "UNKNOWN", "Atom", "Clause", "Interval", "Model", "Rule",
           "as_floats", "parse_atom", "parse_model", "render_atom", "render_clause", "render_name", "render_rule",
           "show", "show_interval"]
