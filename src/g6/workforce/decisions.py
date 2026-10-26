# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Human decisions (workforce OS plan, V4c, under the deferrals package's D3 default).

Two kinds of question wait for a person, never for a model: a ``gate`` (the plan says a human approves
before what follows it runs) and an ``adjudication`` (verifiers disagree on one output and no retry is
left). The runtime asks (one ``approval.requested`` event per question); a person answers through the
local command line (``decide.py``), and the answer is one append-only ``mission_decisions`` row written with
its ``approval.decided`` event in one transaction. Only the actor ``human`` is ever recorded deciding:
``record`` refuses any other, the table refuses any other (a CHECK), and no tool the owner's chat is
served reaches this module (a test reads the toolset's source). Each answer also writes its Ledger §6
intervention row (Phase E Part 2, E1b) in the same transaction, so no decision a person made is ever missing
from the mission's record of their time.

An approval binds what its question asked about (Security Brief 3, SR3-2; review I3): the question's event keeps the
contract and effect digests of the work behind its gate when it is asked, and the answer's event records them. When
they change, the runtime asks the person again as ``<gate>#<n>``; that question records the answer it supersedes, and
only a gate's latest answer counts (``effective``).

A person may say how many minutes an answer took (R5, lead ruling H-1): an optional figure from 0 to 480, kept as
``minutes_declared`` on the answer's event and never anywhere else. It is their word, not a measurement, so it is never
mixed with the time the terminal measured or with how long the question waited (``human_time``).

Audit 010 WF-013: the actor ``human`` names the role a decision fills, not a proven person. Each answer and each
confirmation also records its ``principal`` (``principal``): the identity the channel that took it gave, the channel, and
that G6 authenticated no one, so the record never reads as an independent person's decision.
"""
from __future__ import annotations

import getpass
import math
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from g6.workforce.store import NOT_FOUND
from g6.workforce.economics import HumanIntervention
from g6.workforce.missions import FINISHED, MissionStore

#: What a person may answer, per kind of question.
ANSWERS = {"gate": ("approve", "reject"), "adjudication": ("uphold", "set_aside")}
HUMAN = "human"
#: Ledger §6's kind for each question a person answers (plan ruling P2.4).
KIND_OF = {"gate": "approval", "adjudication": "verification"}
#: The most minutes one answer may declare (R5, lead ruling H-1).
MAX_MINUTES = 480
#: The longest question text kept on its event (what the chat reads in the status).
QUESTION_LIMIT = 2000
# COPY: The end of a question cut at the limit, so the chat knows it reads part of it (founder copy issue 3).
CUT_NOTE = " … (cut at {limit} characters)"
# COPY: The same for a formal question, whose whole text the decide command prints at the terminal (copy issue 3).
CUT_NOTE_DECIDE = " … (cut at {limit} characters; the decide command shows all of it)"


def kept_question(question: Any, detail: dict | None) -> str:
    """The question's text as kept: whole when it fits, else cut so that it ends with a note saying it was cut, within
    ``QUESTION_LIMIT`` (founder copy issue 3). A formal question (its detail carries the challenge decide prints whole)
    says where all of it is shown; nothing else holds the rest of any other question, so its note says only that."""
    text = str(question)
    if len(text) <= QUESTION_LIMIT:
        return text
    note = (CUT_NOTE_DECIDE if (detail or {}).get("challenge") else CUT_NOTE).format(limit=QUESTION_LIMIT)
    return text[:QUESTION_LIMIT - len(note)] + note

_DDL = """
CREATE TABLE IF NOT EXISTS mission_decisions (
    decision_id  TEXT PRIMARY KEY,
    mission_id   TEXT NOT NULL,
    unit_key     TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('gate', 'adjudication')),
    decision     TEXT NOT NULL,
    reason       TEXT NOT NULL,
    actor        TEXT NOT NULL CHECK (actor = 'human'),
    decided_at   TEXT NOT NULL,
    UNIQUE (mission_id, unit_key, kind)
);
CREATE TRIGGER IF NOT EXISTS mission_decisions_no_update BEFORE UPDATE ON mission_decisions
BEGIN SELECT RAISE(ABORT, 'a decision is kept as written'); END;
CREATE TRIGGER IF NOT EXISTS mission_decisions_no_delete BEFORE DELETE ON mission_decisions
BEGIN SELECT RAISE(ABORT, 'a decision is kept as written'); END;
CREATE TRIGGER IF NOT EXISTS interventions_are_a_persons BEFORE INSERT ON evidence
WHEN NEW.kind = 'intervention' AND NOT COALESCE((
    json_extract(NEW.detail_json, '$.actor') = 'human'
    AND json_extract(NEW.detail_json, '$.kind') IN ('clarification', 'approval', 'correction', 'verification',
        'manual_execution', 'escalation', 'configuration', 'other')
    AND json_extract(NEW.detail_json, '$.actor_type') IN ('user', 'operator', 'administrator', 'domain_expert')
    AND length(trim(COALESCE(json_extract(NEW.detail_json, '$.decided_by'), ''))) > 0
    AND json_type(NEW.detail_json, '$.required_by_policy') IN ('true', 'false')
    AND ((json_extract(NEW.detail_json, '$.duration_source') = 'unknown'
          AND json_type(NEW.detail_json, '$.duration_seconds') = 'null')
      OR (json_extract(NEW.detail_json, '$.duration_source') = 'measured'
          AND json_type(NEW.detail_json, '$.duration_seconds') = 'integer'
          AND json_extract(NEW.detail_json, '$.duration_seconds') >= 0
          AND length(COALESCE(json_extract(NEW.detail_json, '$.started_at'), '')) > 0
          AND length(COALESCE(json_extract(NEW.detail_json, '$.ended_at'), '')) > 0))), 0)
BEGIN SELECT RAISE(ABORT, 'an intervention is a person''s, its time measured or unknown, never inferred'); END;
"""
#: Every statement on mission_decisions carries the owner predicate through missions -> ventures.
_OWNED = (" AND EXISTS (SELECT 1 FROM missions m JOIN ventures v ON v.venture_id = m.venture_id"
          " WHERE m.mission_id = mission_decisions.mission_id AND v.owner = ?)")


#: The channels a decision can come through: the library caller's own approver callable (``api``), and the decide
#: command typed at an interactive terminal (``decide``).
CHANNELS = ("approver", "terminal")


def principal(identity: str, *, channel: str) -> dict:
    """Who decided, as recorded beside a decision (audit 010 WF-013): the identity ``channel`` gave and what it does not
    establish. G6 authenticates no one: an approver runs in the caller's own process, and the decide command needs only
    an interactive terminal of this machine's user, which the caller's agent can open, so no decision is recorded as an
    independent person's (``authenticated`` and ``independent_of_caller`` are always false). ``same_process_as_caller``
    is true for an approver (it is called in the caller's process) and unknown (None) at a terminal; ``os_user`` and
    ``pid`` are the deciding process's own."""
    if channel not in CHANNELS:
        raise ValueError(f"a decision's channel is one of {CHANNELS}, got {channel!r}")
    try:
        user = getpass.getuser()
    except (KeyError, OSError, ImportError):  # no user name in this environment
        user = ""
    return {"identity": str(identity), "channel": channel, "authenticated": False, "independent_of_caller": False,
            "same_process_as_caller": True if channel == "approver" else None, "os_user": user, "pid": os.getpid()}


def declared_minutes(value: Any) -> int | float | None:
    """The minutes a person says an answer took (R5, lead ruling H-1): None when they gave none, else a number from 0
    to ``MAX_MINUTES``, kept as given. Anything else is refused before anything is written."""
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not 0 <= value <= MAX_MINUTES):
        # COPY: The refusal of a declared time that is not a number from 0 to 480.
        raise ValueError(f"minutes is how long the decision took you, a number from 0 to {MAX_MINUTES}; "
                         f"{str(value)[:40]!r} is not. Nothing was recorded. (code G6_E_INVALID_PARAM)")
    return value


def said_minutes(decided: list[dict]) -> list:
    """The figures people declared on these ``approval.decided`` events (R5), one per answer that gave one."""
    return [e["detail"]["minutes_declared"] for e in decided if "minutes_declared" in e["detail"]]


def wait_seconds(asked: list[dict], decided: list[dict]) -> list[int]:
    """For each answered question, the seconds from its ``approval.requested`` event to its ``approval.decided`` event,
    as the rows recorded them (R5): how long the question waited, never how long anyone worked."""
    def at(value: Any) -> datetime | None:
        try:
            found = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        return found if found.tzinfo is not None else None

    opened = {(e["detail"].get("kind"), e["detail"].get("unit")): at(e["at"]) for e in asked}
    out = []
    for event in decided:
        start, end = opened.get((event["detail"].get("kind"), event["detail"].get("unit"))), at(event["at"])
        if start is not None and end is not None and end >= start:
            out.append(int((end - start).total_seconds()))
    return out


def human_time(asked: list[dict], decided: list[dict]) -> dict:
    """One mission's person decisions, read from its question and answer events (R5, lead rulings H-1, H-2). Three
    measures, never mixed: ``human_decisions`` counts the answers a person recorded; ``human_minutes_declared`` adds the
    minutes people said those took (None when nobody said, never 0), and ``minutes_coverage`` is the share of answers
    that said one; ``human_wait_minutes`` adds how long each answered question waited (latency, never effort)."""
    said, waits = said_minutes(decided), wait_seconds(asked, decided)
    return {"human_decisions": len(decided), "human_minutes_declared": round(sum(said), 4) if said else None,
            "minutes_coverage": round(len(said) / len(decided), 4) if decided else None,
            "human_wait_minutes": round(sum(waits) / 60, 4) if waits else None}


class Decisions:
    """One owner's questions to a person and the answers, in the venture db beside the missions."""

    def __init__(self, missions: MissionStore) -> None:
        self._missions = missions
        self.owner = missions.owner
        self._conn = missions.conn
        self._conn.executescript(_DDL)

    # -- questions ------------------------------------------------------------------------------------
    def request(self, mission_id: str, unit_key: str, kind: str, *, question: str, detail: dict | None = None,
                binding: dict | None = None) -> None:
        """Ask once (idempotent): the question a person answers about ``unit_key``. ``binding``: what a gate question
        asks about (the contract and effect digests of the work behind it, Security Brief 3 review I3), kept on the
        question's event beside what the person reads; the first asking's stands."""
        if kind not in ANSWERS:
            raise ValueError(f"unknown question kind {kind!r}")
        self._missions.record_event(mission_id, "approval.requested", actor="code",
                                    detail={"unit": unit_key, "kind": kind, "question": kept_question(question, detail),
                                            "detail": detail or {}, **({"binding": binding} if binding else {})},
                                    idem_key=f"approval:{kind}:{unit_key}")

    def requested(self, mission_id: str) -> list[dict]:
        return [{"unit": e["detail"].get("unit", ""), "kind": e["detail"].get("kind", ""),
                 "question": e["detail"].get("question", ""), "detail": e["detail"].get("detail", {})}
                for e in self._missions.events(mission_id, kind="approval.requested")]

    def pending(self, mission_id: str) -> list[dict]:
        """The questions asked and not yet answered, oldest first."""
        return [q for q in self.requested(mission_id) if self.get(mission_id, q["unit"], q["kind"]) is None]

    # -- an approval binds what it was given for (Security Brief 3, SR3-2) ---------------------------------------------
    def gate_of(self, mission_id: str, unit_key: str) -> str:
        """The plan's gate a gate question is about: itself, or the gate a fresh approval (``<gate>#<n>``) is asked
        again for."""
        for asked in self.requested(mission_id):
            if asked["kind"] == "gate" and asked["unit"] == unit_key:
                return str(asked["detail"].get("reapproves") or unit_key)
        return unit_key

    def effective(self, mission_id: str, gate: str) -> tuple[dict | None, dict | None]:
        """The gate's latest question (its own, or the latest fresh approval asked since) and its answer (None while it
        is unanswered). Only the latest counts: each fresh question records the answer it supersedes."""
        chain = [q for q in self.requested(mission_id)
                 if q["kind"] == "gate" and (q["unit"] == gate or q["detail"].get("reapproves") == gate)]
        if not chain:
            return None, None
        return chain[-1], self.get(mission_id, chain[-1]["unit"], "gate")

    def binding(self, mission_id: str, decision_id: str) -> dict | None:
        """What a gate's answer was given for, as recorded with it (None when nothing was)."""
        for event in self._missions.events(mission_id, kind="approval.decided"):
            if event["detail"].get("decision_id") == decision_id:
                found = event["detail"].get("binding")
                return found if isinstance(found, dict) else None
        return None

    def asked_binding(self, mission_id: str, unit_key: str) -> dict | None:
        """What a gate question asked about, as stored when it was asked (review I3: an answer covers the work its
        question described, never the work as it is when answered); None for a question that stored none."""
        for event in self._missions.events(mission_id, kind="approval.requested"):
            if event["detail"].get("unit") == unit_key and event["detail"].get("kind") == "gate":
                found = event["detail"].get("binding")
                return found if isinstance(found, dict) else None
        return None

    def reask(self, mission_id: str, gate: str, *, supersedes: str, question: str, detail: dict | None = None,
              binding: dict | None = None) -> str:
        """Ask a person to approve ``gate`` again, as ``<gate>#<n>``: the answer ``supersedes`` no longer counts, and
        this question records that it is superseded (append-only, like every question). Returns the new question's
        key."""
        asked, _ = self.effective(mission_id, gate)
        if asked is not None and asked["detail"].get("supersedes") == supersedes:
            return asked["unit"]  # already asked again for this answer (a step that was interrupted)
        count = sum(1 for q in self.requested(mission_id)
                    if q["kind"] == "gate" and (q["unit"] == gate or q["detail"].get("reapproves") == gate))
        key = f"{gate}#{count + 1}"
        self.request(mission_id, key, "gate", question=question,
                     detail={**(detail or {}), "reapproves": gate, "supersedes": supersedes}, binding=binding)
        return key

    # -- answers ------------------------------------------------------------------------------------------
    def get(self, mission_id: str, unit_key: str, kind: str) -> dict | None:
        self._missions.get(mission_id)  # NOT_FOUND for an unknown or foreign mission
        row = self._conn.execute(
            "SELECT * FROM mission_decisions WHERE mission_id=? AND unit_key=? AND kind=?" + _OWNED,
            (str(mission_id), unit_key, kind, self.owner)).fetchone()
        return dict(row) if row is not None else None

    def all(self, mission_id: str) -> list[dict]:
        self._missions.get(mission_id)
        rows = self._conn.execute("SELECT * FROM mission_decisions WHERE mission_id=?" + _OWNED + " ORDER BY rowid",
                                  (str(mission_id), self.owner)).fetchall()
        return [dict(r) for r in rows]

    def record(self, mission_id: str, unit_key: str, kind: str, decision: str, *, reason: str, actor: str,
               decided_by: str = "", actor_type: str = "user", required_by_policy: bool = True,
               timing: tuple[datetime, datetime] | None = None, binding: dict | None = None,
               minutes: Any = None, principal: dict | None = None) -> dict:
        """A person's answer to a question that was asked and not yet answered, with its event and its Ledger §6
        intervention, in one transaction. Refused (nothing written) for any actor but ``human``, an answer the
        question does not take, an empty reason, or a question never asked or already answered. ``decided_by`` is who
        decided as the caller knows them (plan ruling P2.3; the owner when not given); the time is never inferred.
        ``binding``: what a gate's answer was given for (its contract and effect digests, Security Brief 3), kept on
        its event; an approval with none recorded covers nothing that acts. ``minutes``: how long the person says the
        answer took (R5; ``declared_minutes``), kept on its event only when given. ``principal``: who answered and
        through which channel (``principal()``, audit 010 WF-013), kept on its event and its intervention."""
        if actor != HUMAN:
            raise ValueError(f"only a human decides; {actor!r} may not (a model's agreement is never a person's)")
        if kind not in ANSWERS:
            raise ValueError(f"unknown question kind {kind!r}")
        if decision not in ANSWERS[kind]:
            raise ValueError(f"a {'gate is approved or rejected' if kind == 'gate' else 'disagreement is upheld or set aside'}"
                             f", not {decision!r}")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("a decision gives its reason")
        minutes = declared_minutes(minutes)
        mission = self._missions.get(mission_id)
        asked = [q for q in self.requested(mission_id) if q["unit"] == unit_key and q["kind"] == kind]
        # review of V4: a finished mission never changes, nor takes a decision; the one exception (#187, R-1) is the
        # question about retiring or promoting a learned probe: its future, never the closed result
        if mission["status"] in FINISHED and not any(
                q["detail"].get("retires") or q["detail"].get("promotes") for q in asked):
            raise ValueError(f"mission {mission_id} is {mission['status']}: it takes no decision")
        if not asked:
            raise ValueError(f"unit {unit_key!r} is not waiting for a human's {kind} decision")
        if self.get(mission_id, unit_key, kind) is not None:
            raise ValueError(f"unit {unit_key!r}'s {kind} was already decided")
        decision_id = f"dc_{uuid.uuid4().hex[:12]}"
        intervention = HumanIntervention(kind=KIND_OF[kind], actor_type=actor_type,
                                         decided_by=decided_by or self.owner, required_by_policy=required_by_policy,
                                         reason=reason[:2000], outcome=decision, work_unit_id=unit_key,
                                         decision_id=decision_id,
                                         metadata={"principal": principal} if principal else {})
        if timing is not None:
            detail = intervention.as_detail()
            for key in ("started_at", "ended_at", "duration_seconds", "duration_source"):
                detail.pop(key)
            intervention = HumanIntervention.timed(started=timing[0], ended=timing[1], **detail)
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO mission_decisions (decision_id, mission_id, unit_key, kind, decision, reason, actor,"
                " decided_at) SELECT ?, m.mission_id, ?, ?, ?, ?, ?, ? FROM missions m WHERE m.mission_id=?"
                " AND EXISTS (SELECT 1 FROM ventures v WHERE v.venture_id = m.venture_id AND v.owner = ?)",
                (decision_id, unit_key, kind, decision, reason[:2000], HUMAN,
                 datetime.now(UTC).isoformat(timespec="seconds"), str(mission_id), self.owner))
            if cur.rowcount == 0:
                raise LookupError(NOT_FOUND)
            self._missions.append_event(mission["mission_id"], "approval.decided", HUMAN,
                                        {"unit": unit_key, "kind": kind, "decision": decision, "reason": reason[:2000],
                                         "decision_id": decision_id, **({"binding": binding} if binding else {}),
                                         **({"minutes_declared": minutes} if minutes is not None else {}),
                                         **({"principal": principal} if principal else {})},
                                        f"decided:{kind}:{unit_key}")
            self.intervene_within(mission["mission_id"], intervention)
        return self.get(mission_id, unit_key, kind) or {}

    def intervene_within(self, mission_id: str, intervention: HumanIntervention, *, idem: str = "") -> str:
        """The intervention's row inside the CALLER's transaction (Ledger §6: written with what it records). Its
        idempotency key is its decision's, or ``idem``."""
        key = idem or f"intervention:{intervention.decision_id or uuid.uuid4().hex[:12]}"
        return self._missions.append_event(mission_id, "intervention", HUMAN, intervention.as_detail(), key)

    def interventions(self, mission_id: str) -> list[HumanIntervention]:
        """The mission's interventions, oldest first (``NOT_FOUND`` for another owner's mission)."""
        return [HumanIntervention.from_detail(e["detail"], id=e["evidence_id"], mission_id=str(mission_id),
                                              recorded_at=e["at"])
                for e in self._missions.events(mission_id, kind="intervention")]

    def confirm(self, mission_id: str, *, acceptance_sha: str, reason: str, actor: str, decided_by: str = "",
                required_by_policy: bool, timing: tuple[datetime, datetime] | None = None,
                principal: dict | None = None) -> HumanIntervention:
        """A person confirms what counts as done (E7d; Ledger §6 ``clarification``), bound to the contract's digest, in
        its own transaction. Refused for any actor but ``human``, an empty reason, or a contract already confirmed.
        ``principal``: who confirmed and through which channel (``principal()``, audit 010 WF-013)."""
        if actor != HUMAN:
            raise ValueError(f"only a person confirms what counts as done; {actor!r} may not")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("a confirmation gives its reason")
        if any(iv.kind == "clarification" and iv.metadata.get("acceptance_sha256") == acceptance_sha
               for iv in self.interventions(mission_id)):
            raise ValueError("what counts as done was already confirmed")
        found = HumanIntervention(kind="clarification", actor_type="user", decided_by=decided_by or self.owner,
                                  required_by_policy=required_by_policy, reason=reason[:2000], outcome="confirmed",
                                  metadata={"acceptance_sha256": acceptance_sha,
                                            **({"principal": principal} if principal else {})})
        if timing is not None:
            detail = found.as_detail()
            for key in ("started_at", "ended_at", "duration_seconds", "duration_source"):
                detail.pop(key)
            found = HumanIntervention.timed(started=timing[0], ended=timing[1], **detail)
        with self._conn:
            self.intervene_within(str(mission_id), found, idem=f"intervention:confirm:{acceptance_sha[:16]}")
        return self.interventions(mission_id)[-1]


__all__ = ["ANSWERS", "CHANNELS", "HUMAN", "KIND_OF", "MAX_MINUTES", "Decisions", "declared_minutes", "human_time",
           "principal", "said_minutes", "wait_seconds"]
