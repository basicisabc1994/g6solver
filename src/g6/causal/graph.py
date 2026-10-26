# SPDX-License-Identifier: Apache-2.0
"""A causal graph, read by G6's own parser from JSON or a strict subset of DOT, and checked before anything uses it.

JSON: ``{"nodes": ["Z", "T", "Y", "U"], "edges": [["Z", "T"], ["T", "Y"], ...], "unobserved": ["U"]}``. ``nodes`` is
required, so an edge or an unobserved name that was never declared is refused rather than guessed at.

DOT, the subset only: ``digraph [name] { statements }``, where a statement is an edge chain ``A -> B -> C``, a node
``A`` or a node with ``[observed=no]`` (or ``yes``, ``true``, ``false``, quoted or not), optionally ended by ``;``.
Comments are ``//`` and ``/* */``, and a line that starts with ``#``. Names are identifiers (a letter or ``_`` then
letters, digits and ``_``, at most 100), optionally in double quotes. Anything else DOT has (undirected graphs, ``--``,
subgraphs, ``graph``/``node``/``edge`` defaults, other attributes, ports, ``strict``) is refused with the line and
column, never skipped. Nothing is evaluated, and no file is read: a graph is text or an object, never a path.

Refused, each with its own code: a graph that does not parse or fit (``G6_E_CAUSAL_GRAPH_INVALID``), a self-loop
(``SELF_LOOP``), a cycle, named (``GRAPH_CYCLE``), an undeclared name (``UNKNOWN_NODE``) and more than
:data:`MAX_NODES` nodes, :data:`MAX_EDGES` edges or :data:`MAX_TEXT` characters (``INPUT_TOO_LARGE``). Repeated nodes
and edges count once.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from g6.causal.envelope import GRAPH_CYCLE, GRAPH_INVALID, INPUT_TOO_LARGE, SELF_LOOP, UNKNOWN_NODE, Refusal

MAX_NODES = 100
MAX_EDGES = 500
MAX_TEXT = 100_000
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
_OBSERVED = {"yes": True, "true": True, "no": False, "false": False}
_REFUSED_WORDS = {"graph": "a graph default statement", "node": "a node default statement",
                  "edge": "an edge default statement", "subgraph": "a subgraph"}


@dataclass(frozen=True)
class CausalGraph:
    """Nodes in declaration order, directed edges ``(cause, effect)`` and the nodes that are not observed."""

    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    unobserved: frozenset[str] = frozenset()
    _parents: dict[str, frozenset[str]] = field(default_factory=dict, compare=False, repr=False)
    _children: dict[str, frozenset[str]] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        parents: dict[str, set[str]] = {n: set() for n in self.nodes}
        children: dict[str, set[str]] = {n: set() for n in self.nodes}
        for a, b in self.edges:
            parents[b].add(a)
            children[a].add(b)
        object.__setattr__(self, "_parents", {n: frozenset(s) for n, s in parents.items()})
        object.__setattr__(self, "_children", {n: frozenset(s) for n, s in children.items()})

    @property
    def observed(self) -> tuple[str, ...]:
        return tuple(n for n in self.nodes if n not in self.unobserved)

    def parents(self, node: str) -> frozenset[str]:
        return self._parents[node]

    def children(self, node: str) -> frozenset[str]:
        return self._children[node]

    def _reach(self, start: Iterable[str], step: dict[str, frozenset[str]]) -> set[str]:
        seen: set[str] = set()
        stack = [m for n in start for m in step[n]]
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(step[node])
        return seen

    def ancestors(self, nodes: Iterable[str]) -> set[str]:
        """Every node with a directed path into one of ``nodes`` (the nodes themselves only if on a path)."""
        return self._reach(nodes, self._parents)

    def descendants(self, nodes: Iterable[str]) -> set[str]:
        """Every node a directed path from one of ``nodes`` reaches (the nodes themselves only if on a path)."""
        return self._reach(nodes, self._children)

    def without_edges_out_of(self, nodes: Iterable[str]) -> CausalGraph:
        cut = set(nodes)
        return CausalGraph(self.nodes, tuple(e for e in self.edges if e[0] not in cut), self.unobserved)

    def without_edges_into(self, nodes: Iterable[str]) -> CausalGraph:
        cut = set(nodes)
        return CausalGraph(self.nodes, tuple(e for e in self.edges if e[1] not in cut), self.unobserved)

    def as_dict(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes), "edges": [list(e) for e in self.edges],
                "unobserved": [n for n in self.nodes if n in self.unobserved]}


def read_graph(spec: Any) -> CausalGraph:
    """``spec`` (a JSON object, or DOT text) read and checked; a :class:`~g6.causal.envelope.Refusal` otherwise."""
    if isinstance(spec, Mapping):
        nodes, edges, unobserved = _from_json(spec)
    elif isinstance(spec, str):
        if len(spec) > MAX_TEXT:
            raise Refusal(INPUT_TOO_LARGE, f"the graph text has {len(spec)} characters, more than the {MAX_TEXT} G6 "
                                           "reads")
        if spec.lstrip().startswith("{"):
            raise Refusal(GRAPH_INVALID, "a graph given as text must be DOT, such as digraph { A -> B; }; give the "
                                         "JSON form as an object, not as a string")
        nodes, edges, unobserved = _Dot(spec).graph()
    else:
        raise Refusal(GRAPH_INVALID, "give the graph as a JSON object or as DOT text (digraph { A -> B; })")
    return _checked(nodes, edges, unobserved)


def _checked(nodes: list[str], edges: list[tuple[str, str]], unobserved: list[str]) -> CausalGraph:
    nodes = list(dict.fromkeys(nodes))
    edges = list(dict.fromkeys(edges))
    if not nodes:
        raise Refusal(GRAPH_INVALID, "the graph has no nodes")
    if len(nodes) > MAX_NODES:
        raise Refusal(INPUT_TOO_LARGE, f"{len(nodes)} nodes, more than the {MAX_NODES} a graph may have")
    if len(edges) > MAX_EDGES:
        raise Refusal(INPUT_TOO_LARGE, f"{len(edges)} edges, more than the {MAX_EDGES} a graph may have")
    declared = set(nodes)
    for a, b in edges:
        for name in (a, b):
            if name not in declared:
                raise Refusal(UNKNOWN_NODE, f"the edge {a} -> {b} names {name!r}, which is not one of the graph's "
                                            "nodes")
        if a == b:
            raise Refusal(SELF_LOOP, f"the edge {a} -> {a} is a self-loop")
    for name in unobserved:
        if name not in declared:
            raise Refusal(UNKNOWN_NODE, f"unobserved names {name!r}, which is not one of the graph's nodes")
    graph = CausalGraph(tuple(nodes), tuple(edges), frozenset(unobserved))
    cycle = _cycle(graph)
    if cycle:
        raise Refusal(GRAPH_CYCLE, "the graph has a cycle, " + " -> ".join(cycle) + ", and a causal graph has none")
    return graph


def _cycle(graph: CausalGraph) -> list[str]:
    """One directed cycle as its nodes, first node repeated at the end, or ``[]`` (depth first, without recursion)."""
    colour = dict.fromkeys(graph.nodes, 0)  # 0 unvisited, 1 on the stack, 2 done
    for root in graph.nodes:
        if colour[root]:
            continue
        path = [root]
        stack = [iter(sorted(graph.children(root), key=graph.nodes.index))]
        colour[root] = 1
        while stack:
            child = next(stack[-1], None)
            if child is None:
                colour[path.pop()] = 2
                stack.pop()
            elif colour[child] == 1:
                return path[path.index(child):] + [child]
            elif colour[child] == 0:
                colour[child] = 1
                path.append(child)
                stack.append(iter(sorted(graph.children(child), key=graph.nodes.index)))
    return []


def _name(value: Any, where: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise Refusal(GRAPH_INVALID, f"{where}: {value!r} is not a node name (a letter or _ then letters, digits and "
                                     "_, at most 100)")
    return value


def _from_json(spec: Mapping[str, Any]) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    extra = sorted(set(spec) - {"nodes", "edges", "unobserved"})
    if extra:
        raise Refusal(GRAPH_INVALID, f"the graph has the key {extra[0]!r}; it takes nodes, edges and unobserved only")
    if "nodes" not in spec:
        raise Refusal(GRAPH_INVALID, "the graph needs nodes: the list of every variable in it")
    parts = {}
    for key in ("nodes", "edges", "unobserved"):
        value = spec.get(key, [])
        if not isinstance(value, list):
            raise Refusal(GRAPH_INVALID, f"{key} must be a list, not {type(value).__name__}")
        if len(value) > 10 * MAX_EDGES:
            raise Refusal(INPUT_TOO_LARGE, f"{key} has {len(value)} items, more than a graph may have")
        parts[key] = value
    nodes = [_name(n, f"node {i}") for i, n in enumerate(parts["nodes"])]
    edges = []
    for i, edge in enumerate(parts["edges"]):
        if not isinstance(edge, list) or len(edge) != 2:
            raise Refusal(GRAPH_INVALID, f"edge {i} must be a list of two names, [cause, effect], not {edge!r}")
        edges.append((_name(edge[0], f"edge {i}"), _name(edge[1], f"edge {i}")))
    unobserved = [_name(n, f"unobserved {i}") for i, n in enumerate(parts["unobserved"])]
    return nodes, edges, unobserved


_TOKEN = re.compile(r"""
    (?P<space>[ \t\r\n]+)
  | (?P<line_comment>//[^\n]*)
  | (?P<block_comment>/\*.*?\*/)
  | (?P<open_comment>/\*)
  | (?P<arrow>->)
  | (?P<undirected>--)
  | (?P<punct>[{}\[\];=,])
  | (?P<quoted>"[^"\\\n]*")
  | (?P<open_quote>")
  | (?P<word>[A-Za-z0-9_.]+)
  | (?P<other>.)
""", re.VERBOSE | re.DOTALL)
_HASH_LINE = re.compile(r"^[ \t]*#[^\n]*", re.MULTILINE)


class _Dot:
    """A recursive-descent reader of the DOT subset; every refusal names the line and column."""

    def __init__(self, text: str) -> None:
        self.text = text
        # A line that starts with # is a comment: blanked first, keeping every position, so it cannot open a quote or a
        # block comment that swallows the lines after it.
        cleaned = _HASH_LINE.sub(lambda m: " " * len(m.group()), text)
        self.tokens = [(m.lastgroup or "", m.group(), m.start()) for m in _TOKEN.finditer(cleaned)
                       if m.lastgroup not in ("space", "line_comment", "block_comment")]
        self.i = 0

    def _where(self, at: int) -> str:
        line = self.text.count("\n", 0, at) + 1
        column = at - (self.text.rfind("\n", 0, at) + 1) + 1
        return f"line {line}, column {column}"

    def fail(self, message: str, at: int | None = None) -> Refusal:
        if at is None:
            at = self.tokens[self.i][2] if self.i < len(self.tokens) else len(self.text)
        if at >= len(self.text.rstrip()) and self.i >= len(self.tokens):
            return Refusal(GRAPH_INVALID, f"at the end of the text: {message}")
        return Refusal(GRAPH_INVALID, f"{self._where(at)}: {message}")

    def peek(self) -> tuple[str, str, int] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str, int]:
        token = self.peek()
        if token is None:
            raise self.fail("the graph ends before its closing brace")
        self.i += 1
        return token

    def expect(self, value: str, what: str) -> None:
        token = self.take()
        if token[1] != value:
            raise self.fail(f"expected {what}, found {token[1]!r}", token[2])

    def name(self) -> str:
        kind, value, at = self.take()
        if kind == "open_comment":
            raise self.fail("a /* comment is never closed", at)
        if kind == "open_quote":
            raise self.fail("a quote is never closed", at)
        text = value[1:-1] if kind == "quoted" else value
        if kind not in ("quoted", "word") or not NAME.fullmatch(text):
            raise self.fail(f"{text!r} is not a node name (a letter or _ then letters, digits and _, at most 100)", at)
        if kind == "word" and text.lower() in _REFUSED_WORDS:
            raise self.fail(f"{_REFUSED_WORDS[text.lower()]} is not part of the DOT G6 reads: give edges A -> B and "
                            "nodes, with [observed=no] for an unobserved one", at)
        return text

    def graph(self) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        if not self.tokens:
            raise Refusal(GRAPH_INVALID, "the graph text is empty: give digraph { A -> B; }")
        for kind, value, at in self.tokens:
            if kind == "open_comment":
                raise self.fail("a /* comment is never closed", at)
            if kind == "open_quote":
                raise self.fail("a quote is never closed", at)
            if kind == "undirected":
                raise self.fail("'--' is an undirected edge, which says nothing about which variable causes which; "
                                "use ->", at)
        kind, value, at = self.take()
        if value.lower() == "strict":
            raise self.fail("'strict' is not part of the DOT G6 reads; repeated edges count once anyway", at)
        if value.lower() == "graph":
            raise self.fail("an undirected graph says nothing about which variable causes which; use digraph", at)
        if value.lower() != "digraph":
            raise self.fail(f"a graph starts with digraph, not {value!r}", at)
        token = self.peek()
        if token is not None and token[0] in ("word", "quoted"):
            self.take()  # the graph's own name, which nothing uses
        self.expect("{", "'{'")
        nodes: list[str] = []
        edges: list[tuple[str, str]] = []
        unobserved: dict[str, bool] = {}
        while True:
            token = self.peek()
            if token is None:
                raise self.fail("the graph ends before its closing brace")
            if token[1] == "}":
                self.take()
                break
            if token[1] == ";":
                self.take()
                continue
            chain = [self.name()]
            while (nxt := self.peek()) is not None and nxt[0] == "arrow":
                self.take()
                chain.append(self.name())
            nodes.extend(chain)
            edges.extend(zip(chain, chain[1:]))
            if (nxt := self.peek()) is not None and nxt[1] == "[":
                if len(chain) > 1:
                    raise self.fail("an edge takes no attributes; give [observed=no] on the node alone", nxt[2])
                unobserved[chain[0]] = not self.observed_attribute()
        if self.peek() is not None:
            raise self.fail(f"found {self.peek()[1]!r} after the closing brace")
        if not nodes:
            raise Refusal(GRAPH_INVALID, "the graph has no nodes")
        return nodes, edges, [n for n, hidden in unobserved.items() if hidden]

    def observed_attribute(self) -> bool:
        self.expect("[", "'['")
        kind, key, at = self.take()
        if key != "observed":
            raise self.fail(f"the only attribute G6 reads is observed (observed=no for an unobserved node), not "
                            f"{key!r}", at)
        self.expect("=", "'=' after observed")
        kind, value, at = self.take()
        word = value[1:-1] if kind == "quoted" else value
        if word.lower() not in _OBSERVED:
            raise self.fail(f"observed takes yes or no (or true or false), not {word!r}", at)
        if (nxt := self.peek()) is not None and nxt[1] in (",", ";"):
            self.take()
        self.expect("]", "']' to close the attributes")
        return _OBSERVED[word.lower()]


__all__ = ["MAX_EDGES", "MAX_NODES", "MAX_TEXT", "NAME", "CausalGraph", "read_graph"]
