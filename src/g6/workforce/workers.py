# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Workers: bounded execution policies (workforce OS plan, P0 T5).

A worker is not a prompt, a model, a component or a persona. It is a ``WorkerSpec``: what it may call
(a non-empty (component, op) allowlist, each pair with a ParamContract), its ceilings (the safety
engine's trusted ``RuntimePolicy``), its rung and whether it is read-only, formed from a template
(``templates/<name>.json``, a starting point) and narrowed under the mission's ceiling.

Narrowing is checked field by field (``NARROWING`` holds one rule per ``RuntimePolicy`` field, and a
test fails if a field is added without one): a worker may only be tighter than its ceiling. The
policy engine reads an EMPTY ``allowed_tools`` as allow-any, so a worker never has one. A plan never
builds a policy: the ceiling is trusted configuration and the plan only names capabilities.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.safety_engine.schema import RuntimePolicy
from g6.workforce.capabilities import ParamContract
from g6.workforce.plans import UnitSpec

RUNGS = ("deterministic", "verify", "local", "pull", "human")
TEMPLATES_DIR = Path(__file__).parent / "templates"
#: A template whose allowlist is the unit's capabilities (or the probed pair).
FROM_UNIT = "from_unit"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_TEMPLATE_FIELDS = frozenset({"name", "purpose", "rung", "read_only", "allowed", "policy", "may_delegate"})
#: A read-only worker writes nothing and runs nothing.
_READ_ONLY_ZERO = ("max_shell_commands", "max_files_modified", "max_lines_changed")
_POLICY_FIELDS = {f.name: f for f in dataclasses.fields(RuntimePolicy)}
_SETS = ("allowed_tools", "forbidden_paths")


def _no_more(requested: Any, ceiling: Any) -> bool:
    return requested <= ceiling


def _no_less(requested: Any, ceiling: Any) -> bool:
    return requested >= ceiling


#: For each RuntimePolicy field: True when the worker's value is no wider than the ceiling's.
NARROWING: dict[str, Callable[[Any, Any], bool]] = {
    "max_steps": _no_more, "max_tool_calls": _no_more, "max_shell_commands": _no_more,
    "max_files_modified": _no_more, "max_lines_changed": _no_more, "max_retries": _no_more,
    "max_runtime_seconds": _no_more, "max_cost_cents": _no_more, "max_privilege_level": _no_more,
    # Never empty (allow-any to the policy engine), and inside the ceiling's list when it has one.
    "allowed_tools": lambda requested, ceiling: bool(requested) and (not ceiling or requested <= ceiling),
    "forbidden_paths": _no_less,  # a superset: more is forbidden, never less
    "require_tests_before_final": lambda requested, ceiling: requested or not ceiling,
    "require_quorum": lambda requested, ceiling: requested or not ceiling,
    "quorum_n": _no_less, "quorum_f": _no_less, "quorum_required_accepts": _no_less,
}


class WideningRefused(ValueError):
    """A worker asked for more than its ceiling allows (or for a field the policy does not have)."""


def _typed(name: str, value: Any) -> bool:
    if name in _SETS:
        return isinstance(value, frozenset) and all(isinstance(v, str) for v in value)
    kind = _POLICY_FIELDS[name].type
    if kind in ("bool", bool):
        return isinstance(value, bool)
    return isinstance(value, int) and not isinstance(value, bool)


def narrow(ceiling: RuntimePolicy, **requested: Any) -> RuntimePolicy:
    """The ceiling with the requested values, refused if any field would widen it."""
    unknown = sorted(set(requested) - set(_POLICY_FIELDS))
    if unknown:
        raise WideningRefused(f"unknown policy field(s) {unknown}")
    values = {name: requested.get(name, getattr(ceiling, name)) for name in _POLICY_FIELDS}
    for name in _SETS:
        if isinstance(values[name], (set, list, tuple)):
            values[name] = frozenset(values[name])
    mistyped = sorted(name for name, value in values.items() if not _typed(name, value))
    if mistyped:
        raise WideningRefused(f"policy field(s) of the wrong type: {mistyped}")
    wider = sorted(name for name in _POLICY_FIELDS if not NARROWING[name](values[name], getattr(ceiling, name)))
    if wider:
        raise WideningRefused(f"a worker may only narrow its ceiling; these would widen it: {wider}")
    return RuntimePolicy(**values)


def _names(pairs: tuple[tuple[str, str], ...]) -> frozenset[str]:
    return frozenset(f"{component}.{op}" for component, op in pairs)


@dataclass(frozen=True)
class WorkerSpec:
    template: str
    purpose: str
    rung: str
    allowed: tuple[tuple[str, str], ...]
    policy: RuntimePolicy
    read_only: bool
    #: Whether this worker may hand part of its unit to children (V4a: only the owner's chat, the pull rung).
    may_delegate: bool = False
    sha256: str = dataclasses.field(init=False, default="")

    def __post_init__(self) -> None:
        if self.rung not in RUNGS:
            raise ValueError(f"rung must be one of {RUNGS}, got {self.rung!r}")
        allowed = tuple(dict.fromkeys((str(c), str(o)) for c, o in self.allowed))
        if not allowed:
            raise ValueError("a worker needs a non-empty allowlist")
        if self.policy.allowed_tools != _names(allowed):
            raise ValueError("the worker's policy must permit exactly its allowlist")
        if self.read_only and any(getattr(self.policy, name) for name in _READ_ONLY_ZERO):
            raise ValueError(f"a read-only worker has {list(_READ_ONLY_ZERO)} at 0")
        object.__setattr__(self, "allowed", allowed)
        policy = {name: sorted(value) if name in _SETS else value
                  for name, value in dataclasses.asdict(self.policy).items()}
        body = {"template": self.template, "purpose": self.purpose, "rung": self.rung,
                "allowed": [list(p) for p in allowed], "policy": policy, "read_only": self.read_only}
        if self.may_delegate:  # only when set, so every worker formed before V4a keeps its digest
            body["may_delegate"] = True
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True)
class Template:
    name: str
    purpose: str
    rung: str
    read_only: bool
    allowed: tuple[tuple[str, str], ...] | None  # None: the unit's capabilities
    policy: dict[str, Any]
    may_delegate: bool = False


def _template(data: Any, label: str) -> Template:
    if not isinstance(data, dict):
        raise ValueError(f"template {label}: a template is a JSON object")  # noqa: TRY004 - one refusal type
    problems = []
    unknown = sorted(set(data) - _TEMPLATE_FIELDS)
    if unknown:
        problems.append(f"unknown field(s) {unknown}")
    if not (isinstance(data.get("name"), str) and _NAME.match(data["name"])):
        problems.append("name must be lowercase letters, digits and '_'")
    if not isinstance(data.get("purpose"), str) or not data["purpose"].strip():
        problems.append("purpose is required")
    if data.get("rung") not in RUNGS:
        problems.append(f"rung must be one of {list(RUNGS)}")
    if not isinstance(data.get("read_only"), bool):
        problems.append("read_only must be true or false")
    if not isinstance(data.get("may_delegate", False), bool):
        problems.append("may_delegate must be true or false")
    allowed = data.get("allowed")
    pairs: tuple[tuple[str, str], ...] | None = None
    if allowed != FROM_UNIT:
        if not (isinstance(allowed, list) and allowed and all(
                isinstance(p, list) and len(p) == 2 and all(isinstance(n, str) and n for n in p) for p in allowed)):
            problems.append(f"allowed must be '{FROM_UNIT}' or a non-empty list of [component, op] pairs")
        else:
            pairs = tuple((p[0], p[1]) for p in allowed)
    policy = data.get("policy", {})
    if not isinstance(policy, dict):
        problems.append("policy must be an object")
        policy = {}
    elif "allowed_tools" in policy:
        problems.append("policy never names allowed_tools: it is derived from the allowlist")
    else:
        for key, value in policy.items():
            if key not in _POLICY_FIELDS:
                problems.append(f"policy names no RuntimePolicy field {key!r}")
            elif key in _SETS:
                if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                    problems.append(f"policy {key} must be a list of strings")
            elif not _typed(key, value):
                problems.append(f"policy {key} has the wrong type")
    if problems:
        raise ValueError(f"template {label}: " + "; ".join(problems))
    return Template(name=data["name"], purpose=data["purpose"], rung=data["rung"], read_only=data["read_only"],
                    allowed=pairs, policy=dict(policy), may_delegate=data.get("may_delegate") is True)


def load_templates(directory: str | Path | None = None) -> dict[str, Template]:
    directory = TEMPLATES_DIR if directory is None else Path(directory)
    out: dict[str, Template] = {}
    for path in sorted(directory.glob("*.json")):
        template = _template(json.loads(path.read_text(encoding="utf-8")), path.name)
        if path.name != f"{template.name}.json":
            raise ValueError(f"{path.name}: a template file is named <name>.json, here {template.name}.json")
        out[template.name] = template
    return out


def form_worker(template: str, *, ceiling: RuntimePolicy, contracts: Callable[[str, str], ParamContract | None],
                unit: UnitSpec | None = None, allowed: tuple[tuple[str, str], ...] | None = None) -> WorkerSpec:
    """The template narrowed to one unit (or one probed pair) under the mission's ceiling."""
    base = load_templates()[template]
    pairs = tuple(allowed) if allowed is not None else (unit.capabilities if unit is not None else ())
    if base.allowed is not None:
        pairs = pairs or base.allowed
        outside = [p for p in pairs if p not in base.allowed]
        if outside:
            raise ValueError(f"template {template!r} does not allow {outside}")
    if not pairs:
        raise ValueError("a worker needs a non-empty allowlist, and the unit names no capability")
    # A pull worker calls no component (the owner's chat writes the output), nor does a human-rung worker (it
    # records a person's decision, V4c), so neither has a contract to check.
    for component, op in (() if base.rung in ("pull", "human") else pairs):
        contract = contracts(component, op)
        if contract is None:
            raise ValueError(f"no ParamContract declares {component}.{op}")
        # A prober may hold an op whose probe is declared to have no effect (V3a); its door lets only those exact
        # probe parameters through.
        if base.read_only and contract.side_effect != "read" and not (base.name == "prober" and contract.shadow_probe):
            raise ValueError(f"a read-only worker cannot call {component}.{op} ({contract.side_effect})")
    requested = {k: frozenset(v) if k in _SETS else v for k, v in base.policy.items()}
    requested["forbidden_paths"] = requested.get("forbidden_paths", frozenset()) | ceiling.forbidden_paths
    if base.read_only:
        requested.update(dict.fromkeys(_READ_ONLY_ZERO, 0))
    if unit is not None:
        requested["max_cost_cents"] = unit.budget_cents
    requested["allowed_tools"] = _names(tuple(pairs))
    return WorkerSpec(template=base.name, purpose=base.purpose, rung=base.rung, allowed=tuple(pairs),
                      policy=narrow(ceiling, **requested), read_only=base.read_only, may_delegate=base.may_delegate)
