# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Plans: the work graph a mission runs, as data (workforce OS plan, P0 T3).

The owner's chat session drafts a plan; ``parse_plan`` is the code that judges it before anything
is stored or run. A plan is ``{"units": [...], "edges": [[from, to], ...]}``: ``to`` consumes what
``from`` produced. A unit is ``do`` (produces), ``verify`` (judges one do unit's output) or ``gate``
(a human decision). Unknown fields are refused rather than dropped, so a misspelt field cannot
silently vanish, and every problem is reported at once, each naming its unit and the rule.

The rule that carries the completion contract: a consumer depends on the VERIFY unit of what it
consumes, never on the producer (``Plan.depends_on``), so "complete" flows downstream only after
verification. Every do unit therefore needs a verify unit. Capability readiness is judged later
(T4); this module only checks that a capability is named as a ``[component, op]`` pair.

Delegation (V4a): a unit may name the do unit that delegated it (``parent``). Every child is judged here,
however its revision was made, so delegation never widens what its parent had (``_delegation``): a child
names only its parent's capabilities (a child verifier also those of the verifiers its parent was given),
its risk and attempts are at most its parent's, the children's budgets are carved out of their parent's
(the mission total counts top-level units only), depth and fan-out are capped, a child verifier judges
only its own subtree and a child reads only what its parent reads or its subtree makes.

External action (V3a): judged with the broker's declared side effects, a unit naming an ``external`` or
``irreversible`` op is refused unless a gate (a person's approval) is upstream of it, by input or edge.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from graphlib import CycleError, TopologicalSorter
from typing import Any

from g6.intent_engine.schema import CheckpointSpec
from g6.workforce.contracts import (
    AUTHORITY,
    AUTHORITY_COPY,
    INVALID_PRODUCED_FACTS,
    authority_rank,
)
from pydantic import ValidationError

RISK_TIERS = ("R0", "R1", "R2", "R3")
UNIT_KINDS = ("do", "verify", "gate")
MAX_UNITS = 200
MAX_ATTEMPTS = 5
#: How deep a delegation goes below a planned unit, and how many children one parent may have (V4a).
MAX_DEPTH = 3
MAX_CHILDREN = 6


def higher(*tiers: str) -> str:
    """The highest of these risk tiers ("R0" when none is given). One that is not a tier counts as R3, so a garbled tier
    can only ask more (E10b)."""
    return max((tier if tier in RISK_TIERS else "R3" for tier in tiers), key=RISK_TIERS.index, default="R0")


#: The least a step's stakes can be, by the worst declared side effect of the ops it names (E10b; TS §9, §10; ruling
#: E10.4): reading changes nothing; a local write can be undone; an external effect reaches someone; an irreversible one
#: cannot be taken back. Mildest first (``capabilities.SIDE_EFFECTS``'s order).
EFFECT_TIERS = {"read": "R0", "local_write": "R1", "external": "R2", "irreversible": "R3"}
_EFFECT_ORDER = tuple(EFFECT_TIERS)


def effect_of(capabilities: Any, effects: Callable[[str, str], str | None]) -> str:
    """The worst declared side effect of these ``(component, op)`` pairs ("read" for none). An op with no contract counts
    as external: it may act (as ``runtime._may_have_acted`` reads it)."""
    found = [effects(component, op) for component, op in capabilities]
    return max((f if f in EFFECT_TIERS else "external" for f in found), key=_EFFECT_ORDER.index, default="read")


def _pairs(caps: Any) -> list[tuple[str, str]]:
    if not isinstance(caps, list):
        return []
    return [(c[0], c[1]) for c in caps if isinstance(c, list) and len(c) == 2 and all(isinstance(n, str) for n in c)]


def unit_floors(data: Any, *, effects: Callable[[str, str], str | None], inherit: str, floor: str = "R0") -> Any:
    """A drafted plan with each unit's risk set from the standard, never lowered (E10b; production-path S9; ruling
    E10.9): a unit that names no risk takes ``inherit`` (a delegated child, its parent's); every unit is at least
    ``floor`` and at least what the worst declared side effect of its ops demands (``EFFECT_TIERS``). A copy: the draft
    is not changed. What is malformed is left as it is, for ``parse_plan`` to refuse."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return data
    if not isinstance(data, dict) or not isinstance(data.get("units"), list):
        return data
    units = [dict(u) if isinstance(u, dict) else u for u in data["units"]]
    by_key = {u["key"]: u for u in units if isinstance(u, dict) and isinstance(u.get("key"), str)}
    settled: dict[str, str] = {}

    def settle(unit: dict, seen: frozenset) -> str:
        key = unit.get("key")
        if isinstance(key, str) and key in settled:
            return settled[key]
        parent = by_key.get(unit.get("parent")) if isinstance(unit.get("parent"), str) else None
        if "risk" in unit:
            base = unit["risk"]
        elif parent is not None and parent.get("key") not in seen:
            base = settle(parent, seen | {key})
        else:
            base = inherit
        if base not in RISK_TIERS:
            return base  # malformed: parse_plan refuses it
        found = higher(base, floor, EFFECT_TIERS[effect_of(_pairs(unit.get("capabilities")), effects)])
        if isinstance(key, str):
            settled[key] = found
        return found

    for unit in units:
        if isinstance(unit, dict):
            risk = settle(unit, frozenset())
            if risk in RISK_TIERS:
                unit["risk"] = risk
    return {**data, "units": units}


def highest_risk(data: Any) -> str:
    """The highest risk any unit of a drafted plan names ("R0" when none does; a malformed one is left for
    ``parse_plan``)."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return "R0"
    units = data.get("units") if isinstance(data, dict) else None
    if not isinstance(units, list):
        return "R0"
    return higher(*(u["risk"] for u in units if isinstance(u, dict) and u.get("risk") in RISK_TIERS))


#: An input that names a stored artifact rather than a unit of this plan.
ARTIFACT_PREFIX = "artifact:"
_KEY = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_GOAL_CHARS = 2000
_PLAN_FIELDS = frozenset({"units", "edges"})
_UNIT_FIELDS = frozenset({"key", "kind", "goal", "inputs", "output_schema", "capabilities", "verifies", "checks",
                          "risk", "budget_cents", "max_attempts", "priority", "call", "oracle", "not_before",
                          "await_file", "parent"})
#: A file a unit awaits in its mission's inbox: a plain name (no folder, not hidden).
_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
#: What an oracle's verdict is, as its planner declares it (a model critic or a human is never an oracle here).
ORACLE_KINDS = ("formal", "deterministic", "test", "oracle")
_CALL_FIELDS = frozenset({"capability", "params", "bind"})
_ORACLE_FIELDS = _CALL_FIELDS | {"expect", "kind", "facts", "produced_facts"}
#: An SMT variable a formal oracle's facts may name.
_SMT_VAR = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_PATH = re.compile(r"[A-Za-z0-9_-]{1,64}(\.[A-Za-z0-9_-]{1,64}){0,7}\Z")


class PlanRejected(ValueError):
    """A plan code will not store, with every problem found (each names its unit and the rule)."""

    def __init__(self, problems: list[str] | tuple[str, ...]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class UnitSpec:
    key: str
    kind: str
    goal: str
    inputs: tuple[str, ...] = ()
    output_schema: str = ""
    capabilities: tuple[tuple[str, str], ...] = ()
    verifies: str = ""
    checks: tuple[CheckpointSpec, ...] = ()
    risk: str = "R0"
    budget_cents: int = 0
    max_attempts: int = 1
    priority: int = 0
    #: A do unit's deterministic call: {"capability": [c, op], "params": {...}, "bind": {param: "unit.path"}}.
    call: dict | None = None
    #: A verify unit's oracle: a call plus {"expect": {"field": path, "equals": value}, "kind": ORACLE_KINDS}.
    oracle: dict | None = None
    #: The unit may not start before this time (UTC, ISO-8601) (V2b: a timer wake).
    not_before: str = ""
    #: The unit may not start until this file is in its mission's inbox (V2b: a file wake).
    await_file: str = ""
    #: The do unit that delegated this one ("" for a unit the plan drew at the top) (V4a).
    parent: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = {"key": self.key, "kind": self.kind, "goal": self.goal, "inputs": list(self.inputs),
               "output_schema": self.output_schema, "capabilities": [list(c) for c in self.capabilities],
               "verifies": self.verifies, "checks": [c.model_dump() for c in self.checks], "risk": self.risk,
               "budget_cents": self.budget_cents, "max_attempts": self.max_attempts, "priority": self.priority}
        for name in ("call", "oracle"):  # only when present, so a plan without them keeps its digest
            if getattr(self, name) is not None:
                out[name] = getattr(self, name)
        for name in ("not_before", "await_file", "parent"):
            if getattr(self, name):
                out[name] = getattr(self, name)
        return out


@dataclass(frozen=True)
class Plan:
    units: tuple[UnitSpec, ...]
    edges: tuple[tuple[str, str], ...]
    sha256: str = ""

    def unit(self, key: str) -> UnitSpec:
        for unit in self.units:
            if unit.key == key:
                return unit
        raise KeyError(f"the plan names no unit {key!r}")

    def depends_on(self) -> dict[str, frozenset[str]]:
        """Each unit's upstream units, with every consumer moved from a do unit onto its verify units."""
        kinds = {u.key: u.kind for u in self.units}
        verifiers: dict[str, set[str]] = {}
        for u in self.units:
            if u.kind == "verify":
                verifiers.setdefault(u.verifies, set()).add(u.key)
        named = {u.key: {i for i in u.inputs if i in kinds} | ({u.verifies} if u.kind == "verify" else set())
                 for u in self.units}
        for before, after in self.edges:
            named[after].add(before)
        out = {}
        for u in self.units:
            deps: set[str] = set()
            for d in named[u.key] & set(kinds):
                checkers = verifiers.get(d, set())
                deps |= checkers if kinds[d] == "do" and checkers and u.key not in checkers else {d}
            out[u.key] = frozenset(deps)
        return out

    def order(self) -> tuple[str, ...]:
        """A dependency order (raises ``graphlib.CycleError`` on a cycle)."""
        return tuple(TopologicalSorter(self.depends_on()).static_order())

    def upstream(self, key: str) -> frozenset[str]:
        """Every unit ``key`` follows by an input or an edge, transitively (never itself)."""
        inputs = {u.key: u.inputs for u in self.units}
        seen: set[str] = set()
        frontier = {key}
        while frontier:
            found = {before for before, after in self.edges if after in frontier}
            found |= {name for k in frontier for name in inputs.get(k, ()) if name in inputs}
            frontier = found - seen - {key}
            seen |= frontier
        return frozenset(seen)

    def to_json(self) -> str:
        """The canonical text the digest is taken over (sorted keys, edges sorted and deduplicated)."""
        body = {"units": [u.as_dict() for u in self.units], "edges": [list(e) for e in self.edges]}
        return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _whole(value: Any, low: int, high: int | None = None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool) and value >= low
            and (high is None or value <= high))


def _unit(raw: Any, index: int, mission_risk: str, problems: list[str]) -> UnitSpec | None:
    if not isinstance(raw, dict):
        problems.append(f"unit #{index}: a unit is a JSON object")
        return None
    key = raw.get("key")
    if not isinstance(key, str) or not _KEY.match(key):
        label = f"unit {key!r}" if isinstance(key, str) else f"unit #{index}"
        problems.append(f"{label}: key must be lowercase letters, digits, '_', '.' or '-' (at most 64)")
        return None
    say = f"unit {key!r}"
    unknown = sorted(set(raw) - _UNIT_FIELDS)
    if unknown:
        problems.append(f"{say}: unknown field(s) {unknown}")
    kind = raw.get("kind")
    if kind not in UNIT_KINDS:
        problems.append(f"{say}: kind must be one of {list(UNIT_KINDS)}, got {kind!r}")
    goal = raw.get("goal")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > _GOAL_CHARS:
        problems.append(f"{say}: goal is required (text, at most {_GOAL_CHARS} characters)")
    inputs = raw.get("inputs", [])
    if not isinstance(inputs, list) or not all(isinstance(i, str) and i for i in inputs):
        problems.append(f"{say}: inputs must be a list of unit keys or '{ARTIFACT_PREFIX}<id>'")
        inputs = []
    output_schema = raw.get("output_schema", "")
    if not isinstance(output_schema, str) or (output_schema and not _NAME.match(output_schema)):
        problems.append(f"{say}: output_schema must be a schema name")
        output_schema = ""
    caps = raw.get("capabilities", [])
    if not isinstance(caps, list) or not all(
            isinstance(c, list) and len(c) == 2 and all(isinstance(n, str) and _NAME.match(n) for n in c)
            for c in caps):
        problems.append(f"{say}: capabilities must be [component, op] pairs of names")
        caps = []
    verifies = raw.get("verifies", "")
    if not isinstance(verifies, str):
        problems.append(f"{say}: verifies must be a unit key")
        verifies = ""
    checks: list[CheckpointSpec] = []
    raw_checks = raw.get("checks", [])
    if not isinstance(raw_checks, list):
        problems.append(f"{say}: checks must be a list of checkpoints")
        raw_checks = []
    for n, check in enumerate(raw_checks):
        try:
            checks.append(CheckpointSpec.model_validate(check))
        except ValidationError as exc:
            problems.append(f"{say}: checks[{n}] is not a checkpoint ({exc.error_count()} error(s))")
    risk = raw.get("risk", "R0")
    if risk not in RISK_TIERS:
        problems.append(f"{say}: risk must be one of {list(RISK_TIERS)}, got {risk!r}")
        risk = "R0"
    elif RISK_TIERS.index(risk) > RISK_TIERS.index(mission_risk):
        problems.append(f"{say}: risk {risk} exceeds the mission's {mission_risk}")
    budget = raw.get("budget_cents", 0)
    if not _whole(budget, 0):
        problems.append(f"{say}: budget_cents must be a whole number of cents >= 0")
        budget = 0
    attempts = raw.get("max_attempts", 1)
    if not _whole(attempts, 1, MAX_ATTEMPTS):
        problems.append(f"{say}: max_attempts must be a whole number from 1 to {MAX_ATTEMPTS}")
        attempts = 1
    elif kind == "gate" and attempts != 1:
        problems.append(f"{say}: a gate is decided once by a person: max_attempts must be 1")
        attempts = 1
    priority = raw.get("priority", 0)
    if not _whole(priority, -1000, 1000):
        problems.append(f"{say}: priority must be a whole number from -1000 to 1000")
        priority = 0
    not_before = _start_time(raw.get("not_before"), say, problems)
    await_file = raw.get("await_file") or ""
    if await_file and not (isinstance(await_file, str) and _FILE.match(await_file)):
        problems.append(f"{say}: await_file must be a file name (letters, digits, '_', '.' or '-'; no folder, "
                        "not hidden)")
        await_file = ""
    parent = raw.get("parent") or ""
    if parent and not (isinstance(parent, str) and _KEY.match(parent)):
        problems.append(f"{say}: parent must be a unit key")
        parent = ""
    capabilities = tuple((c[0], c[1]) for c in caps)
    if kind == "gate" and capabilities:  # review of V4: a gate's worker calls nothing
        problems.append(f"{say}: a gate names no capabilities (a person decides it)")
    sources = set(inputs) | ({verifies} if kind == "verify" and verifies else set())
    call = _step(raw, "call", kind == "do", capabilities, sources, say, problems)
    oracle = _step(raw, "oracle", kind == "verify", capabilities, sources, say, problems)
    if oracle is not None and verifies and not any(
            str(source).partition(".")[0] == verifies
            for source in (*oracle["bind"].values(), *(oracle.get("facts") or {}).values())):
        problems.append(f"{say}: the oracle must read the artifact it judges: bind at least one parameter from "
                        f"'{verifies}.<field path>'")
    return UnitSpec(key=key, kind=str(kind), goal=str(goal or ""), inputs=tuple(inputs), output_schema=output_schema,
                    capabilities=capabilities, verifies=verifies, checks=tuple(checks),
                    risk=risk, budget_cents=budget, max_attempts=attempts, priority=priority, call=call, oracle=oracle,
                    not_before=not_before, await_file=await_file, parent=parent)


def _start_time(value: Any, say: str, problems: list[str]) -> str:
    """A unit's ``not_before`` as one UTC text form ("" when absent); a time without a zone is refused, since
    it would mean a different moment on each machine that reads it."""
    if value in (None, ""):
        return ""
    try:
        when = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        problems.append(f"{say}: not_before must be an ISO-8601 time with a time zone, e.g. "
                        "2026-10-02T09:00:00+10:00")
        return ""
    return when.astimezone(UTC).isoformat(timespec="seconds")


#: The sorts a fact may hold: a number is asserted only as an Int or a Real.
FACT_SORTS = ("int", "real")


def smt_sort(declared: Any) -> str:
    """A declared variable type as the solver reads it (``FormalMethodsBlock._z3_solve``: stripped, lowercased),
    so a fact never takes a sort the solver does not (Codex review of V1c: "Int " was Real here, Int there)."""
    return str(declared).strip().lower()


def _facts(facts: Any, body: dict, params: dict, sources: set[str], say: str, problems: list[str]) -> dict:
    """A formal oracle's facts, ``{smt variable: "<unit>.<field path>"}``: each number read from an input's
    artifact becomes an equality assertion (``rungs``), never text in the formula."""
    if not isinstance(facts, dict):
        problems.append(f"{say}: oracle.facts maps an SMT variable to '<unit>.<field path>'")
        return {}
    if body.get("kind") != "formal":
        problems.append(f"{say}: only a formal oracle reads facts")
    declared = params.get("variables") if isinstance(params.get("variables"), dict) else {}
    for var, source in facts.items():
        unit_key, _, path = str(source).partition(".")
        if not (isinstance(var, str) and _SMT_VAR.match(var)):
            problems.append(f"{say}: fact {var!r} must be a lowercase SMT variable name")
        elif var not in declared:
            problems.append(f"{say}: fact {var!r} is not declared in the oracle's params.variables")
        elif smt_sort(declared[var]) not in FACT_SORTS:
            problems.append(f"{say}: fact {var!r} must be declared Int or Real, not {declared[var]!r}")
        if not isinstance(source, str) or unit_key not in sources or not _PATH.match(path or "-."):
            problems.append(f"{say}: fact {var!r} must read '<an input of this unit>.<field path>', got {source!r}")
    return dict(facts)


def _step(raw: dict, name: str, allowed: bool, capabilities: tuple, sources: set[str], say: str,
          problems: list[str]) -> dict | None:
    """A unit's ``call`` (do units) or ``oracle`` (verify units), normalised; None when absent or broken."""
    if name not in raw:
        return None
    body = raw[name]
    if not allowed:
        problems.append(f"{say}: only a {'do' if name == 'call' else 'verify'} unit has a {name!r}")
        return None
    if not isinstance(body, dict):
        problems.append(f"{say}: {name} is an object")
        return None
    found = len(problems)
    fields = _ORACLE_FIELDS if name == "oracle" else _CALL_FIELDS
    unknown = sorted(set(body) - fields)
    if unknown:
        problems.append(f"{say}: {name} has unknown field(s) {unknown}")
    capability = body.get("capability")
    if not (isinstance(capability, list) and len(capability) == 2 and tuple(capability) in capabilities):
        problems.append(f"{say}: {name}.capability must be one of the unit's capabilities")
    params = body.get("params", {})
    if not isinstance(params, dict):
        problems.append(f"{say}: {name}.params is an object")
        params = {}
    bind = body.get("bind", {})
    if not isinstance(bind, dict):
        problems.append(f"{say}: {name}.bind maps a parameter to '<unit>.<field path>'")
        bind = {}
    for param, source in bind.items():
        unit_key, _, path = str(source).partition(".")
        if param in params:
            problems.append(f"{say}: {name} sets {param!r} both in params and bind")
        if not isinstance(source, str) or unit_key not in sources or not _PATH.match(path or "-."):
            problems.append(f"{say}: {name}.bind {param!r} must read '<an input of this unit>.<field path>', "
                            f"got {source!r}")
    out = {"capability": list(capability) if isinstance(capability, list) else [], "params": params, "bind": bind}
    if name == "oracle":
        expect = body.get("expect")
        if not (isinstance(expect, dict) and set(expect) == {"field", "equals"}
                and isinstance(expect.get("field"), str) and _PATH.match(expect["field"])):
            problems.append(f"{say}: oracle.expect is {{'field': <path>, 'equals': <value>}}")
        if body.get("kind") not in ORACLE_KINDS:
            problems.append(f"{say}: oracle.kind must be one of {list(ORACLE_KINDS)}")
        out.update(expect=expect, kind=body.get("kind"))
        facts = body.get("facts", {})
        if facts:
            out["facts"] = _facts(facts, body, params, sources, say, problems)
        if "produced_facts" in body:
            marked = body["produced_facts"]
            bound = out.get("facts", {})
            if not (isinstance(marked, list) and marked and all(
                    isinstance(var, str) and var in bound
                    and str(bound[var]).partition(".")[0] == raw.get("verifies") for var in marked)):
                problems.append(f"{say}: {INVALID_PRODUCED_FACTS}")
            else:
                out["produced_facts"] = list(marked)
    return out if len(problems) == found else None


def _granted(units: dict[str, UnitSpec], parent: UnitSpec, kind: str) -> set[tuple[str, str]]:
    """What a child of ``parent`` may name: the parent's capabilities, and for a child verifier also those of
    the verifiers the parent was given by whoever drew it (never those of its own delegated verifiers)."""
    granted = set(parent.capabilities)
    if kind == "verify":
        for unit in units.values():
            if unit.kind == "verify" and unit.verifies == parent.key and unit.parent == parent.parent:
                granted |= set(unit.capabilities)
    return granted


def _delegation(units: dict[str, UnitSpec], edges: tuple[tuple[str, str], ...], problems: list[str]) -> None:
    """Every child within its parent's limits (module docstring)."""
    children: dict[str, list[UnitSpec]] = {}
    for unit in units.values():
        if not unit.parent:
            continue
        parent = units.get(unit.parent)
        if parent is None or parent.kind != "do" or parent.key == unit.key:
            problems.append(f"unit {unit.key!r}: parent must be a do unit of this plan (parent {unit.parent!r})")
            continue
        children.setdefault(parent.key, []).append(unit)
    below: dict[str, set[str]] = {}  # each parent's whole subtree
    for unit in units.values():
        depth, seen, at = 0, {unit.key}, unit
        while at.parent in units and at.parent not in seen:
            at = units[at.parent]
            seen.add(at.key)
            below.setdefault(at.key, set()).add(unit.key)
            depth += 1
        if at.parent in seen:
            problems.append(f"unit {unit.key!r}: a parent cycle through {sorted(seen)}")
        elif depth > MAX_DEPTH:
            problems.append(f"unit {unit.key!r}: delegated deeper than {MAX_DEPTH} levels below a planned unit")
    rank = RISK_TIERS.index
    for key, kids in children.items():
        parent, say = units[key], f"unit {key!r}"
        if len(kids) > MAX_CHILDREN:
            problems.append(f"{say}: a parent has at most {MAX_CHILDREN} children, here {len(kids)}")
        carved = sum(k.budget_cents for k in kids)
        if carved > parent.budget_cents:
            problems.append(f"{say}: its children's budgets total {carved} cents, over its parent's budget of "
                            f"{parent.budget_cents} cents")
        subtree = below.get(key, set())
        readable = set(parent.inputs) | subtree
        for kid in kids:
            child = f"unit {kid.key!r}"
            outside = sorted(f"{c}.{o}" for c, o in set(kid.capabilities) - _granted(units, parent, kid.kind))
            if outside:
                problems.append(f"{child}: names {outside}, outside its parent's authority (delegated by {key!r})")
            if rank(kid.risk) > rank(parent.risk):
                problems.append(f"{child}: risk {kid.risk} exceeds its parent's {parent.risk}")
            if kid.max_attempts > parent.max_attempts:
                problems.append(f"{child}: more attempts than its parent ({kid.max_attempts} > {parent.max_attempts})")
            if kid.kind == "verify" and kid.verifies not in subtree | {key}:
                problems.append(f"{child}: a delegated verifier judges only its parent or a unit under it "
                                f"(verifies {kid.verifies!r})")
            reads = set(kid.inputs) | {before for before, after in edges if after == kid.key}  # artifacts too
            strays = sorted(reads - readable)
            if strays:
                problems.append(f"{child}: a delegated unit reads only its parent's inputs or its own subtree, "
                                f"not {strays}")


def _edges(raw: Any, keys: set[str], problems: list[str]) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, list):
        problems.append("plan: edges must be a list of [from, to] pairs")
        return ()
    out = set()
    for edge in raw:
        if not (isinstance(edge, list) and len(edge) == 2 and all(isinstance(k, str) for k in edge)):
            problems.append(f"edge {edge!r}: an edge is a [from, to] pair of unit keys")
        elif edge[0] == edge[1]:
            problems.append(f"edge {edge!r}: a unit cannot consume itself")
        elif not set(edge) <= keys:
            problems.append(f"edge {edge!r} names a unit not in this plan")
        else:
            out.add((edge[0], edge[1]))
    return tuple(sorted(out))


def _contract_problems(unit: UnitSpec, contract: Callable[[str, str], Any]) -> list[str]:
    """What breaks the op's ParamContract in a unit's call or oracle, judged now rather than at the first run (E8e).
    That is a static parameter of the wrong type, a key the contract does not declare (static or bound), or a
    required key neither set nor bound. A bound value is read at run time, so its type is judged then (the door
    checks every call)."""
    problems = []
    for name in ("call", "oracle"):
        step = getattr(unit, name)
        if step is None or len(step.get("capability") or []) != 2:
            continue
        component, op = step["capability"]
        found = contract(component, op)
        if found is None:
            continue  # readiness already says why an undeclared op cannot be planned on
        bound = set(step.get("bind") or {}) | ({"assertions"} if step.get("facts") else set())
        said = [p for p in found.violations(dict(step.get("params") or {}))
                if not any(p == f"missing required {key!r}" for key in bound)]
        stray = sorted(bound - set(found.required) - set(found.optional))
        if stray:
            said.append(f"bound key(s) {stray} are not parameters it declares")
        problems += [f"unit {unit.key!r}: {name} {component}.{op}: {p}" for p in said]
    return problems


#: What each declared side effect needs before a mission may plan an op that has it (Gate W: risk scales the bar;
#: ruling A-6). An op with no declared side effect counts as acting.
EFFECT_AUTHORITY = {"read": "A1", "local_write": "A1", "external": "A2", "irreversible": "A2"}


def _authority_problems(units: dict[str, UnitSpec], authority: Callable[[str, str], str],
                        effects: Callable[[str, str], str | None] | None) -> list[str]:
    """What the authority ladder refuses (Gate W): an A0 op anywhere in a plan and, judged with the broker's declared
    side effects, an op that can act below A2. A verify unit's oracle below A4 is not refused: its verdict is capped
    (ruling A-7)."""
    problems = []
    for unit in units.values():
        for component, op in unit.capabilities:
            found = authority(component, op)
            cls = found if found in AUTHORITY else "A0"
            if cls == "A0":
                problems.append(AUTHORITY_COPY["plan_a0"].format(unit=unit.key, op=f"{component}.{op}", cls=cls))
                continue
            if effects is None:
                continue
            effect = effects(component, op) or "external"
            need = EFFECT_AUTHORITY.get(effect, "A2")
            if authority_rank(cls) < authority_rank(need):
                problems.append(AUTHORITY_COPY["plan_effect"].format(unit=unit.key, op=f"{component}.{op}",
                                                                     effect=effect, cls=cls, need=need))
    return problems


def _gate_problems(plan: Plan, effects: Callable[[str, str], str | None],
                   keys: tuple[str, ...] | None = None) -> list[str]:
    """What the gate rule refuses (V3a), judged with the broker's declared side effects: a unit naming an ``external``
    or ``irreversible`` op with no gate (a person's approval) upstream of it by input or edge. ``keys``: only those
    units (the runtime judges the one it is about to run, Gate W review gw2)."""
    kinds = {u.key: u.kind for u in plan.units}
    problems = []
    for unit in plan.units:
        if keys is not None and unit.key not in keys:
            continue
        gated = any(kinds[k] == "gate" for k in plan.upstream(unit.key))
        for component, op in unit.capabilities:
            effect = effects(component, op)
            if effect in ("external", "irreversible") and not gated:
                problems.append(f"unit {unit.key!r} names an {effect} op {component}.{op}: it must run behind a "
                                "gate a person approves")
    return problems


def parse_plan(data: dict | str, *, risk_tier: str, budget_cents: int,
               ready: Callable[[str, str], str | None] | None = None,
               effects: Callable[[str, str], str | None] | None = None,
               reusable: Callable[[str, str], str | None] | None = None,
               contract: Callable[[str, str], Any] | None = None,
               authority: Callable[[str, str], str] | None = None) -> Plan:
    """The validated plan, or ``PlanRejected`` listing every problem (judged against the mission's risk
    tier and budget). ``ready(component, op)`` is the capability broker's verdict on each named
    capability: None when it is ready, else the reason (storing a plan always passes one). ``effects(component,
    op)`` is its contract's declared side effect (V3a): a unit naming an ``external`` or ``irreversible`` op must
    run behind a gate a person approves (a gate upstream of it by input or edge). ``reusable(artifact_id,
    risk_tier)``: None when a saved result a unit names may be reused by this mission, else why not (E2b).
    ``contract(component, op)``: the op's ParamContract or None; a call's or oracle's parameters that break it are
    refused now (E8e, ``_contract_problems``). ``authority(component, op)``: the op's class (Gate W); when given, an
    A0 op and an op that can act below A2 are refused."""
    if risk_tier not in RISK_TIERS:
        raise ValueError(f"risk tier must be one of {RISK_TIERS}, got {risk_tier!r}")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise PlanRejected([f"plan: not JSON ({exc.msg})"]) from None
    if not isinstance(data, dict):
        raise PlanRejected(["plan: a plan is a JSON object with 'units' and 'edges'"])
    try:  # what SQLite will be asked to store: a lone surrogate passes json but fails the TEXT bind
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise PlanRejected(["plan: text must be valid Unicode (a lone surrogate cannot be stored)"]) from None
    except (TypeError, ValueError) as exc:
        raise PlanRejected([f"plan: not plain JSON data ({type(exc).__name__})"]) from None
    problems: list[str] = []
    unknown = sorted(set(data) - _PLAN_FIELDS)
    if unknown:
        problems.append(f"plan: unknown field(s) {unknown}")
    raw_units = data.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        problems.append("plan: needs at least one unit")
        raw_units = []
    elif len(raw_units) > MAX_UNITS:
        problems.append(f"plan: at most {MAX_UNITS} units, got {len(raw_units)}")
    units: dict[str, UnitSpec] = {}
    for index, raw in enumerate(raw_units):
        unit = _unit(raw, index, risk_tier, problems)
        if unit is None:
            continue
        if unit.key in units:
            problems.append(f"unit {unit.key!r}: duplicate key")
            continue
        units[unit.key] = unit
    for unit in units.values():
        for name in unit.inputs:
            if name.startswith(ARTIFACT_PREFIX):
                why = reusable(name.removeprefix(ARTIFACT_PREFIX), risk_tier) if reusable is not None else None
                if why:
                    problems.append(f"unit {unit.key!r}: {why}")
            elif name not in units:
                problems.append(f"unit {unit.key!r}: input {name!r} is neither a unit of this plan nor "
                                f"'{ARTIFACT_PREFIX}<id>'")
        if unit.kind == "verify":
            target = units.get(unit.verifies)
            if target is None or target.kind != "do":
                problems.append(f"unit {unit.key!r}: a verify unit names the do unit it verifies "
                                f"(verifies {unit.verifies!r})")
        elif unit.verifies:
            problems.append(f"unit {unit.key!r}: only a verify unit names 'verifies'")
        if ready is not None:
            for component, op in unit.capabilities:
                why = ready(component, op)
                if why is not None:
                    problems.append(f"unit {unit.key!r}: capability {component}.{op} is not ready: {why}")
        if contract is not None:
            problems.extend(_contract_problems(unit, contract))
    checked ={u.verifies for u in units.values() if u.kind == "verify"}
    for unit in units.values():
        if unit.kind == "do" and unit.key not in checked:
            problems.append(f"unit {unit.key!r}: every do unit needs a verify unit")
    edges = _edges(data.get("edges", []), set(units), problems)
    _delegation(units, edges, problems)
    total = sum(u.budget_cents for u in units.values() if not u.parent)  # a child's budget is its parent's
    if total > budget_cents:
        problems.append(f"plan: the units' budgets total {total} cents, over the mission budget of "
                        f"{budget_cents} cents")
    plan = Plan(units=tuple(units.values()), edges=edges)
    if effects is not None:
        problems.extend(_gate_problems(plan, effects))
    if authority is not None:
        problems.extend(_authority_problems(units, authority, effects))
    try:
        plan.order()
    except CycleError as exc:
        problems.append(f"plan: dependency cycle through {list(exc.args[1])}")
    if problems:
        raise PlanRejected(problems)
    return replace(plan, sha256=hashlib.sha256(plan.to_json().encode()).hexdigest())
