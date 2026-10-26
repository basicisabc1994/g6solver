# SPDX-License-Identifier: Apache-2.0
"""``TheoryStore``: a scoped SQLite store of candidate, active and retired theories, credited by paired outcomes.

Hardened from the archive's ``learning_layer/theory_store.py`` (audit 006 §7, §11):

- the path and the scope are required arguments, and every query is limited to the scope (LL-008: the archive
  defaulted to ``~/.g6/learning_layer.db`` and every reader saw every theory from every dataset);
- theories enter only as ``candidate`` with zero evidence, and an existing id is never overwritten (LL-007: the
  archive's upsert let a bundle un-retire a theory or install one at strength 1.0);
- credit comes only from paired outcomes, and each training instance counts once per theory (LL-005);
- outcome history is append-only, and the retirement thresholds are this store's arguments (LL-015);
- a theory becomes ``active`` only through :meth:`TheoryStore.activate`, with a promotion recommendation that a
  person accepts (LL-006), decided again from the held-out evidence it carries under a bar no lower than this store's
  ``promotion_policy`` (the decision cannot lower its own), under a name that is neither empty nor automated
  (``auto:``) nor the theory's recorded proposer (LEARNING-01, as ``g6.metaharness`` refuses a proposer's approval).

A bare file name is placed under ``$G6_HOME`` (``g6.core.storage.open_db``); ``":memory:"`` is ephemeral and says so.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from g6.core.adaptation_gate import is_automated
from g6.core.storage import open_db
from g6.learning.paired import CONFIRMED, DISCONFIRMED, credit
from g6.learning.promotion import PromotionPolicy, promote
from g6.learning.schema import THEORY_STATUSES, Theory, posterior_strength

if TYPE_CHECKING:
    from g6.learning.promotion import PromotionDecision

#: The credit label for an instance already credited for the same theory: logged, never counted again.
REPEAT = "repeat"

_DDL = (
    """CREATE TABLE IF NOT EXISTS theories (
        theory_id TEXT PRIMARY KEY, scope TEXT NOT NULL, failure_code TEXT NOT NULL,
        trigger TEXT NOT NULL DEFAULT 'always', instruction TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
        prior_strength REAL NOT NULL DEFAULT 0.5, confirmations INTEGER NOT NULL DEFAULT 0,
        disconfirmations INTEGER NOT NULL DEFAULT 0, strength REAL NOT NULL DEFAULT 0.5,
        status TEXT NOT NULL DEFAULT 'candidate', source_instances TEXT NOT NULL DEFAULT '[]', skill_content TEXT,
        activated_by TEXT NOT NULL DEFAULT '', activation_evidence TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL, updated_at REAL NOT NULL, proposer TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS paired_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, theory_id TEXT NOT NULL REFERENCES theories(theory_id),
        instance_id TEXT NOT NULL, with_theory INTEGER NOT NULL, without_theory INTEGER NOT NULL,
        credit TEXT NOT NULL, run_id TEXT NOT NULL DEFAULT '', recorded_at REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_theories_scope ON theories(scope, status)",
    "CREATE INDEX IF NOT EXISTS idx_outcomes_theory ON paired_outcomes(theory_id, instance_id)",
)


class TheoryStore:
    """Theories for one ``scope`` (for example a component name) in the SQLite database at ``path``.

    ``promotion_policy`` is the lowest bar :meth:`activate` judges a decision by (default ``PromotionPolicy()``): the
    store's owner sets it, never the decision being judged."""

    def __init__(self, path: str | os.PathLike[str], *, scope: str, retire_below: float = 0.2,
                 min_observations: int = 5, promotion_policy: PromotionPolicy | None = None) -> None:
        if not str(scope).strip():
            raise ValueError("G6_E_LEARNING_NO_SCOPE: a TheoryStore needs a scope, for example a component name")
        if not 0.0 <= retire_below <= 1.0 or min_observations < 1:
            raise ValueError("G6_E_LEARNING_BAD_THRESHOLD: retire_below must be in [0, 1] and min_observations >= 1")
        self.path = str(path)
        self.scope = str(scope)
        self.ephemeral = self.path == ":memory:"
        self._retire_below = float(retire_below)
        self._min_observations = int(min_observations)
        self._bar = promotion_policy or PromotionPolicy()
        self._lock = threading.Lock()
        self._conn = open_db(self.path)
        with self._lock:
            for statement in _DDL:
                self._conn.execute(statement)
            if "proposer" not in {row[1] for row in self._conn.execute("PRAGMA table_info(theories)")}:
                # a store made before proposers were kept (LEARNING-01): its theories have none
                self._conn.execute("ALTER TABLE theories ADD COLUMN proposer TEXT NOT NULL DEFAULT ''")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> TheoryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- entry: candidates only ---------------------------------------------------------------------------------

    def add_candidates(self, theories: Iterable[Theory]) -> list[Theory]:
        """Add theories as candidates with zero evidence and their prior as strength. Refuses the whole batch, adding
        nothing, if any id is already stored in any scope (audit 006, LL-007)."""
        items = list(theories)
        ids = [t.theory_id for t in items]
        if len(set(ids)) != len(ids):
            raise ValueError("G6_E_LEARNING_ID_EXISTS: the batch repeats a theory id")
        for t in items:
            if not t.theory_id or not t.failure_code.strip() or not t.instruction.strip():
                raise ValueError(f"G6_E_LEARNING_BAD_THEORY: {t.theory_id!r} needs an id, a failure code and text")
            if not 0.0 < t.prior_strength < 1.0:
                raise ValueError(f"G6_E_LEARNING_BAD_THEORY: prior_strength of {t.theory_id!r} must be in (0, 1)")
        if not items:
            return []
        now = time.time()
        with self._lock:
            marks = ",".join("?" * len(ids))
            taken = [r[0] for r in self._conn.execute(f"SELECT theory_id FROM theories WHERE theory_id IN ({marks})",
                                                      ids)]
            if taken:
                raise ValueError("G6_E_LEARNING_ID_EXISTS: these theory ids are already stored and are not "
                                 "overwritten: " + ", ".join(sorted(taken)))
            for t in items:
                self._conn.execute(
                    "INSERT INTO theories (theory_id, scope, failure_code, trigger, instruction, rationale, "
                    "prior_strength, strength, status, source_instances, skill_content, created_at, updated_at, "
                    "proposer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?)",
                    (t.theory_id, self.scope, t.failure_code, t.trigger, t.instruction, t.rationale, t.prior_strength,
                     t.prior_strength, json.dumps(list(t.source_instances)), t.skill_content, now, now,
                     str(t.proposer).strip()))
            self._conn.commit()
        return [self._require(tid) for tid in ids]

    # --- reads (always within the scope) -------------------------------------------------------------------------

    def get(self, theory_id: str) -> Theory | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM theories WHERE theory_id=? AND scope=?",
                                     (theory_id, self.scope)).fetchone()
        return _row_to_theory(row) if row else None

    def theories(self, status: str | None = None) -> list[Theory]:
        """Theories in this scope, strongest first (ties by id, so the order is deterministic)."""
        if status is not None and status not in THEORY_STATUSES:
            raise ValueError(f"G6_E_LEARNING_BAD_STATUS: {status!r} is not one of {THEORY_STATUSES}")
        query = "SELECT * FROM theories WHERE scope=?" + (" AND status=?" if status else "")
        params = (self.scope, status) if status else (self.scope,)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY strength DESC, theory_id", params).fetchall()
        return [_row_to_theory(r) for r in rows]

    def find_by_failure_code(self, failure_code: str, statuses: Iterable[str] = THEORY_STATUSES) -> list[Theory]:
        wanted = set(statuses)
        return [t for t in self.theories() if t.failure_code == failure_code and t.status in wanted]

    def paired_outcomes(self, theory_id: str) -> list[dict[str, Any]]:
        """Every paired outcome recorded for ``theory_id``, oldest first (append-only history)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT o.* FROM paired_outcomes o JOIN theories t ON t.theory_id = o.theory_id "
                "WHERE o.theory_id=? AND t.scope=? ORDER BY o.id", (theory_id, self.scope)).fetchall()
        return [{"instance_id": r["instance_id"], "with_theory": bool(r["with_theory"]),
                 "without_theory": bool(r["without_theory"]), "credit": r["credit"], "run_id": r["run_id"]}
                for r in rows]

    # --- credit ---------------------------------------------------------------------------------------------------

    def record_paired_outcome(self, theory_id: str, instance_id: str, with_theory: bool, without_theory: bool, *,
                              run_id: str = "") -> str:
        """Credit ``theory_id`` with one training instance run with and without it; return what the pair said.

        Only a pair whose runs disagree moves a counter (audit 006, LL-005). An instance already credited for this
        theory is logged as ``"repeat"`` and changes nothing, so re-running the same tasks cannot add strength.
        """
        label = credit(bool(with_theory), bool(without_theory))
        now = time.time()
        with self._lock:
            row = self._conn.execute("SELECT * FROM theories WHERE theory_id=? AND scope=?",
                                     (theory_id, self.scope)).fetchone()
            if row is None:
                raise KeyError(f"G6_E_LEARNING_UNKNOWN_THEORY: {theory_id!r} is not in scope {self.scope!r}")
            if self._conn.execute("SELECT 1 FROM paired_outcomes WHERE theory_id=? AND instance_id=?",
                                  (theory_id, instance_id)).fetchone():
                label = REPEAT
            self._conn.execute(
                "INSERT INTO paired_outcomes (theory_id, instance_id, with_theory, without_theory, credit, run_id, "
                "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (theory_id, instance_id, int(bool(with_theory)), int(bool(without_theory)), label, run_id, now))
            if label in (CONFIRMED, DISCONFIRMED):
                c = row["confirmations"] + (label == CONFIRMED)
                d = row["disconfirmations"] + (label == DISCONFIRMED)
                strength = posterior_strength(c, d, row["prior_strength"])
                status = row["status"]
                # audit 006, LL-015: the thresholds are the store's arguments, not constants in the theory type.
                if status != "retired" and c + d >= self._min_observations and strength < self._retire_below:
                    status = "retired"
                self._conn.execute("UPDATE theories SET confirmations=?, disconfirmations=?, strength=?, status=?, "
                                   "updated_at=? WHERE theory_id=?", (c, d, strength, status, now, theory_id))
            self._conn.commit()
        return label

    # --- lifecycle ------------------------------------------------------------------------------------------------

    def activate(self, decision: PromotionDecision, *, accepted_by: str) -> Theory:
        """Make a candidate ``active`` because ``accepted_by`` accepted ``decision``, a promotion recommendation.

        This is the only way a theory becomes active. It refuses an empty or automated (``auto:``) name, and the
        theory's own proposer (LEARNING-01), as the meta-harness does; a decision that does not recommend promotion; a theory that is not a candidate here, or
        whose text changed after its evidence was measured; and a decision that does not follow from the held-out
        evidence it carries: :func:`g6.learning.promote` is run again on that evidence and this store's theory, under
        the stricter of the decision's policy and this store's ``promotion_policy`` (review B1-5: a decision with
        ``PromotionPolicy(min_pairs=1, min_discordant=1, alpha=1.0)`` and one made-up pair activated), and must
        recommend it on the same evidence digest (a decision built by hand carries no evidence).
        """
        who = str(accepted_by).strip()
        if not who or is_automated(who):
            raise ValueError("G6_E_LEARNING_ACCEPTANCE_REQUIRED: a person must accept the recommendation (accepted_by "
                             "may not be empty or an auto: name)")
        if decision.recommendation != "promote":
            raise ValueError(f"G6_E_LEARNING_NOT_RECOMMENDED: the decision for {decision.theory_id!r} is "
                             f"{decision.recommendation!r}, not a recommendation to promote")
        theory = self._require(decision.theory_id)
        if theory.proposer.strip() and who.lower() == theory.proposer.strip().lower():
            raise ValueError("G6_E_LEARNING_APPROVER_IS_PROPOSER: the person accepting a change cannot be its proposer")
        if theory.status != "candidate":
            raise ValueError(f"G6_E_LEARNING_NOT_CANDIDATE: {theory.theory_id!r} is {theory.status!r}")
        if theory.digest != decision.theory_digest:
            raise ValueError("G6_E_LEARNING_EVIDENCE_UNBOUND: the theory's text differs from the one the evidence "
                             "was measured on")
        evidence = getattr(decision, "evidence", None)
        own = getattr(decision, "policy", None) or self._bar
        bar = PromotionPolicy(min_pairs=max(own.min_pairs, self._bar.min_pairs),
                              min_discordant=max(own.min_discordant, self._bar.min_discordant),
                              alpha=min(own.alpha, self._bar.alpha))
        again = None if evidence is None else promote(theory, evidence, bar)
        if again is None or again.recommendation != "promote" or again.evidence_digest != decision.evidence_digest:
            raise ValueError("G6_E_LEARNING_DECISION_UNFOUNDED: the decision does not follow from held-out evidence by "
                             f"promote's rules at this store's bar ({bar}; a decision built by hand carries no "
                             "evidence); call promote on the evidence")
        with self._lock:
            self._conn.execute("UPDATE theories SET status='active', activated_by=?, activation_evidence=?, "
                               "updated_at=? WHERE theory_id=?",
                               (who, decision.evidence_digest, time.time(), theory.theory_id))
            self._conn.commit()
        return self._require(theory.theory_id)

    def retire(self, theory_id: str) -> None:
        self._require(theory_id)
        with self._lock:
            self._conn.execute("UPDATE theories SET status='retired', updated_at=? WHERE theory_id=?",
                               (time.time(), theory_id))
            self._conn.commit()

    def _require(self, theory_id: str) -> Theory:
        theory = self.get(theory_id)
        if theory is None:
            raise KeyError(f"G6_E_LEARNING_UNKNOWN_THEORY: {theory_id!r} is not in scope {self.scope!r}")
        return theory


def _row_to_theory(row: sqlite3.Row) -> Theory:
    try:
        sources = list(json.loads(row["source_instances"] or "[]"))
    except (json.JSONDecodeError, TypeError):
        sources = []
    return Theory(theory_id=row["theory_id"], failure_code=row["failure_code"], instruction=row["instruction"],
                  trigger=row["trigger"], rationale=row["rationale"], prior_strength=row["prior_strength"],
                  confirmations=row["confirmations"], disconfirmations=row["disconfirmations"],
                  strength=row["strength"], status=row["status"], scope=row["scope"], source_instances=sources,
                  skill_content=row["skill_content"], activated_by=row["activated_by"],
                  created_at=row["created_at"], updated_at=row["updated_at"], proposer=row["proposer"])


__all__ = ["REPEAT", "TheoryStore"]
