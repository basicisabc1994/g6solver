# SPDX-License-Identifier: Apache-2.0
"""GraphSpec: a LangGraph graph written as typed data, the form a graph takes over MCP.

A spec names the graph's state (its keys and their starting values), its nodes, its edges and its entry node, and the
tools its tool nodes may call (each a family ``ToolSpec``, so the runtime's gate knows what it does). Node operations:

* ``set`` (``key`` = ``value``), ``append`` (``value`` to the list at ``key``), ``increment`` (``key`` by ``by``) and
  ``pass``: deterministic, in G6's process;
* ``llm``: ``prompt`` (and ``system``) with ``{key}`` filled from the state, sent to the model the person passed in from
  Python (``g6.core.llm``); its text goes to ``key``. Over MCP no model is passed, so a graph with an llm node is
  refused before it runs;
* ``tool``: one call of a declared tool, its ``arguments`` (and ``paths``) filled from the state, decided first by the
  runtime's gate; what happened (``verdict`` run, dry_run or refused, with the code, and the result when it ran) goes
  to ``key``;
* ``branch``: no change to the state; it goes to ``then`` when the value at ``key`` is truthy (or equals ``value``,
  when one is given) and to ``else`` otherwise.

A template fills only ``{name}`` or ``{name.field}`` from the state's own values, nothing else: no attribute, index or
call. The spec's digest identifies the graph a thread was started with.

Sizes are capped, so a graph cannot grow its state without limit: a filled value, and a value a node writes, hold at
most :data:`MAX_VALUE_CHARS` (1 MiB, counted in characters of text, anything else by its JSON), and :func:`fill`
refuses one past that before building it (:class:`TooLarge`); a node's own ``value`` and ``arguments`` hold at most
:data:`MAX_SPEC_CHARS`, and the starting state at most :data:`MAX_VALUE_CHARS`.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from g6.agents._runtime import Permissions, ToolSpec

END = "END"
NAME = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
KEY = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
#: A place in the state a template or a branch may read: a key, or a key and one field of the dict it holds.
PLACE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")
FIELD = re.compile(r"\{(" + PLACE.pattern + r")\}")
_TEXT = 20_000
#: The most a filled value, a value a node writes, the starting state or the input holds: 1 MiB, in characters.
MAX_VALUE_CHARS = 1024 * 1024
#: The most a node's ``value`` or ``arguments``, as the spec writes them, holds.
MAX_SPEC_CHARS = 100_000
#: The code of a run refused because a value, a checkpoint or a thread would pass its cap.
TOO_LARGE = "G6_E_AGENT_LANGGRAPH_TOO_LARGE"


class TooLarge(ValueError):
    """A value past its cap; nothing larger was built or written."""


def size_of(value: Any, limit: int) -> int:
    """About how many characters ``value`` holds as JSON; it stops counting once past ``limit``, so it is cheap."""
    size, stack = 0, [value]
    while stack and size <= limit:
        item = stack.pop()
        if isinstance(item, str):
            size += len(item) + 2
        elif isinstance(item, dict):
            size += 2 + 2 * len(item)
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            size += 2 + len(item)
            stack.extend(item)
        else:
            size += 8
    return size


class GraphNode(BaseModel):
    """One node; which fields it uses depends on ``op`` (the module docstring)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    name: str = Field(pattern=NAME)
    op: Literal["set", "append", "increment", "pass", "llm", "tool", "branch"]
    key: str = Field("", max_length=130)
    value: Any = None
    by: float = Field(1, allow_inf_nan=False)
    prompt: str = Field("", max_length=_TEXT)
    system: str = Field("", max_length=_TEXT)
    tool: str = Field("", max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    paths: tuple[str, ...] = Field((), max_length=50)
    then: str = Field("", max_length=64)
    otherwise: str = Field("", max_length=64, alias="else")

    @model_validator(mode="after")
    def _fields_for_its_op(self) -> GraphNode:
        needs = {"set": "key", "append": "key", "increment": "key", "llm": "key", "tool": "key", "branch": "key"}
        if self.op in needs and not self.key:
            raise ValueError(f"node {self.name!r} ({self.op}) needs a key")
        if self.key and not (re.fullmatch(KEY, self.key) or (self.op == "branch" and PLACE.fullmatch(self.key))):
            raise ValueError(f"node {self.name!r}: {self.key!r} is not a state key")
        if self.op == "llm" and not self.prompt.strip():
            raise ValueError(f"node {self.name!r} (llm) needs a prompt")
        if self.op == "tool" and not self.tool:
            raise ValueError(f"node {self.name!r} (tool) needs a tool")
        if self.op == "branch" and not (self.then and self.otherwise):
            raise ValueError(f"node {self.name!r} (branch) needs then and else")
        if size_of(self.value, MAX_SPEC_CHARS) > MAX_SPEC_CHARS or \
                size_of(self.arguments, MAX_SPEC_CHARS) > MAX_SPEC_CHARS:
            raise ValueError(f"node {self.name!r}: its value or arguments hold more than {MAX_SPEC_CHARS} characters")
        return self


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(pattern=NAME)
    target: str = Field(max_length=64)  # a node, or END


class GraphSpec(BaseModel):
    """A whole graph; see the module docstring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: dict[str, Any] = Field(default_factory=dict, max_length=100)
    nodes: tuple[GraphNode, ...] = Field(min_length=1, max_length=50)
    edges: tuple[GraphEdge, ...] = Field((), max_length=200)
    entry: str = Field(pattern=NAME)
    tools: tuple[ToolSpec, ...] = Field((), max_length=50)
    permissions: Permissions = Field(default_factory=Permissions)

    @model_validator(mode="after")
    def _a_graph_that_holds_together(self) -> GraphSpec:
        names = [n.name for n in self.nodes]
        if len(set(names)) != len(names):
            raise ValueError("two nodes share a name")
        known = set(names)
        if self.entry not in known:
            raise ValueError(f"the entry {self.entry!r} is not a node")
        bad = [k for k in self.state if not re.fullmatch(KEY, k)]
        if bad:
            raise ValueError(f"not state keys: {bad}")
        if size_of(self.state, MAX_VALUE_CHARS) > MAX_VALUE_CHARS:
            raise ValueError(f"the starting state holds more than {MAX_VALUE_CHARS} characters")
        for edge in self.edges:
            if edge.source not in known or (edge.target != END and edge.target not in known):
                raise ValueError(f"the edge {edge.source} -> {edge.target} names a node the graph does not have")
        tools = {t.name for t in self.tools}
        for node in self.nodes:
            if node.op in ("set", "append", "increment", "llm", "tool") and node.key not in self.state:
                raise ValueError(f"node {node.name!r} writes {node.key!r}, which the state does not declare")
            if node.op == "branch":
                if node.key.partition(".")[0] not in self.state:
                    raise ValueError(f"node {node.name!r} reads {node.key!r}, which the state does not declare")
                for target in (node.then, node.otherwise):
                    if target != END and target not in known:
                        raise ValueError(f"node {node.name!r} goes to {target!r}, which is not a node")
                if any(e.source == node.name for e in self.edges):
                    raise ValueError(f"node {node.name!r} is a branch: it goes on through then and else, not edges")
            if node.op == "tool" and node.tool not in tools:
                raise ValueError(f"node {node.name!r} calls {node.tool!r}, which the spec's tools do not declare")
        return self

    @property
    def has_llm(self) -> bool:
        return any(n.op == "llm" for n in self.nodes)

    def digest(self) -> str:
        """What identifies this graph: a hash of its canonical form."""
        text = json.dumps(self.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))
        return "spec:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def lookup(state: dict[str, Any], place: str) -> Any:
    """The value at ``place`` (``name`` or ``name.field``) in ``state``; ``KeyError`` when there is none."""
    head, _, field = place.partition(".")
    value = state[head]
    if field:
        if not isinstance(value, dict):
            raise KeyError(place)
        value = value[field]
    return value


def fill(template: Any, state: dict[str, Any], *, limit: int = MAX_VALUE_CHARS) -> Any:
    """``template`` with each ``{name}`` or ``{name.field}`` replaced from ``state`` (text as it is, anything else as
    JSON; a template that is one place alone gives that value itself); dicts and lists are filled throughout. A place
    the state does not hold raises ``KeyError``; a value that would pass ``limit`` characters raises :class:`TooLarge`
    before it is built."""
    left = [limit]

    def spend(size: int) -> None:
        left[0] -= size
        if left[0] < 0:
            raise TooLarge(f"a filled value would hold more than {limit} characters, so it was not built")

    def go(part: Any) -> Any:
        if isinstance(part, str):
            whole = FIELD.fullmatch(part)
            if whole:
                value = lookup(state, whole.group(1))
                spend(size_of(value, left[0]))
                return value
            spend(len(part))
            texts: list[str] = []
            for match in FIELD.finditer(part):
                value = lookup(state, match.group(1))
                if not isinstance(value, str) and size_of(value, left[0]) > left[0]:
                    spend(left[0] + 1)
                text = value if isinstance(value, str) else json.dumps(value, default=str)
                spend(len(text))
                texts.append(text)
            pieces = iter(texts)
            return FIELD.sub(lambda match: next(pieces), part)
        if isinstance(part, dict):
            return {k: go(v) for k, v in part.items()}
        if isinstance(part, (list, tuple)):
            return [go(v) for v in part]
        return part
    return go(template)


__all__ = ["END", "MAX_SPEC_CHARS", "MAX_VALUE_CHARS", "TOO_LARGE", "GraphEdge", "GraphNode", "GraphSpec", "TooLarge",
           "fill", "lookup", "size_of"]