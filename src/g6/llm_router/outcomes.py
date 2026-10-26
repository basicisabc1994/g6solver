# SPDX-License-Identifier: Apache-2.0
"""Recorded outcomes, the router's learning signal: how a model did on a task, kept under the G6 home, and the one
bounded rule by which they may tilt a route.

Merged from the earlier agent router: its outcome store, its Thompson-sampling bandit and its verdict-polarity counts.
Its task-type matrix, review rules, model re-rank and scaffold are not ported.

Every outcome is a claim: what a caller reported (:meth:`OutcomeStore.record`), or, through
:meth:`OutcomeStore.record_fallback`, that a model raised instead of answering in a ``FallbackCaller`` (and, only if
the caller says so, that the model that answered did well). G6 checks none of it. So outcomes never change a label and
never make a route look checked, and they reach a ranking only through :func:`tilts` (:data:`RULE`):

* only when a route is asked to use them, and only once :data:`MIN_OUTCOMES` are recorded among its candidates;
* each candidate's committed score moves by ``OUTCOME_WEIGHT * (theta - 0.5)``, theta drawn from
  ``Beta(successes + 1, failures + 1)`` with a seed the reply records, so by at most ``OUTCOME_WEIGHT / 2`` whatever is
  reported: a model the table puts more than ``OUTCOME_WEIGHT`` ahead is never overtaken;
* they never add a model, a provider or an uninstalled model to a route, and local models stay first.

Nothing here calls a model. The store is one SQLite file, :data:`STORE` under the G6 home
(``g6.core.storage.state_path``), holding at most :data:`MAX_ROWS` outcomes, the oldest dropped first.
"""
from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Iterable, Mapping
from contextlib import closing
from typing import Any

from g6.core.storage import open_db

STORE = "llm_router/outcomes.sqlite"
MIN_OUTCOMES = 5
OUTCOME_WEIGHT = 0.1
MAX_ROWS = 100_000
RULE = (f"Used only when a route asks for them and at least {MIN_OUTCOMES} are recorded among its candidates; each "
        f"committed score then moves by {OUTCOME_WEIGHT:g} x (theta - 0.5), theta drawn from Beta(successes + 1, "
        f"failures + 1) with the recorded seed, so by at most {OUTCOME_WEIGHT / 2:g}; no model or provider is added "
        "and local models stay first.")
_DDL = """CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    model_id TEXT NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    latency_ms REAL NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    reviewer_verdict TEXT NOT NULL DEFAULT '',
    human_correction TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'report',
    created_at REAL NOT NULL
)"""
_COLUMNS = ("task", "model_id", "success", "latency_ms", "cost_usd", "reviewer_verdict", "human_correction", "source",
            "created_at")
# A reviewer's verdict, read coarsely; a word not listed (and every label word) is not read at all.
_PASS = frozenset({"approve", "approved", "pass", "passed", "ok", "okay", "yes", "accept", "accepted", "correct"})
_FAIL = frozenset({"reject", "rejected", "fail", "failed", "block", "blocked", "no", "deny", "denied", "incorrect",
                   "wrong"})
_STATS = ("outcomes", "successes", "failures", "from_fallback", "with_verdict", "verdict_disagreements",
          "human_corrections")


class OutcomeStoreError(RuntimeError):
    """The outcome store could not be opened, read or written."""


def polarity(verdict: str) -> bool | None:
    """A reviewer's verdict as a pass (``True``) or a fail (``False``); ``None`` when it is empty or not one of the
    words this reads."""
    token = (verdict or "").strip().lower()
    head = token.split()[0].strip(".:,;!?") if token else ""
    if token in _PASS or head in _PASS:
        return True
    if token in _FAIL or head in _FAIL:
        return False
    return None


def tilts(counts: Mapping[str, tuple[int, int]], *, seed: int) -> dict[str, float]:
    """Each model's tilt from its ``(successes, failures)`` under :data:`RULE`: all 0.0 below :data:`MIN_OUTCOMES`."""
    if sum(s + f for s, f in counts.values()) < MIN_OUTCOMES:
        return {model: 0.0 for model in counts}
    rng = random.Random(seed)
    return {model: OUTCOME_WEIGHT * (min(max(rng.betavariate(s + 1, f + 1), 0.0), 1.0) - 0.5)
            for model, (s, f) in sorted(counts.items())}


class OutcomeStore:
    """The recorded outcomes: one SQLite file, ``path`` under the G6 home (or an absolute path you choose)."""

    def __init__(self, path: str = STORE) -> None:
        self.path = path

    def _db(self) -> sqlite3.Connection:
        try:
            conn = open_db(self.path)
            conn.execute(_DDL)
            return conn
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise OutcomeStoreError(f"the outcome store {self.path} could not be opened: {exc}") from exc

    def _rows(self, sql: str, args: tuple) -> list[tuple]:
        try:
            with closing(self._db()) as conn:
                return [tuple(row) for row in conn.execute(sql, args).fetchall()]
        except sqlite3.Error as exc:
            raise OutcomeStoreError(f"the outcome store {self.path} could not be read: {exc}") from exc

    def record(self, task: str, model_id: str, success: bool, *, latency_ms: float = 0.0, cost_usd: float = 0.0,
               reviewer_verdict: str = "", human_correction: str = "", source: str = "report") -> int:
        """Record one outcome as reported; its row id. The oldest rows beyond :data:`MAX_ROWS` are dropped."""
        row = (task, model_id, int(bool(success)), float(latency_ms), float(cost_usd), reviewer_verdict,
               human_correction, source, time.time())
        try:
            with closing(self._db()) as conn, conn:
                found = conn.execute(f"INSERT INTO outcomes ({', '.join(_COLUMNS)}) VALUES "
                                     f"({', '.join('?' * len(_COLUMNS))})", row).lastrowid
                conn.execute("DELETE FROM outcomes WHERE id <= ?", (found - MAX_ROWS,))
                return int(found)
        except sqlite3.Error as exc:
            raise OutcomeStoreError(f"the outcome store {self.path} could not be written: {exc}") from exc

    def record_fallback(self, task: str, caller: Any, *, accepted: bool | None) -> int:
        """Record a ``FallbackCaller``'s last call: each model that raised failed (G6 saw that); the model that
        answered succeeded or failed only as ``accepted`` says (your report; ``None`` records nothing for it).
        Returns how many outcomes were recorded."""
        failed = [str(name) for name, _ in getattr(caller, "failures", ())]
        for name in failed:
            self.record(task, name, False, source="fallback")
        answered = getattr(caller, "answered", None)
        if accepted is not None and answered:
            self.record(task, str(answered), accepted)
        return len(failed) + int(accepted is not None and bool(answered))

    def counts(self, task: str, models: Iterable[str]) -> dict[str, tuple[int, int]]:
        """``(successes, failures)`` recorded for ``task``, for each model of ``models``."""
        wanted = list(dict.fromkeys(models))
        found = {model: (0, 0) for model in wanted}
        if wanted:
            marks = ", ".join("?" * len(wanted))
            for model, ok, total in self._rows(f"SELECT model_id, SUM(success), COUNT(*) FROM outcomes WHERE task = ? "
                                               f"AND model_id IN ({marks}) GROUP BY model_id", (task, *wanted)):
                found[model] = (int(ok or 0), int(total) - int(ok or 0))
        return found

    def stats(self, task: str) -> list[dict[str, Any]]:
        """Per model for ``task``: its outcomes, successes and failures, how many came from a ``FallbackCaller``, how
        many carry a verdict this reads, how many of those disagree with the reported success, and how many a person
        corrected. It ranks nothing."""
        found: dict[str, dict[str, int]] = {}
        for model, success, verdict, correction, source in self._rows(
                "SELECT model_id, success, reviewer_verdict, human_correction, source FROM outcomes WHERE task = ? "
                "ORDER BY id", (task,)):
            row = found.setdefault(model, dict.fromkeys(_STATS, 0))
            said = polarity(verdict)
            row["outcomes"] += 1
            row["successes" if success else "failures"] += 1
            row["from_fallback"] += source == "fallback"
            row["with_verdict"] += said is not None
            row["verdict_disagreements"] += said is not None and said != bool(success)
            row["human_corrections"] += bool(correction.strip())
        return [{"model_id": model, **row} for model, row in sorted(found.items())]


__all__ = ["MAX_ROWS", "MIN_OUTCOMES", "OUTCOME_WEIGHT", "RULE", "STORE", "OutcomeStore", "OutcomeStoreError",
           "polarity", "tilts"]