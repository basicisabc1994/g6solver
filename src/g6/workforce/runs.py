# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The attempt ledger: worker instances, runs, artifacts and verdicts (workforce OS plan, P0 T6).

Every attempt at a unit is one ``task_runs`` row and one ``worker_instances`` row (one instance per
attempt, never reused), in the venture db beside the mission, owner-scoped through ``missions``. A
run opens, then closes (or is abandoned) with its event in one transaction; a closed run, a finished
instance and a verdict never change (triggers). An artifact is an ``evidence`` row of kind
``artifact`` written in the same transaction as the run that produced it, bound to its run, producer
instance and digest. A ``verified`` verdict is refused by the database unless an executed,
independent, read-only, model-free oracle passed with no model calls, in a run that closed verified
with an answered oracle call, on the latest artifact of the unit it verifies (by that artifact's
producer, with its digest): no code path can write one otherwise. A verdict row stores its run's
effective outcome, so it never outranks the run. A unit has at most one open run, runs open only
while their mission is active and close only while it is still running (Codex review of T4-T6).

A runner's run records the lease it was opened under (``holder``, ``fence``; never changed), and a
runner opens, closes and abandons only under a lease the mission still holds (V2a), so an attempt left
open under an older lease is known to be orphaned. The owner's chat's own submissions carry no lease.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from g6.workforce.store import NOT_FOUND
from g6.workforce import contracts, economics
from g6.workforce.capabilities import ParamContract
from g6.workforce.contracts import (
    AUTHORITY_COPY,
    BLOCKED,
    FORMAL_RULES,
    NOT_INDEPENDENT,
    QUALIFIED,
    VERIFIED,
    VERIFIER_GRADE,
    Outcome,
    Verdict,
    combine,
)
from g6.workforce.economics import CostEntry, CostTotal, total
from g6.workforce.missions import Lease, LeaseLost, MissionStore, check_actor
from g6.workforce.workers import WorkerSpec

#: An artifact larger than this is not stored inline (a path under the mission directory is V1).
MAX_ARTIFACT_BYTES = 64 * 1024
INSTANCE_ENDS = ("finished", "terminated", "abandoned")

_DDL = """
CREATE TABLE IF NOT EXISTS worker_instances (
    instance_id   TEXT PRIMARY KEY,
    mission_id    TEXT NOT NULL,
    unit_key      TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    template      TEXT NOT NULL,
    rung          TEXT NOT NULL,
    read_only     INTEGER NOT NULL,
    spec_json     TEXT NOT NULL,
    spec_sha      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'running',
    reason        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS worker_instances_end_once BEFORE UPDATE ON worker_instances
WHEN OLD.status <> 'running' OR NEW.status NOT IN ('finished', 'terminated', 'abandoned')
  OR NEW.instance_id <> OLD.instance_id OR NEW.mission_id <> OLD.mission_id OR NEW.read_only <> OLD.read_only
  OR NEW.spec_sha <> OLD.spec_sha OR NEW.attempt <> OLD.attempt
BEGIN SELECT RAISE(ABORT, 'a worker instance runs once and ends once'); END;
CREATE TRIGGER IF NOT EXISTS worker_instances_kept BEFORE DELETE ON worker_instances
BEGIN SELECT RAISE(ABORT, 'worker instances are kept'); END;
CREATE TRIGGER IF NOT EXISTS worker_instances_no_replace BEFORE INSERT ON worker_instances
WHEN EXISTS (SELECT 1 FROM worker_instances WHERE instance_id = NEW.instance_id)
BEGIN SELECT RAISE(ABORT, 'worker instances are kept'); END;

CREATE TABLE IF NOT EXISTS task_runs (
    run_id            TEXT PRIMARY KEY,
    mission_id        TEXT NOT NULL,
    unit_key          TEXT NOT NULL,
    attempt           INTEGER NOT NULL,
    instance_id       TEXT NOT NULL,
    rung              TEXT NOT NULL,
    state             TEXT NOT NULL DEFAULT 'open',
    completion_state  TEXT NOT NULL DEFAULT '',
    outcome_json      TEXT NOT NULL DEFAULT '{}',
    artifact_id       TEXT NOT NULL DEFAULT '',
    calls_json        TEXT NOT NULL DEFAULT '[]',
    model_calls       INTEGER NOT NULL DEFAULT 0,
    cost_cents        INTEGER NOT NULL DEFAULT 0,
    cost_source       TEXT NOT NULL DEFAULT 'none',
    failure_code      TEXT NOT NULL DEFAULT '',
    started_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL DEFAULT '',
    holder            TEXT NOT NULL DEFAULT '',
    fence             INTEGER NOT NULL DEFAULT 0,
    UNIQUE (mission_id, unit_key, attempt)
);
CREATE TRIGGER IF NOT EXISTS task_runs_close_once BEFORE UPDATE ON task_runs
WHEN OLD.state <> 'open' OR NEW.state NOT IN ('closed', 'abandoned') OR NEW.run_id <> OLD.run_id
  OR NEW.mission_id <> OLD.mission_id OR NEW.unit_key <> OLD.unit_key OR NEW.attempt <> OLD.attempt
  OR NEW.instance_id <> OLD.instance_id
BEGIN SELECT RAISE(ABORT, 'a run closes once and never changes after'); END;
CREATE TRIGGER IF NOT EXISTS task_runs_kept BEFORE DELETE ON task_runs
BEGIN SELECT RAISE(ABORT, 'runs are kept'); END;
CREATE TRIGGER IF NOT EXISTS task_runs_no_replace BEFORE INSERT ON task_runs
WHEN EXISTS (SELECT 1 FROM task_runs WHERE run_id = NEW.run_id)
BEGIN SELECT RAISE(ABORT, 'runs are kept'); END;

CREATE TABLE IF NOT EXISTS verdicts (
    verdict_id         TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL DEFAULT '',
    mission_id         TEXT NOT NULL,
    unit_key           TEXT NOT NULL,
    verifies_key       TEXT NOT NULL,
    artifact_id        TEXT NOT NULL,
    artifact_sha       TEXT NOT NULL DEFAULT '',
    producer_instance  TEXT NOT NULL,
    verifier_instance  TEXT NOT NULL,
    verifier_kind      TEXT NOT NULL,
    executed           INTEGER NOT NULL,
    independent        INTEGER NOT NULL,
    model_calls        INTEGER NOT NULL,
    model_free         INTEGER NOT NULL,
    result             TEXT NOT NULL,
    state              TEXT NOT NULL,
    reasons_json       TEXT NOT NULL DEFAULT '[]',
    created_at         TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS verdicts_verified_needs_its_proof BEFORE INSERT ON verdicts
WHEN NEW.state = 'verified' AND NOT (
    NEW.executed = 1 AND NEW.independent = 1 AND NEW.model_calls = 0 AND NEW.model_free = 1
    AND NEW.result = 'pass' AND NEW.verifier_kind <> 'model_critic'
    AND NEW.verifier_instance <> NEW.producer_instance
    AND EXISTS (SELECT 1 FROM worker_instances w WHERE w.instance_id = NEW.verifier_instance
                AND w.mission_id = NEW.mission_id AND w.read_only = 1)
    AND EXISTS (SELECT 1 FROM worker_instances p WHERE p.instance_id = NEW.producer_instance
                AND p.mission_id = NEW.mission_id))
BEGIN SELECT RAISE(ABORT, 'a verified verdict needs an executed, independent, read-only, model-free oracle that passed'); END;
CREATE TRIGGER IF NOT EXISTS verdicts_never_change BEFORE UPDATE ON verdicts
BEGIN SELECT RAISE(ABORT, 'verdicts are kept as given'); END;
CREATE TRIGGER IF NOT EXISTS verdicts_kept BEFORE DELETE ON verdicts
BEGIN SELECT RAISE(ABORT, 'verdicts are kept as given'); END;
CREATE TRIGGER IF NOT EXISTS verdicts_no_replace BEFORE INSERT ON verdicts
WHEN EXISTS (SELECT 1 FROM verdicts WHERE verdict_id = NEW.verdict_id)
BEGIN SELECT RAISE(ABORT, 'verdicts are kept as given'); END;
"""

#: Added after the first release of these tables (Codex review of T4-T6), so they run after the column migration.
_DDL_BOUND = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runs_one_open ON task_runs(mission_id, unit_key) WHERE state = 'open';
CREATE TRIGGER IF NOT EXISTS task_runs_open_while_active BEFORE INSERT ON task_runs
WHEN NOT EXISTS (SELECT 1 FROM missions m WHERE m.mission_id = NEW.mission_id AND m.status = 'active')
BEGIN SELECT RAISE(ABORT, 'a run opens only while its mission is active'); END;
CREATE TRIGGER IF NOT EXISTS task_runs_close_while_active BEFORE UPDATE ON task_runs
WHEN NEW.state = 'closed'
  AND NOT EXISTS (SELECT 1 FROM missions m WHERE m.mission_id = NEW.mission_id AND m.status IN ('active', 'waiting'))
BEGIN SELECT RAISE(ABORT, 'a run closes only while its mission is active'); END;
CREATE TRIGGER IF NOT EXISTS verdicts_verified_is_bound BEFORE INSERT ON verdicts
WHEN NEW.state = 'verified' AND NOT (
    -- what it judged: the latest artifact of the unit it verifies, by that artifact's producer, with its digest
    EXISTS (SELECT 1 FROM task_runs p WHERE p.mission_id = NEW.mission_id AND p.unit_key = NEW.verifies_key
            AND p.state = 'closed' AND p.artifact_id = NEW.artifact_id AND p.instance_id = NEW.producer_instance
            AND NOT EXISTS (SELECT 1 FROM task_runs q WHERE q.mission_id = p.mission_id AND q.unit_key = p.unit_key
                            AND q.state = 'closed' AND q.artifact_id <> '' AND q.rowid > p.rowid))
    AND EXISTS (SELECT 1 FROM evidence e WHERE e.evidence_id = NEW.artifact_id AND e.kind = 'artifact'
                AND e.ref = NEW.mission_id AND json_extract(e.detail_json, '$.sha256') = NEW.artifact_sha
                AND json_extract(e.detail_json, '$.producer') = NEW.producer_instance)
    -- how: its own run closed verified, by this verifier, with no model call and an oracle call that answered
    AND EXISTS (SELECT 1 FROM task_runs r WHERE r.run_id = NEW.run_id AND r.mission_id = NEW.mission_id
                AND r.unit_key = NEW.unit_key AND r.instance_id = NEW.verifier_instance AND r.state = 'closed'
                AND r.completion_state = 'verified' AND r.model_calls = 0
                AND EXISTS (SELECT 1 FROM json_each(r.calls_json) c
                            WHERE json_extract(c.value, '$.refused') = 0
                            AND json_extract(c.value, '$.state') IN ('verified', 'qualified-draft'))))
BEGIN SELECT RAISE(ABORT, 'a verified verdict is bound to its own executed oracle run and to the latest artifact of the unit it verifies'); END;
CREATE TRIGGER IF NOT EXISTS task_runs_holder_is_fixed BEFORE UPDATE ON task_runs
WHEN NEW.holder <> OLD.holder OR NEW.fence <> OLD.fence
BEGIN SELECT RAISE(ABORT, 'a run keeps the holder and fence it was opened under'); END;
"""
#: Gate W (spec amendment 2026-10-05): a verified verdict needs a verifier-grade (A4) oracle, whatever path wrote it.
_DDL_AUTHORITY = """
CREATE TRIGGER IF NOT EXISTS verdicts_verified_needs_an_a4_oracle BEFORE INSERT ON verdicts
WHEN NEW.state = 'verified' AND NEW.authority <> 'A4'
BEGIN SELECT RAISE(ABORT, 'a verified verdict needs a verifier-grade (A4) oracle'); END;
"""
#: Columns added after these tables first shipped (additive, idempotent), as (table, column, kind).
_LATER = (("verdicts", "artifact_sha", "TEXT NOT NULL DEFAULT ''"), ("task_runs", "holder", "TEXT NOT NULL DEFAULT ''"),
          ("task_runs", "fence", "INTEGER NOT NULL DEFAULT 0"), ("verdicts", "authority", "TEXT NOT NULL DEFAULT ''"),
          ("verdicts", "formal_rules", "TEXT NOT NULL DEFAULT ''"))

#: Missions a run may close in: an attempt never lands after its mission closed or was cancelled.
RUNNING = ("active", "waiting")

#: Why an attempt was abandoned (E2a, plan ruling R18): its claim was refused (nothing was sent), its runner lost the
#: mission's lease, or its wall clock ran out; ``abandoned`` when the caller does not say.
ABANDON_CODES = ("abandoned", "abandoned:claim", "abandoned:lease", "abandoned:clock")
#: Every failure code this module records itself (``classes.WORKFORCE_CODES`` maps each).
FAILURE_CODES = (*ABANDON_CODES, "verdict_stale", "verdict_unbound", "unclassified")

#: E1a (Ledger §5, §21, §33): an attempt's cost rows are evidence rows of kind ``cost``, written while it is open (as
#: each call returns, and in the transaction that closes it); its ``cost_cents``/``cost_source`` are exactly their
#: sum, or the database refuses the close. Added after these tables first shipped: run on every open, so an older
#: store migrates. SQLite fires the newest trigger first, so the sum is checked on the close itself (an open run
#: finishing under the holder and fence it was opened under); any other update is refused by the older trigger that
#: says why.
_DDL_COST = f"""
CREATE INDEX IF NOT EXISTS idx_ev_kind_ref ON evidence(kind, ref);
CREATE TRIGGER IF NOT EXISTS cost_rows_while_open BEFORE INSERT ON evidence
WHEN NEW.kind = 'cost' AND NOT EXISTS (SELECT 1 FROM task_runs r
    WHERE r.run_id = json_extract(NEW.detail_json, '$.attempt_id') AND r.mission_id = NEW.ref AND r.state = 'open')
BEGIN SELECT RAISE(ABORT, 'a cost is recorded with its attempt, while the attempt is open'); END;
CREATE TRIGGER IF NOT EXISTS task_runs_cost_is_its_rows BEFORE UPDATE ON task_runs
WHEN OLD.state = 'open' AND NEW.state <> 'open' AND NEW.holder = OLD.holder AND NEW.fence = OLD.fence AND (
    NEW.cost_cents <> (SELECT COALESCE(SUM(json_extract(e.detail_json, '$.amount_minor_units')), 0) FROM evidence e
        WHERE e.kind = 'cost' AND e.ref = NEW.mission_id AND json_extract(e.detail_json, '$.attempt_id') = NEW.run_id
        AND json_extract(e.detail_json, '$.currency') = '{economics.BUDGET_CURRENCY}')
    OR NEW.cost_source <> (CASE
        WHEN EXISTS (SELECT 1 FROM evidence e WHERE e.kind = 'cost' AND e.ref = NEW.mission_id
                     AND json_extract(e.detail_json, '$.attempt_id') = NEW.run_id
                     AND (json_extract(e.detail_json, '$.amount_minor_units') IS NULL
                          OR json_extract(e.detail_json, '$.currency') IS NOT '{economics.BUDGET_CURRENCY}'))
            THEN 'unknown'
        WHEN EXISTS (SELECT 1 FROM evidence e WHERE e.kind = 'cost' AND e.ref = NEW.mission_id
                     AND json_extract(e.detail_json, '$.attempt_id') = NEW.run_id
                     AND json_extract(e.detail_json, '$.estimated') = 1) THEN 'estimated'
        ELSE 'exact' END))
BEGIN SELECT RAISE(ABORT, 'an attempt''s cost is exactly the sum of its cost rows'); END;
"""
#: Results by their content digest (Security Brief 3, review I5: ``makers`` finds who made the same bytes, linked or not).
_DDL_DIGEST = """
CREATE INDEX IF NOT EXISTS idx_ev_artifact_sha ON evidence(json_extract(detail_json, '$.sha256'))
WHERE kind = 'artifact';
"""

#: Every statement on these tables carries the owner predicate through missions -> ventures.
_OWNED = (" AND EXISTS (SELECT 1 FROM missions m JOIN ventures v ON v.venture_id = m.venture_id"
          " WHERE m.mission_id = {table}.mission_id AND v.owner = ?)")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _outcome_json(outcome: Outcome) -> str:
    return json.dumps(outcome.as_record())


def _seconds(start: str, end: str) -> int | None:
    """Whole seconds from ``start`` to ``end`` (ISO-8601), or None when either cannot be read (unknown, never 0)."""
    try:
        return max(0, int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()))
    except (TypeError, ValueError):
        return None


def artifact_sha(content: object) -> str:
    """The digest an artifact's ``content`` is kept under."""
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def outcome_of(row: dict) -> Outcome:
    """A stored run's outcome (an open or empty one is blocked: nothing finished)."""
    return Outcome.from_record(json.loads(row.get("outcome_json") or "{}"),
                               missing=f"run {row.get('run_id')} has no outcome ({row.get('state')})")


@dataclass(frozen=True)
class Run:
    run_id: str
    instance_id: str
    attempt: int


@dataclass(frozen=True)
class VerdictRecord:
    """What a verify run found about one artifact: stored with the run that found it."""

    verifies_key: str
    artifact_id: str
    producer_instance: str
    verdict: Verdict
    model_free: bool
    #: The digest of the artifact the oracle read (what it certifies is exactly that content).
    artifact_sha: str = ""
    #: The class its oracle earned at this use (Gate W): the op's own, A3 at most when the check is not independent of
    #: the output (rungs.oracle_authority: its op made it or anything it was built from, or the receipts rule).
    authority: str = ""
    #: The formal rules a ``formal_methods.solve`` check was made under (``contracts.FORMAL_RULES``); "" for any other.
    formal_rules: str = ""

    def outcome(self) -> Outcome:
        found = self.verdict.outcome()
        if found.state == VERIFIED and not self.model_free:
            found = found.demote(QUALIFIED, "the oracle's op is not declared model-free")
        if found.state == VERIFIED and self.authority != VERIFIER_GRADE:
            reason = (self.verdict.reason if self.verdict.reason in NOT_INDEPENDENT
                      else AUTHORITY_COPY["verdict_capped"])
            found = found.demote(QUALIFIED, reason)
        if found.state == QUALIFIED and self.verdict.reason in NOT_INDEPENDENT:
            found = found.demote(QUALIFIED, self.verdict.reason)
        return found


class RunLedger:
    """One owner's attempts, for the missions their ``MissionStore`` can see."""

    def __init__(self, missions: MissionStore) -> None:
        self._missions = missions
        self.owner = missions.owner
        self._conn = missions.conn
        self._conn.executescript(_DDL)
        for table, column, kind in _LATER:  # a ledger made before the column was recorded
            if column not in {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")  # literal names above
        self._conn.executescript(_DDL_BOUND)
        self._conn.executescript(_DDL_AUTHORITY)
        self._conn.executescript(_DDL_COST)
        self._conn.executescript(_DDL_DIGEST)

    @classmethod
    def reader(cls, missions: MissionStore) -> RunLedger:
        """The ledger for a reader that must not write (E2E Build 7 review I1): no schema statement runs, so a store
        is read as it is (a trigger it lacks is not created)."""
        found = cls.__new__(cls)
        found._missions, found.owner, found._conn = missions, missions.owner, missions.conn
        return found

    # -- writes -----------------------------------------------------------------------------------
    def open(self, mission_id: str, unit_key: str, worker: WorkerSpec, *, actor: str = "code",
             lease: Lease | None = None, retry: dict | None = None) -> Run:
        """A new attempt at the unit: refused unless the mission is active, the unit has no open one and
        (for a runner) ``lease`` is still the mission's. The run records the lease it was opened under.
        With ``retry`` (Ledger §15, E2a) a second or later attempt records why it is tried again, in the same
        transaction."""
        actor = check_actor(actor)
        mission = self._missions.get(mission_id)
        if mission["status"] != "active":
            raise ValueError(f"mission {mission_id} is {mission['status']}, not active: no attempt opens")
        run_id, instance_id, now = f"run_{_uid()}", f"wi_{_uid()}", _now()
        recorded = {"template": worker.template, "allowed": [list(p) for p in worker.allowed],
                    "policy": {k: sorted(v) if isinstance(v, frozenset) else v
                               for k, v in dataclasses.asdict(worker.policy).items()},
                    "read_only": worker.read_only, "rung": worker.rung}
        if worker.may_delegate:
            recorded["may_delegate"] = True
        spec = json.dumps(recorded, sort_keys=True)
        with self._conn:
            self._missions.hold(mission_id, lease)
            if any(r["unit_key"] == unit_key for r in self.open_runs(mission_id)):
                raise ValueError(f"unit {unit_key!r} already has an open attempt; it is waited for or abandoned first")
            attempt = 1 + self._conn.execute(
                "SELECT COUNT(*) FROM task_runs WHERE mission_id=? AND unit_key=?" + _OWNED.format(table="task_runs"),
                (str(mission_id), unit_key, self.owner)).fetchone()[0]
            self._conn.execute(
                "INSERT INTO worker_instances (instance_id, mission_id, unit_key, attempt, template, rung, read_only,"
                " spec_json, spec_sha, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (instance_id, str(mission_id), unit_key, attempt, worker.template, worker.rung,
                 int(worker.read_only), spec, worker.sha256, now))
            self._conn.execute(
                "INSERT INTO task_runs (run_id, mission_id, unit_key, attempt, instance_id, rung, started_at, holder,"
                " fence) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, str(mission_id), unit_key, attempt, instance_id, worker.rung, now,
                 lease.holder if lease else "", lease.fence if lease else 0))
            self._missions.append_event(mission_id, "run.opened", f"worker:{instance_id}",
                                        {"run_id": run_id, "unit": unit_key, "attempt": attempt,
                                         "template": worker.template, "spec_sha": worker.sha256},
                                        f"run.opened:{run_id}")
            if retry is not None and attempt > 1:
                self._missions.append_event(mission_id, "retry", f"worker:{instance_id}",
                                            {"run_id": run_id, "unit": unit_key, "attempt": attempt,
                                             "retry_count": attempt - 1, **retry}, f"retry:{run_id}")
        return Run(run_id, instance_id, attempt)

    def close(self, run_id: str, outcome: Outcome, *, artifact: dict | None = None, calls: tuple = (),
              model_calls: int = 0, failure_code: str = "",
              verdict: VerdictRecord | None = None, lease: Lease | None = None,
              costs: tuple[CostEntry | None, ...] = (), context: dict | None = None,
              within: Callable[[], None] | None = None, mechanisms: tuple = (),
              elicitation: dict | None = None) -> str:
        """Close an open run with its outcome, its artifact (unless the attempt is blocked), the verdict it found and
        its cost rows, all with the run's event in one transaction. Returns the artifact id or "".
        ``within()`` runs inside the same transaction, after the run, its artifact and its verdict are written (a
        person's correction writes its intervention there, E1b).

        Refused once the mission is no longer active. A verdict counts only on what it is bound to: the
        latest artifact of the unit it verifies, by that artifact's producer, with its digest, judged by an
        oracle call this run recorded as answered; otherwise the run is blocked. The verdict row stores the
        run's effective outcome, so it never outranks its run.

        Phase E Part 1: ``costs[i]`` is what call ``i`` cost (None: refused before it was sent). The rows are written
        before the run closes (a row ``record_cost`` already wrote is not written twice), and the run's
        ``cost_cents``/``cost_source`` are the sum of all its rows (the database refuses any other, E1a). ``context``
        is the attempt's Ledger §18 telemetry, kept on its ``run.closed`` event. A blocked attempt always carries a
        failure code (``unclassified`` when none was given, E2a). ``elicitation`` is a check's
        FailureElicitationRecord (FE §28, E10c), kept on its ``run.closed`` event with the attempt's cost and time; a
        finding the ledger refused keeps none."""
        row = self._open_row(run_id)
        mission = self._missions.get(row["mission_id"])
        if mission["status"] not in RUNNING:
            raise ValueError(f"mission {row['mission_id']} is {mission['status']}, not active: its runs no longer close")
        if verdict is not None:
            outcome = combine(outcome, verdict.outcome())
            code, unbound = self._unbound(row["mission_id"], verdict, calls)
            if unbound:
                outcome = combine(outcome, Outcome(BLOCKED, reasons=(unbound,), source="ledger"))
                failure_code = failure_code or code
                mechanisms = ()  # a finding the ledger refused is no check performed on the output (fix pass M4)
                elicitation = None
        if outcome.state == BLOCKED and not failure_code:
            failure_code = "unclassified"
        body = None
        if artifact is not None and outcome.state != BLOCKED:
            content = json.dumps(artifact.get("content"), sort_keys=True, default=str)
            if len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
                raise ValueError(f"the artifact is too large to store inline ({len(content)} chars)")
            body = {"run_id": run_id, "unit": row["unit_key"], "schema": str(artifact.get("schema", "")),
                    "sha256": artifact_sha(artifact.get("content")), "content": artifact.get("content"),
                    "producer": row["instance_id"], "rung": row["rung"], "model_calls": model_calls}
            if artifact.get("provenance"):  # E2b: how it was made, so a later mission can judge whether to reuse it
                body["provenance"] = artifact["provenance"]
        artifact_id = ""
        with self._conn:
            self._missions.hold(row["mission_id"], lease)
            if body is not None:
                artifact_id = self._missions.append_event(row["mission_id"], "artifact", f"worker:{row['instance_id']}",
                                                          body, f"artifact:{run_id}")
            spent = self._record_costs(row, {str(i): entry for i, entry in enumerate(costs) if entry is not None})
            # The run closes first, so the database can bind a verified verdict to this run's recorded calls.
            self._finish(row, "closed", "finished", outcome, artifact_id=artifact_id, calls=calls,
                         model_calls=model_calls, failure_code=failure_code, cost=spent, context=context,
                         mechanisms=mechanisms, checked=verdict is not None, elicitation=elicitation)
            if verdict is not None:
                found = verdict.verdict
                stored = outcome
                self._conn.execute(
                    "INSERT INTO verdicts (verdict_id, run_id, mission_id, unit_key, verifies_key, artifact_id,"
                    " artifact_sha, producer_instance, verifier_instance, verifier_kind, executed, independent,"
                    " model_calls, model_free, result, state, reasons_json, created_at, authority, formal_rules)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"vd_{_uid()}", run_id, row["mission_id"], row["unit_key"], verdict.verifies_key,
                     verdict.artifact_id, verdict.artifact_sha,
                     verdict.producer_instance, row["instance_id"], found.verifier_kind, int(found.executed),
                     int(found.independent), found.model_calls, int(verdict.model_free), found.result, stored.state,
                     json.dumps(list(stored.reasons)), _now(), verdict.authority, verdict.formal_rules))
                self._missions.append_event(row["mission_id"], "verdict", f"worker:{row['instance_id']}",
                                            {"run_id": run_id, "verifies": verdict.verifies_key,
                                             "artifact_id": verdict.artifact_id, "result": found.result,
                                             "state": stored.state}, f"verdict:{run_id}")
            if within is not None:
                within()
        return artifact_id

    def _unbound(self, mission_id: str, verdict: VerdictRecord, calls: tuple) -> tuple[str, str]:
        """(the code, why) when the verdict is not bound to what it claims to judge, or ("", ""). An output that changed
        while it was judged is ``verdict_stale`` (judged again on the new one); any other mismatch is ``verdict_unbound``
        (never retried blind; plan ruling P2.16)."""
        try:
            judged = self.artifact(mission_id, verdict.artifact_id)
        except LookupError:
            return "verdict_unbound", f"the output judged ({verdict.artifact_id}) is not this mission's"
        if judged.get("unit") != verdict.verifies_key:
            return "verdict_unbound", f"the output judged is not one of unit {verdict.verifies_key!r}'s"
        latest = self.last_artifact(mission_id, verdict.verifies_key)
        if latest is None or latest["artifact_id"] != verdict.artifact_id:
            return "verdict_stale", (f"unit {verdict.verifies_key!r} produced a newer output while this one was being "
                                     "checked")
        if judged.get("producer") != verdict.producer_instance:
            return "verdict_unbound", "the check names a producer that did not make the output it judged"
        if judged.get("sha256") != verdict.artifact_sha:
            return "verdict_unbound", "the output's digest is not the one the check read"
        if not any(isinstance(c, dict) and c.get("refused") is False and c.get("state") != BLOCKED for c in calls):
            return "verdict_unbound", "this run recorded no check call that answered"
        return "", ""

    def reserve_call(self, run_id: str, index: int, contract: ParamContract, *, charged: bool = False,
                     lease: Lease | None = None) -> str:
        """Commit a call's ceiling BEFORE invocation; return a budget refusal reason or an empty string.

        Stored worker and mission limits bind costs plus unresolved holds. A write lock serializes admission
        across connections, including unleased callers. A call identity is single use, even after settlement.
        Store/lease/replay failures raise: the door must send nothing. This owns its transaction, so success
        means another connection can see the hold. The existing cost event settles it by invocation id; no
        reservation is deleted or treated as a payment. An uncertain call's hold never expires automatically.
        """
        if self._conn.in_transaction:
            raise ValueError("a call reservation must commit in its own transaction before invocation")
        if economics.hard_limit(index) != (index, True):
            raise ValueError("a call index is a whole number >= 0")
        ceiling = contract.cost_ceiling
        priced = ceiling is not None and ceiling.currency == economics.BUDGET_CURRENCY
        if charged and not priced:  # a strict budget (``charged``): an unbounded cost is refused
            raise ValueError("a charged call must declare a bounded AUD cost")
        row = self._open_row(run_id)
        mid, unit, invocation = row["mission_id"], row["unit_key"], f"{run_id}#{index}"
        with self._conn:
            locked = self._conn.execute(
                "UPDATE missions SET lease_fence=lease_fence WHERE mission_id=? AND status='active'"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id=missions.venture_id AND v.owner=?)",
                (mid, self.owner))
            if not locked.rowcount:
                raise ValueError("only an active mission may reserve a call")
            row = self._open_row(run_id)  # closing or takeover may have won before the write lock
            mission = self._missions.get(mid)
            identity = (lease.holder, lease.fence) if lease else ("", 0)
            if identity != (row["holder"], row["fence"]) or (lease is None and mission["lease_holder"]):
                raise LeaseLost("a call reservation requires the attempt's current mission lease")
            self._missions.hold(mid, lease)
            worker = json.loads(self.instance(mid, row["instance_id"])["spec_json"])
            if [contract.component, contract.op] not in worker["allowed"]:
                raise ValueError("a reservation must name an allowed capability")
            reservations = self._cost_entries("cost.reserved", mid, run_id=run_id)
            costs = self.cost_entries(mid, run_id=run_id)
            if any(e.capability_invocation_id == invocation for e in (*reservations, *costs)):
                raise ValueError(f"call {invocation} already has a reservation or cost record")
            amount = ceiling.amount_minor_units if priced else 0
            for label, limit, spent in (
                ("step", economics.hard_limit(worker["policy"].get("max_cost_cents"))[0],
                 self.committed_total(mid, unit_key=unit)),
                ("mission", economics.hard_limit(mission["budget_cents"])[0], self.committed_total(mid)),
            ):
                used = spent.known_minor_units
                if charged and spent.amount_minor_units is None:
                    used = max(used, limit)
                if amount > max(0, limit - used):
                    return (f"this {label} has {economics.format_money(max(0, limit - used), economics.BUDGET_CURRENCY)} "
                            "left after recorded costs and outstanding reservations; this call needs "
                            f"{economics.format_money(amount, economics.BUDGET_CURRENCY)}")
            entry = economics.call_cost(ceiling, model_free=contract.model_free, component=contract.component,
                                        op=contract.op, charged_model_calls=0, process_model_calls=0)
            entry = dataclasses.replace(entry, work_unit_id=unit, attempt_id=run_id,
                                        worker_instance_id=row["instance_id"], capability_invocation_id=invocation,
                                        method="reserved from the contract before invocation; not a recorded charge")
            self._missions.append_event(
                mid, "cost.reserved", f"worker:{row['instance_id']}",
                {**entry.as_detail(), "contract_sha": contract.sha256, "holder": row["holder"],
                 "fence": row["fence"], "attempt": row["attempt"]}, f"cost.reserved:{run_id}:{index}")
        return ""

    def record_cost(self, run_id: str, index: int, entry: CostEntry) -> None:
        """What call ``index`` of an open attempt cost, written the moment the call returns (the door calls this), in
        its own transaction: nothing that can fail later (the close, the runner, the process) stands between a call
        that was sent and its cost row (fix C1 of the Part 1 review). The close does not write it again. No lease is
        checked: what a sent call spent is a fact, whoever holds the mission now."""
        row = self._open_row(run_id)
        with self._conn:
            self._record_costs(row, {str(int(index)): entry})

    def abandon(self, run_id: str, reason: str, *, code: str = "abandoned", lease: Lease | None = None) -> None:
        """An open run that will never close (its process died, its runner lost the lease, or its claim
        was refused). ``code`` says which (``ABANDON_CODES``); an attempt that may have sent something gets one
        cost row of unknown amount (plan ruling R18: a call it had in flight has no row), beside the rows of the calls
        it recorded; one whose claim was refused sent nothing."""
        if code not in ABANDON_CODES:
            raise ValueError(f"an abandon names one of {ABANDON_CODES}, got {code!r}")
        row = self._open_row(run_id)
        costs = {} if code == "abandoned:claim" else {"abandoned": economics.abandoned_cost()}
        with self._conn:
            self._missions.hold(row["mission_id"], lease)
            spent = self._record_costs(row, costs)
            self._finish(row, "abandoned", "abandoned", Outcome(BLOCKED, reasons=(f"abandoned: {reason}",)),
                         failure_code=code, cost=spent)

    def _finish(self, row: dict, run_state: str, instance_status: str, outcome: Outcome, *, artifact_id: str = "",
                calls: tuple = (), model_calls: int = 0, failure_code: str = "", cost: CostTotal | None = None,
                context: dict | None = None, mechanisms: tuple = (), checked: bool = False,
                elicitation: dict | None = None) -> None:
        cost = cost if cost is not None else total(())
        now = _now()
        cur = self._conn.execute(
            "UPDATE task_runs SET state=?, completion_state=?, outcome_json=?, artifact_id=?, calls_json=?,"
            " model_calls=?, failure_code=?, finished_at=?, cost_cents=?, cost_source=?"
            " WHERE run_id=? AND state='open'" + _OWNED.format(table="task_runs"),
            (run_state, outcome.state, _outcome_json(outcome), artifact_id, json.dumps(list(calls), default=str),
             int(model_calls), failure_code, now, cost.known_minor_units, cost.summary, row["run_id"], self.owner))
        if cur.rowcount == 0:
            raise ValueError(f"run {row['run_id']} is no longer open")
        self._conn.execute(
            "UPDATE worker_instances SET status=?, reason=?, finished_at=? WHERE instance_id=? AND status='running'"
            + _OWNED.format(table="worker_instances"),
            (instance_status, "; ".join(outcome.reasons)[:300], now, row["instance_id"], self.owner))
        detail = {"run_id": row["run_id"], "unit": row["unit_key"], "state": run_state,
                  "completion_state": outcome.state, "artifact_id": artifact_id,
                  "cost": {"known_minor_units": cost.known_minor_units, "currency": cost.currency,
                           "summary": cost.summary, "unknown_entries": cost.unknown_entries}}
        if context is not None:
            detail["context"] = context
        if mechanisms or checked:  # Ledger §10, per attempt (E2c); [] when the check's finding was refused
            detail["verification"] = [dict(m) for m in mechanisms]
        if elicitation is not None:  # FE §28 (E10c): one per verify cycle, with what it cost and how long it took
            detail["elicitation"] = {**elicitation, "cost": dict(detail["cost"]),
                                     "latency_seconds": _seconds(row["started_at"], now)}
        self._missions.append_event(row["mission_id"], "run.closed", f"worker:{row['instance_id']}", detail,
                                    f"run.closed:{row['run_id']}")

    def _record_costs(self, row: dict, costs: dict[str, CostEntry]) -> CostTotal:
        """The attempt's cost rows not yet written, inside the caller's transaction while the run is still open; returns
        the total of ALL its rows (those written as each call returned included), as the database sums them. Key ``i``
        is call ``i`` (``<run_id>#<i>``; a call refused before it was sent has none); ``abandoned`` is an abandon's."""
        mission_id, run_id = row["mission_id"], row["run_id"]
        have = {e.capability_invocation_id for e in self.cost_entries(mission_id, run_id=run_id)}
        for key, entry in costs.items():
            invocation = entry.capability_invocation_id or f"{run_id}#{key}"
            if invocation in have:  # written when its call returned (``record_cost``)
                continue
            entry = dataclasses.replace(
                entry, mission_id=mission_id, work_unit_id=row["unit_key"], attempt_id=run_id,
                worker_instance_id=row["instance_id"], capability_invocation_id=invocation)
            self._missions.append_event(mission_id, "cost", f"worker:{row['instance_id']}",
                                        {**entry.as_detail(), "attempt": row["attempt"]}, f"cost:{run_id}:{key}")
            have.add(invocation)
        return total(self.cost_entries(mission_id, run_id=run_id))

    def _open_row(self, run_id: str) -> dict:
        found = self._conn.execute("SELECT * FROM task_runs WHERE run_id=?" + _OWNED.format(table="task_runs"),
                                   (str(run_id), self.owner)).fetchone()
        if found is None:
            raise LookupError(NOT_FOUND)
        if found["state"] != "open":
            raise ValueError(f"run {run_id} is {found['state']}, not open")
        return dict(found)

    # -- reads ------------------------------------------------------------------------------------
    def run(self, mission_id: str, run_id: str) -> dict:
        found = [r for r in self.runs(mission_id) if r["run_id"] == run_id]
        if not found:
            raise LookupError(NOT_FOUND)
        return found[0]

    def runs(self, mission_id: str, unit_key: str | None = None) -> list[dict]:
        self._missions.get(mission_id)
        sql, args = "SELECT * FROM task_runs WHERE mission_id=?", [str(mission_id)]
        if unit_key is not None:
            sql += " AND unit_key=?"
            args.append(unit_key)
        rows = self._conn.execute(sql + _OWNED.format(table="task_runs") + " ORDER BY rowid", (*args, self.owner))
        found = [dict(r) for r in rows.fetchall()]
        limits = self._suite_limits(mission_id)
        for row in found:
            reasons = tuple(dict.fromkeys(reason for (run, check, _, _), texts in limits.items()
                                          if (run, check) == (row["run_id"], row["unit_key"]) for reason in texts))
            if reasons:
                outcome = contracts.limited_outcome(outcome_of(row), reasons)
                row.update(outcome_json=json.dumps(outcome.as_record()), completion_state=outcome.state)
        return found

    def _suite_limits(self, mission_id: str) -> dict:
        return contracts.suite_receipt_limits(
            [event["detail"] for event in self._missions.events(mission_id, kind="run.closed")])

    def open_runs(self, mission_id: str) -> list[dict]:
        return [r for r in self.runs(mission_id) if r["state"] == "open"]

    def attempts(self, mission_id: str, unit_key: str, *, after_run: str = "") -> int:
        """The unit's attempts; with ``after_run``, only those opened after that run (of any unit) was (V4b: a
        verifier's attempts on the output that run produced)."""
        if not after_run:
            return len(self.runs(mission_id, unit_key))
        self._missions.get(mission_id)
        return self._conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE mission_id=? AND unit_key=?"
            " AND rowid > (SELECT r.rowid FROM task_runs r WHERE r.run_id=?)" + _OWNED.format(table="task_runs"),
            (str(mission_id), unit_key, str(after_run), self.owner)).fetchone()[0]

    def instance(self, mission_id: str, instance_id: str) -> dict:
        self._missions.get(mission_id)
        found = self._conn.execute(
            "SELECT * FROM worker_instances WHERE mission_id=? AND instance_id=?"
            + _OWNED.format(table="worker_instances"), (str(mission_id), str(instance_id), self.owner)).fetchone()
        if found is None:
            raise LookupError(NOT_FOUND)
        return dict(found)

    def artifact(self, mission_id: str, artifact_id: str) -> dict:
        for event in self._missions.events(mission_id, kind="artifact"):
            if event["evidence_id"] == artifact_id:
                return {"artifact_id": artifact_id, **event["detail"]}
        raise LookupError(NOT_FOUND)

    def find_artifact(self, artifact_id: str) -> tuple[str, dict]:
        """(the mission that made it, the artifact with its time) for one of THIS owner's artifacts, in any of their
        missions; another owner's id is ``LookupError(NOT_FOUND)`` exactly as an unknown one (E2b: per account)."""
        row = self._conn.execute(
            "SELECT e.ref, e.detail_json, e.at FROM evidence e JOIN missions m ON m.mission_id = e.ref"
            " AND m.venture_id = e.venture_id JOIN ventures v ON v.venture_id = m.venture_id"
            " WHERE e.evidence_id = ? AND e.kind = 'artifact' AND v.owner = ?",
            (str(artifact_id), self.owner)).fetchone()
        if row is None:
            raise LookupError(NOT_FOUND)
        return row[0], {"artifact_id": str(artifact_id), "at": row[2], **json.loads(row[1] or "{}")}

    def makers(self, sha256: str) -> set[tuple[str, str]]:
        """The ops that made any of THIS owner's results whose content has this digest, in any of their missions
        (Security Brief 3, review I5: an op that made the very bytes a check judges, linked or not, cannot certify them)."""
        rows = self._conn.execute(
            "SELECT json_extract(e.detail_json, '$.provenance.call.component'),"
            " json_extract(e.detail_json, '$.provenance.call.op') FROM evidence e JOIN missions m"
            " ON m.mission_id = e.ref AND m.venture_id = e.venture_id JOIN ventures v ON v.venture_id = m.venture_id"
            " WHERE e.kind = 'artifact' AND json_extract(e.detail_json, '$.sha256') = ? AND v.owner = ?",
            (str(sha256), self.owner)).fetchall()
        return {(str(component), str(op)) for component, op in rows if component and op}

    def mission(self, mission_id: str) -> dict:
        """The mission as this owner sees it (reuse reads the risk tier a result was made for)."""
        return self._missions.get(mission_id)

    def last_artifact(self, mission_id: str, unit_key: str) -> dict | None:
        """The artifact of the unit's latest closed run that produced one."""
        for row in reversed(self.runs(mission_id, unit_key)):
            if row["state"] == "closed" and row["artifact_id"]:
                return self.artifact(mission_id, row["artifact_id"])
        return None

    def verdicts(self, mission_id: str, verifies_key: str | None = None) -> list[dict]:
        """The unit's (or mission's) verdicts as stored, each at the class the formal rules in force give it (task
        #165): a ``formal_methods.solve`` verdict stored at A4 under other rules than ``FORMAL_RULES``, or none, reads
        at A3, so reuse, the roll-up and the auditor, which all read here, refuse to serve it as verified and its check
        runs again. Explicit incomplete-suite receipts also cap their own verdict's effective outcome. The row
        itself is kept as given."""
        self._missions.get(mission_id)
        sql = ("SELECT verdicts.*, EXISTS (SELECT 1 FROM task_runs r, json_each(r.calls_json) c"
               " WHERE r.run_id = verdicts.run_id AND json_extract(c.value, '$.component') = 'formal_methods'"
               " AND json_extract(c.value, '$.op') = 'solve') AS formal_call FROM verdicts WHERE mission_id=?")
        args = [str(mission_id)]
        if verifies_key is not None:
            sql += " AND verifies_key=?"
            args.append(verifies_key)
        rows = self._conn.execute(sql + _OWNED.format(table="verdicts") + " ORDER BY rowid", (*args, self.owner))
        stored = rows.fetchall()
        limits = self._suite_limits(mission_id)
        found = []
        for row in stored:
            verdict = dict(row)
            formal = verdict.pop("formal_call")
            if formal and verdict.get("authority") == VERIFIER_GRADE and verdict.get("formal_rules", "") != FORMAL_RULES:
                verdict["authority"] = "A3"
            reasons = limits.get((verdict["run_id"], verdict["unit_key"], verdict["verifies_key"], verdict["artifact_id"]))
            if reasons:
                original = Outcome(verdict["state"], reasons=tuple(json.loads(verdict["reasons_json"])))
                effective = contracts.limited_outcome(original, reasons)
                verdict.update(state=effective.state, reasons_json=json.dumps(effective.reasons))
            found.append(verdict)
        return found

    def cost_entries(self, mission_id: str | None = None, *, unit_key: str | None = None,
                     run_id: str | None = None) -> list[CostEntry]:
        """This owner's cost rows, oldest first (Ledger §21): one attempt's, one unit's, one mission's, or (no
        mission id) the whole account's. Another owner's mission id is ``LookupError(NOT_FOUND)``."""
        return self._cost_entries("cost", mission_id, unit_key=unit_key, run_id=run_id)

    def _cost_entries(self, kind: str, mission_id: str | None = None, *, unit_key: str | None = None,
                      run_id: str | None = None) -> list[CostEntry]:
        sql = ("SELECT e.evidence_id, e.ref, e.detail_json, e.at FROM evidence e"
               " JOIN missions m ON m.mission_id = e.ref AND m.venture_id = e.venture_id"
               " JOIN ventures v ON v.venture_id = m.venture_id WHERE e.kind = ? AND v.owner = ?")
        args: list = [kind, self.owner]
        if mission_id is not None:
            self._missions.get(mission_id)
            sql += " AND e.ref = ?"
            args.append(str(mission_id))
        if unit_key is not None:
            sql += " AND json_extract(e.detail_json, '$.work_unit_id') = ?"
            args.append(unit_key)
        if run_id is not None:
            sql += " AND json_extract(e.detail_json, '$.attempt_id') = ?"
            args.append(run_id)
        rows = self._conn.execute(sql + " ORDER BY e.rowid", args).fetchall()
        return [CostEntry.from_detail(json.loads(r[2] or "{}"), id=r[0], mission_id=r[1], recorded_at=r[3])
                for r in rows]

    def pending_reservations(self, mission_id: str | None = None, *, unit_key: str | None = None,
                             run_id: str | None = None) -> list[CostEntry]:
        """Unresolved call ceilings, distinct from charges, including calls whose attempt was abandoned."""

        def identity(entry: CostEntry) -> tuple:
            return (entry.mission_id, entry.attempt_id, entry.work_unit_id, entry.worker_instance_id,
                    entry.capability_invocation_id)

        settled = {identity(e) for e in self.cost_entries(mission_id, unit_key=unit_key, run_id=run_id)}
        return [e for e in self._cost_entries("cost.reserved", mission_id, unit_key=unit_key, run_id=run_id)
                if identity(e) not in settled]

    def committed_total(self, mission_id: str | None = None, *, unit_key: str | None = None) -> CostTotal:
        """Budget commitment: binding costs plus unresolved ceilings, never a claim of observed spend.

        Admission calls this under its write lock so settlement cannot race the two reads.
        """
        return self.cost_total(mission_id, unit_key=unit_key, binding_only=True).plus(
            total(self.pending_reservations(mission_id, unit_key=unit_key)))

    def cost_total(self, mission_id: str | None = None, *, unit_key: str | None = None, run_id: str | None = None,
                   binding_only: bool = False) -> CostTotal:
        """The attempts' machine cost in the budget's currency (Ledger §21), filtered as ``cost_entries``. An attempt
        closed before cost rows were kept (``cost_source`` 'none') counts as one entry of unknown cost, never 0 (plan
        ruling R4); an open attempt's calls count as each one's row is written, the moment it returns (fix C1)."""
        found = total(self.cost_entries(mission_id, unit_key=unit_key, run_id=run_id), binding_only=binding_only)
        sql, args = "SELECT COUNT(*) FROM task_runs WHERE state <> 'open' AND cost_source = 'none'", []
        if mission_id is not None:
            sql += " AND mission_id = ?"
            args.append(str(mission_id))
        if unit_key is not None:
            sql += " AND unit_key = ?"
            args.append(unit_key)
        if run_id is not None:
            sql += " AND run_id = ?"
            args.append(run_id)
        legacy = self._conn.execute(sql + _OWNED.format(table="task_runs"), (*args, self.owner)).fetchone()[0]
        return dataclasses.replace(found, entries=found.entries + legacy,
                                   unknown_entries=found.unknown_entries + legacy)

    def outcome(self, row: dict) -> Outcome:
        return outcome_of(row)


__all__ = ["ABANDON_CODES", "FAILURE_CODES", "INSTANCE_ENDS", "MAX_ARTIFACT_BYTES", "Run", "RunLedger",
           "VerdictRecord", "artifact_sha", "outcome_of"]
