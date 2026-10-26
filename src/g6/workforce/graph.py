# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The work graph: a mission's plan as task_tracker units (workforce OS plan, P0 T3).

Each unit of the mission's reserved plan revision is one ``TaskTrackerStore`` row in
``<db_dir>/workforce/units.db`` (title = the unit key, project_id = the mission id, component =
``workforce``) and every tracker call carries the mission's owner. The tracker's code is used
unchanged; what it lacks is held here, and in two rules this module installs on its own units.db:

* a unique index on (owner, mission, key): one row per unit, whatever writers race;
* a trigger: a complete workforce unit is final (the tracker would let it be claimed again).

The unit's spec lives in the append-only plan, never in the row (``fail_task`` overwrites
``metadata_json``). Dependencies are the plan's, with consumers moved onto verify units
(``Plan.depends_on``), checked here on every claim and written as tracker edges too.

venture.db and units.db cannot share a transaction, so materializing is ordered and resumable: take
the mission's lease and reserve its latest revision (venture.db), create each unit and its edges in
dependency order (find-or-create), record the materialization, then activate (which ends the lease).
A failure releases the lease; a writer that died loses it when it expires. Until the mission is
``active`` nothing is claimed, and a unit that moved meanwhile is refused, never adopted.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path

from g6.core.storage import open_db
from g6.workforce.missions import MATERIALIZED_KEY, MissionStore, check_actor
from g6.workforce.plans import MAX_UNITS, Plan, UnitSpec
from g6.workforce.store import default_db_path
from g6.workforce.tracker import TaskTrackerStore

COMPONENT = "workforce"
#: The only tracker statuses a unit is claimed from (a retry claims a failed unit).
CLAIMABLE = frozenset({"pending", "failed"})
#: Who holds a unit while the supervisor re-opens it (V4b).
SUPERVISOR = "supervisor"
_RULES = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_workforce_unit ON tasks(owner, project_id, title) WHERE component = 'workforce';
CREATE TABLE IF NOT EXISTS workforce_reopens (
    grant_id    TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    granted_at  TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_workforce_reopen_unused ON workforce_reopens(task_id) WHERE used = 0;
CREATE TRIGGER IF NOT EXISTS workforce_reopens_kept BEFORE DELETE ON workforce_reopens
BEGIN SELECT RAISE(ABORT, 'reopen grants are kept'); END;
CREATE TRIGGER IF NOT EXISTS workforce_reopens_used_once BEFORE UPDATE ON workforce_reopens
WHEN OLD.used <> 0 OR NEW.used <> 1 OR NEW.grant_id <> OLD.grant_id OR NEW.task_id <> OLD.task_id
  OR NEW.reason <> OLD.reason OR NEW.granted_at <> OLD.granted_at
BEGIN SELECT RAISE(ABORT, 'a reopen grant is used once and kept'); END;
CREATE TRIGGER IF NOT EXISTS workforce_reopen_used AFTER UPDATE OF status ON tasks
WHEN OLD.component = 'workforce' AND OLD.status = 'complete' AND NEW.status = 'in_progress'
BEGIN UPDATE workforce_reopens SET used = 1 WHERE task_id = OLD.id AND used = 0; END;
"""
#: A complete unit is final, except one move to in_progress under an unused reopen grant (V4b). The trigger
#: replaces the P0 one (same name) in a units.db made before V4b.
_FINAL = """
CREATE TRIGGER workforce_complete_is_final BEFORE UPDATE OF status ON tasks
WHEN OLD.component = 'workforce' AND OLD.status = 'complete' AND NEW.status <> 'complete'
 AND NOT (NEW.status = 'in_progress'
          AND EXISTS (SELECT 1 FROM workforce_reopens r WHERE r.task_id = OLD.id AND r.used = 0))
BEGIN SELECT RAISE(ABORT, 'a complete workforce unit is final'); END;
"""


def default_units_path() -> Path:
    return default_db_path().parent / "workforce" / "units.db"


def _blocker(row: dict) -> str:
    """Why an escalated unit is blocked (the tracker keeps it in the row's metadata)."""
    if row.get("status") != "blocked":
        return ""
    try:
        found = json.loads(row.get("metadata_json") or "{}")
    except ValueError:
        return ""
    return str(found.get("blocker") or "") if isinstance(found, dict) else ""


class WorkGraph:
    """One owner's units, for the missions their ``MissionStore`` can see."""

    def __init__(self, missions: MissionStore, units: str | Path, *, tracker: TaskTrackerStore | None = None) -> None:
        self._missions = missions
        self.owner = missions.owner
        self._tracker = tracker if tracker is not None else TaskTrackerStore(str(units))
        self._units = Path(units)
        conn = open_db(Path(units).resolve())
        try:
            conn.executescript(_RULES)
            found = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name="
                                 "'workforce_complete_is_final'").fetchone()
            if found is None or "workforce_reopens" not in found[0]:  # absent, or the P0 rule: replace it whole
                conn.executescript("BEGIN IMMEDIATE; DROP TRIGGER IF EXISTS workforce_complete_is_final;"
                                   + _FINAL + "COMMIT;")
        finally:
            conn.close()
        self._holder = f"graph:{uuid.uuid4().hex[:12]}"

    # -- materialize ------------------------------------------------------------------------------
    def materialize(self, mission_id: str, *, actor: str) -> dict[str, str]:
        """The reserved plan revision as units and edges, then the mission ``active``. Returns key -> task id."""
        actor = check_actor(actor)
        mission = self._missions.get(mission_id)
        if mission["status"] != "awaiting_plan":
            return self._settled(mission)
        revision = self._missions.begin_materialize(mission_id, holder=self._holder, actor=actor)
        try:
            ids = self._build(mission_id, revision)
            self._missions.record_event(mission_id, "unit.materialized", actor=actor,
                                        detail={"revision": revision, "units": ids},
                                        idem_key=f"{MATERIALIZED_KEY}{revision}")
            self._missions.activate(mission_id, holder=self._holder, actor=actor)
        except BaseException:
            self._release(mission_id)
            raise
        return ids

    def _build(self, mission_id: str, revision: int) -> dict[str, str]:
        _, plan = self._missions.plan(mission_id, revision)
        rows = self._rows(mission_id)
        self._agree(plan, rows, revision, unstarted=True)
        return self._create(mission_id, plan, rows)

    def extend(self, mission_id: str) -> dict[str, str]:
        """A running mission's reserved revision that names units not yet in units.db (V4a: a delegation's
        children): those units and their edges, created (find-or-create, so it resumes after a crash). Nothing to
        do (an empty dict) when every unit has its row."""
        mission = self._missions.get(mission_id)
        if mission["status"] not in ("active", "waiting", "closing") or not mission["plan_revision"]:
            return {}
        _, plan = self._missions.plan(mission_id, mission["plan_revision"])
        rows = self._rows(mission_id)
        if all(u.key in rows for u in plan.units):
            return {}
        self._agree(plan, rows, mission["plan_revision"])
        return self._create(mission_id, plan, rows)

    def _create(self, mission_id: str, plan: Plan, rows: dict[str, list[dict]]) -> dict[str, str]:
        ids = {key: found[0]["id"] for key, found in rows.items()}
        deps = plan.depends_on()
        for key in plan.order():  # each unit, then at once its edges (its upstream units already exist)
            if key not in ids:
                unit = plan.unit(key)
                try:
                    ids[key] = self._tracker.create_task(title=key, description=unit.goal, priority=unit.priority,
                                                         component=COMPONENT, project_id=str(mission_id),
                                                         owner=self.owner)["id"]
                except sqlite3.IntegrityError:  # another writer created it first (the unique index): find it
                    found = self._rows(mission_id).get(key)
                    if not found:
                        raise
                    ids[key] = found[0]["id"]
            for dep in sorted(deps[key]):
                if ids[dep] in self._upstream(ids[key]):
                    continue
                added = self._tracker.add_dependency(ids[key], ids[dep], owner=self.owner)
                if "error" in added and ids[dep] not in self._upstream(ids[key]):
                    raise RuntimeError(f"unit {key!r}: the tracker refused the edge to {dep!r}: {added['error']}")
        return ids

    def _settled(self, mission: dict) -> dict[str, str]:
        """A mission past ``awaiting_plan``: its units' ids, if its reserved plan is fully materialized."""
        revision = mission["plan_revision"]
        if not revision:
            raise ValueError(f"a mission materializes its plan while 'awaiting_plan', not {mission['status']!r}")
        _, plan = self._missions.plan(mission["mission_id"], revision)
        rows = self._rows(mission["mission_id"])
        self._agree(plan, rows, revision)
        if len(rows) != len(plan.units):
            raise ValueError(f"plan revision {revision} is not fully materialized")
        return {key: found[0]["id"] for key, found in rows.items()}

    def _release(self, mission_id: str) -> None:
        self._missions.release(mission_id, holder=self._holder)

    def _agree(self, plan: Plan, rows: dict[str, list[dict]], revision: int, *, unstarted: bool = False) -> None:
        strays = sorted(set(rows) - {u.key for u in plan.units})
        doubles = sorted(key for key, found in rows.items() if len(found) > 1)
        problems = []
        if strays:
            problems.append(f"units plan revision {revision} does not name: {strays}")
        if doubles:
            problems.append(f"more than one unit for: {doubles}")
        if unstarted:
            moved = sorted(f"{key} ({found[0]['status']})" for key, found in rows.items()
                           if found[0]["status"] != "pending")
            if moved:
                problems.append(f"units moved before materialization finished: {moved}")
        if not problems:
            ids = {key: found[0]["id"] for key, found in rows.items()}
            deps = plan.depends_on()
            for key, task_id in ids.items():
                drawn = {ids[d] for d in deps[key] if d in ids}
                if self._upstream(task_id) - drawn:
                    problems.append(f"unit {key!r} has a dependency plan revision {revision} does not draw")
        if problems:
            raise ValueError("the tracker disagrees with the plan: " + "; ".join(problems))

    # -- reads ------------------------------------------------------------------------------------
    def units(self, mission_id: str) -> list[dict]:
        """The materialized units in plan order: key, kind, the unit that delegated it (V4a), task id, tracker
        status and, for an escalated unit, why."""
        _, plan, rows = self._view(mission_id)
        return [{"key": u.key, "kind": u.kind, "parent": u.parent, "task_id": rows[u.key]["id"],
                 "status": rows[u.key]["status"], "assigned_agent": rows[u.key]["assigned_agent"],
                 "reason": _blocker(rows[u.key])}
                for u in plan.units if u.key in rows]

    def spec(self, mission_id: str, key: str) -> UnitSpec:
        """The unit as the plan states it (never the tracker row, which ``fail_task`` rewrites)."""
        return self._plan(self._missions.get(mission_id)).unit(key)

    def claimable(self, mission_id: str) -> list[str]:
        """Pending or failed units whose dependencies are all complete, highest priority first (on an active
        mission, or a waiting one: what it waits for is ready but held)."""
        mission, plan, rows = self._view(mission_id)
        if mission["status"] not in ("active", "waiting"):
            return []
        deps = plan.depends_on()
        ready = [u for u in plan.units if u.key in rows and rows[u.key]["status"] in CLAIMABLE
                 and not self._waiting(deps[u.key], rows)]
        return [u.key for u in sorted(ready, key=lambda u: -u.priority)]

    # -- moves ------------------------------------------------------------------------------------
    def claim(self, mission_id: str, key: str, *, agent: str) -> dict:
        agent = check_actor(agent)
        mission, plan, rows = self._view(mission_id)
        row = self._row(plan, rows, key)
        if mission["status"] != "active":
            raise ValueError(f"units are claimed while the mission is 'active', not {mission['status']!r}")
        if row["status"] not in CLAIMABLE:
            raise ValueError(f"unit {key!r} is {row['status']!r}: only a pending or failed unit is claimed")
        waiting = self._waiting(plan.depends_on()[key], rows)
        if waiting:
            raise ValueError(f"unit {key!r} waits on {waiting}")
        return self._move(key, lambda: self._tracker.claim_task(row["id"], assigned_agent=agent, owner=self.owner))

    def complete(self, mission_id: str, key: str, *, agent: str | None = None) -> dict:
        row = self._held(mission_id, key, agent)
        return self._move(key, lambda: self._tracker.complete_task(row["id"], owner=self.owner))

    def escalate(self, mission_id: str, key: str, reason: str, *, agent: str | None = None) -> dict:
        """A pending or running unit no rung here can finish: blocked, for the supervisor or a human."""
        row = self._held(mission_id, key, agent)
        return self._move(key, lambda: self._tracker.block_task(row["id"], blocker_reason=str(reason),
                                                                owner=self.owner))

    def fail(self, mission_id: str, key: str, reason: str, *, agent: str | None = None) -> dict:
        row = self._held(mission_id, key, agent)
        return self._move(key, lambda: self._tracker.fail_task(row["id"], error_message=str(reason),
                                                               owner=self.owner))

    def reopen(self, mission_id: str, key: str, reason: str) -> dict:
        """The supervisor re-opens a unit for another attempt (V4b): a complete unit moves to ``failed`` (so it
        is claimable again) through one reopen grant written here and used up by the move; a failed or pending
        unit is open already. Resumable: a unit a crash left mid-reopen (held by the supervisor) is finished."""
        _, plan, rows = self._view(mission_id)
        row = self._row(plan, rows, key)
        if row["status"] == "complete":
            self._grant(row["id"], reason)
            self._move(key, lambda: self._tracker.claim_task(row["id"], assigned_agent=SUPERVISOR, owner=self.owner))
            row = self._row(*self._view(mission_id)[1:], key)
        if row["status"] == "in_progress" and row["assigned_agent"] == SUPERVISOR:
            return self._move(key, lambda: self._tracker.fail_task(row["id"], error_message=f"reopened: {reason}",
                                                                   owner=self.owner))
        if row["status"] in CLAIMABLE:
            return row
        raise ValueError(f"unit {key!r} is {row['status']!r}: the supervisor re-opens only a complete unit")

    def _grant(self, task_id: str, reason: str) -> None:
        conn = open_db(self._units.resolve())
        try:
            with conn:
                conn.execute("INSERT OR IGNORE INTO workforce_reopens (grant_id, task_id, reason, granted_at)"
                             " SELECT ?, ?, ?, datetime('now') WHERE NOT EXISTS"
                             " (SELECT 1 FROM workforce_reopens WHERE task_id=? AND used=0)",
                             (f"rg_{uuid.uuid4().hex[:12]}", task_id, str(reason)[:300], task_id))
        finally:
            conn.close()

    def _held(self, mission_id: str, key: str, agent: str | None) -> dict:
        """The unit's row, refused when ``agent`` (the attempt moving it) is not the attempt that holds it: a
        stale attempt's late move never lands on a retry another attempt has claimed (Codex V2)."""
        _, plan, rows = self._view(mission_id)
        row = self._row(plan, rows, key)
        if agent is not None and row["assigned_agent"] != agent:
            raise ValueError(f"unit {key!r} was taken by another attempt ({row['assigned_agent'] or 'none'}): "
                             f"{agent} may not move it")
        return row

    # -- helpers ----------------------------------------------------------------------------------
    def _plan(self, mission: dict) -> Plan:
        """The reserved revision once there is one, else the latest."""
        return self._missions.plan(mission["mission_id"], mission["plan_revision"] or None)[1]

    def _rows(self, mission_id: str) -> dict[str, list[dict]]:
        limit = 2 * MAX_UNITS + 1
        found = self._tracker.list_tasks(project_id=str(mission_id), component=COMPONENT, limit=limit,
                                         owner=self.owner)
        if len(found) >= limit:
            raise ValueError(f"the tracker holds {len(found)}+ units for this mission, more than any plan names")
        rows: dict[str, list[dict]] = {}
        for row in found:
            rows.setdefault(row["title"], []).append(row)
        return rows

    def _view(self, mission_id: str) -> tuple[dict, Plan, dict[str, dict]]:
        mission = self._missions.get(mission_id)
        plan = self._plan(mission)
        rows = self._rows(mission_id)
        self._agree(plan, rows, mission["plan_revision"])
        return mission, plan, {key: found[0] for key, found in rows.items()}

    @staticmethod
    def _row(plan: Plan, rows: dict[str, dict], key: str) -> dict:
        plan.unit(key)  # KeyError for a unit the plan does not name
        if key not in rows:
            raise ValueError(f"unit {key!r} is not materialized")
        return rows[key]

    @staticmethod
    def _waiting(deps: frozenset[str], rows: dict[str, dict]) -> list[str]:
        return sorted(d for d in deps if d not in rows or rows[d]["status"] != "complete")

    def _upstream(self, task_id: str) -> set[str]:
        return {e["depends_on_id"] for e in self._tracker.get_dependencies(task_id, owner=self.owner)["depends_on"]}

    @staticmethod
    def _move(key: str, write: Callable[[], dict]) -> dict:
        try:
            result = write()
        except sqlite3.DatabaseError as exc:  # a rule on units.db refused it (e.g. a complete unit is final)
            raise ValueError(f"unit {key!r} changed underneath this move: {exc}") from None
        if not result or "error" in result:
            raise ValueError(f"unit {key!r}: the tracker refused the move: {result.get('error', 'no such unit')}")
        return result
