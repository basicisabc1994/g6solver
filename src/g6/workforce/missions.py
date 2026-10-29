# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Missions: the persistent unit of work (workforce OS plan, P0 T2).

A mission holds the owner's objective in their words and the clarified objective as the intent
engine's own ``IntentContract`` (its ``checkpoints`` are the acceptance checks, its ``constraints``,
``success_criteria`` and ``deadline`` the rest), plus what the contract lacks: a status on the
mission state machine, a risk tier, a budget in integer cents and, once done, the rolled-up outcome.
It replaces the planned slice-1 ``pain_specs`` table (ruling W1).

Tenancy copies ``operator_tools.CheckStore``: an owner is required and every statement carries the
owner predicate through ``ventures``, so another owner's mission id is refused with the store's one
``NOT_FOUND``, exactly as an unknown id is. Mission events are venture ``evidence`` rows (kind = the
event kind, ref = the mission id), written once per idempotency key and never rewritten; a finished
mission never changes (a trigger, not a convention).

One runner per running mission (V2a): the runner lease reuses the materialization lease's columns
once the mission is running, plus a fence that every take bumps. A runner passes its ``Lease`` to each
write it makes, and the write's own transaction first checks that the lease is still the mission's
(``hold``), so a runner whose lease was taken over lands nothing. Time decides only WHEN a lease may
be taken (it expired, or was released); the fence alone decides whose writes land, so a clock that runs
ahead can hand a lease over early but can never make two writers.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from g6.intent_engine.schema import IntentContract
from g6.workforce.store import NOT_FOUND, OwnerScope, VentureStore, unscoped
from g6.workforce import acceptance as accept_rules
from g6.workforce import contracts, scheduler
from g6.workforce.contracts import BLOCKED, Outcome
from g6.workforce.plans import (
    RISK_TIERS,
    Plan,
    PlanRejected,
    higher,
    highest_risk,
    parse_plan,
    unit_floors,
)

STATUSES = ("eliciting", "awaiting_plan", "active", "waiting", "closing", "done", "cancelled")
FINISHED = frozenset({"done", "cancelled"})
#: Legal moves; ``active`` first only through activate() (a materialized plan), ``done`` only through
#: close() and ``cancelled`` only through cancel().
_NEXT = {
    "eliciting": frozenset({"awaiting_plan"}),
    "awaiting_plan": frozenset({"eliciting"}),
    "active": frozenset({"waiting", "closing"}),
    "waiting": frozenset({"active"}),
    "closing": frozenset({"active"}),
}
EVENT_KINDS = frozenset({
    "coding.draft_authorized", "coding.draft_returned",  # scratch grant and bound producer provenance
    "coding.output_refused",  # rejected producer bytes; never a runnable/accepted artifact
    "plan.generation",  # local proposals, including failed/interrupted inference; never verification evidence
    "mission.created", "mission.contract", "mission.status", "mission.closed",
    "plan.revision", "plan.reserved", "unit.materialized", "run.opened", "run.closed",
    "artifact", "verdict", "message", "approval.requested", "approval.decided", "wake", "lease.taken",
    # Phase E Part 1: what an attempt cost (E1a), a budget's pressure (E1c), and why a unit is tried again (E2a)
    "cost", "cost.reserved", "budget", "retry",
    # Phase E Part 2: a person's intervention (E1b)
    "intervention",
    "unmet_demand",  # what a pain spec asked that G6 cannot check or cannot meet (E7d)
    "schedule",  # E8e: the scheduler's decisions for a stored plan (one per revision; the first cut of E2d)
    "escalation",  # E2d: the scheduler's run-time decision for a code step whose result failed its check
    "preflight",  # the objective's standard, retrieval and selected routes, once per mission
    "reconfiguration",  # E2E Build 6: a change of course inside the frozen standard (reconfigure.py)
    "standard",  # E10b: a mission's standard was derived or raised (one per contract revision; TS §35)
    "control",  # RVC R6.3: a frozen check run by G6 against the unpatched baseline, killed when it failed there
    "formal",  # PGA P1.4: a confirmation challenge minted, consumed or refused (formal_approval.py); never shown to the chat
})
#: What unmet demand records (E7d): a need no check G6 can run measures, or a capability not available here.
UNMET_KINDS = ("cannot_check", "not_available")
#: The templates a mission is made from, its task class (E9a): ``ad_hoc`` for one the owner's chat drafted itself,
#: ``verify_task`` for a coding agent's call to verify_task (``verify_record``). E5a adds its templates here.
AD_HOC = "ad_hoc"
TEMPLATES = (AD_HOC, "verify_task", "code_change", "format_json")
#: Templates whose missions record a check G6 already gave and run nothing on the owner's behalf (E9a): E7d's
#: confirmation does not apply to them, and their standard is still derived and kept (E10b; S12; ruling E10.12).
RECORD_ONLY = ("verify_task",)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ACTORS = frozenset({"code", "chat", "human"})
#: How long one writer may hold a mission's materialization before another may take it over.
LEASE_SECONDS = 300
#: How long a runner's lease on a running mission lasts unless renewed (the runner's heartbeat renews it).
RUN_LEASE_SECONDS = 60
#: The statuses a runner leases: a mission with work in hand (a finished one never changes).
RUNNING = ("active", "waiting", "closing")
_RUNNING_SQL = "('active', 'waiting', 'closing')"
#: The idempotency-key stem of the event that records a materialized revision (``<stem><revision>``).
MATERIALIZED_KEY = "materialized:"
#: Columns added after the missions table first shipped on this branch (additive, idempotent).
_LATER_COLUMNS = {"plan_revision": "INTEGER NOT NULL DEFAULT 0", "lease_holder": "TEXT NOT NULL DEFAULT ''",
                  "lease_until": "TEXT NOT NULL DEFAULT ''", "lease_fence": "INTEGER NOT NULL DEFAULT 0",
                  "acceptance_json": "TEXT NOT NULL DEFAULT ''"}


class LeaseHeld(RuntimeError):
    """Another runner holds the mission's lease; it may be taken once it expires or is released."""

    def __init__(self, holder: str, until: str) -> None:
        self.holder, self.until = holder, until
        super().__init__(f"runner {holder} holds this mission until {until}")


class LeaseLost(RuntimeError):
    """This runner's lease is no longer the mission's (taken over, released or the mission finished):
    its write is refused and nothing changed."""


@dataclass(frozen=True)
class Lease:
    """A runner's hold on one running mission: ``fence`` is the take it came from."""

    mission_id: str
    holder: str
    fence: int
    until: str


def stamp(at: datetime) -> str:
    """One UTC text form for every lease time, so the stored times compare in time order."""
    return at.astimezone(UTC).isoformat(timespec="seconds")

_DDL = """
CREATE TABLE IF NOT EXISTS missions (
    mission_id         TEXT PRIMARY KEY,
    venture_id         TEXT NOT NULL,
    objective          TEXT NOT NULL,
    contract_json      TEXT NOT NULL DEFAULT '{}',
    risk_tier          TEXT NOT NULL,
    budget_cents       INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'eliciting',
    completion_state   TEXT NOT NULL DEFAULT '',
    outcome_json       TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    plan_revision      INTEGER NOT NULL DEFAULT 0,
    acceptance_json    TEXT NOT NULL DEFAULT '',
    lease_holder       TEXT NOT NULL DEFAULT '',
    lease_until        TEXT NOT NULL DEFAULT '',
    lease_fence        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_missions_venture ON missions(venture_id, created_at);
CREATE TRIGGER IF NOT EXISTS missions_finished_are_final BEFORE UPDATE ON missions
WHEN OLD.status IN ('done', 'cancelled')
BEGIN SELECT RAISE(ABORT, 'a finished mission does not change'); END;
CREATE TRIGGER IF NOT EXISTS missions_are_kept BEFORE DELETE ON missions
BEGIN SELECT RAISE(ABORT, 'missions are kept'); END;
CREATE TRIGGER IF NOT EXISTS missions_no_replace BEFORE INSERT ON missions
WHEN EXISTS (SELECT 1 FROM missions WHERE mission_id = NEW.mission_id)
BEGIN SELECT RAISE(ABORT, 'missions are kept'); END;
CREATE TABLE IF NOT EXISTS mission_plans (
    mission_id   TEXT NOT NULL,
    revision     INTEGER NOT NULL,
    plan_json    TEXT NOT NULL,
    plan_sha     TEXT NOT NULL,
    actor        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (mission_id, revision)
);
CREATE TRIGGER IF NOT EXISTS mission_plans_no_update BEFORE UPDATE ON mission_plans
BEGIN SELECT RAISE(ABORT, 'a plan revision is never rewritten'); END;
CREATE TRIGGER IF NOT EXISTS mission_plans_no_delete BEFORE DELETE ON mission_plans
BEGIN SELECT RAISE(ABORT, 'a plan revision is never rewritten'); END;
CREATE TRIGGER IF NOT EXISTS mission_plans_no_replace BEFORE INSERT ON mission_plans
WHEN EXISTS (SELECT 1 FROM mission_plans WHERE mission_id = NEW.mission_id AND revision = NEW.revision)
BEGIN SELECT RAISE(ABORT, 'a plan revision is never rewritten'); END;
"""

_OWNED = " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=m.venture_id AND v.owner=?)"


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def check_actor(actor: str) -> str:
    """``code``, ``chat``, ``human`` or ``worker:<id>``: who did something, recorded with it."""
    actor = str(actor)
    if actor in _ACTORS or (actor.startswith("worker:") and len(actor) > len("worker:")):
        return actor
    raise ValueError(f"unknown actor {actor!r}: one of {sorted(_ACTORS)} or worker:<id>")


def _contract(value: IntentContract | dict | None, objective: str) -> IntentContract:
    if value is None:
        return IntentContract(goal=objective)
    return value if isinstance(value, IntentContract) else IntentContract.model_validate(value)


class MissionStore:
    """One owner's missions, in the venture db (built from a scope, or a store plus an owner)."""

    def __init__(self, source: VentureStore | OwnerScope, owner: str | None = None) -> None:
        vstore, scope = unscoped(source)
        self.owner = scope.owner if scope is not None else (owner or "")
        if not self.owner:
            raise ValueError("a mission store needs an owner")
        self._vstore = vstore
        self._conn = vstore.conn
        self._conn.executescript(_DDL)
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(missions)").fetchall()}
        for column, kind in _LATER_COLUMNS.items():
            if column not in have:
                self._conn.execute(f"ALTER TABLE missions ADD COLUMN {column} {kind}")  # literal names above
        self._conn.commit()

    @classmethod
    def reader(cls, source: VentureStore | OwnerScope, owner: str | None = None) -> MissionStore:
        """This owner's missions for a reader that must not write (E2E Build 7 review I1): no schema statement runs."""
        found = cls.__new__(cls)
        vstore, scope = unscoped(source)
        found.owner = scope.owner if scope is not None else (owner or "")
        if not found.owner:
            raise ValueError("a mission store needs an owner")
        found._vstore, found._conn = vstore, vstore.conn
        return found

    # -- missions -----------------------------------------------------------------------------------
    def create(self, venture_id: str, *, objective: str, risk_tier: str, budget_cents: int, actor: str,
               contract: IntentContract | dict | None = None, template: str = AD_HOC, task_sha256: str = "",
               mode: str | None = None, materials: dict | None = None, request_key: str = "") -> str:
        """A new mission, ``eliciting``. Its creation event records what E9a's trace derives the task class and the
        environment from: the ``template`` it was made from and the G6 build that made it; ``task_sha256`` (the
        digest of the task it works on, when a template names one) links a later attempt at the same task.
        ``mode``: the mission's resource mode (Ledger §25; E2d), recorded only when one other than Balanced is
        chosen."""
        objective = str(objective or "").strip()
        if not objective:
            raise ValueError("a mission needs the owner's objective")
        if risk_tier not in RISK_TIERS:
            raise ValueError(f"risk tier must be one of {RISK_TIERS}, got {risk_tier!r}")
        if not isinstance(budget_cents, int) or isinstance(budget_cents, bool) or budget_cents < 0:
            raise ValueError(f"budget_cents must be a whole number of cents >= 0, got {budget_cents!r}")
        if template not in TEMPLATES:
            raise ValueError(f"a mission's template is one of {TEMPLATES}, got {template!r}")
        if task_sha256 and not _DIGEST.match(str(task_sha256)):
            raise ValueError("task_sha256 is a SHA-256 digest (64 lowercase hex characters)")
        if mode is not None and mode not in scheduler.MODE_NAMES:
            raise ValueError(f"a mission's resource mode is one of {scheduler.MODE_NAMES}, got {mode!r}")
        actor = check_actor(actor)
        if request_key and (actor != "code" or not isinstance(request_key, str)
                            or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,160}", request_key)):
            raise ValueError("An internal creation request key requires code and at most 160 safe characters.")
        # Lazy: the trace module imports this one.
        from g6.workforce.trace import g6_build

        created = {"risk_tier": risk_tier, "budget_cents": budget_cents, "template": template, "build": g6_build()}
        if materials is not None:
            if (not isinstance(materials, dict) or len(materials) > 16
                    or not all(isinstance(k, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", k)
                               for k in materials)):
                raise ValueError("materials must be an object of at most 16 named JSON values")
            try:
                text = json.dumps(materials, ensure_ascii=False, allow_nan=False)
                if len(text.encode("utf-8")) > 1_048_576:
                    raise ValueError("too large")
                created["materials"] = json.loads(text)
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError("materials must be finite JSON within 1 MiB") from exc
        if task_sha256:
            created["task_sha256"] = str(task_sha256)
        if mode not in (None, scheduler.DEFAULT_MODE):
            created["resource_mode"] = mode  # only when chosen, so every other mission keeps its record (P3.3)
        body =_contract(contract, objective).model_dump_json()
        if request_key:
            semantic = {"objective": objective, "contract": json.loads(body),
                        "creation": {k: v for k, v in created.items() if k != "build"}}
            created["request_sha256"] = hashlib.sha256(json.dumps(
                semantic, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        mid, now = _uid(), _now()
        try:
            with self._conn:  # request collision rolls back the unused mission and its creation together
                cur = self._conn.execute(
                    "INSERT INTO missions (mission_id, venture_id, objective, contract_json, risk_tier, budget_cents,"
                    " status, created_at, updated_at)"
                    " SELECT ?, v.venture_id, ?, ?, ?, ?, 'eliciting', ?, ? FROM ventures v"
                    " WHERE v.venture_id=? AND v.owner=?",
                    (mid, objective, body, risk_tier, budget_cents, now, now, str(venture_id), self.owner))
                if cur.rowcount == 0:
                    raise LookupError(NOT_FOUND)
                if request_key:
                    self._vstore.append_evidence(str(venture_id), "mission.created", ref=mid,
                        detail={**created, "actor": actor}, idem_key="mission-create:" + request_key)
                else:
                    self._append_event(str(venture_id), mid, "mission.created", actor, created, "created")
        except sqlite3.IntegrityError:
            row = self._conn.execute("SELECT ref, detail_json FROM evidence WHERE venture_id=? "
                "AND kind='mission.created' AND idem_key=?", (str(venture_id), "mission-create:" + request_key)).fetchone()
            if not request_key or row is None:
                raise
            self.get(row[0])  # ownership remains mandatory, including the collision path
            recorded = json.loads(row[1])
            if recorded.get("actor") != "code" or recorded.get("request_sha256") != created["request_sha256"]:
                raise ValueError("This creation request already has a different payload; conflicting reuse is refused.") from None
            return row[0]
        return mid

    def earlier(self, venture_id: str, *, template: str, task_sha256: str) -> list[str]:
        """This owner's missions in the venture made from ``template`` for the task with this digest, oldest first: the
        next one made is that task's next attempt (E9a: a repair is recorded as one)."""
        vid = OwnerScope(self._vstore, self.owner).resolve(venture_id)
        rows = self._conn.execute(
            "SELECT e.ref, e.detail_json FROM evidence e JOIN missions m ON m.mission_id = e.ref AND m.venture_id = e.venture_id"
            " WHERE e.venture_id=? AND e.kind='mission.created' AND json_extract(e.detail_json, '$.template')=?"
            " AND (json_extract(e.detail_json, '$.task_sha256')=? OR ("
            " COALESCE(json_extract(e.detail_json, '$.task_sha256'), '')='' AND"
            " CASE WHEN json_valid(m.acceptance_json) THEN"
            " json_extract(m.acceptance_json, '$.metadata.coding.task_signature.sha256') ELSE '' END=?))"
            + _OWNED + " ORDER BY e.rowid",
            (vid, str(template), str(task_sha256), str(task_sha256), self.owner)).fetchall()
        found = []
        for mid, raw in rows:
            created = json.loads(raw)
            if created.get("task_sha256") == task_sha256:
                found.append(mid)  # Preserve the primary historical identity; never override a conflict.
            elif template == "code_change" and created.get("actor") == "code":
                accepted = self.acceptance(mid)
                if (accepted.metadata.get("coding", {}).get("task_signature", {}).get("sha256") == task_sha256
                        and any(e["detail"].get("actor") == "code"
                                and e["detail"].get("acceptance_sha256") == accepted.sha256
                                for e in self.events(mid, kind="standard"))):
                    found.append(mid)
        return found

    def get(self, mission_id: str) -> dict:
        """The effective mission outcome under current evidence rules; stored history never changes."""
        out = self._get(mission_id)
        if out["status"] in FINISHED:
            closed = [event["detail"] for event in self.events(mission_id, kind="run.closed")]
            limits = contracts.current_suite_limits(closed)
            if limits:
                outcome = contracts.limited_outcome(Outcome.from_record(out["outcome"]), limits)
                out.update(outcome=outcome.as_record(), completion_state=outcome.state, evidence_limits=list(limits))
        return out

    def _get(self, mission_id: str) -> dict:
        """Owner-scoped stored row, also used to authorize raw event reads without recursive projection."""
        row = self._conn.execute("SELECT m.* FROM missions m WHERE m.mission_id=?" + _OWNED,
                                 (str(mission_id), self.owner)).fetchone()
        if row is None:
            raise LookupError(NOT_FOUND)
        out = dict(row)
        out["contract"] = json.loads(out.pop("contract_json") or "{}")
        out["outcome"] = json.loads(out.pop("outcome_json") or "{}")
        raw = out.pop("acceptance_json", "") or ""
        try:
            out["acceptance"] = json.loads(raw) if raw else None
        except ValueError:
            out["acceptance"] = {"unreadable": raw[:200]}  # read back as the unreadable contract (fail closed)
        return out

    def mode(self, mission_id: str) -> str:
        """The mission's resource mode (Ledger §25; E2d): Balanced unless its creation chose another."""
        created = self.events(mission_id, kind="mission.created")
        return scheduler.mode_of(created[0]["detail"] if created else {})

    def list(self, venture_id: str) -> list[dict]:
        vid = OwnerScope(self._vstore, self.owner).resolve(venture_id)
        rows = self._conn.execute("SELECT m.mission_id FROM missions m WHERE m.venture_id=?" + _OWNED
                                  + " ORDER BY m.rowid", (vid, self.owner)).fetchall()
        return [self.get(r[0]) for r in rows]

    def contract(self, mission_id: str) -> IntentContract:
        return IntentContract.model_validate(self.get(mission_id)["contract"])

    def set_contract(self, mission_id: str, contract: IntentContract | dict, *, actor: str) -> None:
        actor = check_actor(actor)
        mission = self.get(mission_id)
        if mission["status"] != "eliciting":
            raise ValueError(f"the contract is fixed once the owner accepts it (status {mission['status']})")
        body = _contract(contract, mission["objective"]).model_dump_json()
        with self._conn:
            self._update(mission_id, "eliciting", "contract_json=?", (body,))
            self._append_event(mission["venture_id"], mission_id, "mission.contract", actor, {})

    def accept(self, mission_id: str, acceptance: accept_rules.AcceptanceContract, *, actor: str,
               contract: IntentContract | dict | None = None) -> dict:
        """``eliciting`` -> ``awaiting_plan`` with the clarified contract (when given) and the acceptance contract it
        compiled into (E2c), in one transaction: a refusal changes nothing. Both are fixed from then on. The mission's
        tier rises to the standard's, never lowered (E10b; ruling E10.5): what was declared stays on its creation event.
        Accepted again while ``awaiting_plan`` before any plan revision exists (a plan raised the stakes, ruling E10.8),
        the contract is the next revision and the tier is kept. A record-only template is not asked for a complete
        spec (ruling E10.12)."""
        actor = check_actor(actor)
        mission = self.get(mission_id)
        from g6.workforce import coding

        pending = coding.clarification_request(self, mission_id)
        reused = coding.reuse_reference(self, mission_id)
        if reused and (actor != "code" or acceptance.sha256 != reused["standard_sha256"]
                       or (contract is not None and _contract(contract, mission["objective"]) != self.contract(mission_id))):
            raise ValueError("Harness reuse must preserve the exact recorded source standard and contract.")
        if pending:
            clarification = acceptance.metadata.get("coding", {}).get("intake_clarification", {})
            if actor != "code" or clarification.get("request_id") != pending["request_id"]:
                raise ValueError("Resolve the coding clarification through mission_accept before accepting a standard.")
        # Ruling E10.8, narrowly: again only when a plan raised this mission's standard and the raised standard needs the
        # owner's answers before it can be confirmed (no plan is stored yet); any other mission is accepted once.
        again = (mission["status"] == "awaiting_plan" and not self._revision(mission_id)
                 and mission["acceptance"] is not None and self.acceptance(mission_id).revision > 1
                 and not self.acceptance(mission_id).metadata.get("spec_complete"))
        if mission["status"] != "eliciting" and not again:
            # COPY: when a mission may be accepted (E10b adds the second time, ruling E10.8).
            raise ValueError(f"a mission is accepted while 'eliciting', or again before any plan is stored when a plan "
                             f"raised its standard and G6 asks for the owner's answers; it is {mission['status']!r}")
        tier = higher(mission["risk_tier"], accept_rules.risk_code(acceptance))
        if mission["acceptance"] is not None:  # accepted before: this is the next revision (TS §35)
            acceptance = replace(acceptance, revision=self.acceptance(mission_id).revision + 1)
        acceptance = accept_rules.floor(acceptance, tier)
        acceptance = scheduler.with_mode(acceptance, self.mode(mission_id))  # E2d: what the mode adds, in the digest
        if reused and acceptance.sha256 != reused["standard_sha256"]:
            raise ValueError("Current authority requires a different standard; harness reuse cannot silently change it.")
        assignments, values = "status=?, acceptance_json=?, risk_tier=?", ["awaiting_plan", acceptance.to_json(), tier]
        if contract is not None:
            assignments += ", contract_json=?"
            values.append(_contract(contract, mission["objective"]).model_dump_json())
        if (tier in accept_rules.CONFIRM_TIERS and not self.records_only(mission_id)
                and not acceptance.metadata.get("spec_complete")):
            asked = " ".join(str(q[1]) for q in acceptance.metadata.get("missing") or [])
            raise ValueError("This mission cannot be accepted yet: what counts as done is not complete for work "
                             f"this consequential ({tier}). Nothing was recorded. To resolve: ask the "
                             f"owner, then accept it again with the answers: {asked} (code G6_E_MISSING_PARAM)")
        # COPY: the reason recorded when the chat accepts the mission again, before any plan (ruling E10.8).
        reason = "the contract was accepted" + (" again, before any plan" if again else "")
        with self._conn:
            self._update(mission_id, mission["status"], assignments, tuple(values), " AND plan_revision=0")
            if contract is not None:
                self._append_event(mission["venture_id"], mission_id, "mission.contract", actor, {})
            self._append_event(mission["venture_id"], mission_id, "mission.status", actor,
                               {"from": mission["status"], "to": "awaiting_plan", "reason": reason,
                                "acceptance_sha256": acceptance.sha256})
            self._append_event(mission["venture_id"], mission_id, "standard", actor,
                               self._standard(acceptance, mission["risk_tier"], tier, "accepted"),
                               f"standard:{acceptance.revision}")
        return self.get(mission_id)

    def acceptance(self, mission_id: str) -> accept_rules.AcceptanceContract:
        """What counts as done for this mission (E2c): the contract it was accepted with, never asking less than its
        tier. A mission accepted before contracts were kept has one compiled from the clarified contract it holds,
        on every read. One that cannot be read holds the result back (fail closed)."""
        mission = self.get(mission_id)
        stored = mission["acceptance"]
        try:
            if stored is None:
                return scheduler.with_mode(accept_rules.compile_contract(
                    objective=mission["objective"], contract=IntentContract.model_validate(mission["contract"]),
                    risk_tier=mission["risk_tier"], budget_cents=mission["budget_cents"], compiled_on_read=True),
                    self.mode(mission_id))
            return accept_rules.floor(accept_rules.AcceptanceContract.from_dict(stored), mission["risk_tier"])
        except (TypeError, ValueError, KeyError) as exc:  # pydantic's ValidationError is a ValueError
            return accept_rules.unreadable(mission["risk_tier"], str(exc))

    def confirmed(self, mission_id: str) -> bool:
        """Whether a person confirmed this mission's current contract of what counts as done (E7d): a clarification
        intervention bound to its digest."""
        sha = self.acceptance(mission_id).sha256
        return any(e["detail"].get("kind") == "clarification" and e["detail"].get("outcome") == "confirmed"
                   and (e["detail"].get("metadata") or {}).get("acceptance_sha256") == sha
                   for e in self.events(mission_id, kind="intervention"))

    def template(self, mission_id: str) -> str:
        """The template the mission was made from (E9a), as its creation event records it."""
        created = self.events(mission_id, kind="mission.created")
        return str(created[0]["detail"].get("template") or AD_HOC) if created else AD_HOC

    def records_only(self, mission_id: str) -> bool:
        """Whether the mission records a check already given and runs nothing (``RECORD_ONLY``)."""
        return self.template(mission_id) in RECORD_ONLY

    def strengthen(self, mission_id: str, stronger: accept_rules.AcceptanceContract, *, actor: str,
                   reason: str) -> dict:
        """The one door that raises a mission's standard (E10b; TS §34-§35; S15): ``stronger`` is stored as the next
        revision and the mission's tier raised to it, with its ``standard`` event, in one transaction. A contract that
        would ask less in any way, or that is not exactly the next revision, is refused: weakening is never
        automatic. A mission accepted before standards were kept, or a finished one, keeps the standard it has."""
        actor = check_actor(actor)
        mission = self.get(mission_id)
        if mission["status"] in FINISHED or mission["acceptance"] is None:
            # COPY: the refusal when a mission's standard cannot be raised.
            raise ValueError("this mission keeps the standard it has: it is finished, or was accepted before standards "
                             "were kept (code G6_E_POLICY_BLOCKED)")
        current = self.acceptance(mission_id)
        lowers = accept_rules.weaker(stronger, current)
        if lowers or stronger.revision != current.revision + 1:
            # COPY: the refusal of a lowered standard (STD-005).
            raise ValueError("A standard is only ever raised, one revision at a time: this would "
                             f"{'; '.join(lowers) or 'not be the next revision'}. Nothing was recorded "
                             "(code G6_E_POLICY_BLOCKED)")
        tier = higher(mission["risk_tier"], accept_rules.risk_code(stronger))
        stored = accept_rules.floor(stronger, tier)
        with self._conn:
            self._update(mission_id, mission["status"], "acceptance_json=?, risk_tier=?", (stored.to_json(), tier))
            self._append_event(mission["venture_id"], mission_id, "standard", actor,
                               self._standard(stored, mission["risk_tier"], tier, reason),
                               f"standard:{stored.revision}")
        return self.get(mission_id)

    @staticmethod
    def _standard(contract: accept_rules.AcceptanceContract, before: str, after: str, reason: str) -> dict:
        """A ``standard`` event's detail (TS §35: a change of standard is logged): the revision, the tier before and
        after, why, the contract's digest (the auditor's U3 reads it) and the policy it was derived under."""
        return {"revision": contract.revision, "from": before, "to": after, "reason": str(reason)[:300],
                "acceptance_sha256": contract.sha256, "policy_version": contract.policy_version,
                "derived_from": [dict(d) for d in contract.derived_from]}

    def _versioned(self, mission_id: str, effects: Callable[[str, str], str | None] | None) -> Any:
        """The mission's contract when its plan's steps are set from it (E10b): one derived under a standard policy,
        with the broker's side effects to read; else None (a mission accepted before standards were kept, or whose
        template derived its own standard, plans as before)."""
        if effects is None or self.get(mission_id)["acceptance"] is None:
            return None
        contract = self.acceptance(mission_id)
        return contract if contract.policy_version else None

    def normalized(self, mission_id: str, plan: Any, effects: Callable[[str, str], str | None] | None) -> Any:
        """The drafted plan with each step's stakes set by the standard (E10b; S9; ruling E10.9): a step the plan did
        not rate takes the stakes the mission was accepted at (a delegated child its parent's), and no step is below
        the task's own signals or what its tools do. The same plan when ``_versioned`` is None. ``add_plan``, the
        service's resume path and a delegation all call this, so one draft always stores one digest."""
        contract = self._versioned(mission_id, effects)
        if contract is None:
            return plan
        return unit_floors(plan, effects=effects, inherit=accept_rules.base_tier(contract),
                           floor=accept_rules.code_floor(contract))

    def unconfirmed_standard(self, mission_id: str) -> tuple[accept_rules.Requirement, ...]:
        """A requirement no check can meet while a consequential mission's raised standard waits for its owner's
        confirmation (S15; ruling E10.11): () unless the standard is consequential, derived, kept and unconfirmed, and
        the mission runs work on the owner's behalf."""
        mission = self.get(mission_id)
        if (mission["risk_tier"] not in accept_rules.CONFIRM_TIERS or mission["acceptance"] is None
                or self.records_only(mission_id)):
            return ()
        contract = self.acceptance(mission_id)
        if not contract.policy_version or self.confirmed(mission_id):
            return ()
        # COPY: why a result waits for the owner's confirmation of a raised standard.
        return (accept_rules.Requirement(
            "you confirmed what counts as done", "", {},
            f"it was raised to revision {contract.revision} and you have not confirmed it; to resolve: python -P -m "
            f"g6.workforce.decide confirm {mission_id} --reason \"...\""),)

    def record_unmet(self, mission_id: str, *, kind: str, subject: str, why: str, unit: str = "") -> None:
        """A need G6 cannot check or cannot meet (E7d), once per mission and need, for the PMF instruments (C3)."""
        if kind not in UNMET_KINDS:
            raise ValueError(f"unmet demand is one of {UNMET_KINDS}, got {kind!r}")
        key = hashlib.sha256(f"{kind}:{subject}".encode()).hexdigest()[:16]
        self.record_event(mission_id, "unmet_demand", actor="code",
                          detail={"kind": kind, "subject": str(subject)[:200], "why": str(why)[:300], "unit": unit},
                          idem_key=f"unmet:{key}")

    def unmet(self, mission_id: str) -> list[dict]:
        return [{"mission_id": str(mission_id), "at": e["at"], **{k: e["detail"].get(k, "") for k in
                                                                    ("kind", "subject", "why", "unit")}}
                for e in self.events(mission_id, kind="unmet_demand")]

    # -- plans (append-only revisions)--------------------------------------------------------------
    def _coding_plan(self, mission_id: str, plan: Plan) -> None:
        standard = self.acceptance(mission_id).metadata.get("coding")
        if standard:
            from g6.workforce import coding

            problems = coding.plan_problems(standard, plan)
            if problems:
                raise PlanRejected(problems)

    def add_plan(self, mission_id: str, plan: dict | str, *, actor: str,
                 ready: Callable[[str, str], str | None],
                 effects: Callable[[str, str], str | None] | None = None,
                 reusable: Callable[[str, str], str | None] | None = None,
                 contract: Callable[[str, str], Any] | None = None,
                 authority: Callable[[str, str], str] | None = None, origin: str = "") -> int:
        """Validate a drafted plan against this mission's risk tier and budget and every capability it
        names against the broker (``ready``: ``Capabilities.ready``; ``effects``: ``Capabilities.side_effect``,
        so an external op runs only behind a gate; ``reusable``: ``Runtime.reusable``, so a saved result a unit
        names is judged now, E2b), then store it as the next revision with its event, in one transaction. Returns
        the revision number. ``contract``: ``Capabilities.contract``, so a call that breaks its op's contract is
        refused now (E8e). ``authority``: ``Capabilities.authority`` (Gate W): an A0 op, or an op that can act below
        A2, is refused now."""
        actor = check_actor(actor)
        if origin not in ("", "local_planner") or (origin and actor != "code"):
            raise ValueError("Generated plan provenance is recorded by code at admission.")
        mission = self.get(mission_id)
        if mission["status"] != "awaiting_plan":
            raise ValueError(f"a plan is added while the mission is 'awaiting_plan', not {mission['status']!r}")
        if mission["plan_revision"]:
            raise ValueError(f"plan revision {mission['plan_revision']} is reserved for materialization; "
                             "replanning comes with the supervisor (V1)")
        current = self._versioned(mission_id, effects)
        drafted = self.normalized(mission_id, plan, effects)
        # A versioned mission is judged at its highest step's stakes, which the strengthening below makes its own (S9);
        # one accepted before standards were kept is judged at its tier, as before (a step above it is refused).
        judged_at = mission["risk_tier"] if current is None else higher(mission["risk_tier"], highest_risk(drafted))
        parsed = parse_plan(drafted, risk_tier=judged_at, budget_cents=mission["budget_cents"], ready=ready,
                            effects=effects, reusable=reusable, contract=contract, authority=authority)
        self._coding_plan(mission_id, parsed)
        raised = ""
        if current is not None:  # E10b: the plan's steps may raise the standard, as its next revision, upward only
            stronger = accept_rules.for_plan(current, parsed, effects)
            if stronger is not current:
                self.strengthen(mission_id, stronger, actor="code", reason="the plan's steps ask for more")
                raised = "; ".join(str(d["why"]) for d in stronger.derived_from[len(current.derived_from):])
                mission = self.get(mission_id)
        # A mission accepted before contracts were kept (none stored) plans as before (fix pass I2); a record-only
        # template is not asked (ruling E10.12).
        if (mission["risk_tier"] in accept_rules.CONFIRM_TIERS and mission["acceptance"] is not None
                and not self.records_only(mission_id) and not self.confirmed(mission_id)):
            if raised:
                # COPY: the refusal when a plan raised the mission's standard and the owner has not confirmed it.
                raise ValueError(
                    f"This plan raised what counts as done for this mission to {mission['risk_tier']} ({raised}), and "
                    "you have not confirmed the raised standard. The raised standard was kept; the plan was not "
                    "stored. To resolve: confirm it at your terminal: python -P -m g6.workforce.decide confirm "
                    f"{mission_id} --reason \"...\", then submit the same plan again; if G6 says the spec is not "
                    "complete, accept the mission again with the owner's answers first (code G6_E_PENDING_APPROVAL)")
            raise ValueError("This plan cannot be stored yet: what counts as done for this mission "
                             f"({mission['risk_tier']}) has not been confirmed by you. Nothing was stored. To resolve: "
                             f"confirm it at your terminal: python -P -m g6.workforce.decide confirm {mission_id} "
                             "--reason \"...\" (code G6_E_PENDING_APPROVAL)")
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO mission_plans (mission_id, revision, plan_json, plan_sha, actor, created_at)"
                " SELECT m.mission_id,"
                " 1 + COALESCE((SELECT MAX(p.revision) FROM mission_plans p WHERE p.mission_id=m.mission_id), 0),"
                " ?, ?, ?, ? FROM missions m WHERE m.mission_id=? AND m.status='awaiting_plan' AND m.plan_revision=0"
                + _OWNED,
                (parsed.to_json(), parsed.sha256, actor, _now(), str(mission_id), self.owner))
            if cur.rowcount == 0:
                self.get(mission_id)  # NOT_FOUND for an unknown or foreign id
                raise ValueError("the mission changed underneath this call; read it again")
            revision = self._revision(mission_id)
            self._append_event(mission["venture_id"], mission_id, "plan.revision", actor,
                               {"revision": revision, "plan_sha": parsed.sha256, "units": len(parsed.units),
                                "origin": origin},
                               f"plan:{revision}")
        return revision

    def amend_plan(self, mission_id: str, plan: Plan, *, expect_revision: int, actor: str, detail: dict,
                   within: Callable[[int], object] | None = None) -> int:
        """The next revision of a running mission's plan (V4a: a delegation), judged by the caller
        (``parse_plan``), stored and made the mission's reserved revision by a compare-and-set on
        ``expect_revision`` (a concurrent amendment wins: ``ValueError``), with its ``plan.revision`` event and
        ``within(revision)`` (a write that must commit with it, e.g. waking the mission), in one transaction."""
        actor = check_actor(actor)
        mission = self.get(mission_id)
        if mission["status"] not in ("active", "waiting"):
            raise ValueError(f"a running mission's plan is amended while it is active or waiting, not "
                             f"{mission['status']!r}")
        self._coding_plan(mission_id, plan)
        expect = int(expect_revision)
        with self._conn:
            cur = self._conn.execute(
                "UPDATE missions SET plan_revision=plan_revision+1, updated_at=? WHERE mission_id=? AND plan_revision=?"
                " AND status IN ('active', 'waiting')"
                " AND (SELECT MAX(p.revision) FROM mission_plans p WHERE p.mission_id = missions.mission_id) = ?"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
                (_now(), str(mission_id), expect, expect, self.owner))
            if cur.rowcount == 0:
                raise ValueError("the mission changed underneath this call; read it again")
            revision = expect + 1
            self._conn.execute(
                "INSERT INTO mission_plans (mission_id, revision, plan_json, plan_sha, actor, created_at)"
                " VALUES (?,?,?,?,?,?)", (str(mission_id), revision, plan.to_json(), plan.sha256, actor, _now()))
            self._append_event(mission["venture_id"], mission_id, "plan.revision", actor,
                               {"revision": revision, "plan_sha": plan.sha256, "units": len(plan.units), **detail},
                               f"plan:{revision}")
            if within is not None:
                within(revision)
        return revision

    def plan(self, mission_id: str, revision: int | None = None) -> tuple[int, Plan]:
        """``(revision, plan)``: the latest revision, or the one named. A stored plan is re-judged on
        the way out and must still match its digest."""
        mission = self.get(mission_id)
        sql = ("SELECT p.revision, p.plan_json, p.plan_sha FROM mission_plans p"
               " JOIN missions m ON m.mission_id=p.mission_id WHERE p.mission_id=?" + _OWNED)
        args: list = [str(mission_id), self.owner]
        if revision is not None:
            sql += " AND p.revision=?"
            args.append(int(revision))
        row = self._conn.execute(sql + " ORDER BY p.revision DESC LIMIT 1", args).fetchone()
        if row is None:
            raise LookupError("this mission has no plan" + (f" revision {revision}" if revision is not None else ""))
        parsed = parse_plan(row["plan_json"], risk_tier=mission["risk_tier"], budget_cents=mission["budget_cents"])
        if parsed.sha256 != row["plan_sha"]:
            raise ValueError(f"plan revision {row['revision']} does not match its digest")
        # Older admission could persist a graph that omitted the frozen tests.
        # Refuse its execution/recovery, while leaving terminal history readable.
        if mission["status"] not in FINISHED:
            try:
                self._coding_plan(mission_id, parsed)
            except PlanRejected as exc:
                # MCP's ordinary PlanRejected advice to resubmit cannot repair a
                # reserved revision. Preserve it and name a supported next step.
                raise ValueError(
                    "The stored coding plan does not preserve its accepted checks, so it cannot continue. "
                    "Its history has been retained. Create a new mission with mission_create using the original "
                    "objective and repository, then confirm its standard before execution. " + str(exc)) from None
        return row["revision"], parsed

    # -- materialization (graph.py drives these; one writer at a time) ------------------------------
    def begin_materialize(self, mission_id: str, *, holder: str, actor: str) -> int:
        """Take the materialization lease and reserve the latest plan revision (kept on later takes).

        One writer at a time: a lease held by another holder refuses until it expires, so a writer
        that died does not strand the mission. Once reserved, no new revision is added and every
        read of the units uses the reserved one. Returns the reserved revision."""
        actor = check_actor(actor)
        mission = self.get(mission_id)
        if mission["status"] != "awaiting_plan":
            raise ValueError(f"a mission materializes its plan while 'awaiting_plan', not {mission['status']!r}")
        now = _now()
        until = (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat(timespec="seconds")
        with self._conn:
            cur = self._conn.execute(
                "UPDATE missions SET plan_revision = CASE WHEN plan_revision > 0 THEN plan_revision ELSE"
                " (SELECT MAX(p.revision) FROM mission_plans p WHERE p.mission_id = missions.mission_id) END,"
                " lease_holder=?, lease_until=?, updated_at=?"
                " WHERE mission_id=? AND status='awaiting_plan'"
                " AND EXISTS (SELECT 1 FROM mission_plans p WHERE p.mission_id = missions.mission_id)"
                " AND (lease_holder='' OR lease_holder=? OR lease_until < ?)"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
                (str(holder), until, now, str(mission_id), str(holder), now, self.owner))
            if cur.rowcount == 0:
                current = self.get(mission_id)
                if not self._revision(mission_id):
                    raise LookupError("this mission has no plan")
                raise ValueError(f"another writer is materializing this mission until {current['lease_until']}")
            revision = self.get(mission_id)["plan_revision"]
            if not mission["plan_revision"]:  # the first take reserves; a later take (a retry) only leases
                self._append_event(mission["venture_id"], mission_id, "plan.reserved", actor, {"revision": revision},
                                   f"reserved:{revision}")
        return revision

    def activate(self, mission_id: str, *, holder: str, actor: str) -> dict:
        """``awaiting_plan`` -> ``active``: only by the lease holder, once the reserved revision's
        materialization is recorded (the ``unit.materialized`` event); the lease ends with it."""
        actor = check_actor(actor)
        mission = self.get(mission_id)
        revision = mission["plan_revision"]
        with self._conn:
            cur = self._conn.execute(
                "UPDATE missions SET status='active', lease_holder='', lease_until='', updated_at=?"
                " WHERE mission_id=? AND status='awaiting_plan' AND plan_revision > 0 AND lease_holder=?"
                " AND EXISTS (SELECT 1 FROM evidence e WHERE e.venture_id = missions.venture_id"
                " AND e.idem_key = missions.mission_id || ':' || ? || missions.plan_revision)"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
                (_now(), str(mission_id), str(holder), MATERIALIZED_KEY, self.owner))
            if cur.rowcount == 0:
                raise ValueError("a mission becomes active when the lease holder has materialized its reserved plan")
            self._append_event(mission["venture_id"], mission_id, "mission.status", actor,
                               {"from": "awaiting_plan", "to": "active",
                                "reason": f"plan revision {revision} materialized"})
        return self.get(mission_id)

    def release(self, mission_id: str, *, holder: str) -> None:
        """Give the lease back (a writer that failed); the reservation stays."""
        with self._conn:
            self._conn.execute(
                "UPDATE missions SET lease_holder='', lease_until='' WHERE mission_id=? AND lease_holder=?"
                " AND status='awaiting_plan'"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
                (str(mission_id), str(holder), self.owner))

    def _revision(self, mission_id: str) -> int:
        return self._conn.execute(
            "SELECT MAX(p.revision) FROM mission_plans p JOIN missions m ON m.mission_id=p.mission_id"
            " WHERE p.mission_id=?" + _OWNED, (str(mission_id), self.owner)).fetchone()[0]

    # -- the runner lease (V2a: one runner per running mission) ----------------------------------------
    def take_lease(self, mission_id: str, *, holder: str, now: datetime | None = None,
                   seconds: int = RUN_LEASE_SECONDS, actor: str = "code",
                   alive: Callable[[str], bool | None] | None = None) -> Lease:
        """This runner's lease on a running mission: refused (``LeaseHeld``) while another holds it. Whether
        the other still holds it is ``alive(holder)`` when that knows (True: a live process, however long
        its attempt; False: it is gone), else whether its lease has expired. Every take bumps the fence and
        is recorded with whom it was taken from."""
        actor, holder = check_actor(actor), str(holder or "")
        if not holder:
            raise ValueError("a lease needs a holder")
        at = now or datetime.now(UTC)
        mission = self.get(mission_id)
        if mission["status"] not in RUNNING:
            raise ValueError(f"a runner leases a running mission, not a {mission['status']!r} one")
        current, until, fence = mission["lease_holder"], mission["lease_until"], mission["lease_fence"]
        if current and current != holder:
            known = alive(current) if alive is not None else None
            if known is True or (known is None and until >= stamp(at)):
                raise LeaseHeld(current, until)
        new_until = stamp(at + timedelta(seconds=seconds))
        with self._conn:
            cur = self._conn.execute(
                "UPDATE missions SET lease_holder=?, lease_until=?, lease_fence=lease_fence+1, updated_at=?"
                f" WHERE mission_id=? AND status IN {_RUNNING_SQL} AND lease_holder=? AND lease_fence=?"
                " AND lease_until=?"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
                (holder, new_until, _now(), str(mission_id), current, fence, until, self.owner))
            if cur.rowcount == 0:  # another runner took it, or its holder renewed it, between the read and this write
                again = self.get(mission_id)
                raise LeaseHeld(again["lease_holder"], again["lease_until"])
            self._append_event(mission["venture_id"], mission_id, "lease.taken", actor,
                               {"holder": holder, "fence": fence + 1, "previous": current, "previous_until": until},
                               f"lease:{fence + 1}")
        return Lease(str(mission_id), holder, fence + 1, new_until)

    def renew_lease(self, lease: Lease, *, now: datetime | None = None, seconds: int = RUN_LEASE_SECONDS) -> Lease:
        """The same lease, running ``seconds`` from ``now`` (``LeaseLost`` once it is no longer the mission's)."""
        until = stamp((now or datetime.now(UTC)) + timedelta(seconds=seconds))
        with self._conn:
            cur = self._conn.execute(
                "UPDATE missions SET lease_until=? WHERE mission_id=? AND lease_holder=? AND lease_fence=?"
                f" AND status IN {_RUNNING_SQL}"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
                (until, lease.mission_id, lease.holder, lease.fence, self.owner))
        if cur.rowcount == 0:
            raise LeaseLost(f"runner {lease.holder} no longer holds mission {lease.mission_id}")
        return Lease(lease.mission_id, lease.holder, lease.fence, until)

    def release_lease(self, lease: Lease) -> None:
        """Give the lease back so the next runner takes it at once (a lease no longer held is left alone)."""
        with self._conn:
            self._conn.execute(
                "UPDATE missions SET lease_holder='', lease_until='' WHERE mission_id=? AND lease_holder=?"
                f" AND lease_fence=? AND status IN {_RUNNING_SQL}"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
                (lease.mission_id, lease.holder, lease.fence, self.owner))

    def check_lease(self, lease: Lease | None) -> None:
        """``LeaseLost`` unless ``lease`` is still its mission's (``None``: unleased). For a write that cannot
        share the lease's transaction (a move in units.db): checked right before it."""
        if lease is None:
            return
        found = self._conn.execute(
            "SELECT m.lease_holder, m.lease_fence, m.status FROM missions m WHERE m.mission_id=?" + _OWNED,
            (lease.mission_id, self.owner)).fetchone()
        if found is None or (found[0], found[1]) != (lease.holder, lease.fence) or found[2] in FINISHED:
            raise LeaseLost(f"runner {lease.holder} no longer holds mission {lease.mission_id}: its move is refused")

    def hold(self, mission_id: str, lease: Lease | None) -> None:
        """Inside the CALLER's transaction, before its writes: refuse (``LeaseLost``) unless ``lease`` is still
        the mission's. Its write lock is held to the commit, so no take lands in between. ``None``: unleased."""
        if lease is None:
            return
        if str(mission_id) != lease.mission_id:
            raise LeaseLost(f"a lease on mission {lease.mission_id} does not cover mission {mission_id}")
        cur = self._conn.execute(
            "UPDATE missions SET lease_fence=lease_fence WHERE mission_id=? AND lease_holder=? AND lease_fence=?"
            " AND status NOT IN ('done', 'cancelled')"
            " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
            (lease.mission_id, lease.holder, lease.fence, self.owner))
        if cur.rowcount == 0:
            raise LeaseLost(f"runner {lease.holder} no longer holds mission {mission_id}: its write is refused")

    # -- status -------------------------------------------------------------------------------------
    def transition(self, mission_id: str, to: str, *, reason: str, actor: str, lease: Lease | None = None) -> dict:
        """One move on the state machine (a runner passes its ``lease``: refused once it is lost)."""
        actor = check_actor(actor)
        mission = self.get(mission_id)
        current = mission["status"]
        if to not in _NEXT.get(current, frozenset()):
            raise ValueError(f"a mission cannot move from {current!r} to {to!r}")
        # A reserved plan is being (or was) materialized into units: going back to eliciting would strand them.
        unreserved = current == "awaiting_plan"
        if unreserved and mission["plan_revision"]:
            raise ValueError(f"plan revision {mission['plan_revision']} is reserved; replanning is V1")
        with self._conn:
            self.hold(mission_id, lease)
            self._update(mission_id, current, "status=?", (to,), " AND plan_revision=0" if unreserved else "")
            self._append_event(mission["venture_id"], mission_id, "mission.status", actor,
                               {"from": current, "to": to, "reason": str(reason)})
        return self.get(mission_id)

    def move(self, mission_id: str, to: str, *, expect: str, reason: str, actor: str,
             detail: dict | None = None) -> None:
        """One state-machine move inside the CALLER's transaction (for a write that must commit with it):
        ``expect`` is the status the caller read, and a concurrent move wins (``ValueError``)."""
        actor = check_actor(actor)
        if to not in _NEXT.get(expect, frozenset()) or expect == "awaiting_plan":
            raise ValueError(f"a mission cannot move from {expect!r} to {to!r} here")
        venture_id = self.get(mission_id)["venture_id"]
        self._update(mission_id, expect, "status=?", (to,))
        self._append_event(venture_id, mission_id, "mission.status", actor,
                           {"from": expect, "to": to, "reason": str(reason), **(detail or {})})

    def close(self, mission_id: str, outcome: Outcome, *, actor: str, lease: Lease | None = None,
              detail: dict | None = None) -> dict:
        """``closing`` -> ``done``, recording the rolled-up outcome (``detail``: more for the ``mission.closed`` event,
        e.g. the acceptance evaluation, E2c)."""
        actor = check_actor(actor)
        current = self.get(mission_id)["status"]
        if current != "closing":
            raise ValueError(f"a mission closes from 'closing', not {current!r}")
        return self._finish(mission_id, current, "done", outcome, actor, lease, detail)

    def cancel(self, mission_id: str, *, reason: str, actor: str) -> dict:
        """Any unfinished status -> ``cancelled``, which is blocked-escalated with the reason."""
        actor = check_actor(actor)
        current = self.get(mission_id)["status"]
        if current in FINISHED:
            raise ValueError(f"a {current} mission does not change")
        return self._finish(mission_id, current, "cancelled", Outcome(BLOCKED, reasons=(f"cancelled: {reason}",)),
                            actor)

    def _finish(self, mission_id: str, current: str, to: str, outcome: Outcome, actor: str,
                lease: Lease | None = None, detail: dict | None = None) -> dict:
        venture_id = self.get(mission_id)["venture_id"]
        body = json.dumps({"state": outcome.state, "degraded": outcome.degraded, "reasons": list(outcome.reasons),
                           "evidence": list(outcome.evidence), "source": outcome.source})
        with self._conn:  # the finish and its event commit together: a failed event leaves the mission open
            self.hold(mission_id, lease)
            self._update(mission_id, current, "status=?, completion_state=?, outcome_json=?",
                         (to, outcome.state, body))
            self._append_event(venture_id, mission_id, "mission.closed", actor,
                               {"status": to, "state": outcome.state, **(detail or {})})
        return self.get(mission_id)

    def _update(self, mission_id: str, expected_status: str, assignments: str, values: tuple,
                also: str = "") -> None:
        """One owner-scoped compare-and-set on the status the caller read (a concurrent move wins).
        Runs inside the caller's transaction, beside the event that records it."""
        cur = self._conn.execute(
            # `assignments` and `also` are this module's literal fragments, never caller input.
            f"UPDATE missions SET {assignments}, updated_at=? WHERE mission_id=? AND status=?{also}"
            " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
            (*values, _now(), str(mission_id), expected_status, self.owner))
        if cur.rowcount == 0:
            self.get(mission_id)  # NOT_FOUND for an unknown or foreign id
            raise ValueError("the mission changed underneath this call; read it again")

    @property
    def conn(self):
        """The venture db connection the mission tables live in (the attempt ledger's tables too)."""
        return self._conn

    @property
    def scope(self) -> OwnerScope:
        """This owner's view of the venture store the missions live in (V3b records replies through it)."""
        return OwnerScope(self._vstore, self.owner)

    def append_event(self, mission_id: str, kind: str, actor: str, detail: dict, idem_key: str = "") -> str:
        """An event row inside the CALLER's transaction (for a write that must commit with its event). The
        mission resolves through this owner's scope and its venture is derived from it, never supplied, so
        another owner's mission is ``LookupError(NOT_FOUND)`` like an unknown one."""
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown mission event kind {kind!r}")
        actor = check_actor(actor)
        venture_id = self.get(mission_id)["venture_id"]
        return self._append_event(venture_id, mission_id, kind, actor, detail, idem_key)

    def _append_event(self, venture_id: str, mission_id: str, kind: str, actor: str, detail: dict,
                      idem_key: str = "") -> str:
        """An event row inside the caller's transaction (``kind`` and ``actor`` already checked)."""
        key = f"{mission_id}:{idem_key}" if idem_key else ""
        return self._vstore.append_evidence(venture_id, kind, ref=str(mission_id),
                                            detail={**detail, "actor": actor}, idem_key=key)

    # -- events (venture evidence rows) ---------------------------------------------------------------
    def record_event(self, mission_id: str, kind: str, *, actor: str, detail: dict | None = None,
                     idem_key: str = "") -> str:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown mission event kind {kind!r}")
        actor = check_actor(actor)
        venture_id = self.get(mission_id)["venture_id"]
        key = f"{mission_id}:{idem_key}" if idem_key else ""
        return self._vstore.record_evidence(venture_id, kind, ref=str(mission_id),
                                            detail={**(detail or {}), "actor": actor}, idem_key=key)

    def messages(self, mission_id: str, *, to: str | None = None) -> list[dict]:
        """Messages between the mission's units (V4b: a verifier's reasons to the unit it sent back), oldest
        first; ``to``: only those to that unit."""
        out = []
        for event in self.events(mission_id, kind="message"):
            found = event["detail"]
            if to is None or found.get("to") == to:
                out.append({"from": found.get("from", ""), "to": found.get("to", ""), "text": found.get("text", ""),
                            "refs": found.get("refs", {})})
        return out

    def events(self, mission_id: str, *, kind: str | None = None) -> list[dict]:
        """The mission's history, oldest first (rowid order, never the racy ``seq``)."""
        venture_id = self._get(mission_id)["venture_id"]
        sql, args = "SELECT * FROM evidence WHERE venture_id=? AND ref=?", [venture_id, str(mission_id)]
        if kind is not None:
            sql += " AND kind=?"
            args.append(kind)
        out = []
        for row in self._conn.execute(sql + " ORDER BY rowid", args).fetchall():
            d = dict(row)
            if d["kind"] not in EVENT_KINDS:
                continue
            d["detail"] = json.loads(d.pop("detail_json") or "{}")
            out.append(d)
        return out
