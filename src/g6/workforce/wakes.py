# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Wakes: what a waiting mission waits for, and what wakes it (workforce OS plan, V2b).

A mission waits when nothing can run but units held back by their start time (``not_before``), by a file
not yet in the mission's inbox (``await_file``) or by the owner's chat (a pull unit). ``wait`` moves it
``active`` -> ``waiting`` and arms one wake per held unit, in one transaction under the runner's lease.
Looking at a waiting mission costs nothing: ``due`` reads the clock and the inbox, never a model.

``fire`` ends the whole waiting episode in one transaction: ``waiting`` -> ``active`` (a compare-and-set,
so of runners racing to fire it exactly one does), the fired wakes ``fired``, every other armed wake
``dropped`` (the next wait arms afresh), and ONE ``wake`` event, however many wakes fired. A file wake
records the file's digest and size; a timer fires by the firing runner's clock, and the event says which
reading. A wake is armed once and ends once; wake rows are kept (triggers).

The inbox is ``<inbox>/<venture id>/<mission id>/<name>``: a name is a plain file name (plans.py), and a
folder or a link with that name is not a drop. An approval wake (V4c) waits for a person's decision on a gate or a
disagreement; it is due once the decision is recorded (``decided``), so a decision whose own wake never fired still
wakes the mission.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from g6.workforce.missions import Lease, MissionStore, stamp

WAKE_KINDS = ("timer", "file", "submission", "approval", "retry")

_DDL = """
CREATE TABLE IF NOT EXISTS mission_wakes (
    wake_id     TEXT PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    unit_key    TEXT NOT NULL,
    at          TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'armed',
    armed_at    TEXT NOT NULL,
    ended_at    TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mission_wakes_armed ON mission_wakes(mission_id, kind, unit_key)
WHERE state = 'armed';
CREATE TRIGGER IF NOT EXISTS mission_wakes_end_once BEFORE UPDATE ON mission_wakes
WHEN OLD.state <> 'armed' OR NEW.state NOT IN ('fired', 'dropped') OR NEW.wake_id <> OLD.wake_id
  OR NEW.mission_id <> OLD.mission_id OR NEW.kind <> OLD.kind OR NEW.unit_key <> OLD.unit_key
  OR NEW.at <> OLD.at OR NEW.name <> OLD.name OR NEW.armed_at <> OLD.armed_at
BEGIN SELECT RAISE(ABORT, 'a wake is armed once and ends once'); END;
CREATE TRIGGER IF NOT EXISTS mission_wakes_kept BEFORE DELETE ON mission_wakes
BEGIN SELECT RAISE(ABORT, 'wakes are kept'); END;
CREATE TRIGGER IF NOT EXISTS mission_wakes_no_replace BEFORE INSERT ON mission_wakes
WHEN EXISTS (SELECT 1 FROM mission_wakes WHERE wake_id = NEW.wake_id)
BEGIN SELECT RAISE(ABORT, 'wakes are kept'); END;
"""

#: Every statement on mission_wakes carries the owner predicate through missions -> ventures.
_OWNED = (" AND EXISTS (SELECT 1 FROM missions m JOIN ventures v ON v.venture_id = m.venture_id"
          " WHERE m.mission_id = mission_wakes.mission_id AND v.owner = ?)")


class _NotThisEpisode(Exception):
    """A fire that must change nothing: its wakes ended, or the mission was woken first."""


class WaitStale(Exception):
    """The mission changed (an attempt opened or closed) while the runner decided to wait: look again."""


@dataclass(frozen=True)
class Want:
    """What holds one unit back: its start time (``timer``), its file (``file``), the chat (``submission``) or a
    person's decision (``approval``, V4c), or an explicit zero-inference draft retry (``retry``).
    Retry wakes are never due automatically; the runtime consumes their receipt token under lease."""

    kind: str
    unit_key: str
    at: str = ""
    name: str = ""


def _digest(path: Path) -> tuple[str, int]:
    found, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            found.update(chunk)
            size += len(chunk)
    return found.hexdigest(), size


class Wakes:
    """One owner's mission wakes, in the venture db beside the missions."""

    def __init__(self, missions: MissionStore, inbox: str | Path | None = None,
                 decided: Callable[[str, str], bool] | None = None) -> None:
        self._missions = missions
        self.owner = missions.owner
        self._conn = missions.conn
        self._inbox = Path(inbox) if inbox else None
        #: Whether a person has decided (mission id, unit key): an approval wake is due then (V4c).
        self._decided = decided or (lambda mission_id, unit_key: False)
        self._conn.executescript(_DDL)

    # -- the inbox ----------------------------------------------------------------------------------
    def path(self, mission: dict, name: str) -> Path | None:
        """Where the mission's awaited file is dropped (None: this runner has no inbox)."""
        if self._inbox is None:
            return None
        return self._inbox / str(mission["venture_id"]) / str(mission["mission_id"]) / name

    def dropped(self, mission: dict, name: str) -> Path | None:
        """The awaited file when it is in the inbox as a regular file (a folder or a link is not a drop)."""
        found = self.path(mission, name)
        try:
            if found is None or found.is_symlink() or not found.is_file():
                return None
        except OSError:
            return None
        return found

    # -- waiting ------------------------------------------------------------------------------------
    def wait(self, mission_id: str, wants: list[Want], *, reason: str, lease: Lease | None, now: datetime,
             runs_seen: int | None = None, actor: str = "code") -> list[dict]:
        """``active`` -> ``waiting``, arming one wake per want, in one transaction under ``lease``. With
        ``runs_seen`` (the mission's attempts when the runner chose to wait), ``WaitStale`` and nothing written
        if any attempt opened or closed since, or one is open: the chat may have delivered what it waits for."""
        if not wants:
            raise ValueError("a mission waits on at least one wake")
        bad = sorted({w.kind for w in wants} - set(WAKE_KINDS))
        if bad:
            raise ValueError(f"unknown wake kind(s) {bad}")
        armed_at = stamp(now)
        with self._conn:
            self._missions.hold(mission_id, lease)  # from here the write lock is held: no attempt lands meanwhile
            if runs_seen is not None:
                count, still_open = self._conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(state = 'open'), 0) FROM task_runs WHERE mission_id=?",
                    (str(mission_id),)).fetchone()
                if count != runs_seen or still_open:
                    raise WaitStale(f"mission {mission_id} changed while its runner decided to wait")
            self._missions.move(mission_id, "waiting", expect="active", reason=reason, actor=actor,
                                detail={"wakes": [asdict(w) for w in wants]})
            for want in wants:
                self._conn.execute(
                    "INSERT INTO mission_wakes (wake_id, mission_id, kind, unit_key, at, name, armed_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (f"wk_{uuid.uuid4().hex[:12]}", str(mission_id), want.kind, want.unit_key, want.at, want.name,
                     armed_at))
        return self.armed(mission_id)

    def armed(self, mission_id: str) -> list[dict]:
        return self._rows(mission_id, armed_only=True)

    def all(self, mission_id: str) -> list[dict]:
        return self._rows(mission_id, armed_only=False)

    def _rows(self, mission_id: str, *, armed_only: bool) -> list[dict]:
        mission = self._missions.get(mission_id)  # NOT_FOUND for an unknown or foreign mission
        sql = "SELECT * FROM mission_wakes WHERE mission_id=?" + (" AND state='armed'" if armed_only else "")
        rows = self._conn.execute(sql + _OWNED + " ORDER BY rowid", (str(mission_id), self.owner)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if item["kind"] == "file":
                where = self.path(mission, item["name"])
                item["path"] = "" if where is None else str(where)
            out.append(item)
        return out

    def due(self, mission_id: str, now: datetime) -> list[dict]:
        """The armed wakes whose event has happened (a timer whose time has come by ``now``, a file in the
        inbox, a person's recorded decision); a submission wake fires only through the chat's submission."""
        mission = self._missions.get(mission_id)
        if mission["status"] != "waiting":
            return []
        clock = stamp(now)
        return [w for w in self.armed(mission_id)
                if (w["kind"] == "timer" and w["at"] <= clock)
                or (w["kind"] == "file" and self.dropped(mission, w["name"]) is not None)
                or (w["kind"] == "approval" and self._decided(str(mission_id), w["unit_key"]))]

    # -- waking ---------------------------------------------------------------------------------------
    def fire(self, mission_id: str, wakes: list[dict], *, now: datetime, actor: str = "code",
             lease: Lease | None = None) -> bool:
        """Wake the mission on ``wakes`` (one transaction, one ``wake`` event, under the runner's ``lease``:
        ``LeaseLost`` once it is not the mission's). False when it is no longer waiting, nothing it names can
        fire, or any wake named is no longer armed (a delayed fire of an ended episode changes nothing)."""
        mission = self._missions.get(mission_id)
        if mission["status"] != "waiting" or not wakes:
            return False
        fired = []
        for wake in wakes:
            item = {"kind": wake["kind"], "unit": wake["unit_key"]}
            if wake["kind"] == "timer":
                item["at"] = wake["at"]
            elif wake["kind"] == "file":
                found = self.dropped(mission, wake["name"])
                if found is None:
                    continue  # it went away between the look and the wake: not a drop
                try:
                    digest, size = _digest(found)
                except OSError:
                    continue
                item.update(name=wake["name"], sha256=digest, bytes=size)
            fired.append((wake["wake_id"], item))
        if not fired:
            return False
        ended = stamp(now)
        why = ", ".join(f"{item['kind']} for {item['unit']}" for _, item in fired)
        ids = [wake_id for wake_id, _ in fired]
        marks = ",".join("?" * len(ids))
        try:
            with self._conn:
                self._missions.hold(mission_id, lease)
                woken = self._conn.execute(
                    f"UPDATE mission_wakes SET state='fired', ended_at=? WHERE mission_id=? AND state='armed'"
                    f" AND wake_id IN ({marks})" + _OWNED, (ended, str(mission_id), *ids, self.owner)).rowcount
                if woken != len(ids):
                    raise _NotThisEpisode  # a wake named here already ended: roll back, change nothing
                try:
                    self._missions.move(mission_id, "active", expect="waiting", reason=f"woken: {why}", actor=actor)
                except ValueError:
                    raise _NotThisEpisode from None  # someone else woke it first
                dropped = self._conn.execute(
                    "UPDATE mission_wakes SET state='dropped', ended_at=? WHERE mission_id=? AND state='armed'"
                    + _OWNED, (ended, str(mission_id), self.owner)).rowcount
                self._missions.append_event(mission_id, "wake", actor,
                                            {"fired": [item for _, item in fired], "dropped": dropped,
                                             "clock": ended}, f"wake:{min(ids)}")
        except _NotThisEpisode:
            return False
        return True

    def end(self, mission_id: str, *, reason: str, now: datetime, key: str, actor: str = "code") -> int:
        """Inside the CALLER's transaction: end a waiting mission's episode because its plan changed under it
        (V4a: a delegation gave it new work), not on an event it waited for. ``waiting`` -> ``active``, every
        armed wake ``dropped``, one ``wake`` event saying why. Returns how many wakes were dropped."""
        ended = stamp(now)
        self._missions.move(mission_id, "active", expect="waiting", reason=reason, actor=actor)
        dropped = self._conn.execute(
            "UPDATE mission_wakes SET state='dropped', ended_at=? WHERE mission_id=? AND state='armed'" + _OWNED,
            (ended, str(mission_id), self.owner)).rowcount
        self._missions.append_event(mission_id, "wake", actor,
                                    {"fired": [], "dropped": dropped, "clock": ended, "reason": reason},
                                    f"wake:{key}")
        return dropped

    def fire_submission(self, mission_id: str, unit_key: str, *, now: datetime) -> bool:
        """The chat delivered a unit the waiting mission waits for: fire that unit's submission wake."""
        wanted = [w for w in self.armed(mission_id) if w["kind"] == "submission" and w["unit_key"] == unit_key]
        if not wanted:
            raise ValueError(f"the mission is not waiting for the chat to submit unit {unit_key!r}")
        return self.fire(mission_id, wanted, now=now, actor="chat")


__all__ = ["WAKE_KINDS", "Wakes", "Want"]
