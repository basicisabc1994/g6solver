# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Capability readiness: exists is not ready (workforce OS plan, P0 T4).

Each (component, op) climbs a ladder and stops at the first rung it fails, saying why:

* ``declared``: a ParamContract (``params/<component>.<op>.json``) names its keys and types;
* ``registered`` / ``loads``: the op is one of this library's capabilities (the workforce bench's ops) and can run
  here. The library ships no component registry: another component's op climbs these rungs only through a registry
  the caller injects (``registry=``: ``list_all``, ``get_class``, ``describe``; a test's scripted world), else it
  stops at ``declared``;
* ``deps_installed``: the import names its contract declares (``packages``) are installed (``find_spec``);
* ``probe_passed``: a probe ran the contract's probe parameters through the door, the answer was not
  blocked or degraded and carried the expected field, within the TTL, for this exact contract;
* ``measured_suitable``: competence measured on real work (V5; nothing reaches it yet).

Probes are rows in ``preflight_runs`` (append-only, with their time and the owner who ran them) and
every question re-reads that owner's rows: nothing is cached in process, and no owner reads another's
probe or its diagnostic (Codex review of T4-T6). Only ``read`` ops are probed here, and an op whose contract
declares ``shadow_probe`` (V3a: its probe parameters have no effect, e.g. ``workforce.send`` with no message); a
probe of any other op that writes or reaches outside needs approval. ``match`` returns nothing below
``probe_passed``.

The probe never calls a component itself: the caller passes ``invoke(component, op, params) ->
reply``, which is the single door (``calls.py``).
"""
from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from g6.workforce import bench
from g6.workforce.store import OwnerScope, unscoped
from g6.workforce.contracts import AUTHORITY, BLOCKED, Outcome, answered_field
from g6.workforce.economics import Money
from g6.workforce.missions import check_actor

LEVELS = ("undeclared", "declared", "registered", "loads", "deps_installed", "probe_passed", "measured_suitable")
SIDE_EFFECTS = ("read", "local_write", "external", "irreversible")
#: How a contract's keys are proven to be what the op reads (job packs track reads; others have an input model).
CHECKS = ("read_tracking", "input_model")
TYPES = ("str", "int", "number", "bool", "list", "dict")
PROBE_TTL = timedelta(hours=12)
PARAMS_DIR = Path(__file__).parent / "params"
_COMPONENT = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_OP = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_IMPORT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,63}\Z")
_REQUIRED_FIELDS = frozenset({"component", "op", "required", "probe_params", "expect_field", "side_effect", "check"})
_FIELDS = _REQUIRED_FIELDS | {"optional", "backend_package", "model_free", "shadow_probe", "packages", "cost_ceiling",
                              "valid_for_seconds", "operational"}
_REASON_CHARS = 300
#: Coverage ledger R2 (founder brief §9): the hand-entered keys of a contract's ``operational`` block. The block sits
#: outside the digest (lead ruling L-1) with its own ``operational_sha256``; the rest of §9 is derived on read
#: (``operational.py``). A route can only lose an option by it, never gain authority.
OPERATIONAL_KEYS = ("mitigates_failure_modes", "introduces_failure_modes", "state_reads", "state_writes",
                    "state_evidence")
#: The state an op may read or write: a closed vocabulary.
STATES = ("venture_repo", "scratch", "missions", "runs", "preflight_runs", "episodes", "harness")
#: The mode a claim holds for (lead ruling L-2): "" is the op's default call; the others are reserved for the check
#: units that call ``run_tests`` with ``regression`` (and its ``learned`` probe), which R2 does not route.
MODES = ("", "regression", "learned")
_FAILURE_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_NODEID = re.compile(r"tests/[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+\Z")


def _operational(block: Any) -> list[str]:
    """Why an ``operational`` block is refused ([] when it is well formed)."""
    if not (isinstance(block, dict) and set(block) == set(OPERATIONAL_KEYS)):
        return [f"operational is an object with exactly the keys {list(OPERATIONAL_KEYS)}"]
    problems = []
    claims = block["mitigates_failure_modes"]
    if not (isinstance(claims, list) and all(
            isinstance(c, dict) and set(c) == {"failure_mode", "when", "evidence"}
            and isinstance(c["failure_mode"], str) and _FAILURE_ID.match(c["failure_mode"]) and c["when"] in MODES
            and isinstance(c["evidence"], str) and _NODEID.match(c["evidence"]) for c in claims)):
        problems.append(f"operational.mitigates_failure_modes lists {{failure_mode, when (one of {list(MODES)}), "
                        "evidence (a test id)}")
    introduced = block["introduces_failure_modes"]
    if not (isinstance(introduced, list) and len(set(map(str, introduced))) == len(introduced)
            and all(isinstance(i, str) and _FAILURE_ID.match(i) for i in introduced)):
        problems.append("operational.introduces_failure_modes is a list of distinct failure mode ids")
    for part in ("state_reads", "state_writes"):
        names = block[part]
        if not (isinstance(names, list) and len(set(map(str, names))) == len(names)
                and all(name in STATES for name in names)):
            problems.append(f"operational.{part} is a list of distinct states from {list(STATES)}")
    if not (isinstance(block["state_evidence"], str) and _NODEID.match(block["state_evidence"])):
        problems.append("operational.state_evidence is a test id")
    return problems

_DDL = """
CREATE TABLE IF NOT EXISTS preflight_runs (
    probe_id      TEXT PRIMARY KEY,
    owner         TEXT NOT NULL DEFAULT '',
    component     TEXT NOT NULL,
    op            TEXT NOT NULL,
    contract_sha  TEXT NOT NULL,
    actor         TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    passed        INTEGER NOT NULL,
    state         TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS preflight_runs_no_update BEFORE UPDATE ON preflight_runs
BEGIN SELECT RAISE(ABORT, 'a probe is never rewritten'); END;
CREATE TRIGGER IF NOT EXISTS preflight_runs_no_delete BEFORE DELETE ON preflight_runs
BEGIN SELECT RAISE(ABORT, 'a probe is never rewritten'); END;
CREATE TRIGGER IF NOT EXISTS preflight_runs_no_replace BEFORE INSERT ON preflight_runs
WHEN EXISTS (SELECT 1 FROM preflight_runs WHERE probe_id = NEW.probe_id)
BEGIN SELECT RAISE(ABORT, 'a probe is never rewritten'); END;
"""


def _typed(value: Any, kind: str) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    return isinstance(value, {"str": str, "int": int, "number": (int, float), "list": list, "dict": dict}[kind])


@dataclass(frozen=True)
class ParamContract:
    """What one op reads: its keys and types, how to probe it, what it touches."""

    component: str
    op: str
    required: dict[str, str]
    optional: dict[str, str]
    probe_params: dict[str, Any]
    expect_field: str
    side_effect: str
    check: str
    sha256: str
    #: The backend package this op needs (``describe()["backend"]["packages"]`` key), for a component
    #: whose backend has several (formal_methods: z3, lean, ...); empty when it needs the whole backend.
    backend_package: str = ""
    #: Declared in this reviewed file, never inferred: the op answers by computation and reaches no
    #: model on any path. Only an oracle whose op says so can make a verdict ``verified`` (a zero
    #: model-call count is not proof: a provider can be reached outside every counted door).
    model_free: bool = False
    #: Declared in this reviewed file (V3a): the op's probe parameters have no effect, so it may be probed, and
    #: a read-only worker may send it exactly those parameters (nothing else) though the op is not ``read``.
    shadow_probe: bool = False
    #: Declared in this reviewed file (plan ruling, Phase W): the import names this op needs, each checked with
    #: ``find_spec``; an empty tuple claims none. When declared, they replace the component-wide backend reading
    #: (``backend_package`` keeps its meaning). None: not declared, and the component-wide rule holds.
    packages: tuple[str, ...] | None = None
    #: Declared in this reviewed file (Phase E E1a/E1c, plan ruling R5): the most one call costs, as money. A declared 0
    #: is exact; a ceiling above 0 is charged when no actual is reported; None (undeclared): the cost is unknown, and
    #: a charged run refuses the call.
    cost_ceiling: Money | None = None
    #: Declared in this reviewed file (E2b, plan ruling R8): how long a verified result this op made may be reused.
    valid_for_seconds: int | None = None
    #: Declared in this reviewed file (coverage ledger R2): what the op mitigates and introduces, and the state it
    #: reads and writes (``OPERATIONAL_KEYS``). Outside ``sha256`` (lead ruling L-1); None when undeclared.
    operational: dict | None = field(default=None, hash=False)
    #: The digest of ``operational`` alone, which every decision that reads it records; "" when undeclared.
    operational_sha256: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> ParamContract:
        if not isinstance(data, dict):
            raise ValueError("a param contract is a JSON object")  # noqa: TRY004 - one refusal type for any bad contract
        label = f"{data.get('component')!s}.{data.get('op')!s}"
        problems = []
        unknown, missing = sorted(set(data) - _FIELDS), sorted(_REQUIRED_FIELDS - set(data))
        if unknown:
            problems.append(f"unknown field(s) {unknown}")
        if missing:
            problems.append(f"missing field(s) {missing}")
        if not (isinstance(data.get("component"), str) and _COMPONENT.match(data["component"])):
            problems.append("component must be a registry name (lowercase, digits, '_')")
        if not (isinstance(data.get("op"), str) and _OP.match(data["op"])):
            problems.append("op must be an operation name")
        keys: dict[str, dict[str, str]] = {}
        for part in ("required", "optional"):
            table = data.get(part, {})
            if not isinstance(table, dict) or not all(
                    isinstance(k, str) and _KEY.match(k) and k != "op" and v in TYPES for k, v in table.items()):
                problems.append(f"{part} maps parameter names (never 'op', the selector) to one of {list(TYPES)}")
                table = {}
            keys[part] = dict(table)
        overlap = sorted(set(keys["required"]) & set(keys["optional"]))
        if overlap:
            problems.append(f"{overlap} are both required and optional")
        if not (isinstance(data.get("expect_field"), str) and _KEY.match(data["expect_field"])):
            problems.append("expect_field names the field a probe's answer must carry")
        if data.get("side_effect") not in SIDE_EFFECTS:
            problems.append(f"side_effect must be one of {list(SIDE_EFFECTS)}")
        if data.get("check") not in CHECKS:
            problems.append(f"check must be one of {list(CHECKS)}")
        package = data.get("backend_package", "")
        if not isinstance(package, str) or (package and not _PACKAGE.match(package)):
            problems.append("backend_package names one capability package (e.g. formal-z3)")
        model_free = data.get("model_free", False)
        if not isinstance(model_free, bool):
            problems.append("model_free is true or false")
        shadow_probe = data.get("shadow_probe", False)
        if not isinstance(shadow_probe, bool):
            problems.append("shadow_probe is true or false")
        packages = data.get("packages")
        if "packages" in data and not (
                isinstance(packages, list) and len(set(packages)) == len(packages)
                and all(isinstance(p, str) and _IMPORT_NAME.match(p) for p in packages)):
            problems.append("packages is a list of distinct import names (e.g. z3, google.cloud); [] claims none")
        ceiling = None
        if "cost_ceiling" in data:
            try:
                ceiling = Money.from_dict(data["cost_ceiling"])
            except ValueError as exc:
                problems.append(f"cost_ceiling: {exc}")
        valid_for = data.get("valid_for_seconds")
        if "valid_for_seconds" in data and not (isinstance(valid_for, int) and not isinstance(valid_for, bool)
                                                 and valid_for >= 1):
            problems.append("valid_for_seconds is a whole number of seconds >= 1")
        if "operational" in data:
            problems += _operational(data["operational"])
        probe = data.get("probe_params")
        if not isinstance(probe, dict):
            problems.append("probe_params must be an object")
            probe = {}
        if problems:
            raise ValueError(f"param contract {label}: " + "; ".join(problems))
        body = {"component": data["component"], "op": data["op"], "required": keys["required"],
                "optional": keys["optional"], "probe_params": probe, "expect_field": data["expect_field"],
                "side_effect": data["side_effect"], "check": data["check"], "backend_package": package}
        if model_free:  # only when declared, so a contract without it keeps its digest
            body["model_free"] = True
        if shadow_probe:
            body["shadow_probe"] = True
        if "packages" in data:  # only when declared (an empty list is a declaration), so the others keep their digest
            body["packages"] = list(packages)
        if ceiling is not None:  # only when declared, so every other contract keeps its digest (ruling R19)
            body["cost_ceiling"] = ceiling.as_dict()
        if "valid_for_seconds" in data:
            body["valid_for_seconds"] = valid_for
        sha = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if "packages" in body:
            body["packages"] = tuple(body["packages"])
        if "cost_ceiling" in body:
            body["cost_ceiling"] = Money.from_dict(body["cost_ceiling"])
        if "operational" in data:  # after the digest (lead ruling L-1): its own digest, so no probe goes stale
            body["operational"] = json.loads(json.dumps(data["operational"]))
            body["operational_sha256"] = hashlib.sha256(json.dumps(
                data["operational"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        contract = cls(**body, sha256=sha)
        refused = contract.violations(probe)
        if refused:
            raise ValueError(f"param contract {label}: probe_params break the contract: " + "; ".join(refused))
        return contract

    def violations(self, params: Any) -> list[str]:
        """Why these parameters may not be sent (empty when they may): checked before dispatch."""
        if not isinstance(params, dict):
            return ["parameters must be an object"]
        problems = [f"missing required {key!r}" for key in self.required if key not in params]
        unknown = sorted(set(params) - set(self.required) - set(self.optional))
        if unknown:
            problems.append(f"unknown key(s) {unknown}")
        for key, value in params.items():
            kind = self.required.get(key) or self.optional.get(key)
            if kind and not _typed(value, kind):
                problems.append(f"{key!r} must be {kind}")
        return problems

    def probes_freely(self) -> bool:
        """Whether a probe of this op runs without approval: it only reads, or its probe has no effect (V3a)."""
        return self.side_effect == "read" or self.shadow_probe


@functools.lru_cache(maxsize=1)
def shipped_contracts() -> dict[tuple[str, str], ParamContract]:
    """The workforce's own shipped contracts (``params/workforce.*.json``), parsed once per process (audit 010 WF-009).
    A file of any other name is never read; a malformed workforce contract still refuses."""
    return _read(PARAMS_DIR, "workforce.*.json")


def load_contracts(directory: str | Path | None = None) -> dict[tuple[str, str], ParamContract]:
    """Every ``<component>.<op>.json`` in ``directory``; by default the shipped contracts (:func:`shipped_contracts`)."""
    if directory is None:
        return dict(shipped_contracts())
    return _read(Path(directory), "*.json")


def _read(directory: Path, pattern: str) -> dict[tuple[str, str], ParamContract]:
    out: dict[tuple[str, str], ParamContract] = {}
    for path in sorted(directory.glob(pattern)):
        try:
            contract = ParamContract.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from None
        if path.name != f"{contract.component}.{contract.op}.json":
            raise ValueError(f"{path.name}: a contract file is named <component>.<op>.json, here "
                             f"{contract.component}.{contract.op}.json")
        out[(contract.component, contract.op)] = contract
    return out


def _installed(package: str) -> bool:
    """Whether an import name is found here (``find_spec``: nothing is imported but a dotted name's parents)."""
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


def _packages_met(contract: ParamContract) -> tuple[str, str, ParamContract]:
    """``deps_installed``, unless a package the contract declares is not installed here (it stops at ``loads``)."""
    label = f"{contract.component}.{contract.op}"
    missing = [p for p in contract.packages or () if not _installed(p)]
    if missing:
        return "loads", "; ".join(f"{label} needs {p}, which is not installed here" for p in missing), contract
    return "deps_installed", "", contract


def probe_verdict(reply: Any, contract: ParamContract) -> tuple[bool, str, str]:
    """(passed, the answer's state, why not) for one probe's answer: it passes when it is neither blocked-escalated nor
    degraded and the component's own answer carries the contract's ``expect_field``. ``Capabilities.probe`` records
    it; the census judges a gated op's own-code samples by it (``scripts/capability_map/census.py``)."""
    outcome = Outcome.from_envelope(reply, source=f"probe:{contract.component}.{contract.op}")
    state = outcome.state
    if state == BLOCKED or outcome.degraded:
        return False, state, (f"the answer was {'blocked-escalated' if state == BLOCKED else 'degraded'}: "
                              + "; ".join(outcome.reasons))
    if not answered_field(reply, contract.expect_field):
        return False, state, f"the component's answer carries no {contract.expect_field!r}"
    return True, state, ""


@dataclass(frozen=True)
class Readiness:
    component: str
    op: str
    level: str
    reason: str = ""
    probe_id: str = ""
    probed_at: str = ""

    def at_least(self, level: str) -> bool:
        return LEVELS.index(self.level) >= LEVELS.index(level)


class Capabilities:
    """One owner's view of what can run here, read fresh on every question. What is installed is the
    machine's; a probe's outcome (and its diagnostic) is the owner's who ran it and no one else's."""

    def __init__(self, source: OwnerScope, *,
                 contracts: dict[tuple[str, str], ParamContract] | None = None, registry: Any = None,
                 ops_for: Callable[[str], frozenset[str] | None] | None = None,
                 now: Callable[[], datetime] | None = None, ttl: timedelta = PROBE_TTL,
                 authority: Callable[[str, str], str] | None = None) -> None:
        vstore, scope = unscoped(source)
        if scope is None:
            raise TypeError("capabilities are read for one owner: pass the owner's scope (VentureStore.for_owner)")
        self.owner = scope.owner
        self._conn = vstore.conn
        self._conn.executescript(_DDL)
        if "owner" not in {r[1] for r in self._conn.execute("PRAGMA table_info(preflight_runs)")}:
            with self._conn:  # probes recorded before they were owned belong to no owner
                self._conn.execute("ALTER TABLE preflight_runs ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_preflight_owned"
                           " ON preflight_runs(owner, component, op, contract_sha)")
        self._contracts = load_contracts() if contracts is None else dict(contracts)
        #: An injected component registry (None: the library has none, so only the bench's ops can run).
        self._registry = registry
        self._ops_for = ops_for or (lambda name: None)
        self._now = now or (lambda: datetime.now(UTC))
        self._ttl = ttl
        self._authority = authority or (lambda component, op: "")

    def contract(self, component: str, op: str) -> ParamContract | None:
        return self._contracts.get((component, op))

    def authority(self, component: str, op: str) -> str:
        """The op's verification authority class from the census reading this owner's view was given; an op it has no
        reading of, or a reading off the ladder, is A0 (fail closed; Gate W ruling A-9)."""
        found = str(self._authority(component, op) or "")
        return found if found in AUTHORITY else "A0"

    def side_effect(self, component: str, op: str) -> str | None:
        """What the op's contract declares it touches (the plan's gate rule reads it, V3a); None when undeclared."""
        found = self._contracts.get((component, op))
        return found.side_effect if found is not None else None

    def contracts(self) -> dict[tuple[str, str], ParamContract]:
        return dict(self._contracts)

    def probed_pairs(self) -> list[tuple[str, str]]:
        """Every (component, op) this owner has a passing probe row for, sorted (readiness still decides)."""
        rows = self._conn.execute(
            "SELECT DISTINCT component, op FROM preflight_runs WHERE owner=? AND passed=1 ORDER BY component, op",
            (self.owner,)).fetchall()
        return [(row["component"], row["op"]) for row in rows]

    def due(self, *, within: timedelta, limit: int) -> list[tuple[str, str]]:
        """The pairs whose probe comes due (E3; ruling P3.13), oldest first, at most ``limit``: this owner's pairs whose
        latest probe of the current contract passed and expires within ``within`` (or has expired), and whose contract
        declares the op model-free and lets it be probed freely, so a background re-probe calls no model (R6). The
        owner's chat probes any other op itself, as before."""
        found = []
        for component, op in self.probed_pairs():
            contract = self._contracts.get((component, op))
            if contract is None or not contract.model_free or not contract.probes_freely():
                continue
            row = self._conn.execute(
                "SELECT finished_at, passed FROM preflight_runs"
                " WHERE owner=? AND component=? AND op=? AND contract_sha=? ORDER BY rowid DESC LIMIT 1",
                (self.owner, component, op, contract.sha256)).fetchone()
            if row is None or not row["passed"]:
                continue
            if datetime.fromisoformat(row["finished_at"]) + self._ttl - self._now() <= within:
                found.append((row["finished_at"], component, op))
        return [(component, op) for _, component, op in sorted(found)[:max(0, int(limit))]]

    # -- the ladder -------------------------------------------------------------------------------
    def _static(self, component: str, op: str) -> tuple[str, str, ParamContract | None]:
        """The highest rung below the probe, the reason it stops there, and the contract."""
        contract = self._contracts.get((component, op))
        if contract is None:
            return "undeclared", f"no ParamContract declares {component}.{op}", None
        if component == bench.COMPONENT:  # the workforce's own bench: never in a registry
            if op not in bench.OPS:
                return "loads", f"the bench does not accept op {op!r} (it declares {sorted(bench.OPS)})", contract
            why = bench.missing(op)
            if why:
                return "loads", why, contract
            return _packages_met(contract)
        if self._registry is None:  # the library has no component registry: only the bench's ops can run
            return "declared", f"{component}.{op} is not a capability of this library", contract
        if component not in self._registry.list_all():
            return "declared", f"{component} is not registered", contract
        try:
            loads = self._registry.get_class(component) is not None
        except Exception as exc:  # noqa: BLE001 - a block that raises while loading does not load
            return "registered", f"{component} does not load here ({type(exc).__name__})", contract
        if not loads:
            return "registered", f"{component} does not load here", contract
        accepted = self._ops_for(component)
        if accepted and op not in accepted:
            return "loads", f"{component} does not accept op {op!r} (it declares {sorted(accepted)})", contract
        # installed: True (all), False (none), "partial" (some), None (the component declares no backend).
        backend = (self._registry.describe(component) or {}).get("backend") or {}
        packages = backend.get("packages") or {}
        if contract.backend_package:
            if (packages.get(contract.backend_package) or {}).get("installed") is not True:
                return "loads", f"{component}'s backend package {contract.backend_package} is not installed here", \
                    contract
        elif contract.packages is None and backend.get("installed") not in (True, None):
            missing = sorted(k for k, v in packages.items() if (v or {}).get("installed") is not True)
            return "loads", (f"{component}'s backend is not installed here, or only partly (missing {missing}); "
                             "a contract may name the one package its op needs"), contract
        return _packages_met(contract)

    def readiness(self, component: str, op: str) -> Readiness:
        level, reason, contract = self._static(component, op)
        if level != "deps_installed":
            return Readiness(component, op, level, reason)
        if not contract.probes_freely():
            return Readiness(component, op, level,
                             f"a probe of a {contract.side_effect} op needs approval (V3); none runs here")
        row = self._conn.execute(
            "SELECT probe_id, finished_at, passed, reason FROM preflight_runs"
            " WHERE owner=? AND component=? AND op=? AND contract_sha=? ORDER BY rowid DESC LIMIT 1",
            (self.owner, component, op, contract.sha256)).fetchone()
        if row is None:
            return Readiness(component, op, level, "no probe has run for this contract")
        probe_id, probed = row["probe_id"], row["finished_at"]
        if not row["passed"]:
            return Readiness(component, op, level, f"the last probe failed at {probed}: {row['reason']}",
                             probe_id, probed)
        expires = datetime.fromisoformat(probed) + self._ttl
        if self._now() > expires:
            return Readiness(component, op, level,
                             f"the last probe passed at {probed} and expired at {expires.isoformat()}", probe_id,
                             probed)
        return Readiness(component, op, "probe_passed", "", probe_id, probed)

    def history(self, component: str, op: str) -> tuple[int, int]:
        """This owner's probes of the op, (passed, all), whatever its contract declared then (E2E Build 6: a route's
        measured chance of executing; a declared price does not change whether its runner runs)."""
        row = self._conn.execute("SELECT COALESCE(SUM(passed), 0), COUNT(*) FROM preflight_runs"
                                 " WHERE owner=? AND component=? AND op=?", (self.owner, component, op)).fetchone()
        return int(row[0]), int(row[1])

    def ready(self, component: str, op: str) -> str | None:
        """None when the pair may be planned on, else why not (the plan validator's question)."""
        found = self.readiness(component, op)
        return None if found.at_least("probe_passed") else f"{found.level}: {found.reason}"

    def match(self, pairs: Iterable[tuple[str, str]]) -> list[Readiness]:
        """Only the pairs at ``probe_passed`` or above."""
        return [r for r in (self.readiness(c, o) for c, o in pairs) if r.at_least("probe_passed")]

    # -- probes -----------------------------------------------------------------------------------
    def probe(self, component: str, op: str, invoke: Callable[[str, str, dict], Any], *, actor: str) -> Readiness:
        """Run the contract's probe through ``invoke`` (the door) and record it, if the pair has
        reached ``deps_installed`` and its op only reads or declares a probe with no effect (``shadow_probe``);
        otherwise nothing is called or written."""
        actor = check_actor(actor)
        level, _reason, contract = self._static(component, op)
        if level != "deps_installed" or not contract.probes_freely():
            return self.readiness(component, op)
        started = self._stamp()
        try:
            reply = invoke(component, op, dict(contract.probe_params))
        except Exception as exc:  # noqa: BLE001 - a probe that raises is a failed probe
            passed, state, why = False, BLOCKED, f"the probe raised {type(exc).__name__}: {exc}"
        else:
            passed, state, why = probe_verdict(reply, contract)
        with self._conn:
            self._conn.execute(
                "INSERT INTO preflight_runs (probe_id, owner, component, op, contract_sha, actor, started_at,"
                " finished_at, passed, state, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], self.owner, component, op, contract.sha256, actor, started, self._stamp(),
                 int(passed), state, why[:_REASON_CHARS]))
        return self.readiness(component, op)

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="seconds")
