# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The unit store the work graph keeps a plan's units in (vendored subset of the archive's ``task_tracker`` store).

Only what ``graph.WorkGraph`` calls is kept: tasks, their dependency edges, their event log and the caller-keyed
refusal log, with the same schema, transitions and ownership rules. Scheduling, reviews, costs, progress snapshots,
search and deletion belong to the task tracker component and are not ported.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from g6.core.storage import open_db

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"scheduled", "in_progress", "blocked"}),
    "scheduled": frozenset({"in_progress", "pending"}),
    "in_progress": frozenset({"complete", "failed", "blocked"}),
    "blocked": frozenset({"in_progress"}),
    # RS-16-F04: these 'complete -> in_progress' / 'failed -> in_progress' edges are
    # KEPT INTENTIONALLY — claim_task uses 'failed -> in_progress' for the legitimate
    # retry path (tests/.../test_store.py::test_retry_failed_task). The resurrection
    # bug is fully closed by unblock_task's blocked-only guard (see store.py), so the
    # edges stay rather than break retry. Do not remove without auditing claim_task
    # retry callers first.
    "complete": frozenset({"in_progress"}),
    "failed": frozenset({"in_progress"}),
}


_DDL = """\
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    progress_pct REAL NOT NULL DEFAULT 0.0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    cost_compute_seconds REAL NOT NULL DEFAULT 0.0,
    time_started TEXT,
    time_completed TEXT,
    estimated_completion TEXT,
    scheduled_at TEXT,
    rq_job_id TEXT,
    assigned_agent TEXT,
    component TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'not_required',
    review_comment TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL DEFAULT '',
    requires_review INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    project_id TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_tasks_component ON tasks(component);
CREATE INDEX IF NOT EXISTS idx_tasks_review ON tasks(review_status);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);

CREATE TABLE IF NOT EXISTS task_dependencies (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    depends_on_id TEXT NOT NULL,
    dep_type TEXT NOT NULL DEFAULT 'blocks',
    created_at TEXT NOT NULL DEFAULT '',
    UNIQUE(task_id, depends_on_id)
);
CREATE INDEX IF NOT EXISTS idx_deps_task ON task_dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_deps_depends ON task_dependencies(depends_on_id);

CREATE TABLE IF NOT EXISTS task_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON task_events(event_type);

-- Refused access attempts, keyed by the DENIED PRINCIPAL (2026-08-12 review).
-- These rows used to be written into `task_events` against the ADDRESSED task,
-- which made every refusal an attacker-controlled write into the VICTIM's own
-- data: `count_all` censuses the child tables by `task_id IN (owned tasks)`, so
-- a caller could inflate another tenant's reported row census at will (measured:
-- 120 refused reads grew the victim's `task_events` from 8 to 128) and pollute
-- the trail this design leans on for attribution. Filing the refusal against the
-- caller keeps the attribution, removes the cross-tenant write, and keeps the
-- census honest. UNIQUE(caller, event_type, subject_id) + `hits` collapses a
-- hammering caller to one row per distinct (caller, op, subject) instead of one
-- row per attempt. Deliberately NOT part of `count_all`'s tenant census: it is
-- caller-keyed, not task-keyed, and counting it would restore the channel this
-- table exists to close.
CREATE TABLE IF NOT EXISTS access_refusals (
    id TEXT PRIMARY KEY,
    caller TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    subject_id TEXT NOT NULL DEFAULT '',
    hits INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL DEFAULT '',
    last_seen TEXT NOT NULL DEFAULT '',
    UNIQUE(caller, event_type, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_refusals_caller ON access_refusals(caller);
CREATE INDEX IF NOT EXISTS idx_refusals_subject ON access_refusals(subject_id);
"""


class TaskTrackerStore:
    """SQLite store for a plan's units (the subset of the archive's task tracker the work graph uses)."""

    def __init__(self, db_path: str = ":memory:") -> None:
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._conn = open_db(os.path.abspath(db_path) if db_path != ":memory:" else db_path, check_same_thread=False)
        # V2 (UI.md §12): additive project_id migration for pre-existing DBs.
        # Must run BEFORE the DDL: on an old DB the CREATE TABLE IF NOT EXISTS
        # is a no-op, so idx_tasks_project would otherwise reference a column
        # that does not exist yet. Idempotent — checked via PRAGMA table_info.
        existing = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if existing is not None:
            columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "project_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE tasks ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
                )
            # Ownership scoping (2026-07-19): additive `owner` column carrying the
            # authenticated caller principal that created a task, so delete/list
            # can confine an authenticated caller to their own tasks. Empty on
            # legacy rows and on locally-created tasks -> unrestricted, byte-
            # identical to pre-scoping behaviour. Same idempotent shape as
            # project_id above.
            if "owner" not in columns:
                self._conn.execute(
                    "ALTER TABLE tasks ADD COLUMN owner TEXT NOT NULL DEFAULT ''"
                )
        self._conn.executescript(_DDL)
        self._conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _uid() -> str:
        return "task_" + uuid.uuid4().hex[:12]

    @staticmethod
    def _generic_uid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _j(obj: Any) -> str:
        return json.dumps(obj, default=str)

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        d = dict(row)
        d["requires_review"] = bool(d.get("requires_review", 0))
        return d

    def _log_event(self, task_id: str, event_type: str, detail: dict[str, Any] | None = None) -> None:
        self._conn.execute(
            "INSERT INTO task_events (id, task_id, event_type, detail_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (self._generic_uid(), task_id, event_type, self._j(detail or {}), self._now()),
        )

    def _log_refusal(self, caller: str, event_type: str, subject_id: str) -> None:
        """Record ONE refused access against the DENIED PRINCIPAL.

        Every ownership refusal in this store used to land in ``task_events``
        keyed by the ADDRESSED task id. That made a refusal an attacker-controlled
        write into another tenant's data with three consequences, all measured in
        the 2026-08-12 review: the victim's ``count_all`` census inflated one row
        per refused read (120 refused reads took a victim's ``task_events`` from
        8 to 128), the victim's audit trail filled with events the victim never
        caused, and neither was capped, deduped or rate-limited.

        Filing against the caller keeps full attribution — ``caller`` was already
        the payload of the old event's ``detail_json`` — while removing the
        cross-tenant write entirely. ``subject_id`` records WHAT was addressed so
        an operator can still ask "who probed task X"; it is a plain column, not
        a foreign key, and no census counts this table.

        The upsert collapses repeats: a caller hammering the same (op, subject)
        keeps ONE row and increments ``hits``, so a refusal storm costs bounded
        storage rather than one row per attempt.
        """
        now = self._now()
        self._conn.execute(
            "INSERT INTO access_refusals "
            "(id, caller, event_type, subject_id, hits, first_seen, last_seen) "
            "VALUES (?,?,?,?,1,?,?) "
            "ON CONFLICT(caller, event_type, subject_id) DO UPDATE SET "
            "hits = hits + 1, last_seen = excluded.last_seen",
            (self._generic_uid(), caller, event_type, subject_id, now, now),
        )
        self._conn.commit()

    def _ownership_error(
        self, task: dict[str, Any], owner: str, op: str
    ) -> dict[str, Any] | None:
        """Refuse a mutation when an authenticated caller addresses a task it does
        not own; return None (allowed) otherwise.

        Ownership scoping (#86, extends #80's delete/list scoping to the full
        mutation family). When a caller principal is supplied (the authenticated
        SSE surface), a mutating op may touch only the caller's OWN task. An empty
        ``owner`` (local / internal / system / admin path) is unrestricted, so
        single-operator and system callers are byte-identical to before this
        scoping. Mirrors ``delete_task``'s ownership refusal; unlike delete (which
        returns False) a refused lifecycle/review mutation surfaces an explicit
        authorization error so the caller is not misled into thinking the op
        applied. A missing task is NOT handled here — callers keep their existing
        not-found return (``{}``) so existence is not newly disclosed.
        """
        if owner and task.get("owner", "") != owner:
            # Caller-keyed, never the victim's row -- see `_log_refusal`.
            self._log_refusal(owner, f"{op}_refused_ownership", task.get("id", ""))
            return {"error": "Not authorized: task is owned by another caller"}
        return None

    def _read_denied(
        self, task: dict[str, Any], owner: str, op: str, task_id: str = ""
    ) -> bool:
        """True when *owner* may not READ the addressed task — auditing why.

        The read counterpart of ``_ownership_error`` (#153 follow-up). Three
        deliberate differences, all about not building an existence oracle:

        * it returns a BOOLEAN, never an error payload. Every caller renders a
          refused read with its OWN not-found shape (``{}`` / ``[]`` / zeroed
          counts) — byte-identical to what a genuinely absent ``task_id``
          produces. A read is the op an attacker enumerates with, so answering
          "not authorized" would confirm, for any guessed id, that the row is
          live and belongs to somebody else. The mutation family answers
          "Not authorized" instead because a silently-refused WRITE would
          mislead the caller into believing it applied; a refused read carries
          no such risk, so the two families differ on purpose.

        * an UNKNOWN subject is refused HERE, not by each caller's own
          short-circuit, and it does the SAME WORK. Byte-identical bodies were
          only ever half the claim: the audit write cost ~0.46 ms while an
          unknown id cost ~0.01 ms, so the LATENCY answered the very question the
          body refused to (measured 2026-08-12; removing only the audit write
          collapsed the difference, which is what proved the cause). Auditing the
          unknown-subject branch too equalises the work instead of describing the
          gap: both answers now cost one refusal upsert plus one commit. It is
          also the better telemetry — reads aimed at ids that do not exist are
          exactly what enumeration looks like. The caller's OWN ids stay on the
          cheap path, and a caller learns nothing from timing its own rows.
          Distinguishable to the OPERATOR (two event types), identical to the
          CALLER (same body, same work).

        * the refusal is filed against the CALLER, never the addressed task —
          see :meth:`_log_refusal` for why that matters.

        The event type is still ``{op}_refused_ownership`` for a live foreign row,
        in exactly the style of ``update_refused_ownership`` /
        ``delete_refused_ownership`` / ``approve_refused_ownership``, so it stays
        a sound "the ownership predicate fired" witness; an unknown subject gets
        the distinct ``{op}_refused_unknown_subject`` so the two never blur.

        ``owner=""`` (local / stdio / internal / system) is unrestricted, exactly
        as everywhere else in this store: no refusal, no audit, no extra work.
        """
        if not owner:
            return False
        if not task:
            self._log_refusal(owner, f"{op}_refused_unknown_subject", task_id)
            return True
        if task.get("owner", "") == owner:
            return False
        self._log_refusal(owner, f"{op}_refused_ownership", task.get("id", ""))
        return True

    _REDACTED = "<redacted: owned by another caller>"

    def _incomplete_blockers(self, task_id: str, owner: str = "") -> list[dict[str, Any]]:
        """This task's not-yet-complete upstream dependencies.

        Shared by ``claim_task`` and ``unblock_task``. A blocker owned by a
        DIFFERENT principal still blocks — dropping it would silently weaken the
        dependency gate — but EVERY disclosing field is redacted, because the JOIN
        would otherwise hand this caller another tenant's data. Such an edge can
        no longer be created (``add_dependency`` scopes ``depends_on_id``); this
        covers legacy rows and any ownerless/system-made edge. ``owner=""``
        redacts nothing.

        All three fields, not just the title (2026-08-12 review). The first pass
        redacted ``title`` alone while the dict still carried ``status`` and —
        worse — ``depends_on_id``, the foreign task's REAL id, which is precisely
        what :meth:`get_dependencies` and :meth:`dependency_graph` are scoped to
        withhold. Not reachable through any surface today (both blocks collapse
        the error dict via ``Result.fail(raw["error"])`` and drop ``blocked_by``
        entirely), so this is defence in depth rather than a live leak — but the
        store API is the boundary being defended, and half a redaction at that
        boundary is a leak waiting for the next caller that forwards the dict.
        The COUNT of blockers is intentionally NOT hidden: it is the honest reason
        the claim was refused, and the caller created the edge or inherited it.
        """
        rows = self._conn.execute(
            "SELECT d.depends_on_id, t.title, t.status, t.owner AS blocker_owner "
            "FROM task_dependencies d "
            "JOIN tasks t ON t.id = d.depends_on_id "
            "WHERE d.task_id = ? AND t.status != 'complete'",
            (task_id,),
        ).fetchall()
        blockers: list[dict[str, Any]] = []
        for row in rows:
            blocker = dict(row)
            blocker_owner = blocker.pop("blocker_owner", "")
            if owner and blocker_owner != owner:
                for field in ("title", "status", "depends_on_id"):
                    blocker[field] = self._REDACTED
            blockers.append(blocker)
        return blockers

    def create_task(
        self,
        title: str,
        description: str = "",
        priority: int = 0,
        component: str = "",
        tags: str = "",
        requires_review: bool = False,
        parent_id: str | None = None,
        metadata_json: str = "{}",
        assigned_agent: str = "",
        project_id: str = "",
        owner: str = "",
    ) -> dict[str, Any]:
        uid = self._uid()
        now = self._now()
        self._conn.execute(
            "INSERT INTO tasks "
            "(id, title, description, status, priority, progress_pct, cost_usd, "
            "cost_compute_seconds, assigned_agent, component, tags, review_status, "
            "requires_review, parent_id, created_at, updated_at, metadata_json, "
            "project_id, owner) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uid, title, description, "pending", priority, 0.0, 0.0,
                0.0, assigned_agent, component, tags, "not_required",
                int(requires_review), parent_id, now, now, metadata_json,
                project_id, owner,
            ),
        )
        self._log_event(uid, "created", {"title": title})
        self._conn.commit()
        # UNSCOPED: read-back of the row this call just inserted with this exact
        # owner, so the predicate could only ever be vacuous here.
        return self.get_task_unscoped(uid)

    def get_task_unscoped(self, task_id: str) -> dict[str, Any]:
        """Read a task row with NO ownership filter. **INTERNAL USE ONLY.**

        This is the raw SELECT. It is deliberately spelled ``_unscoped`` so that
        every call site announces, in its own text, that it is reading a row the
        caller may not own — the caller-facing read is :meth:`get_task`.

        Legitimate uses are exactly three shapes, and nothing else:

        1. the pre-read that FEEDS ``_ownership_error`` — it must see a row it
           does not own in order to produce a correct ownership refusal at all;
        2. a post-write read-back, where ownership was already established
           earlier in the same method;
        3. a system/worker context with no caller principal (the RQ scheduled-task
           callback), which is unrestricted by construction.

        Anything caller-facing must use :meth:`get_task`. Routing a caller-facing
        op here is a cross-tenant read; routing an internal caller through
        :meth:`get_task` breaks ownership refusal (the predicate would see
        nothing to refuse). Both directions are covered by mutation tests.
        """
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_dict(row)

    def get_task(self, task_id: str, owner: str = "") -> dict[str, Any]:
        """Caller-facing task read, confined to *owner*'s own rows.

        Returns ``{}`` when the task does not exist **or** belongs to another
        principal — the two are deliberately indistinguishable, see
        :meth:`_read_denied`. ``owner=""`` (local / stdio / internal / system) is
        unrestricted, byte-identical to the pre-scoping behaviour.
        """
        task = self.get_task_unscoped(task_id)
        if self._read_denied(task, owner, "get", task_id):
            return {}
        return task

    def list_tasks(
        self,
        status: str = "",
        component: str = "",
        tags: str = "",
        review_status: str = "",
        parent_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        project_id: str = "",
        owner: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        # V2 (UI.md §12): applied ONLY when set — an empty filter returns
        # everything, byte-identical to the pre-project-scoping behavior.
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        # Ownership scoping (2026-07-19): when a caller principal is supplied
        # (authenticated surface), confine the listing to that caller's own
        # tasks so one customer cannot enumerate another's. An empty owner
        # (local / internal / admin path) returns everything, unchanged.
        if owner:
            clauses.append("owner = ?")
            params.append(owner)
        if component:
            clauses.append("component = ?")
            params.append(component)
        if tags:
            clauses.append("tags LIKE ?")
            params.append(f"%{tags}%")
        if review_status:
            clauses.append("review_status = ?")
            params.append(review_status)
        if parent_id is not None:
            clauses.append("parent_id = ?")
            params.append(parent_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        cur = self._conn.execute(
            f"SELECT * FROM tasks WHERE {where} "
            "ORDER BY priority DESC, created_at ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._row_to_dict(row) for row in cur.fetchall()]

    _VALID_EXTRA_COLS = frozenset({
        "assigned_agent", "time_started", "time_completed", "progress_pct",
        "review_status", "scheduled_at", "rq_job_id", "metadata_json",
    })

    def _transition(self, task_id: str, new_status: str, extra_fields: dict[str, Any] | None = None, owner: str = "") -> dict[str, Any]:
        # UNSCOPED: feeds `_ownership_error` below — the single chokepoint that
        # owns the refusal for the whole transition family.
        task = self.get_task_unscoped(task_id)
        if not task:
            return {}
        # Ownership scoping (#86): the single chokepoint for every status
        # transition (claim/complete/fail/block/unblock/schedule/cancel_scheduled),
        # so an authenticated caller cannot drive another principal's task through
        # its lifecycle. owner="" (local/internal/system) is unrestricted.
        err = self._ownership_error(task, owner, f"transition_{new_status}")
        if err:
            return err
        current = task["status"]
        allowed = VALID_TRANSITIONS.get(current, frozenset())
        if new_status not in allowed:
            return {"error": f"Cannot transition from '{current}' to '{new_status}'"}

        sets = ["status = ?", "updated_at = ?"]
        vals: list[Any] = [new_status, self._now()]
        if extra_fields:
            for k, v in extra_fields.items():
                if k not in self._VALID_EXTRA_COLS:
                    return {"error": f"Disallowed transition field: {k}"}
                sets.append(f"{k} = ?")
                vals.append(v)
        vals.extend([task_id, current])
        cur = self._conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ? AND status = ?", vals,
        )
        if cur.rowcount == 0:
            return {"error": f"Concurrent modification: task {task_id} status changed"}
        self._log_event(task_id, f"status_{new_status}", extra_fields)
        self._conn.commit()
        return self.get_task_unscoped(task_id)  # read-back; ownership settled above

    def claim_task(self, task_id: str, assigned_agent: str = "", owner: str = "") -> dict[str, Any]:
        # UNSCOPED: feeds `_ownership_error` below.
        task = self.get_task_unscoped(task_id)
        if not task:
            return {}
        # Ownership scoping (#86): refuse before the blocker query so a non-owner
        # cannot even enumerate another principal's dependency structure.
        err = self._ownership_error(task, owner, "claim")
        if err:
            return err
        blockers = self._incomplete_blockers(task_id, owner)
        if blockers:
            return {
                "error": "Task has incomplete dependencies",
                "blocked_by": blockers,
            }
        return self._transition(task_id, "in_progress", {
            "assigned_agent": assigned_agent,
            "time_started": self._now(),
        })

    def complete_task(self, task_id: str, owner: str = "") -> dict[str, Any]:
        # UNSCOPED: only reads `requires_review` to shape the extra fields; the
        # ownership refusal is owned by `_transition` below, and a scoped read
        # here would turn that refusal into a not-found.
        task = self.get_task_unscoped(task_id)
        if not task:
            return {}
        extra: dict[str, Any] = {"time_completed": self._now(), "progress_pct": 100.0}
        if task.get("requires_review"):
            extra["review_status"] = "pending_review"
        result = self._transition(task_id, "complete", extra, owner=owner)
        return result

    def fail_task(self, task_id: str, error_message: str = "", owner: str = "") -> dict[str, Any]:
        return self._transition(task_id, "failed", {"metadata_json": self._j({"error": error_message})}, owner=owner)

    def block_task(self, task_id: str, blocker_reason: str = "", owner: str = "") -> dict[str, Any]:
        return self._transition(task_id, "blocked", {"metadata_json": self._j({"blocker": blocker_reason})}, owner=owner)

    def add_dependency(self, task_id: str, depends_on_id: str, dep_type: str = "blocks", owner: str = "") -> dict[str, Any]:
        if task_id == depends_on_id:
            return {"error": "A task cannot depend on itself"}
        # UNSCOPED on the MUTATED task: `_ownership_error` must judge a row the
        # caller may not own so the refusal stays the #86 "Not authorized".
        subject = self.get_task_unscoped(task_id)
        if not subject:
            return {"error": f"Task not found: {task_id}"}
        # Ownership scoping (#86): the caller must own the task being MODIFIED
        # (task_id gains the dependency edge).
        err = self._ownership_error(subject, owner, "add_dependency")
        if err:
            return err
        # SCOPED on the REFERENCED task. Two leaks close here. The small one is
        # the found/not-found distinction over another tenant's id space. The
        # large one is the edge itself: an accepted cross-tenant edge makes the
        # blocker JOIN in claim_task/unblock_task hand this caller the other
        # tenant's task `title` and `status` — an unscoped READ manufactured out
        # of a write. A foreign id is refused as NOT FOUND, the same answer a
        # bogus id gets, so this does not become an existence oracle either.
        referenced = self.get_task_unscoped(depends_on_id)
        # `_read_denied` first: it is what audits BOTH the foreign and the unknown
        # reference, and it must not be short-circuited away by `not referenced`.
        if self._read_denied(referenced, owner, "add_dependency_ref", depends_on_id) or not referenced:
            return {"error": f"Task not found: {depends_on_id}"}
        if self._would_create_cycle(task_id, depends_on_id):
            return {"error": f"Adding dependency {task_id} -> {depends_on_id} would create a cycle"}
        try:
            self._conn.execute(
                "INSERT INTO task_dependencies (id, task_id, depends_on_id, dep_type, created_at) VALUES (?,?,?,?,?)",
                (self._generic_uid(), task_id, depends_on_id, dep_type, self._now()),
            )
            self._log_event(task_id, "dependency_added", {"depends_on": depends_on_id, "type": dep_type})
            self._conn.commit()
        except sqlite3.IntegrityError:
            return {"error": "Dependency already exists"}
        return {"ok": True, "task_id": task_id, "depends_on_id": depends_on_id}

    def _would_create_cycle(self, task_id: str, depends_on_id: str) -> bool:
        """DFS check: would adding task_id -> depends_on_id create a cycle?"""
        visited: set[str] = set()
        stack = [task_id]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            cur = self._conn.execute(
                "SELECT task_id FROM task_dependencies WHERE depends_on_id = ?", (node,),
            )
            for row in cur.fetchall():
                upstream = row[0]
                if upstream == depends_on_id:
                    return True
                stack.append(upstream)
        return False

    def get_dependencies(self, task_id: str, owner: str = "") -> dict[str, Any]:
        """This task's dependency edges, confined to *owner*'s own tasks.

        A foreign ``task_id`` renders as the empty envelope an unknown id already
        produced, so this is not an existence oracle. When the task IS owned, the
        edge lists are still filtered to owned counterparts: an edge names another
        tenant's ``task_id``, which is precisely the identifier every other op
        here is now refusing to disclose.
        """
        task = self.get_task_unscoped(task_id)
        empty = {"task_id": task_id, "depends_on": [], "depended_by": []}
        if self._read_denied(task, owner, "get_dependencies", task_id):
            return empty
        if owner:
            upstream = self._conn.execute(
                "SELECT d.* FROM task_dependencies d JOIN tasks t ON t.id = d.depends_on_id "
                "WHERE d.task_id = ? AND t.owner = ?", (task_id, owner),
            ).fetchall()
            downstream = self._conn.execute(
                "SELECT d.* FROM task_dependencies d JOIN tasks t ON t.id = d.task_id "
                "WHERE d.depends_on_id = ? AND t.owner = ?", (task_id, owner),
            ).fetchall()
        else:
            upstream = self._conn.execute(
                "SELECT * FROM task_dependencies WHERE task_id = ?", (task_id,),
            ).fetchall()
            downstream = self._conn.execute(
                "SELECT * FROM task_dependencies WHERE depends_on_id = ?", (task_id,),
            ).fetchall()
        return {
            "task_id": task_id,
            "depends_on": [dict(r) for r in upstream],
            "depended_by": [dict(r) for r in downstream],
        }
