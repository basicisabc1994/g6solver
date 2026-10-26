# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""An approval must outlive the process that issued it, and prove it wasn't edited.

THE TWO DEFECTS THIS PINS. ``ApprovalAuthority`` kept records in a per-process
dict on a module-global singleton. Measured against ``docker-compose.prod.yml``,
``web`` / ``rest`` / ``mcp`` / ``celery-worker`` are separate processes, so:

1. an approval minted on the surface the customer used is INVISIBLE to the
   process that runs the gate; and
2. every approval is erased by a restart.

(2) is fatal for the founder's stated purpose — the account holder approving
regulated work "acts as a legal guard to protect g6 in the case that we get
sued". A consent record a routine deploy erases is evidence of nothing.

Two authorities over one database is the honest simulation of (1): that is
exactly what two containers sharing a volume are. Closing and reopening is (2).

The tamper tests carry a POSITIVE CONTROL — an untouched chain must verify — or
"detects tampering" would be satisfied by a checker that always says no.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from g6.core.approval_authority import (
    ApprovalAuthority,
    get_approval_authority,
    reset_approval_authority,
)
from g6.core.approval_store import (
    DB_PATH_ENV,
    GENESIS_HASH,
    InMemoryApprovalStore,
    SqliteApprovalStore,
    configured_store,
)

SCOPE = "job:pharmaceutical:assess_dosage"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "approvals.db")


@pytest.fixture(params=["memory", "sqlite"])
def any_store(request, tmp_path):
    """Both backends, so equivalence is asserted rather than assumed."""
    if request.param == "memory":
        yield InMemoryApprovalStore()
        return
    store = SqliteApprovalStore(str(tmp_path / "equiv.db"))
    yield store
    store.close()


class TestTheDefectItself:
    """One database, two authorities — i.e. two containers sharing a volume."""

    def test_an_approval_issued_in_one_process_is_seen_by_another(
        self, db_path
    ) -> None:
        issuer = ApprovalAuthority(store=SqliteApprovalStore(db_path))
        gate = ApprovalAuthority(store=SqliteApprovalStore(db_path))

        record = issuer.issue(reviewer_id="customer:42", scope=SCOPE, run_id="run-1")

        assert gate.check(record.approval_id, scope=SCOPE, run_id="run-1") is True

    def test_the_in_memory_store_does_NOT_see_it(self) -> None:
        """The control that shows the test above is measuring something real."""
        issuer = ApprovalAuthority()
        gate = ApprovalAuthority()

        record = issuer.issue(reviewer_id="customer:42", scope=SCOPE, run_id="run-1")

        assert gate.check(record.approval_id, scope=SCOPE, run_id="run-1") is False

    def test_an_approval_survives_a_restart(self, db_path) -> None:
        before = SqliteApprovalStore(db_path)
        record = ApprovalAuthority(store=before).issue(
            reviewer_id="customer:42", scope=SCOPE, run_id="run-1"
        )
        before.close()

        after = ApprovalAuthority(store=SqliteApprovalStore(db_path))
        assert after.check(record.approval_id, scope=SCOPE, run_id="run-1") is True

    def test_consumption_is_visible_to_the_other_process(self, db_path) -> None:
        """Otherwise a single-use approval is single-use PER PROCESS."""
        first = ApprovalAuthority(store=SqliteApprovalStore(db_path))
        second = ApprovalAuthority(store=SqliteApprovalStore(db_path))

        record = first.issue(reviewer_id="customer:42", scope=SCOPE, single_use=True)
        assert first.check(record.approval_id, scope=SCOPE, consume=True) is True

        assert second.check(record.approval_id, scope=SCOPE, consume=True) is False

    def test_revocation_is_visible_to_the_other_process(self, db_path) -> None:
        first = ApprovalAuthority(store=SqliteApprovalStore(db_path))
        second = ApprovalAuthority(store=SqliteApprovalStore(db_path))

        record = first.issue(
            reviewer_id="customer:42", scope=SCOPE, single_use=False
        )
        assert second.check(record.approval_id, scope=SCOPE) is True

        assert first.revoke(record.approval_id) is True
        assert second.check(record.approval_id, scope=SCOPE) is False


class TestTheTwoBackendsAgree:
    """Durability must not quietly change what an approval MEANS."""

    def test_a_good_approval_passes(self, any_store) -> None:
        auth = ApprovalAuthority(store=any_store)
        record = auth.issue(reviewer_id="customer:42", scope=SCOPE, run_id="run-1")

        assert auth.check(record.approval_id, scope=SCOPE, run_id="run-1") is True

    def test_the_wrong_scope_is_refused(self, any_store) -> None:
        auth = ApprovalAuthority(store=any_store)
        record = auth.issue(reviewer_id="customer:42", scope=SCOPE)

        assert auth.check(record.approval_id, scope="job:lawyer:file_motion") is False

    def test_an_omitted_run_cannot_disable_a_pin(self, any_store) -> None:
        """F-3, re-asserted on both backends."""
        auth = ApprovalAuthority(store=any_store)
        record = auth.issue(reviewer_id="customer:42", scope=SCOPE, run_id="run-1")

        assert auth.check(record.approval_id, scope=SCOPE) is False
        assert auth.check(record.approval_id, scope=SCOPE, run_id="run-2") is False

    def test_an_expired_approval_is_refused(self, any_store) -> None:
        auth = ApprovalAuthority(store=any_store)
        record = auth.issue(
            reviewer_id="customer:42", scope=SCOPE, ttl_seconds=0.01
        )
        time.sleep(0.05)

        assert auth.check(record.approval_id, scope=SCOPE) is False

    def test_a_fabricated_id_is_refused(self, any_store) -> None:
        auth = ApprovalAuthority(store=any_store)

        assert auth.check("totally-made-up", scope=SCOPE) is False

    def test_single_use_is_single_use(self, any_store) -> None:
        auth = ApprovalAuthority(store=any_store)
        record = auth.issue(reviewer_id="customer:42", scope=SCOPE, single_use=True)

        assert auth.check(record.approval_id, scope=SCOPE, consume=True) is True
        assert auth.check(record.approval_id, scope=SCOPE, consume=True) is False

    def test_an_oracle_record_round_trips_its_issuer_and_policy(
        self, any_store
    ) -> None:
        """The fields the audit distinction rests on must survive persistence."""
        from g6.core.approval_authority import ISSUER_POLICY_ORACLE

        auth = ApprovalAuthority(store=any_store)
        record = auth.issue(
            reviewer_id="policy_oracle",
            scope=SCOPE,
            issuer_kind=ISSUER_POLICY_ORACLE,
            policy_version="sha256:deadbeef",
        )

        resolved = auth.resolve(record.approval_id)
        assert resolved is not None
        assert resolved.issuer_kind == ISSUER_POLICY_ORACLE
        assert resolved.policy_version == "sha256:deadbeef"
        assert auth.check(record.approval_id, scope=SCOPE) is False


class TestTheChainDetectsTampering:
    """Durable is not enough — it must be provably unedited."""

    def _issue_three(self, store):
        auth = ApprovalAuthority(store=store)
        return [
            auth.issue(reviewer_id=f"customer:{n}", scope=SCOPE, single_use=False)
            for n in range(3)
        ]

    def test_an_untouched_chain_verifies(self, db_path) -> None:
        """POSITIVE CONTROL. Without it, "detects tampering" could mean
        "always reports broken", which detects nothing."""
        store = SqliteApprovalStore(db_path)
        self._issue_three(store)

        result = store.verify_chain()
        assert result["ok"] is True
        assert result["checked"] == 3
        assert result["broken_at"] is None

    def test_an_empty_chain_verifies(self, db_path) -> None:
        store = SqliteApprovalStore(db_path)

        assert store.verify_chain()["ok"] is True

    def test_editing_a_payload_breaks_it(self, db_path) -> None:
        store = SqliteApprovalStore(db_path)
        records = self._issue_three(store)
        store.close()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT id, payload_json FROM approval_events WHERE approval_id = ?",
            (records[1].approval_id,),
        ).fetchone()
        payload = json.loads(row[1])
        payload["reviewer_id"] = "someone-else"
        conn.execute(
            "UPDATE approval_events SET payload_json = ? WHERE id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), row[0]),
        )
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).verify_chain()["ok"] is False

    def test_deleting_an_event_breaks_it(self, db_path) -> None:
        """The revocation an operator would most want to make disappear."""
        store = SqliteApprovalStore(db_path)
        records = self._issue_three(store)
        auth = ApprovalAuthority(store=store)
        auth.revoke(records[0].approval_id)
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM approval_events WHERE event = 'revoked'")
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).verify_chain()["ok"] is False

    def test_relabelling_an_event_to_another_approval_breaks_it(
        self, db_path
    ) -> None:
        """approval_id is inside the hash, not merely a column beside it."""
        store = SqliteApprovalStore(db_path)
        records = self._issue_three(store)
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE approval_events SET approval_id = ? WHERE approval_id = ?",
            (records[2].approval_id, records[1].approval_id),
        )
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).verify_chain()["ok"] is False

    def test_changing_an_event_kind_breaks_it(self, db_path) -> None:
        store = SqliteApprovalStore(db_path)
        self._issue_three(store)
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE approval_events SET event = 'revoked' WHERE id = "
            "(SELECT MIN(id) FROM approval_events)"
        )
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).verify_chain()["ok"] is False

    def test_truncating_the_tail_breaks_it(self, db_path) -> None:
        """THE ATTACK A BARE HASH CHAIN MISSES, and this test found it.

        Delete the most recent events and every surviving link still matches its
        predecessor, so the shortened log verifies perfectly. That is exactly the
        edit someone would make to a legal record. Only the anchor catches it.
        """
        store = SqliteApprovalStore(db_path)
        self._issue_three(store)
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute(
            "DELETE FROM approval_events WHERE id = "
            "(SELECT MAX(id) FROM approval_events)"
        )
        conn.commit()
        conn.close()

        result = SqliteApprovalStore(db_path).verify_chain()
        assert result["ok"] is False
        assert "removed" in result["reason"]

    def test_deleting_the_anchor_breaks_it(self, db_path) -> None:
        """Removing the thing that detects truncation is itself detected."""
        store = SqliteApprovalStore(db_path)
        self._issue_three(store)
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM approval_anchor")
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).verify_chain()["ok"] is False

    def test_clearing_is_not_reported_as_tampering(self, db_path) -> None:
        """A checker that cries wolf on a deliberate reset gets ignored."""
        store = SqliteApprovalStore(db_path)
        self._issue_three(store)
        store.clear()

        assert store.verify_chain()["ok"] is True

    def test_the_limit_of_the_guarantee_is_pinned_not_claimed_away(
        self, db_path
    ) -> None:
        """HONEST LIMITATION, asserted so nobody later claims more.

        Someone with write access can rewrite the log AND the anchor together,
        and it verifies. The anchor raises the cost of tampering; it does not
        make the record tamper-PROOF. Real non-repudiation against the operator
        needs the tail published where we cannot retract it.
        """
        store = SqliteApprovalStore(db_path)
        self._issue_three(store)
        store.close()

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT id, entry_hash FROM approval_events ORDER BY id ASC"
        ).fetchall()
        conn.execute("DELETE FROM approval_events WHERE id = ?", (rows[-1][0],))
        conn.execute(
            "UPDATE approval_anchor SET event_count = ?, tail_hash = ? WHERE id = 1",
            (len(rows) - 1, rows[-2][1]),
        )
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).verify_chain()["ok"] is True

    def test_the_chain_starts_at_genesis(self, db_path) -> None:
        store = SqliteApprovalStore(db_path)
        self._issue_three(store)
        store.close()

        conn = sqlite3.connect(db_path)
        first = conn.execute(
            "SELECT prev_hash FROM approval_events ORDER BY id ASC LIMIT 1"
        ).fetchone()
        conn.close()

        assert first[0] == GENESIS_HASH


class TestEvidenceIsNotSweptAway:
    """A legal record is not a cache."""

    def test_purge_deletes_nothing_from_the_durable_store(self, db_path) -> None:
        store = SqliteApprovalStore(db_path)
        auth = ApprovalAuthority(store=store)
        record = auth.issue(reviewer_id="customer:42", scope=SCOPE, single_use=True)
        auth.check(record.approval_id, scope=SCOPE, consume=True)

        assert auth.purge_expired() == 0
        assert store.verify_chain()["ok"] is True
        assert auth.resolve(record.approval_id) is not None

    def test_the_in_memory_store_still_purges(self) -> None:
        """Unchanged — it purges to bound a dict, which has no evidence value."""
        auth = ApprovalAuthority()
        record = auth.issue(reviewer_id="customer:42", scope=SCOPE, single_use=True)
        auth.check(record.approval_id, scope=SCOPE, consume=True)

        assert auth.purge_expired() == 1
        assert auth.resolve(record.approval_id) is None

    def test_a_consumed_id_stays_resolvable(self, db_path) -> None:
        """So it cannot be re-presented and read as fresh."""
        auth = ApprovalAuthority(store=SqliteApprovalStore(db_path))
        record = auth.issue(reviewer_id="customer:42", scope=SCOPE, single_use=True)
        auth.check(record.approval_id, scope=SCOPE, consume=True)

        resolved = auth.resolve(record.approval_id)
        assert resolved is not None
        assert resolved.consumed_at is not None
        assert resolved.is_live() is False

    def test_history_records_what_happened_not_just_the_end_state(
        self, db_path
    ) -> None:
        store = SqliteApprovalStore(db_path)
        auth = ApprovalAuthority(store=store)
        record = auth.issue(
            reviewer_id="customer:42", scope=SCOPE, single_use=False
        )
        auth.revoke(record.approval_id)

        events = [e["event"] for e in store.history(record.approval_id)]
        assert events == ["issued", "revoked"]


class TestRealProcessesNotJustRealObjects:
    """The defects an independent review found, because I tested objects.

    Two ``ApprovalAuthority`` instances in ONE interpreter share a
    ``threading.RLock``, so they serialize by accident and every race is hidden.
    Two OS processes do not. These spawn real interpreters — the same thing
    ``web`` / ``rest`` / ``mcp`` / ``celery`` are to each other.
    """

    def _run(self, tmp_path, db_path: str, source: str, tag: str) -> str:
        """Run `source` in a real child interpreter and return its stdout."""
        import os
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[3]
        script = tmp_path / f"child_{tag}.py"
        script.write_text(source, encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(root / "components"), str(root / "bases"), str(root)]
        )
        env["G6_DISABLE_LLM"] = "1"
        env["G6_APPROVAL_DB"] = db_path
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, env=env, timeout=180, check=False,
        )
        assert proc.returncode == 0, f"child {tag} failed: {proc.stderr[-2000:]}"
        return proc.stdout.strip()

    def test_concurrent_issuance_does_not_fork_the_chain(self, tmp_path) -> None:
        """MEASURED DEFECT D1. Two writers read the same tail and both append.

        The result is a FORKED chain, so ``verify_chain`` reports tampering on
        entirely honest data — and rows are lost too (measured: 60 of 120
        survived). A tamper detector with false positives is worse than none: it
        gets switched off, and then it is absent for the true positive.

        WHAT THIS TEST DOES AND DOES NOT PROVE, stated because the distinction
        is easy to overclaim. Mutating the store back to its original form —
        autocommit, tail read outside any transaction — makes this FAIL, so the
        test genuinely catches the defect.

        ON ``BEGIN IMMEDIATE`` vs ``BEGIN DEFERRED`` this suite is UNRELIABLE,
        and an earlier version of this docstring stated the result as settled in
        one direction, which was wrong. Measured: the DEFERRED mutant SURVIVED
        here, and an independent re-review killed it 5 times out of 5 on a
        different machine. So the outcome is timing-dependent and must not be
        read as proof either way. What IS established is that wrapping the read
        and the write in ONE transaction stops the fork. IMMEDIATE remains the
        right choice on its own merits — DEFERRED has to upgrade its lock
        mid-transaction and can fail with SQLITE_BUSY — and that is a reasoned
        preference, not a finding of this test.
        """
        db_path = str(tmp_path / "race.db")
        source = (
            "import os\n"
            "from g6.core.approval_store import SqliteApprovalStore\n"
            "from g6.core.approval_authority import ApprovalAuthority\n"
            "auth = ApprovalAuthority(store=SqliteApprovalStore(os.environ['G6_APPROVAL_DB']))\n"
            "for _ in range(60):\n"
            "    auth.issue(reviewer_id='customer:1', scope='job:x:y', single_use=False)\n"
            "print('done')\n"
        )
        import concurrent.futures as cf

        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._run, tmp_path, db_path, source, tag)
                for tag in ("a", "b")
            ]
            for future in futures:
                assert future.result() == "done"

        store = SqliteApprovalStore(db_path)
        result = store.verify_chain()
        assert result["ok"] is True, f"chain forked under concurrency: {result}"
        assert result["checked"] == 120

    def test_a_single_use_approval_cannot_be_consumed_twice(self, tmp_path) -> None:
        """MEASURED DEFECT D2. get-then-mark is a TOCTOU across processes.

        Both processes saw the approval live, both consumed it, and ``check()``
        returned True to BOTH. "Single use" that is only single-use-per-process
        is not a limit on an approval that releases regulated work.
        """
        db_path = str(tmp_path / "spend.db")
        store = SqliteApprovalStore(db_path)
        auth = ApprovalAuthority(store=store)
        ids = [
            auth.issue(
                reviewer_id="customer:1", scope=SCOPE, single_use=True
            ).approval_id
            for _ in range(60)
        ]
        store.close()
        (tmp_path / "ids.txt").write_text("\n".join(ids), encoding="utf-8")

        source = (
            "import os, pathlib\n"
            "from g6.core.approval_store import SqliteApprovalStore\n"
            "from g6.core.approval_authority import ApprovalAuthority\n"
            "ids = pathlib.Path(os.environ['G6_IDS']).read_text().split()\n"
            "auth = ApprovalAuthority(store=SqliteApprovalStore(os.environ['G6_APPROVAL_DB']))\n"
            f"won = [i for i in ids if auth.check(i, scope={SCOPE!r}, consume=True)]\n"
            "print(len(won))\n"
        )
        import os as _os

        _os.environ["G6_IDS"] = str(tmp_path / "ids.txt")
        try:
            import concurrent.futures as cf

            with cf.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(self._run, tmp_path, db_path, source, tag)
                    for tag in ("c", "d")
                ]
                wins = [int(f.result()) for f in futures]
        finally:
            _os.environ.pop("G6_IDS", None)

        # Each id may be won at most once, so the wins must total exactly the
        # number issued -- never more.
        assert sum(wins) == len(ids), f"double-spend: {wins} wins over {len(ids)} ids"

        after = SqliteApprovalStore(db_path)
        for approval_id in ids:
            events = [e["event"] for e in after.history(approval_id)]
            assert events.count("consumed") <= 1, (
                f"{approval_id} consumed {events.count('consumed')} times"
            )
        assert after.verify_chain()["ok"] is True


class TestABrokenDatabaseRefusesRatherThanRaises:
    """MEASURED DEFECT D3. `check()` promises never to raise.

    A dict could not fail. A database can: missing, unreadable, corrupt, full.
    Two callers do NOT wrap this call — ``validation_gates._human_approval_gate``
    and the job HITL gate via ``check_operation_approval`` — so an escaping
    exception would be a 500 on a regulated request instead of a refusal.
    Refusing is both the promise and the safe answer.
    """

    def test_a_corrupt_database_refuses(self, tmp_path) -> None:
        """Corrupt the file UNDER a live store, so the fault lands at check().

        Constructing a store on an already-corrupt file fails in ``__init__``,
        which is a different (and louder) path. What matters here is the store
        that was healthy when the process started and is not any more.
        """
        db_path = tmp_path / "corrupt.db"
        store = SqliteApprovalStore(str(db_path))
        auth = ApprovalAuthority(store=store)
        record = auth.issue(
            reviewer_id="customer:42", scope=SCOPE, single_use=False
        )
        assert auth.check(record.approval_id, scope=SCOPE) is True  # control

        store.close()
        db_path.write_bytes(b"this is not a database" * 100)

        assert auth.check(record.approval_id, scope=SCOPE) is False
        assert auth.resolve(record.approval_id) is None
        assert auth.revoke(record.approval_id) is False

    def test_an_invalid_json_payload_refuses(self, tmp_path) -> None:
        """`get()` caught TypeError but not JSONDecodeError."""
        db_path = tmp_path / "badjson.db"
        store = SqliteApprovalStore(str(db_path))
        record = ApprovalAuthority(store=store).issue(
            reviewer_id="customer:42", scope=SCOPE
        )
        store.close()

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE approval_events SET payload_json = ? WHERE approval_id = ?",
            ("{not json at all", record.approval_id),
        )
        conn.commit()
        conn.close()

        auth = ApprovalAuthority(store=SqliteApprovalStore(str(db_path)))
        assert auth.check(record.approval_id, scope=SCOPE) is False

    def test_an_unopenable_path_refuses_at_the_hitl_gate(
        self, monkeypatch, tmp_path
    ) -> None:
        """The job HITL gate does not wrap its call — so this must not raise."""
        from g6.core.approval_authority import check_operation_approval

        monkeypatch.setenv(DB_PATH_ENV, str(tmp_path))  # a DIRECTORY, not a file
        reset_approval_authority()
        try:
            assert (
                check_operation_approval(
                    "some-id", agent_name="pharmaceutical", operation="assess_dosage"
                )
                is False
            )
        finally:
            reset_approval_authority()

    def test_an_unopenable_path_refuses_at_the_human_approval_gate(
        self, monkeypatch, tmp_path
    ) -> None:
        from g6.core.validation_gates import get

        monkeypatch.setenv(DB_PATH_ENV, str(tmp_path))
        reset_approval_authority()
        try:
            assert get("human_approval")(approval_id="x", scope=SCOPE) is False
        finally:
            reset_approval_authority()


class TestTheHashIsDomainSeparated:
    """The docstring sells a domain separator; nothing verified it existed."""

    def test_the_separator_is_actually_applied(self) -> None:
        """Byte-for-byte identical to entry_hash EXCEPT the prefix.

        An earlier version of this test also omitted a trailing separator, so it
        passed for a reason that had nothing to do with the domain prefix — and
        a mutation that deleted the prefix left it green. The comparison has to
        differ in exactly one thing or it measures nothing.
        """
        import hashlib

        from g6.core.approval_store import entry_hash

        undomained = hashlib.sha256()
        undomained.update(GENESIS_HASH.encode("utf-8"))
        undomained.update(b"\x00")
        undomained.update(b"aid")
        undomained.update(b"\x00")
        undomained.update(b"issued")
        undomained.update(b"\x00")
        undomained.update(b"{}")

        assert (
            entry_hash(GENESIS_HASH, "aid", "issued", "{}")
            != undomained.hexdigest()
        ), "the domain separator is not actually being applied"

    def test_every_field_changes_the_hash(self) -> None:
        """Otherwise a field is "in the hash" only in the docstring."""
        from g6.core.approval_store import entry_hash

        base = entry_hash(GENESIS_HASH, "aid", "issued", "{}")
        assert entry_hash("f" * 64, "aid", "issued", "{}") != base
        assert entry_hash(GENESIS_HASH, "other", "issued", "{}") != base
        assert entry_hash(GENESIS_HASH, "aid", "revoked", "{}") != base
        assert entry_hash(GENESIS_HASH, "aid", "issued", '{"a":1}') != base


class TestTheStoreFailsClosed:
    """Anything it cannot faithfully reconstruct, it refuses to reconstruct."""

    def test_an_id_issued_twice_is_refused(self, db_path) -> None:
        """A duplicate issue is a token collision or a tampered log."""
        store = SqliteApprovalStore(db_path)
        record = ApprovalAuthority(store=store).issue(
            reviewer_id="customer:42", scope=SCOPE
        )
        store.close()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT approval_id, event, timestamp, payload_json, prev_hash, "
            "entry_hash FROM approval_events WHERE approval_id = ?",
            (record.approval_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO approval_events (approval_id, event, timestamp, "
            "payload_json, prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).get(record.approval_id) is None

    def test_a_forged_event_inserted_mid_chain_breaks_it(self, db_path) -> None:
        store = SqliteApprovalStore(db_path)
        records = [
            ApprovalAuthority(store=store).issue(
                reviewer_id=f"customer:{n}", scope=SCOPE, single_use=False
            )
            for n in range(3)
        ]
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO approval_events (id, approval_id, event, timestamp, "
            "payload_json, prev_hash, entry_hash) VALUES "
            "(1000, ?, 'revoked', 1.0, '{}', ?, ?)",
            (records[0].approval_id, GENESIS_HASH, "f" * 64),
        )
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).verify_chain()["ok"] is False

    def test_an_unknown_id_is_none(self, db_path) -> None:
        assert SqliteApprovalStore(db_path).get("nope") is None

    @pytest.mark.parametrize("bad", ("", None, 0, [], {}))
    def test_a_non_id_is_none(self, db_path, bad) -> None:
        assert SqliteApprovalStore(db_path).get(bad) is None

    def test_a_payload_from_another_schema_is_refused(self, db_path) -> None:
        """Reconstructing a PARTIAL approval would be worse than none."""
        store = SqliteApprovalStore(db_path)
        record = ApprovalAuthority(store=store).issue(
            reviewer_id="customer:42", scope=SCOPE
        )
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE approval_events SET payload_json = ? WHERE approval_id = ?",
            (json.dumps({"approval_id": record.approval_id, "surprise": 1}),
             record.approval_id),
        )
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).get(record.approval_id) is None

    def test_events_before_an_issue_are_refused(self, db_path) -> None:
        store = SqliteApprovalStore(db_path)
        record = ApprovalAuthority(store=store).issue(
            reviewer_id="customer:42", scope=SCOPE
        )
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE approval_events SET event = 'revoked'")
        conn.commit()
        conn.close()

        assert SqliteApprovalStore(db_path).get(record.approval_id) is None

    def test_a_store_with_no_path_is_refused(self) -> None:
        with pytest.raises(ValueError, match="db_path"):
            SqliteApprovalStore("")


class TestTheProcessWideAuthorityTakesItsStoreFromTheEnvironment:
    """Nothing changes until it is configured — and it must be, before ship."""

    @pytest.fixture(autouse=True)
    def _isolated_singleton(self, monkeypatch):
        monkeypatch.delenv(DB_PATH_ENV, raising=False)
        reset_approval_authority()
        yield
        reset_approval_authority()

    def test_unset_means_in_memory(self) -> None:
        assert isinstance(configured_store(), InMemoryApprovalStore)

    def test_set_means_durable(self, monkeypatch, db_path) -> None:
        monkeypatch.setenv(DB_PATH_ENV, db_path)

        assert isinstance(configured_store(), SqliteApprovalStore)

    def test_the_singleton_honours_it(self, monkeypatch, db_path) -> None:
        monkeypatch.setenv(DB_PATH_ENV, db_path)

        record = get_approval_authority().issue(
            reviewer_id="customer:42", scope=SCOPE, single_use=False
        )

        # A DIFFERENT authority over the same file — the other container.
        other = ApprovalAuthority(store=SqliteApprovalStore(db_path))
        assert other.check(record.approval_id, scope=SCOPE) is True

    def test_a_blank_value_is_not_a_path(self, monkeypatch) -> None:
        monkeypatch.setenv(DB_PATH_ENV, "   ")

        assert isinstance(configured_store(), InMemoryApprovalStore)


# ---------------------------------------------------------------------------
# Everything below was added after an independent re-review. Each class pins a
# defect that the first round of tests did not catch, and the reason it did not
# catch it is recorded with it — the misses are as informative as the fixes.
# ---------------------------------------------------------------------------


class _ConnProxy:
    """Delegates to a real connection so a test can interleave one operation.

    Used instead of monkeypatching sqlite3 so the store under test keeps its own
    real connection and real transactions — the thing being tested is WHEN it
    reads, not whether sqlite works.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AppendBetweenReads(_ConnProxy):
    """Lands one honest append immediately after the event rows are read."""

    def __init__(self, real, hook) -> None:
        super().__init__(real)
        self._hook = hook
        self._fired = False

    def execute(self, sql, *args, **kwargs):
        cursor = self._real.execute(sql, *args, **kwargs)
        if not self._fired and "FROM approval_events ORDER BY id ASC" in sql:
            self._fired = True
            self._hook()
        return cursor


class _FailingTxnControl(_ConnProxy):
    """Fails COMMIT once, and optionally ROLLBACK once — a simulated disk fault."""

    def __init__(self, real, *, fail_rollback: bool = False) -> None:
        super().__init__(real)
        self._fail_rollback = fail_rollback
        self.commit_failed = False
        self.rollback_failed = False

    def execute(self, sql, *args, **kwargs):
        statement = sql.strip().upper()
        if statement.startswith("COMMIT") and not self.commit_failed:
            self.commit_failed = True
            raise sqlite3.OperationalError("disk I/O error (simulated)")
        if (
            self._fail_rollback
            and statement.startswith("ROLLBACK")
            and not self.rollback_failed
        ):
            self.rollback_failed = True
            raise sqlite3.OperationalError("rollback also failed (simulated)")
        return self._real.execute(sql, *args, **kwargs)


class TestTheDetectorDoesNotCryTamperOnHonestData:
    """The false-positive failure mode, on the READ path this time.

    WHY IT WAS MISSED. The first round fixed exactly this race on the WRITE path
    and argued, correctly, that a detector with false positives gets switched off
    and is then absent for the true positive. The same argument applies to
    ``verify_chain`` itself, which read the rows and the anchor as two separate
    statements — and nobody looked, because the write path had just been fixed.
    """

    def test_an_append_between_the_two_reads_is_not_reported_as_truncation(
        self, db_path
    ) -> None:
        store = SqliteApprovalStore(db_path)
        authority = ApprovalAuthority(store=store)
        for _ in range(3):
            authority.issue(reviewer_id="customer:1", scope=SCOPE)

        peer = SqliteApprovalStore(db_path)

        def honest_append() -> None:
            """Exactly what another container does all day."""
            ApprovalAuthority(store=peer).issue(reviewer_id="customer:2", scope=SCOPE)

        store._conn = _AppendBetweenReads(store._get_conn(), honest_append)
        result = store.verify_chain()
        peer.close()

        assert result["ok"] is True, (
            f"false tamper alarm on intact data: {result['reason']}"
        )

    def test_and_a_real_truncation_is_still_caught(self, db_path) -> None:
        """POSITIVE CONTROL. Without this, the fix above could be 'always ok'."""
        store = SqliteApprovalStore(db_path)
        authority = ApprovalAuthority(store=store)
        for _ in range(3):
            authority.issue(reviewer_id="customer:1", scope=SCOPE)
        store.close()

        conn = sqlite3.connect(db_path)
        conn.execute(
            "DELETE FROM approval_events WHERE id = (SELECT MAX(id) FROM approval_events)"
        )
        conn.commit()
        conn.close()

        result = SqliteApprovalStore(db_path).verify_chain()
        assert result["ok"] is False
        assert "removed" in result["reason"]


class TestOneDiskHiccupDoesNotWedgeTheFleet:
    """A failed COMMIT used to latch the connection AND hold the write lock.

    WHY IT WAS MISSED. Every test drove the store through its public API on a
    healthy disk, where COMMIT does not fail. The failure needs to be injected;
    it will not turn up on its own, and "the durable legal record stops accepting
    approvals everywhere until someone restarts it" is not a failure mode you
    want to discover in production.
    """

    def test_the_store_still_works_after_a_failed_commit(self, db_path) -> None:
        store = SqliteApprovalStore(db_path)
        store._conn = _FailingTxnControl(store._get_conn())

        with pytest.raises(sqlite3.OperationalError):
            ApprovalAuthority(store=store).issue(reviewer_id="customer:1", scope=SCOPE)

        # THE ASSERTION THAT MATTERS: the next one goes through. Before the fix
        # this raised "cannot start a transaction within a transaction" forever.
        record = ApprovalAuthority(store=store).issue(
            reviewer_id="customer:1", scope=SCOPE
        )
        assert store.get(record.approval_id) is not None

    def test_a_peer_container_can_still_write_after_the_fault(self, db_path) -> None:
        """The lock has to be RELEASED, not just the local state repaired —
        otherwise one container's disk error stops every other container."""
        store = SqliteApprovalStore(db_path)
        store._conn = _FailingTxnControl(store._get_conn())
        with pytest.raises(sqlite3.OperationalError):
            ApprovalAuthority(store=store).issue(reviewer_id="customer:1", scope=SCOPE)

        peer = ApprovalAuthority(store=SqliteApprovalStore(db_path))
        record = peer.issue(reviewer_id="customer:2", scope=SCOPE)
        assert peer.check(record.approval_id, scope=SCOPE) is True

    def test_a_connection_that_cannot_be_rolled_back_is_discarded(self, db_path) -> None:
        """When ROLLBACK fails too, the connection is wedged and holding the
        lock. Keeping it cached is what turned a hiccup into an outage."""
        store = SqliteApprovalStore(db_path)
        proxy = _FailingTxnControl(store._get_conn(), fail_rollback=True)
        store._conn = proxy

        with pytest.raises(sqlite3.OperationalError):
            ApprovalAuthority(store=store).issue(reviewer_id="customer:1", scope=SCOPE)

        assert proxy.commit_failed and proxy.rollback_failed
        assert store._conn is None, "the wedged connection was kept"

        # And it recovers by reconnecting.
        record = ApprovalAuthority(store=store).issue(
            reviewer_id="customer:1", scope=SCOPE
        )
        assert store.get(record.approval_id) is not None

    def test_the_original_error_is_not_masked_by_the_rollback_failure(
        self, db_path
    ) -> None:
        """The disk error is the diagnosis; 'rollback also failed' is a symptom
        of it. Surfacing the symptom instead would send someone the wrong way."""
        store = SqliteApprovalStore(db_path)
        store._conn = _FailingTxnControl(store._get_conn(), fail_rollback=True)

        with pytest.raises(sqlite3.OperationalError) as caught:
            ApprovalAuthority(store=store).issue(reviewer_id="customer:1", scope=SCOPE)

        assert "disk I/O error" in str(caught.value)


class TestBringUpUnderContention:
    """Construction itself was racy.

    WHY IT WAS MISSED. Every other test constructs the store on an idle
    database. The topology this module exists for — web, rest, mcp and celery
    opening the same file at once on deploy — is the one case where several
    processes construct simultaneously, and none of the tests did that.
    """

    def test_constructing_while_a_peer_holds_the_write_lock_waits(
        self, tmp_path
    ) -> None:
        """Switching a database to WAL needs its exclusive lock.

        AND ``busy_timeout`` DOES NOT COVER IT — measured on one connection with
        the timeout already at 3000ms, the WAL pragma raised after 0.00s while
        ordinary DDL waited the full 3.34s. SQLite does not run the busy handler
        for a journal-mode change, so the fix an independent review prescribed —
        set the timeout before the WAL pragma — changes nothing at all: the
        timeout was already 5000ms from ``sqlite3.connect``'s own default. The
        WAL switch needs an explicit retry, and this test fails against the
        reorder alone.
        """
        db = str(tmp_path / "coldstart.db")
        # A non-WAL file, so the constructor really does have to switch it.
        seed = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
        seed.execute("CREATE TABLE placeholder (x)")
        seed.execute("BEGIN IMMEDIATE")  # a peer mid-write

        def release() -> None:
            time.sleep(0.4)
            seed.execute("COMMIT")
            seed.close()

        releaser = threading.Thread(target=release)
        releaser.start()
        started = time.monotonic()
        store = SqliteApprovalStore(db)  # must WAIT its turn, not fail
        elapsed = time.monotonic() - started
        releaser.join()

        assert elapsed >= 0.3, (
            f"returned in {elapsed:.2f}s — it did not wait for the lock"
        )
        assert store.verify_chain()["ok"] is True
        store.close()


class TestAMultiUseApprovalSurvivesAsMultiUse:
    """``single_use`` has to be in the persisted payload, not just the object.

    WHY IT WAS MISSED. Dropping ``single_use`` from ``_ISSUED_FIELDS`` was a
    SURVIVING MUTANT: replay falls back to the dataclass default of True, so a
    multi-use approval silently becomes single-use after a round-trip and the
    second redemption is refused. Every existing round-trip test used the
    default, where the bug is invisible — a default that matches the field you
    are testing tests nothing.
    """

    def test_it_can_still_be_redeemed_twice_after_a_reload(self, db_path) -> None:
        first = SqliteApprovalStore(db_path)
        record = ApprovalAuthority(store=first).issue(
            reviewer_id="customer:1", scope=SCOPE, single_use=False
        )
        first.close()

        reopened = SqliteApprovalStore(db_path)
        authority = ApprovalAuthority(store=reopened)
        assert authority.check(record.approval_id, scope=SCOPE, consume=True) is True
        assert authority.check(record.approval_id, scope=SCOPE, consume=True) is True

    def test_and_the_flag_itself_survives(self, db_path) -> None:
        first = SqliteApprovalStore(db_path)
        record = ApprovalAuthority(store=first).issue(
            reviewer_id="customer:1", scope=SCOPE, single_use=False
        )
        first.close()

        assert SqliteApprovalStore(db_path).get(record.approval_id).single_use is False

    def test_but_a_single_use_one_is_still_spent_after_a_reload(self, db_path) -> None:
        """POSITIVE CONTROL: the above must not be 'consume never works'."""
        first = SqliteApprovalStore(db_path)
        record = ApprovalAuthority(store=first).issue(
            reviewer_id="customer:1", scope=SCOPE, single_use=True
        )
        first.close()

        authority = ApprovalAuthority(store=SqliteApprovalStore(db_path))
        assert authority.check(record.approval_id, scope=SCOPE, consume=True) is True
        assert authority.check(record.approval_id, scope=SCOPE, consume=True) is False
