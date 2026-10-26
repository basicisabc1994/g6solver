# SPDX-License-Identifier: Apache-2.0
"""Mailbox: durable messages between agents, one SQLite file under G6's state folder (``g6.core.storage.open_db``).

A **group** has named members and, if it is typed, a message schema (field names, each ``text``, ``integer``,
``number``, ``boolean``, ``list`` or ``object``; every field required and no other allowed), checked when a message is
sent: one that does not fit is refused, never stored. A **message** is sent by a member to one other member or to the
whole group; its id is unique and rises, and a ``dedupe_key`` makes a retried send store it once.

A member **receives** the messages for it (to it, or to the group, and not its own) after its cursor, oldest first, a
page at a time: ``next_cursor`` and ``more`` page through all of them, however many (the archive returned only the
newest 50). Each message says how many times it has been delivered to that member. A member **acknowledges** up to an
id it has received; until then the messages come back on the next receive, so delivery is at least once and a crash
loses nothing, and the reader drops a repeat by its id. An acknowledgement past what the member has received, or one
that spans any message for it that was never delivered to it (a receive with a later ``since_cursor`` skips some), is
refused in the same transaction that would move the cursor, so nothing is skipped unseen. **History** is every message
of a group with whom it was delivered to and who acknowledged it.

A group holds at most :data:`MAX_MESSAGES_PER_GROUP` messages (10,000) and the store at most :data:`MAX_STORE_BYTES`
of message bodies (256 MiB); a send past either is refused (``G6_E_MAILBOX_GROUP_FULL``, ``G6_E_MAILBOX_STORE_FULL``)
and stores nothing. A body holds at most :data:`MAX_BODY_BYTES` (64 KiB).

The sender is the name the caller declares: it is checked against the group's members, not authenticated. A store op
answers what it did (stored, delivered, acknowledged, with ids), a fact of G6's own record: ``qualified-draft``, never
``verified`` (AGENT_NANOCLAW-01, AGENT_OPENCLAW-01).
"""
from __future__ import annotations

import json
import re
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from g6.core.reliability_envelope import WarningCard
from g6.core.storage import open_db, state_path

DEFAULT_FILE = ("agents", "mailbox.db")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
MAX_MEMBERS = 50
MAX_BODY_BYTES = 64 * 1024
#: The most messages one group holds, and the most message bodies the whole store holds, in bytes.
MAX_MESSAGES_PER_GROUP = 10_000
MAX_STORE_BYTES = 256 * 1024 * 1024
MAX_PAGE = 200
FieldType = Literal["text", "integer", "number", "boolean", "list", "object"]
_TYPES: dict[str, Any] = {"text": str, "integer": int, "number": float | int, "boolean": bool, "list": list,
                          "object": dict}
_P = "G6_E_MAILBOX_"
CODES = {name: _P + name for name in (
    "GROUP_CREATED", "GROUP_EXISTS", "NO_GROUP", "NOT_A_MEMBER", "BAD_NAME", "BAD_MESSAGE", "TOO_LARGE", "STORED",
    "ALREADY_STORED", "DELIVERED", "ACKED", "ACK_UNSEEN", "HISTORY", "GROUP_DELETED", "GROUP_FULL", "STORE_FULL")}
_ACTIONS = {
    CODES["GROUP_CREATED"]: "Send with send, and have each member receive and then ack.",
    CODES["GROUP_EXISTS"]: "Pick another group name, or delete the group first.",
    CODES["NO_GROUP"]: "Create the group with create_group first.",
    CODES["NOT_A_MEMBER"]: "Use a member's name, as the group was created with.",
    CODES["BAD_NAME"]: "Names are letters, digits and _ . : -, at most 64.",
    CODES["BAD_MESSAGE"]: "Send a body that fits the group's schema: every field, of its type, and no other.",
    CODES["TOO_LARGE"]: "Send a smaller body: at most 64 KiB as JSON.",
    CODES["STORED"]: "The recipients receive it with receive; keep the id.",
    CODES["ALREADY_STORED"]: "It was stored before, under this id; nothing was added.",
    CODES["DELIVERED"]: "Handle each message (drop one whose id you have handled), then ack up to the last one.",
    CODES["ACKED"]: "Receive again for what came after.",
    CODES["ACK_UNSEEN"]: "Receive first, from your cursor (no since_cursor), and ack only up to the last id you received.",
    CODES["GROUP_FULL"]: "Have the members receive and ack, then delete the group (or start another) to make room.",
    CODES["STORE_FULL"]: "Delete groups whose messages are handled, to make room in the store.",
    CODES["HISTORY"]: "Read it; history acknowledges nothing.",
    CODES["GROUP_DELETED"]: "The group and its messages are gone.",
}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (name TEXT PRIMARY KEY, members TEXT NOT NULL, schema TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, group_name TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT,
    body TEXT NOT NULL, dedupe_key TEXT, sent REAL NOT NULL, UNIQUE (group_name, sender, dedupe_key));
CREATE INDEX IF NOT EXISTS messages_by_group ON messages (group_name, id);
CREATE TABLE IF NOT EXISTS deliveries (
    group_name TEXT NOT NULL, member TEXT NOT NULL, message_id INTEGER NOT NULL, times INTEGER NOT NULL,
    first REAL NOT NULL, PRIMARY KEY (group_name, member, message_id));
CREATE TABLE IF NOT EXISTS cursors (
    group_name TEXT NOT NULL, member TEXT NOT NULL, acked_upto INTEGER NOT NULL, PRIMARY KEY (group_name, member));
"""


def reply(op: str, state: str, code: str, message: str, **fields: Any) -> dict[str, Any]:
    """One op's answer in the library envelope."""
    card = WarningCard(code=code, message=message, suggested_action=_ACTIONS.get(code, "Read the reason."),
                       severity="blocked" if state == "blocked-escalated" else "amber").model_dump()
    return {"component": "agent_nanoclaw", "op": op, **fields, "code": code, "message": message,
            "completion_state": state, "warning_card": card,
            "evidence": [{"kind": f"mailbox_{op}", "code": code, "fact_of": "G6's own message store",
                          "independent_check": None}]}


def _refused(op: str, code: str, message: str, **fields: Any) -> dict[str, Any]:
    return reply(op, "blocked-escalated", CODES[code], message, **fields)


def _schema_model(schema: dict[str, str]) -> type[BaseModel]:
    fields = {name: (_TYPES[kind], ...) for name, kind in schema.items()}
    return create_model("GroupMessage", __config__=ConfigDict(extra="forbid", strict=True), **fields)  # type: ignore[call-overload]


class Mailbox:
    """See the module docstring. ``path`` defaults to ``$G6_HOME/agents/mailbox.db``."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else state_path(*DEFAULT_FILE)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Any:
        with closing(open_db(self.path)) as db:
            with db:
                yield db

    @staticmethod
    def _bad(*names: str | None) -> str:
        bad = [n for n in names if n is not None and not NAME.fullmatch(n)]
        return f"{bad[0]!r} is not a name" if bad else ""

    def _group(self, db: Any, name: str) -> dict[str, Any] | None:
        row = db.execute("SELECT name, members, schema FROM groups WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return {"name": row["name"], "members": json.loads(row["members"]),
                "schema": json.loads(row["schema"]) if row["schema"] else None}

    def create_group(self, group: str, members: list[str], *, schema: dict[str, FieldType] | None = None
                     ) -> dict[str, Any]:
        """A new group of ``members``, typed by ``schema`` when one is given."""
        op = "create_group"
        why = self._bad(group, *members)
        if why or not members or len(members) > MAX_MEMBERS or len(set(members)) != len(members):
            return _refused(op, "BAD_NAME", why or f"a group needs 1 to {MAX_MEMBERS} members, each named once")
        if schema is not None:
            bad = [k for k, v in schema.items() if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", k) or v not in _TYPES]
            if bad or not schema:
                return _refused(op, "BAD_MESSAGE", f"the schema's fields must be names, each of type "
                                                   f"{', '.join(_TYPES)}; check {bad or 'it is empty'}")
        with self._db() as db:
            if self._group(db, group) is not None:
                return _refused(op, "GROUP_EXISTS", f"there is already a group {group!r}")
            db.execute("INSERT INTO groups (name, members, schema, created) VALUES (?, ?, ?, ?)",
                       (group, json.dumps(members), json.dumps(schema) if schema else None, time.time()))
        return reply(op, "qualified-draft", CODES["GROUP_CREATED"], f"the group {group!r} was created with "
                     f"{len(members)} member(s){', typed' if schema else ''}", group=group, members=members,
                     schema=schema)

    def send(self, group: str, sender: str, body: Any, *, to: str | None = None,
             dedupe_key: str | None = None) -> dict[str, Any]:
        """Store ``body`` from ``sender`` for ``to`` (one member) or, without it, the whole group."""
        op = "send"
        why = self._bad(group, sender, to, dedupe_key)
        if why:
            return _refused(op, "BAD_NAME", why)
        try:
            text = json.dumps(body)
        except (TypeError, ValueError) as exc:
            return _refused(op, "BAD_MESSAGE", f"the body is not JSON: {exc}")
        if len(text.encode("utf-8")) > MAX_BODY_BYTES:
            return _refused(op, "TOO_LARGE", f"the body is larger than {MAX_BODY_BYTES} bytes")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")   # the caps are checked and the message stored in one transaction
            found = self._group(db, group)
            if found is None:
                return _refused(op, "NO_GROUP", f"there is no group {group!r}")
            strangers = [n for n in (sender, to) if n is not None and n not in found["members"]]
            if strangers:
                return _refused(op, "NOT_A_MEMBER", f"{strangers[0]!r} is not a member of {group!r}")
            if found["schema"]:
                try:
                    _schema_model(found["schema"]).model_validate(body)
                except ValidationError as exc:
                    said = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'body'}: {e['msg']}" for e in exc.errors())
                    return _refused(op, "BAD_MESSAGE", f"the message does not fit the group's schema: {said}"[:500])
            if dedupe_key is not None:
                held = db.execute("SELECT id FROM messages WHERE group_name = ? AND sender = ? AND dedupe_key = ?",
                                  (group, sender, dedupe_key)).fetchone()
                if held is not None:
                    return reply(op, "qualified-draft", CODES["ALREADY_STORED"], f"a message from {sender!r} with "
                                 f"this dedupe_key was stored before, as {held['id']}; nothing was added",
                                 group=group, id=held["id"])
            count = db.execute("SELECT COUNT(*) AS n FROM messages WHERE group_name = ?", (group,)).fetchone()["n"]
            if count >= MAX_MESSAGES_PER_GROUP:
                return _refused(op, "GROUP_FULL", f"the group {group!r} holds {count} messages, its most "
                                                  f"({MAX_MESSAGES_PER_GROUP}); nothing was stored")
            size = db.execute("SELECT COALESCE(SUM(LENGTH(CAST(body AS BLOB))), 0) AS n FROM messages").fetchone()["n"]
            if size + len(text.encode("utf-8")) > MAX_STORE_BYTES:
                return _refused(op, "STORE_FULL", f"the store holds {size} bytes of messages, and this one would pass "
                                                  f"its most ({MAX_STORE_BYTES} bytes); nothing was stored")
            cur = db.execute("INSERT INTO messages (group_name, sender, recipient, body, dedupe_key, sent) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (group, sender, to, text, dedupe_key, time.time()))
            new = cur.lastrowid
        return reply(op, "qualified-draft", CODES["STORED"], f"stored as message {new} for "
                     f"{repr(to) if to else 'the whole group'}", group=group, id=new, to=to)

    def receive(self, group: str, member: str, *, since_cursor: int | None = None, limit: int = 50
                ) -> dict[str, Any]:
        """The messages for ``member`` after ``since_cursor`` (its acknowledged cursor by default), oldest first."""
        op = "receive"
        why = self._bad(group, member)
        if why:
            return _refused(op, "BAD_NAME", why)
        limit = max(1, min(int(limit), MAX_PAGE))
        with self._db() as db:
            found = self._group(db, group)
            if found is None:
                return _refused(op, "NO_GROUP", f"there is no group {group!r}")
            if member not in found["members"]:
                return _refused(op, "NOT_A_MEMBER", f"{member!r} is not a member of {group!r}")
            acked = self._cursor(db, group, member)
            after = acked if since_cursor is None else max(int(since_cursor), acked)
            rows = db.execute("SELECT id, sender, recipient, body, sent FROM messages WHERE group_name = ? AND id > ? "
                              "AND sender != ? AND (recipient IS NULL OR recipient = ?) ORDER BY id LIMIT ?",
                              (group, after, member, member, limit + 1)).fetchall()
            page, more = rows[:limit], len(rows) > limit
            now, out = time.time(), []
            for row in page:
                db.execute("INSERT INTO deliveries (group_name, member, message_id, times, first) VALUES (?, ?, ?, 1, ?)"
                           " ON CONFLICT (group_name, member, message_id) DO UPDATE SET times = times + 1",
                           (group, member, row["id"], now))
                times = db.execute("SELECT times FROM deliveries WHERE group_name = ? AND member = ? AND "
                                   "message_id = ?", (group, member, row["id"])).fetchone()["times"]
                out.append({"id": row["id"], "sender": row["sender"], "sender_is": "declared, not authenticated",
                            "to": row["recipient"], "body": json.loads(row["body"]), "sent": row["sent"],
                            "deliveries": times})
        cursor = out[-1]["id"] if out else after
        return reply(op, "qualified-draft", CODES["DELIVERED"], f"{len(out)} message(s) for {member!r}"
                     f"{', and more after them' if more else ''}; ack up to the last id once each is handled",
                     group=group, member=member, messages=out, next_cursor=cursor, more=more, acked_upto=acked)

    @staticmethod
    def _cursor(db: Any, group: str, member: str) -> int:
        row = db.execute("SELECT acked_upto FROM cursors WHERE group_name = ? AND member = ?",
                         (group, member)).fetchone()
        return int(row["acked_upto"]) if row else 0

    def ack(self, group: str, member: str, *, upto: int) -> dict[str, Any]:
        """Acknowledge every message for ``member`` up to ``upto``, which it must have received."""
        op = "ack"
        why = self._bad(group, member)
        if why:
            return _refused(op, "BAD_NAME", why)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")   # the check and the cursor's move are one transaction
            found = self._group(db, group)
            if found is None:
                return _refused(op, "NO_GROUP", f"there is no group {group!r}")
            if member not in found["members"]:
                return _refused(op, "NOT_A_MEMBER", f"{member!r} is not a member of {group!r}")
            seen = db.execute("SELECT MAX(message_id) AS top FROM deliveries WHERE group_name = ? AND member = ?",
                              (group, member)).fetchone()["top"] or 0
            if int(upto) > seen:
                return _refused(op, "ACK_UNSEEN", f"{member!r} has received messages up to {seen}, not {upto}: "
                                                  "acknowledging more would skip messages it never saw")
            held = self._cursor(db, group, member)
            unseen = db.execute(
                "SELECT COUNT(*) AS n FROM messages m WHERE m.group_name = ? AND m.id > ? AND m.id <= ? AND "
                "m.sender != ? AND (m.recipient IS NULL OR m.recipient = ?) AND NOT EXISTS (SELECT 1 FROM deliveries d "
                "WHERE d.group_name = m.group_name AND d.member = ? AND d.message_id = m.id)",
                (group, held, int(upto), member, member, member)).fetchone()["n"]
            if unseen:
                return _refused(op, "ACK_UNSEEN", f"acknowledging up to {upto} would skip {unseen} message(s) for "
                                                  f"{member!r} that were never delivered to it; receive them first")
            done = max(held, int(upto))
            db.execute("INSERT INTO cursors (group_name, member, acked_upto) VALUES (?, ?, ?) ON CONFLICT "
                       "(group_name, member) DO UPDATE SET acked_upto = excluded.acked_upto", (group, member, done))
        return reply(op, "qualified-draft", CODES["ACKED"], f"{member!r} has acknowledged every message up to {done}",
                     group=group, member=member, acked_upto=done)

    def history(self, group: str, *, limit: int = 100) -> dict[str, Any]:
        """Every message of ``group``, oldest first, with whom each was delivered to and who acknowledged it."""
        op = "history"
        why = self._bad(group)
        if why:
            return _refused(op, "BAD_NAME", why)
        with self._db() as db:
            found = self._group(db, group)
            if found is None:
                return _refused(op, "NO_GROUP", f"there is no group {group!r}")
            rows = db.execute("SELECT id, sender, recipient, body, sent FROM messages WHERE group_name = ? ORDER BY id "
                              "LIMIT ?", (group, max(1, min(int(limit), 1000)))).fetchall()
            cursors = {r["member"]: r["acked_upto"] for r in db.execute(
                "SELECT member, acked_upto FROM cursors WHERE group_name = ?", (group,)).fetchall()}
            out = []
            for row in rows:
                times = {r["member"]: r["times"] for r in db.execute(
                    "SELECT member, times FROM deliveries WHERE group_name = ? AND message_id = ?",
                    (group, row["id"])).fetchall()}
                out.append({"id": row["id"], "sender": row["sender"], "to": row["recipient"],
                            "body": json.loads(row["body"]), "sent": row["sent"], "delivered_to": times,
                            "acked_by": sorted(m for m, upto in cursors.items() if upto >= row["id"] and m in times)})
        return reply(op, "qualified-draft", CODES["HISTORY"], f"{len(out)} message(s) in {group!r}", group=group,
                     members=found["members"], schema=found["schema"], messages=out)

    def delete_group(self, group: str) -> dict[str, Any]:
        """Delete ``group`` with its messages, saying how many went and how many were not yet acknowledged."""
        op = "delete_group"
        why = self._bad(group)
        if why:
            return _refused(op, "BAD_NAME", why)
        with self._db() as db:
            found = self._group(db, group)
            if found is None:
                return _refused(op, "NO_GROUP", f"there is no group {group!r}")
            cursors = {r["member"]: r["acked_upto"] for r in db.execute(
                "SELECT member, acked_upto FROM cursors WHERE group_name = ?", (group,)).fetchall()}
            rows = db.execute("SELECT id, sender, recipient FROM messages WHERE group_name = ?", (group,)).fetchall()
            unacked = sum(1 for r in rows if any(
                cursors.get(m, 0) < r["id"] for m in found["members"]
                if m != r["sender"] and (r["recipient"] is None or r["recipient"] == m)))
            for table in ("messages", "deliveries", "cursors"):
                db.execute(f"DELETE FROM {table} WHERE group_name = ?", (group,))
            db.execute("DELETE FROM groups WHERE name = ?", (group,))
        return reply(op, "qualified-draft", CODES["GROUP_DELETED"], f"the group {group!r} was deleted with "
                     f"{len(rows)} message(s), {unacked} of them not yet acknowledged by every recipient",
                     group=group, messages_deleted=len(rows), unacknowledged=unacked)


__all__ = ["CODES", "DEFAULT_FILE", "MAX_BODY_BYTES", "MAX_MESSAGES_PER_GROUP", "MAX_STORE_BYTES", "Mailbox", "reply"]