# SPDX-License-Identifier: Apache-2.0
"""The Datalog g6.datalog accepts, read by its own parser and checked before anything is evaluated.

The language, strictly::

    parent(alice, bob).                                  % a fact: constants only
    ancestor(X, Y) :- parent(X, Y).                      // a rule
    ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).
    lonely(X) :- person(X), not friend(X, _Y), X != bob. % stratified negation and comparisons
    ancestor(alice, X)?                                  % a query

* A relation name starts with a lower-case letter (``[a-z][A-Za-z0-9_]*``); a relation has one number of arguments
  everywhere, and may have none (``raining.``). ``not`` is a keyword.
* A variable starts upper-case or with ``_``; each lone ``_`` is a variable of its own. A constant is a name
  (``alice``), a double-quoted string (``"Bob Smith"``, escapes ``\\"`` ``\\\\`` ``\\n`` ``\\t``) or an integer
  (``-7``, at most 18 digits). A name and the string with the same text are the same constant; the integer ``1`` and
  the string ``"1"`` are not.
* A rule body is atoms, negated atoms (``not p(X)``) and comparisons (``=``, ``!=``, ``<``, ``<=``, ``>``, ``>=``).
  Comparisons order integers by value and texts by code point, with every integer below every text.
* Comments run from ``%`` or ``//`` to the end of the line.

Facts may also come as declarative JSON, ``{"parent": [["alice", "bob"]]}``, where every string is a constant (so
``"Alice"`` is one here). Rules stay text, because a JSON string cannot say whether it is a variable.

A program is refused, with its own code and the line and column found, when it does not parse
(``G6_E_DATALOG_PARSE_ERROR``), when a relation is used with two arities (``_ARITY_CLASH``), when a rule is unsafe
(``_UNSAFE_RULE``: a variable of the head, of a negated atom or of a comparison bound by no positive body atom; a fact
that is not ground; an anonymous variable in a head, a negated atom or a comparison) and when its negation cannot be
stratified (``_NOT_STRATIFIABLE``: a relation depending on its own negation, with the cycle named). Nothing in the text
is ever run as code.
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from g6.datalog.envelope import (
    ARITY_CLASH,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    NOT_STRATIFIABLE,
    PARSE_ERROR,
    UNKNOWN_RELATION,
    UNSAFE_RULE,
    Refusal,
)

Value = str | int

#: Limits on what is read, each refused with ``G6_E_DATALOG_INPUT_TOO_LARGE``. Read at call time, so a test can lower
#: them.
MAX_TEXT = 1_000_000
MAX_FACTS = 10_000
MAX_RULES = 1_000
MAX_QUERIES = 100
MAX_ARITY = 32
MAX_BODY = 32
MAX_STRING = 1_000
MAX_NAME = 200
_MAX_DIGITS = 18
_SHOWN = 20

_NAME = re.compile(r"[a-z][A-Za-z0-9_]*\Z")
_TOKEN = re.compile(r"""
    (?P<ws>[ \t\r\f\v]+)
  | (?P<nl>\n)
  | (?P<comment>%[^\n]*|//[^\n]*)
  | (?P<name>[a-z][A-Za-z0-9_]*)
  | (?P<var>[A-Z_][A-Za-z0-9_]*)
  | (?P<int>-?[0-9]+)
  | (?P<string>")
  | (?P<punct>:-|\?-|!=|<=|>=|==|[(),.?=<>])
""", re.VERBOSE)
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}
COMPARISONS = ("=", "!=", "<", "<=", ">", ">=")


# --- the program, as data -----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Var:
    """A variable. A lone ``_`` is read as a fresh variable named ``_#n``, which no program can write."""

    name: str

    @property
    def anonymous(self) -> bool:
        return self.name.startswith("_#")


@dataclass(frozen=True)
class Const:
    """A constant: a text (a name or a string) or an integer."""

    value: Value


Term = Var | Const


@dataclass(frozen=True)
class Atom:
    relation: str
    args: tuple[Term, ...] = ()
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)

    @property
    def variables(self) -> list[Var]:
        return [t for t in self.args if isinstance(t, Var)]


@dataclass(frozen=True)
class Negation:
    atom: Atom
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Comparison:
    op: str
    left: Term
    right: Term
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)

    @property
    def variables(self) -> list[Var]:
        return [t for t in (self.left, self.right) if isinstance(t, Var)]


Literal = Atom | Negation | Comparison


@dataclass(frozen=True)
class Rule:
    head: Atom
    body: tuple[Literal, ...]
    line: int = field(default=0, compare=False)
    column: int = field(default=0, compare=False)

    @property
    def positives(self) -> list[Atom]:
        return [lit for lit in self.body if isinstance(lit, Atom)]

    @property
    def negations(self) -> list[Atom]:
        return [lit.atom for lit in self.body if isinstance(lit, Negation)]

    @property
    def comparisons(self) -> list[Comparison]:
        return [lit for lit in self.body if isinstance(lit, Comparison)]


@dataclass(frozen=True)
class Program:
    """A parsed and checked program: its facts by relation, its rules and queries in order, each relation's arity and
    stratum, the strata in evaluation order, and the relations that depend on themselves."""

    facts: dict[str, frozenset[tuple[Value, ...]]]
    rules: tuple[Rule, ...]
    queries: tuple[Atom, ...]
    arities: dict[str, int]
    strata: dict[str, int]
    order: tuple[tuple[str, ...], ...]
    recursive: frozenset[str]
    fact_count: int = 0

    @property
    def defined(self) -> set[str]:
        """The relations a fact or a rule head gives tuples to."""
        return set(self.facts) | {r.head.relation for r in self.rules}

    @property
    def undefined(self) -> list[str]:
        """Relations used in a rule body that no fact or rule defines: empty, and often a misspelling."""
        return sorted(set(self.arities) - self.defined)

    def constants(self) -> list[Value]:
        """Every constant in the facts and rules, in the comparison order."""
        found: set[Value] = {v for rows in self.facts.values() for row in rows for v in row}
        for rule in self.rules:
            for lit in rule.body:
                terms = lit.args if isinstance(lit, Atom) else lit.atom.args if isinstance(lit, Negation) else (
                    lit.left, lit.right)
                found |= {t.value for t in terms if isinstance(t, Const)}
            found |= {t.value for t in rule.head.args if isinstance(t, Const)}
        return sorted(found, key=order_key)


def order_key(value: Value) -> tuple[int, int, str]:
    """The comparison order: integers by value, below texts by code point."""
    return (0, value, "") if isinstance(value, int) else (1, 0, value)


# --- rendering ----------------------------------------------------------------------------------------------------------

def render_value(value: Value) -> str:
    if isinstance(value, int):
        return str(value)
    if _NAME.match(value) and value != "not":
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'


def render_term(term: Term) -> str:
    return ("_" if term.anonymous else term.name) if isinstance(term, Var) else render_value(term.value)


def render_atom(atom: Atom) -> str:
    return atom.relation + (f"({', '.join(render_term(t) for t in atom.args)})" if atom.args else "")


def render_literal(lit: Literal) -> str:
    if isinstance(lit, Negation):
        return "not " + render_atom(lit.atom)
    if isinstance(lit, Comparison):
        return f"{render_term(lit.left)} {lit.op} {render_term(lit.right)}"
    return render_atom(lit)


def render_rule(rule: Rule) -> str:
    return f"{render_atom(rule.head)} :- {', '.join(render_literal(lit) for lit in rule.body)}."


def render_tuple(relation: str, row: tuple[Value, ...]) -> str:
    return relation + (f"({', '.join(render_value(v) for v in row)})" if row else "")


# --- the tokenizer ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Token:
    kind: str           # name, var, int, string, eof, or the punctuation itself
    text: str
    value: Any
    line: int
    column: int


def _problem(line: int | None, column: int | None, message: str) -> dict[str, Any]:
    return {"line": line, "column": column, "message": message}


def _at(line: int | None, column: int | None, message: str) -> str:
    return f"line {line}, column {column}: {message}" if line is not None else message


def _parse_error(line: int, column: int, message: str) -> Refusal:
    return Refusal(PARSE_ERROR, _at(line, column, message), problems=[_problem(line, column, message)])


def _tokens(text: str) -> Iterator[_Token]:
    pos, line, line_start = 0, 1, 0
    while pos < len(text):
        column = pos - line_start + 1
        found = _TOKEN.match(text, pos)
        if found is None:
            raise _parse_error(line, column, f"unexpected character {text[pos]!r}")
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
        if kind == "int":
            if text.startswith(".", found.end()) and text[found.end() + 1:found.end() + 2].isdigit():
                raise _parse_error(line, column, "decimal numbers are not supported: a number is an integer")
            if len(chunk.lstrip("-")) > _MAX_DIGITS:
                raise _parse_error(line, column, f"the integer is too large (at most {_MAX_DIGITS} digits)")
            yield _Token("int", chunk, int(chunk), line, column)
        elif kind == "punct" and chunk == "==":
            raise _parse_error(line, column, "unexpected characters '==' (equality is written =)")
        elif kind in ("name", "var") and len(chunk) > MAX_NAME:
            raise _parse_error(line, column, f"the name is longer than {MAX_NAME} characters")
        else:
            yield _Token(chunk if kind == "punct" else kind, chunk, chunk, line, column)
        pos = found.end()
    yield _Token("eof", "", None, line, len(text) - line_start + 1)


def _string(text: str, start: int, line: int, column: int) -> tuple[str, int]:
    out, pos = [], start + 1
    while pos < len(text) and text[pos] != "\n":
        ch = text[pos]
        if ch == '"':
            if len(out) > MAX_STRING:
                raise _parse_error(line, column, f"the string is longer than {MAX_STRING} characters")
            return "".join(out), pos + 1
        if ch == "\\":
            nxt = text[pos + 1:pos + 2]
            if nxt not in _ESCAPES:
                raise _parse_error(line, column + pos - start, f"unknown escape '\\{nxt}' in a string (use \\\" \\\\ "
                                                               "\\n or \\t)")
            out.append(_ESCAPES[nxt])
            pos += 2
            continue
        out.append(ch)
        pos += 1
    raise _parse_error(line, column, "unterminated string: a string ends with \" on the same line")


# --- the parser ---------------------------------------------------------------------------------------------------------

def _show(tok: _Token) -> str:
    if tok.kind == "eof":
        return "the end of the program"
    if tok.kind == "string":
        return "a string"
    if tok.kind == "int":
        return f"the integer {tok.text}"
    if tok.kind == "var":
        return f"the variable {tok.text}"
    if tok.kind == "name" and tok.text == "not":
        return "the keyword not"
    return f"'{tok.text}'"


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = list(_tokens(text))
        self.i = 0
        self.fresh = 0

    def peek(self, ahead: int = 0) -> _Token:
        return self.tokens[min(self.i + ahead, len(self.tokens) - 1)]

    def take(self) -> _Token:
        tok = self.peek()
        self.i += 1
        return tok

    def fail(self, tok: _Token, message: str) -> Refusal:
        return _parse_error(tok.line, tok.column, message)

    def statements(self) -> Iterator[tuple[str, Any]]:
        while self.peek().kind != "eof":
            yield self.statement()

    def statement(self) -> tuple[str, Any]:
        first = self.peek()
        if first.kind == "?-":
            raise self.fail(first, "write a query as atom?, e.g. ancestor(alice, X)?")
        if first.kind == "name" and first.text == "not":
            raise self.fail(first, "a head cannot be negated: not belongs in a rule body")
        head = self.atom("at the start of a statement")
        after = self.take()
        if after.kind == ".":
            return "fact", head
        if after.kind == "?":
            return "query", head
        if after.kind != ":-":
            raise self.fail(after, f"expected '.', ':-' or '?' after the atom, found {_show(after)}")
        body = [self.literal()]
        while (sep := self.take()).kind == ",":
            body.append(self.literal())
            if len(body) > MAX_BODY:
                raise Refusal(INPUT_TOO_LARGE, _at(first.line, first.column, f"the rule has more than {MAX_BODY} "
                                                                             "body literals"))
        if sep.kind != ".":
            raise self.fail(sep, f"expected '.' or ',' after the literal, found {_show(sep)}")
        return "rule", Rule(head, tuple(body), first.line, first.column)

    def atom(self, where: str) -> Atom:
        tok = self.take()
        if tok.kind != "name" or tok.text == "not":
            hint = (": a relation name starts with a lower-case letter" if tok.kind == "var" else "")
            raise self.fail(tok, f"expected a relation name {where}, found {_show(tok)}{hint}")
        args: list[Term] = []
        if self.peek().kind == "(":
            self.take()
            if self.peek().kind == ")":
                self.take()
            else:
                while True:
                    args.append(self.term())
                    sep = self.take()
                    if sep.kind == ")":
                        break
                    if sep.kind != ",":
                        raise self.fail(sep, f"expected ',' or ')' after an argument, found {_show(sep)}")
        if len(args) > MAX_ARITY:
            raise Refusal(INPUT_TOO_LARGE, _at(tok.line, tok.column, f"{tok.text} has more than {MAX_ARITY} "
                                                                     "arguments"))
        return Atom(tok.text, tuple(args), tok.line, tok.column)

    def term(self) -> Term:
        tok = self.take()
        if tok.kind == "var":
            if tok.text == "_":
                self.fresh += 1
                return Var(f"_#{self.fresh}")
            return Var(tok.text)
        if tok.kind == "name":
            if tok.text == "not":
                raise self.fail(tok, 'not is a keyword: write "not" in quotes to use it as a constant')
            return Const(tok.text)
        if tok.kind in ("int", "string"):
            return Const(tok.value)
        raise self.fail(tok, f"expected a term (a variable, a name, a quoted string or an integer), found {_show(tok)}")

    def literal(self) -> Literal:
        tok = self.peek()
        if tok.kind == "name" and tok.text == "not":
            self.take()
            return Negation(self.atom("after not"), tok.line, tok.column)
        if tok.kind == "name" and self.peek(1).kind not in COMPARISONS:
            return self.atom("in the body")
        if tok.kind not in ("name", "var", "int", "string"):
            raise self.fail(tok, f"expected a relation name, a negated atom or a comparison in the body, found "
                                 f"{_show(tok)}")
        left = self.term()
        op = self.take()
        if op.kind not in COMPARISONS:
            raise self.fail(op, f"expected a comparison ({', '.join(COMPARISONS)}) after the term, found {_show(op)}")
        return Comparison(op.kind, left, self.term(), tok.line, tok.column)


# --- reading and checking a program -------------------------------------------------------------------------------------

def _json_value(value: Any, where: str) -> Value:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise Refusal(INPUT_INVALID, f"{where}: {value!r} is not a constant: a constant is a string or an integer")
    if isinstance(value, int) and abs(value) >= 10 ** _MAX_DIGITS:
        raise Refusal(INPUT_INVALID, f"{where}: the integer is too large (at most {_MAX_DIGITS} digits)")
    if isinstance(value, str) and len(value) > MAX_STRING:
        raise Refusal(INPUT_INVALID, f"{where}: the string is longer than {MAX_STRING} characters")
    return value


def _json_facts(facts: Mapping[str, Any]) -> Iterator[tuple[str, tuple[Value, ...], str]]:
    if not isinstance(facts, Mapping):
        raise Refusal(INPUT_INVALID, "facts must map each relation name to a list of rows")
    for name, rows in facts.items():
        if name == "not":
            raise Refusal(INPUT_INVALID, "facts: not is a keyword, not a relation name")
        if not isinstance(name, str) or not _NAME.match(name) or len(name) > MAX_NAME:
            raise Refusal(INPUT_INVALID, f"facts: {name!r} is not a relation name: a relation name starts with a "
                                         "lower-case letter and has only letters, digits and underscores")
        if not isinstance(rows, list):
            raise Refusal(INPUT_INVALID, f"facts.{name} must be a list of rows, each a list of constants")
        for i, row in enumerate(rows):
            if not isinstance(row, list):
                raise Refusal(INPUT_INVALID, f"facts.{name}[{i}] must be a list of constants")
            if len(row) > MAX_ARITY:
                raise Refusal(INPUT_TOO_LARGE, f"facts.{name}[{i}] has more than {MAX_ARITY} values")
            yield name, tuple(_json_value(v, f"facts.{name}[{i}][{j}]") for j, v in enumerate(row)), f"facts.{name}[{i}]"


def _ground(atom: Atom) -> tuple[Value, ...] | None:
    return None if atom.variables else tuple(t.value for t in atom.args)


def _plural(n: int) -> str:
    return f"{n} argument" if n == 1 else f"{n} arguments"


class _Arities:
    def __init__(self) -> None:
        self.seen: dict[str, tuple[int, str]] = {}

    def use(self, relation: str, arity: int, label: str, line: int | None = None, column: int | None = None,
            path: str = "") -> None:
        """``relation`` used with ``arity`` arguments, where ``label`` says (``at line 2, column 1``, ``in
        facts.p[0]``); refused when its first use had another arity."""
        first = self.seen.setdefault(relation, (arity, label))
        if first[0] != arity:
            message = f"{relation} is used with {_plural(arity)} here but with {first[0]} {first[1]}"
            raise Refusal(ARITY_CLASH, _at(line, column, message) if line is not None else f"{path}: {message}",
                          problems=[_problem(line, column, message)])


def _where(line: int, column: int) -> str:
    return f"at line {line}, column {column}"


def parse_program(text: str = "", facts: Mapping[str, Any] | None = None) -> Program:
    """``text`` (and any JSON ``facts``) read and checked: one arity per relation, safe rules, stratified negation. A
    :class:`~g6.datalog.envelope.Refusal` with its code, the reason and ``problems`` (line, column, message) when not."""
    if not isinstance(text, str):
        raise Refusal(INPUT_INVALID, "the program must be text")
    if len(text) > MAX_TEXT:
        raise Refusal(INPUT_TOO_LARGE, f"the program is {len(text)} characters, more than the {MAX_TEXT} a program "
                                       "may have")
    arities = _Arities()
    rows: dict[str, set[tuple[Value, ...]]] = {}
    count = 0

    def add_fact(relation: str, row: tuple[Value, ...]) -> None:
        nonlocal count
        count += 1
        if count > MAX_FACTS:
            raise Refusal(INPUT_TOO_LARGE, f"{count} facts, more than the {MAX_FACTS} a program may have")
        rows.setdefault(relation, set()).add(row)

    for relation, row, where in _json_facts(facts or {}):
        arities.use(relation, len(row), f"in {where}", path=where)
        add_fact(relation, row)
    rules: list[Rule] = []
    queries: list[Atom] = []
    unsafe: list[dict[str, Any]] = []
    for kind, item in _Parser(text).statements():
        atoms = [item] if kind != "rule" else [item.head, *item.positives, *item.negations]
        for atom in atoms:
            arities.use(atom.relation, len(atom.args), _where(atom.line, atom.column), atom.line, atom.column)
        if kind == "fact":
            row = _ground(item)
            if row is None:
                names = ", ".join(dict.fromkeys(render_term(v) for v in item.variables))
                unsafe.append(_problem(item.line, item.column, f"the fact {render_atom(item)} is unsafe: a fact must "
                                                               f"be ground, and {names} is a variable"))
            else:
                add_fact(item.relation, row)
        elif kind == "rule":
            rules.append(item)
            if len(rules) > MAX_RULES:
                raise Refusal(INPUT_TOO_LARGE, f"{len(rules)} rules, more than the {MAX_RULES} a program may have")
            if issues := _unsafe(item):
                unsafe.append(_problem(item.line, item.column, f"the rule {render_rule(item)} is unsafe: "
                                                               + "; ".join(issues)))
        else:
            queries.append(item)
            if len(queries) > MAX_QUERIES:
                raise Refusal(INPUT_TOO_LARGE, f"{len(queries)} queries, more than the {MAX_QUERIES} a program may "
                                               "have")
    if unsafe:
        first = unsafe[0]
        more = f" (and {len(unsafe) - 1} more)" if len(unsafe) > 1 else ""
        raise Refusal(UNSAFE_RULE, _at(first["line"], first["column"], first["message"]) + more,
                      problems=unsafe[:_SHOWN])
    relations = {name: arity for name, (arity, _) in arities.seen.items()}
    for query in queries:
        if query.relation not in _defined_or_used(rows, rules):
            raise _unknown(query.relation, query.line, query.column)
    strata, order, recursive = _stratify(relations, rules)
    return Program({name: frozenset(found) for name, found in rows.items()}, tuple(rules), tuple(queries), relations,
                   strata, order, recursive, count)


def _defined_or_used(rows: Mapping[str, Any], rules: list[Rule]) -> set[str]:
    names = set(rows)
    for rule in rules:
        names |= {rule.head.relation, *(a.relation for a in rule.positives), *(a.relation for a in rule.negations)}
    return names


def _unknown(relation: str, line: int | None, column: int | None) -> Refusal:
    message = f"{relation} is not a relation of the program: no fact or rule uses it"
    return Refusal(UNKNOWN_RELATION, _at(line, column, message), problems=[_problem(line, column, message)])


def _unsafe(rule: Rule) -> list[str]:
    bound = {v.name for atom in rule.positives for v in atom.variables}
    issues: list[str] = []

    def unbound(variables: list[Var], where: str, anonymous: str) -> None:
        for var in variables:
            issue = anonymous if var.anonymous else (
                f"{var.name} appears in {where} but in no positive body atom" if var.name not in bound else "")
            if issue and issue not in issues:
                issues.append(issue)

    unbound(rule.head.variables, "the head", "an anonymous variable in the head has no value to give")
    for atom in rule.negations:
        unbound(atom.variables, "a negated atom",
                "an anonymous variable in a negated atom is not allowed (give it its own rule: has_q(X) :- q(X, _). "
                "then not has_q(X))")
    for comparison in rule.comparisons:
        unbound(comparison.variables, "a comparison", "an anonymous variable in a comparison has no value")
    return issues


def _stratify(relations: dict[str, int], rules: list[Rule]
              ) -> tuple[dict[str, int], tuple[tuple[str, ...], ...], frozenset[str]]:
    """Each relation's stratum (a negated relation strictly below the rule that negates it), the strata in order, and
    the relations that depend on themselves. Refused when negation runs through a cycle."""
    edges: dict[str, dict[str, Rule | None]] = {name: {} for name in relations}   # body -> head -> a negating rule
    for rule in rules:
        head = rule.head.relation
        for atom in rule.positives:
            edges[atom.relation].setdefault(head, None)
        for atom in rule.negations:
            edges[atom.relation][head] = rule
    components = _components(edges)
    component_of = {name: i for i, members in enumerate(components) for name in members}
    for members in components:
        for source in members:
            for target, negating in edges[source].items():
                if negating is not None and component_of[target] == component_of[source]:
                    raise _negative_cycle(edges, target, source, negating)
    stratum = dict.fromkeys(range(len(components)), 0)
    for i in reversed(range(len(components))):  # Tarjan lists a component after those it reaches: walk sources first
        for source in components[i]:
            for target, negating in edges[source].items():
                j = component_of[target]
                if j != i:
                    stratum[j] = max(stratum[j], stratum[i] + (1 if negating is not None else 0))
    strata = {name: stratum[component_of[name]] for name in relations}
    levels = sorted(set(strata.values()))
    order = tuple(tuple(sorted(n for n in relations if strata[n] == level)) for level in levels)
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


def _negative_cycle(edges: dict[str, dict[str, Any]], head: str, negated: str, rule: Rule) -> Refusal:
    # ``head`` depends on ``not negated``; find how ``negated`` depends on ``head`` (a path head -> ... -> negated).
    previous: dict[str, str | None] = {head: None}
    frontier = [head]
    while frontier and negated not in previous:
        nxt = []
        for node in frontier:
            for target in edges[node]:
                if target not in previous:
                    previous[target] = node
                    nxt.append(target)
        frontier = nxt
    path = [negated]
    while previous.get(path[-1]) is not None:
        path.append(previous[path[-1]])
    path.reverse()  # head, ..., negated
    if negated == head:
        message = f"negation through recursion: {head} depends on its own negation, so no stratified order exists"
    else:
        through = f" through {', '.join(path[1:-1])}" if len(path) > 2 else ""
        message = (f"negation through recursion: {head} depends on not {negated}, and {negated} depends on "
                   f"{head}{through}, so no stratified order exists")
    return Refusal(NOT_STRATIFIABLE, _at(rule.line, rule.column, message), cycle=path,
                   problems=[_problem(rule.line, rule.column, message)])


def parse_goal(text: str, program: Program) -> Atom:
    """One goal (an atom, with or without a closing ``?``) read against ``program``: its relation must be the
    program's, with the same arity."""
    if not isinstance(text, str) or not text.strip():
        raise Refusal(INPUT_INVALID, "give a goal, an atom such as ancestor(alice, X)")
    if len(text) > MAX_TEXT:
        raise Refusal(INPUT_TOO_LARGE, f"the goal is longer than {MAX_TEXT} characters")
    parser = _Parser(text)
    goal = parser.atom("at the start of the goal")
    if parser.peek().kind == "?":
        parser.take()
    if (rest := parser.peek()).kind != "eof":
        raise parser.fail(rest, f"a goal is one atom, such as ancestor(alice, X); found {_show(rest)} after it")
    if goal.relation not in program.arities:
        raise _unknown(goal.relation, goal.line, goal.column)
    if len(goal.args) != program.arities[goal.relation]:
        message = (f"{goal.relation} is used with {_plural(len(goal.args))} here but has "
                   f"{_plural(program.arities[goal.relation])} in the program")
        raise Refusal(ARITY_CLASH, _at(goal.line, goal.column, message),
                      problems=[_problem(goal.line, goal.column, message)])
    return goal


__all__ = ["COMPARISONS", "Atom", "Comparison", "Const", "Literal", "MAX_ARITY", "MAX_BODY", "MAX_FACTS",
           "MAX_QUERIES", "MAX_RULES", "MAX_STRING", "MAX_TEXT", "Negation", "Program", "Rule", "Term", "Value", "Var",
           "order_key", "parse_goal", "parse_program", "render_atom", "render_literal", "render_rule", "render_term",
           "render_tuple", "render_value"]
