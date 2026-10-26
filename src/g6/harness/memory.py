# SPDX-License-Identifier: Apache-2.0
"""What the harness remembers between runs: the checks that failed, per task family, and the outputs that failed them.

SQLite records in ``$G6_HOME/harness.db`` (so ``g6 state snapshot`` / ``rollback`` cover it). Facts only: a check
key, an output hash, a run id and a time. Nothing here decides anything; the harness reads it at plan time.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime

from g6.core.storage import open_db

_SCHEMA = ("CREATE TABLE IF NOT EXISTS failures (id INTEGER PRIMARY KEY, family TEXT NOT NULL, check_key TEXT NOT NULL,"
           " output_sha TEXT NOT NULL, run_id TEXT NOT NULL, recorded_at TEXT NOT NULL)")


class Memory:
    """``path`` defaults to ``$G6_HOME/harness.db``; ``":memory:"`` keeps nothing past the process."""

    def __init__(self, path: str | os.PathLike[str] = "harness.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = open_db(self._path)
            self._conn.execute(_SCHEMA)
            self._conn.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, family TEXT NOT NULL,"
                               " reply_json TEXT NOT NULL, recorded_at TEXT NOT NULL)")
            self._conn.execute("CREATE TABLE IF NOT EXISTS evaluations (id INTEGER PRIMARY KEY, family TEXT NOT NULL,"
                               " batch_json TEXT NOT NULL)")
            self._conn.commit()
        return self._conn

    def record(self, family: str, check_key: str, output_sha: str, run_id: str) -> None:
        db = self._db()
        db.execute("INSERT INTO failures (family, check_key, output_sha, run_id, recorded_at) VALUES (?, ?, ?, ?, ?)",
                   (family, check_key, output_sha, run_id, datetime.now(UTC).isoformat(timespec="seconds")))
        db.commit()

    def failures(self, family: str) -> list[dict[str, str]]:
        """The checks that failed in earlier runs of ``family``, earliest first, one entry per check."""
        rows = self._db().execute("SELECT check_key, MIN(run_id) AS run_id, MIN(recorded_at) AS first_seen, COUNT(*) AS"
                                  " times FROM failures WHERE family = ? GROUP BY check_key ORDER BY first_seen",
                                  (family,)).fetchall()
        return [{"check": r["check_key"], "run_id": r["run_id"], "first_seen": r["first_seen"], "times": r["times"]}
                for r in rows]

    def seen_failing(self, family: str, output_sha: str) -> bool:
        """This exact output failed a check in an earlier run of ``family``."""
        return self._db().execute("SELECT 1 FROM failures WHERE family = ? AND output_sha = ? LIMIT 1",
                                  (family, output_sha)).fetchone() is not None

    def _remember_run(self, family: str, reply: dict) -> None:
        """Only run writes observations; do not retain the executor's output text."""
        record = {k: v for k, v in reply.items() if k != "output"}
        db = self._db()
        db.execute("INSERT INTO runs (run_id, family, reply_json, recorded_at) VALUES (?, ?, ?, ?)",
                   (reply["run_id"], family, json.dumps(record), datetime.now(UTC).isoformat()))
        db.commit()

    def run_record(self, run_id: str) -> dict | None:
        row = self._db().execute("SELECT reply_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row["reply_json"]) if row else None

    def runs(self, family: str) -> list[dict]:
        rows = self._db().execute("SELECT reply_json FROM runs WHERE family=? ORDER BY rowid", (family,)).fetchall()
        return [json.loads(r["reply_json"]) for r in rows]

    def failure_counts(self, by: str = "check", min_count: int = 1) -> dict[str, int]:
        if by not in {"check", "family"} or type(min_count) is not int or min_count < 1:
            raise ValueError("G6_E_HARNESS_ANALYTICS: invalid grouping or min_count")
        column = "check_key" if by == "check" else "family"
        rows = self._db().execute(f"SELECT {column} AS name, COUNT(*) AS n FROM failures GROUP BY {column} "
                                  "HAVING COUNT(*) >= ? ORDER BY name", (min_count,)).fetchall()
        return {r["name"]: r["n"] for r in rows}

    def _remember_evaluation(self, family: str, standard_sha: str, candidate: dict, baseline: dict, stable: bool) -> None:
        batch = {"standard_sha": standard_sha, "candidate": {k: v["run_id"] for k, v in candidate.items()},
                 "baseline": {k: v["run_id"] for k, v in baseline.items()}, "stable": stable}
        db = self._db()
        db.execute("INSERT INTO evaluations (family, batch_json) VALUES (?, ?)", (family, json.dumps(batch)))
        db.commit()

    def _evaluation(self, family: str) -> dict | None:
        row = self._db().execute("SELECT batch_json FROM evaluations WHERE family=? ORDER BY id DESC LIMIT 1",
                                 (family,)).fetchone()
        return json.loads(row["batch_json"]) if row else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
