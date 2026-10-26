# SPDX-License-Identifier: Apache-2.0
"""Checkpoints: LangGraph's checkpoint store for G6, one SQLite file under G6's state folder.

It implements LangGraph's ``BaseCheckpointSaver`` (``get_tuple``, ``list``, ``put``, ``put_writes``,
``delete_thread``) over ``g6.core.storage.open_db`` (WAL, foreign keys), so a graph's state after each super-step, and
the writes of the tasks that finished within a super-step that failed, are on disk before the next one starts (the
runtime runs graphs with ``durability="sync"``). A run that is stopped, killed or fails resumes from there, in this
process or another.

Checkpoints are read back through LangGraph's own serializer in its strict form: no pickle, and only the types
LangGraph lists as safe, plus any the person names (``allowed_types``), may be rebuilt, so a checkpoint file someone
changed cannot make G6 import and build an arbitrary class (the archive kept checkpoints in one JSON file under the home
folder, rewritten whole on every write, and its Python path kept them in memory: AGENT_LANGGRAPH-02).

Beside LangGraph's two tables it keeps ``g6_threads``: each thread's id, the digest of the graph that started it (a
resume with a different graph is refused) and when it was started and last written.

Sizes are capped before anything is written: one serialised checkpoint, and one batch of a task's writes, hold at
most :data:`MAX_CHECKPOINT_BYTES` (1 MiB), and a thread's checkpoints and writes together at most
:data:`MAX_THREAD_BYTES` (32 MiB); past either, the write is refused with ``G6_E_AGENT_LANGGRAPH_TOO_LARGE`` and
nothing is written. ``list`` applies its ``limit`` in SQL, so it reads back no more checkpoints than it returns.
"""
from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import AsyncIterator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
    writes_sort_key,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from g6.agents._runtime import AgentRefusal
from g6.agents.agent_langgraph.spec import TOO_LARGE
from g6.core.storage import open_db, state_path

#: The most one serialised checkpoint, or one batch of a task's writes, may hold: 1 MiB.
MAX_CHECKPOINT_BYTES = 1024 * 1024
#: The most a thread's checkpoints and writes may hold together: 32 MiB.
MAX_THREAD_BYTES = 32 * 1024 * 1024
#: Where the checkpoints live unless a path is given.
DEFAULT_FILE = ("agents", "langgraph_checkpoints.db")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id));
CREATE TABLE IF NOT EXISTS writes (
    thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL, task_path TEXT NOT NULL DEFAULT '', idx INTEGER NOT NULL, channel TEXT NOT NULL,
    type TEXT, value BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx));
CREATE TABLE IF NOT EXISTS g6_threads (
    thread_id TEXT PRIMARY KEY, graph_digest TEXT NOT NULL, started REAL NOT NULL, updated REAL NOT NULL);
"""
_ASYNC = "G6's checkpoint store is synchronous: run the graph with stream or invoke, not their async forms"


def strict_serializer(allowed_types: Iterable[tuple[str, str]] = ()) -> JsonPlusSerializer:
    """LangGraph's serializer with no pickle, rebuilding only the types LangGraph lists as safe and ``allowed_types``
    (``(module, class name)`` pairs)."""
    allowed = {tuple(t) for t in allowed_types}
    return JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=allowed or None)


class Checkpoints(BaseCheckpointSaver[str]):
    """See the module docstring. ``path`` defaults to ``$G6_HOME/agents/langgraph_checkpoints.db``."""

    def __init__(self, path: str | Path | None = None, *, allowed_types: Iterable[tuple[str, str]] = ()) -> None:
        super().__init__(serde=strict_serializer(allowed_types))
        self.path = Path(path) if path is not None else state_path(*DEFAULT_FILE)
        self.conn = open_db(self.path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self.lock = threading.Lock()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    @contextmanager
    def _cursor(self, commit: bool = False) -> Iterator[Any]:
        with self.lock:
            cur = self.conn.cursor()
            try:
                yield cur
                if commit:
                    self.conn.commit()
            finally:
                cur.close()

    # --- the threads G6 keeps ------------------------------------------------------------------------------------------

    def thread(self, thread_id: str) -> dict[str, Any] | None:
        """The thread's record (its graph's digest, when it was started and last written), or ``None``."""
        with self._cursor() as cur:
            row = cur.execute("SELECT thread_id, graph_digest, started, updated FROM g6_threads WHERE thread_id = ?",
                              (thread_id,)).fetchone()
        return dict(row) if row else None

    def claim(self, thread_id: str, digest: str) -> None:
        """Record a new thread, started by the graph with ``digest``."""
        now = time.time()
        with self._cursor(commit=True) as cur:
            cur.execute("INSERT INTO g6_threads (thread_id, graph_digest, started, updated) VALUES (?, ?, ?, ?)",
                        (thread_id, digest, now, now))

    @staticmethod
    def _room(cur: Any, thread_id: str, adding: int, what: str) -> None:
        """Refuse ``adding`` bytes more for the thread when one write, or the thread, would pass its cap."""
        if adding > MAX_CHECKPOINT_BYTES:
            raise AgentRefusal(TOO_LARGE, f"{what} of {adding} bytes would pass the cap of {MAX_CHECKPOINT_BYTES} "
                                          "bytes, so nothing was written")
        held = cur.execute("SELECT COALESCE(SUM(LENGTH(checkpoint)), 0) + COALESCE(SUM(LENGTH(metadata)), 0) AS n "
                           "FROM checkpoints WHERE thread_id = ?", (thread_id,)).fetchone()["n"]
        held += cur.execute("SELECT COALESCE(SUM(LENGTH(value)), 0) AS n FROM writes WHERE thread_id = ?",
                            (thread_id,)).fetchone()["n"]
        if held + adding > MAX_THREAD_BYTES:
            raise AgentRefusal(TOO_LARGE, f"the thread {thread_id!r} holds {held} bytes, and {what} of {adding} more "
                                          f"would pass its cap of {MAX_THREAD_BYTES} bytes, so nothing was written")

    # --- LangGraph's saver ---------------------------------------------------------------------------------------------

    def _pending(self, cur: Any, thread_id: str, ns: str, checkpoint_id: str) -> list[tuple[str, str, Any]]:
        rows = cur.execute("SELECT task_id, channel, type, value, task_path, idx FROM writes WHERE thread_id = ? AND "
                           "checkpoint_ns = ? AND checkpoint_id = ?", (thread_id, ns, checkpoint_id)).fetchall()
        ordered = sorted(rows, key=lambda r: writes_sort_key(r["task_path"], r["task_id"], r["idx"]))
        return [(r["task_id"], r["channel"], self.serde.loads_typed((r["type"], r["value"]))) for r in ordered]

    def _tuple(self, cur: Any, row: Any) -> CheckpointTuple:
        thread_id, ns, checkpoint_id = row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]
        parent = row["parent_checkpoint_id"]
        return CheckpointTuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint_id}},
            self.serde.loads_typed((row["type"], row["checkpoint"])),
            json.loads(row["metadata"]) if row["metadata"] is not None else {},
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": parent}} if parent else None,
            self._pending(cur, thread_id, ns, checkpoint_id))

    def get_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        conf = config["configurable"]
        thread_id, ns = str(conf["thread_id"]), conf.get("checkpoint_ns", "")
        with self._cursor() as cur:
            if checkpoint_id := get_checkpoint_id(config):
                row = cur.execute("SELECT * FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND "
                                  "checkpoint_id = ?", (thread_id, ns, checkpoint_id)).fetchone()
            else:
                row = cur.execute("SELECT * FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? ORDER BY "
                                  "checkpoint_id DESC LIMIT 1", (thread_id, ns)).fetchone()
            return self._tuple(cur, row) if row else None

    def list(self, config: dict[str, Any] | None, *, filter: dict[str, Any] | None = None,
             before: dict[str, Any] | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        wheres, values = [], []
        if config is not None:
            conf = config["configurable"]
            wheres.append("thread_id = ?")
            values.append(str(conf["thread_id"]))
            if conf.get("checkpoint_ns") is not None:
                wheres.append("checkpoint_ns = ?")
                values.append(conf["checkpoint_ns"])
            if checkpoint_id := get_checkpoint_id(config):
                wheres.append("checkpoint_id = ?")
                values.append(checkpoint_id)
        if before is not None:
            wheres.append("checkpoint_id < ?")
            values.append(get_checkpoint_id(before))
        query = "SELECT * FROM checkpoints" + (" WHERE " + " AND ".join(wheres) if wheres else "")
        query += " ORDER BY checkpoint_id DESC"
        if limit is not None and not filter:   # the limit in SQL, so no row past it is read, let alone deserialised
            query += " LIMIT ?"
            values.append(max(0, int(limit)))
        with self._cursor() as cur:
            rows = cur.execute(query, values).fetchall()
        shown = 0
        for row in rows:
            if limit is not None and shown >= limit:
                return
            meta = json.loads(row["metadata"]) if row["metadata"] is not None else {}
            if filter and any(meta.get(k) != v for k, v in filter.items()):
                continue
            with self._cursor() as cur:
                item = self._tuple(cur, row)   # only a row that is returned is deserialised
            shown += 1
            yield item

    def put(self, config: dict[str, Any], checkpoint: Checkpoint, metadata: CheckpointMetadata,
            new_versions: ChannelVersions) -> dict[str, Any]:
        conf = config["configurable"]
        thread_id, ns = str(conf["thread_id"]), conf.get("checkpoint_ns", "")
        kind, blob = self.serde.dumps_typed(checkpoint)
        meta = json.dumps(get_checkpoint_metadata(config, metadata), ensure_ascii=False).encode("utf-8", "ignore")
        with self._cursor(commit=True) as cur:
            self._room(cur, thread_id, len(blob) + len(meta), "a checkpoint")
            cur.execute("INSERT OR REPLACE INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
                        "parent_checkpoint_id, type, checkpoint, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (thread_id, ns, checkpoint["id"], conf.get("checkpoint_id"), kind, blob, meta))
            cur.execute("UPDATE g6_threads SET updated = ? WHERE thread_id = ?", (time.time(), thread_id))
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config: dict[str, Any], writes: Sequence[tuple[str, Any]], task_id: str,
                   task_path: str = "") -> None:
        conf = config["configurable"]
        verb = "INSERT OR REPLACE" if all(w[0] in WRITES_IDX_MAP for w in writes) else "INSERT OR IGNORE"
        rows = [(str(conf["thread_id"]), str(conf.get("checkpoint_ns", "")), str(conf["checkpoint_id"]), task_id,
                 task_path, WRITES_IDX_MAP.get(channel, idx), channel, *self.serde.dumps_typed(value))
                for idx, (channel, value) in enumerate(writes)]
        with self._cursor(commit=True) as cur:
            self._room(cur, str(conf["thread_id"]), sum(len(r[-1] or b"") for r in rows), "a task's writes")
            cur.executemany(f"{verb} INTO writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, "
                            "channel, type, value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def delete_thread(self, thread_id: str) -> None:
        with self._cursor(commit=True) as cur:
            for table in ("checkpoints", "writes", "g6_threads"):
                cur.execute(f"DELETE FROM {table} WHERE thread_id = ?", (str(thread_id),))

    def get_next_version(self, current: str | int | None, channel: None) -> str:
        number = 0 if current is None else current if isinstance(current, int) else int(str(current).split(".")[0])
        return f"{number + 1:032}.{random.random():016}"

    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        raise NotImplementedError(_ASYNC)

    async def alist(self, config: dict[str, Any] | None, *, filter: dict[str, Any] | None = None,
                    before: dict[str, Any] | None = None, limit: int | None = None) -> AsyncIterator[CheckpointTuple]:
        raise NotImplementedError(_ASYNC)
        yield  # pragma: no cover - makes this an async generator

    async def aput(self, config: dict[str, Any], checkpoint: Checkpoint, metadata: CheckpointMetadata,
                   new_versions: ChannelVersions) -> dict[str, Any]:
        raise NotImplementedError(_ASYNC)

    async def aput_writes(self, config: dict[str, Any], writes: Sequence[tuple[str, Any]], task_id: str,
                          task_path: str = "") -> None:
        raise NotImplementedError(_ASYNC)


__all__ = ["DEFAULT_FILE", "MAX_CHECKPOINT_BYTES", "MAX_THREAD_BYTES", "Checkpoints", "strict_serializer"]