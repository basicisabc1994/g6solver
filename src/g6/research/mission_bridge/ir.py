# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The research ``MissionIR`` (integrated audit ENG-01): a mission graph as typed, hashed, immutable records.

It maps onto what G6 already has and adds nothing to it. A node's ``source_operation`` and ``declared_effect`` are the
``(component, op)`` pair and side effect a ``UnitSpec`` capability and its ParamContract carry; ``requires`` and
``provides`` are the typed artifacts a unit's inputs and output_schema name; ``standard`` is the frozen standard's hash
(``AcceptanceContract.sha256``) and predicates; ``budget_cents`` is a unit's budget, ``None`` meaning unknown (never 0,
as ``economics`` has it). The document is read strictly: an unknown field, a wrong type or a value outside its set is
refused with every problem listed, never repaired. ``validate`` (validate.py) judges the graph; nothing here runs an op.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .flag import require_research

SCHEMA = "mission_ir.v1"
EFFECTS = ("read", "local_write", "external", "irreversible")
DATA_CLASSES = ("public", "synthetic", "sensitive")
EGRESS = ("none", "declared")
APPROVAL_SCOPES = ("egress", "egress_sensitive")
_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_OP = re.compile(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+\Z")


class IRRejected(ValueError):
    """A mission document the bridge will not read, with every problem found."""

    def __init__(self, problems: list[str] | tuple[str, ...]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Standard:
    standard_id: str
    sha256: str
    predicates: tuple[str, ...]


@dataclass(frozen=True)
class NodeStandard:
    """The standard a node says it works to; it may add predicates but may not drop one or change the hash."""

    sha256: str
    predicates: tuple[str, ...]


@dataclass(frozen=True)
class Approval:
    node: str
    scope: str


@dataclass(frozen=True)
class Node:
    id: str
    role: str
    source_operation: str
    declared_effect: str
    requires: tuple[tuple[str, str], ...]
    provides: tuple[tuple[str, str], ...]
    data_class: str = "public"
    egress: str = "none"
    budget_cents: int | None = None
    verifies: str = ""
    standard: NodeStandard | None = None
    #: A person's decision: it names no operation, runs nothing, and is what an external op waits behind.
    gate: bool = False

    @property
    def component(self) -> str:
        return self.source_operation.split(".", 1)[0]


@dataclass(frozen=True)
class MissionIR:
    mission_id: str
    domain: str
    high_stakes: bool
    inputs: tuple[tuple[str, str], ...]
    standard: Standard
    nodes: tuple[Node, ...]
    budget_cents: int | None
    charged: bool
    approvals: tuple[Approval, ...]
    required_roles: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        def pairs(items):
            return {name: kind for name, kind in items}

        nodes = []
        for n in self.nodes:
            body: dict[str, Any] = {
                "id": n.id, "role": n.role, "source_operation": n.source_operation,
                "declared_effect": n.declared_effect, "requires": pairs(n.requires), "provides": pairs(n.provides),
                "data_class": n.data_class, "egress": n.egress, "budget_cents": n.budget_cents, "verifies": n.verifies,
                "gate": n.gate}
            if n.standard is not None:
                body["standard"] = {"standard_sha256": n.standard.sha256, "predicates": list(n.standard.predicates)}
            nodes.append(body)
        return {
            "schema": SCHEMA, "mission_id": self.mission_id, "domain": self.domain, "high_stakes": self.high_stakes,
            "inputs": pairs(self.inputs),
            "standard": {"standard_id": self.standard.standard_id, "standard_sha256": self.standard.sha256,
                         "predicates": list(self.standard.predicates)},
            "nodes": nodes, "budget_cents": self.budget_cents, "charged": self.charged,
            "approvals": [{"node": a.node, "scope": a.scope} for a in self.approvals],
            "required_roles": list(self.required_roles)}

    @property
    def sha256(self) -> str:
        text = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, raw: Any) -> MissionIR:
        require_research()
        problems: list[str] = []
        ir = _read(raw, problems)
        if problems or ir is None:
            raise IRRejected(problems or ["mission: not readable"])
        return ir


# --- reading ----------------------------------------------------------------------------------------------------------

_TOP = {"mission_id", "domain", "high_stakes", "inputs", "standard", "nodes", "budget_cents", "charged", "approvals",
        "required_roles"}
_TOP_REQUIRED = {"mission_id", "domain", "high_stakes", "inputs", "standard", "nodes"}
_NODE = {"id", "role", "source_operation", "declared_effect", "requires", "provides", "data_class", "egress",
         "budget_cents", "verifies", "standard", "gate"}
_NODE_REQUIRED = {"id", "role", "source_operation", "declared_effect", "requires", "provides"}


def _fields(raw: Any, allowed: set[str], required: set[str], where: str, problems: list[str]) -> dict | None:
    if not isinstance(raw, Mapping):
        problems.append(f"{where}: must be an object")
        return None
    body = dict(raw)
    for name in sorted(set(body) - allowed):
        problems.append(f"{where}: unknown field {name!r}")
    for name in sorted(required - set(body)):
        problems.append(f"{where}: {name!r} is required")
    return body


def _text(value: Any, where: str, problems: list[str], *, pattern: re.Pattern | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: must be non-empty text")
        return ""
    if pattern is not None and not pattern.match(value):
        problems.append(f"{where}: {value!r} is not in the allowed form")
        return ""
    return value


def _choice(value: Any, options: tuple[str, ...], where: str, problems: list[str]) -> str:
    if value not in options:
        problems.append(f"{where}: must be one of {options}, got {value!r}")
        return ""
    return value


def _cents(value: Any, where: str, problems: list[str]) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        problems.append(f"{where}: must be a whole number of cents, 0 or more, or null (unknown)")
        return None
    return value


def _flag(value: Any, where: str, problems: list[str]) -> bool:
    if not isinstance(value, bool):
        problems.append(f"{where}: must be true or false")
        return False
    return value


def _typed(raw: Any, where: str, problems: list[str]) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, Mapping):
        problems.append(f"{where}: must be an object of name to type")
        return ()
    out = []
    for name, kind in raw.items():
        out.append((_text(name, f"{where} name", problems, pattern=_ID),
                    _text(kind, f"{where}[{name!r}] type", problems)))
    return tuple(sorted(out))


def _predicates(raw: Any, where: str, problems: list[str]) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        problems.append(f"{where}: must be a non-empty list of predicate names")
        return ()
    return tuple(sorted({_text(item, where, problems) for item in raw}))


def _sha(value: Any, where: str, problems: list[str]) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        problems.append(f"{where}: must be a sha256 hex digest")
        return ""
    return value


def _node(raw: Any, index: int, problems: list[str]) -> Node | None:
    where = f"nodes[{index}]"
    body = _fields(raw, _NODE, _NODE_REQUIRED, where, problems)
    if body is None:
        return None
    node_id = _text(body.get("id"), f"{where}.id", problems, pattern=_ID)
    where = f"node {node_id!r}" if node_id else where
    own = None
    if body.get("standard") is not None:
        found = _fields(body["standard"], {"standard_sha256", "predicates"}, {"standard_sha256", "predicates"},
                        f"{where}.standard", problems)
        if found is not None:
            own = NodeStandard(_sha(found.get("standard_sha256"), f"{where}.standard.standard_sha256", problems),
                               _predicates(found.get("predicates"), f"{where}.standard.predicates", problems))
    verifies = body.get("verifies", "")
    if verifies:
        verifies = _text(verifies, f"{where}.verifies", problems, pattern=_ID)
    gate = _flag(body.get("gate", False), f"{where}.gate", problems)
    if gate:
        operation = body.get("source_operation")
        if operation != "":
            problems.append(f"{where}.source_operation: a gate is a person's decision and names no operation")
    else:
        operation = _text(body.get("source_operation"), f"{where}.source_operation", problems, pattern=_OP)
    return Node(
        id=node_id, role=_text(body.get("role"), f"{where}.role", problems), gate=gate,
        source_operation=operation,
        declared_effect=_choice(body.get("declared_effect"), EFFECTS, f"{where}.declared_effect", problems),
        requires=_typed(body.get("requires"), f"{where}.requires", problems),
        provides=_typed(body.get("provides"), f"{where}.provides", problems),
        data_class=_choice(body.get("data_class", "public"), DATA_CLASSES, f"{where}.data_class", problems),
        egress=_choice(body.get("egress", "none"), EGRESS, f"{where}.egress", problems),
        budget_cents=_cents(body.get("budget_cents"), f"{where}.budget_cents", problems),
        verifies=verifies or "", standard=own)


def _read(raw: Any, problems: list[str]) -> MissionIR | None:
    body = _fields(raw, _TOP, _TOP_REQUIRED, "mission", problems)
    if body is None:
        return None
    std = None
    found = _fields(body.get("standard"), {"standard_id", "standard_sha256", "predicates"},
                    {"standard_id", "standard_sha256", "predicates"}, "standard", problems)
    if found is not None:
        std = Standard(_text(found.get("standard_id"), "standard.standard_id", problems),
                       _sha(found.get("standard_sha256"), "standard.standard_sha256", problems),
                       _predicates(found.get("predicates"), "standard.predicates", problems))
    raw_nodes = body.get("nodes")
    nodes: list[Node] = []
    if not isinstance(raw_nodes, list) or not raw_nodes:
        problems.append("mission: needs at least one node")
    else:
        for index, item in enumerate(raw_nodes):
            read = _node(item, index, problems)
            if read is not None:
                nodes.append(read)
        seen: set[str] = set()
        for item in nodes:
            if item.id in seen:
                problems.append(f"node {item.id!r}: duplicate id")
            seen.add(item.id)
    approvals = []
    raw_approvals = body.get("approvals", [])
    if not isinstance(raw_approvals, list):
        problems.append("approvals: must be a list")
        raw_approvals = []
    for index, item in enumerate(raw_approvals):
        got = _fields(item, {"node", "scope"}, {"node", "scope"}, f"approvals[{index}]", problems)
        if got is not None:
            approvals.append(Approval(_text(got.get("node"), f"approvals[{index}].node", problems, pattern=_ID),
                                      _choice(got.get("scope"), APPROVAL_SCOPES, f"approvals[{index}].scope", problems)))
    roles = body.get("required_roles", [])
    if not isinstance(roles, list):
        problems.append("required_roles: must be a list")
        roles = []
    if problems or std is None:
        return None
    return MissionIR(
        mission_id=_text(body.get("mission_id"), "mission_id", problems),
        domain=_text(body.get("domain"), "domain", problems),
        high_stakes=_flag(body.get("high_stakes"), "high_stakes", problems),
        inputs=_typed(body.get("inputs"), "inputs", problems), standard=std, nodes=tuple(nodes),
        budget_cents=_cents(body.get("budget_cents"), "budget_cents", problems),
        charged=_flag(body.get("charged", False), "charged", problems), approvals=tuple(approvals),
        required_roles=tuple(_text(r, "required_roles", problems) for r in roles)) if not problems else None
