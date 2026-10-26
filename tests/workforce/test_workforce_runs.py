# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The attempt ledger: worker instances, runs, artifacts and verdicts (workforce OS plan, P0 T6).

Pinned: one worker instance per attempt, never reused; a run opens and closes (or is abandoned) with
its event in one transaction, and a closed run never changes; an artifact is an evidence row bound to
its run, producer and digest; a ``verified`` verdict is refused BY THE DATABASE unless an executed,
independent, read-only, model-free oracle passed with no model calls, so no code path can write one
otherwise; another owner sees and moves nothing. Offline, temp db.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from types import SimpleNamespace

import pytest
from g6.safety_engine.schema import RuntimePolicy
from g6.workforce import store
from g6.workforce import contracts as wc
from g6.workforce import missions as wm
from g6.workforce import runs as wr
from g6.workforce import workers as ww

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}


def _worker(read_only: bool = False) -> ww.WorkerSpec:
    policy = RuntimePolicy(max_shell_commands=0, max_files_modified=0, max_lines_changed=0,
                           allowed_tools=frozenset({"solver.solve"}))
    return ww.WorkerSpec(template="verifier" if read_only else "doer", purpose="p",
                         rung="verify" if read_only else "deterministic", allowed=(("solver", "solve"),),
                         policy=policy, read_only=read_only)


def _active(vstore, store_, venture: str) -> str:
    """A mission standing as active (how a mission gets there is the graph's tests; runs open only then)."""
    mid = store_.create(venture, objective="Sum the ledger", risk_tier="R1", budget_cents=0, actor="chat")
    with vstore.conn:
        vstore.conn.execute("UPDATE missions SET status='active' WHERE mission_id=?", (mid,))
    return mid


@pytest.fixture
def rig(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    alice = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    bob = vstore.create_venture("bob", "Bob's", **_CAPS)["venture_id"]
    a, b = wm.MissionStore(vstore.for_owner("alice")), wm.MissionStore(vstore.for_owner("bob"))
    return SimpleNamespace(vstore=vstore, a=a, mid=_active(vstore, a, alice), ledger=wr.RunLedger(a),
                           other=wr.RunLedger(b), bob_mid=_active(vstore, b, bob),
                           second_mid=_active(vstore, a, alice))


def _closed(rig, unit="sum", content=None):
    run = rig.ledger.open(rig.mid, unit, _worker())
    artifact = rig.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED, source="solver.solve"),
                                artifact={"schema": "total", "content": content or {"total": 6}}, model_calls=0)
    return run, artifact


def test_each_attempt_opens_a_new_instance_and_run(rig):
    first = rig.ledger.open(rig.mid, "sum", _worker())
    rig.ledger.abandon(first.run_id, "the process died")
    second = rig.ledger.open(rig.mid, "sum", _worker())
    assert (first.attempt, second.attempt) == (1, 2) and first.instance_id != second.instance_id
    assert rig.ledger.attempts(rig.mid, "sum") == 2
    assert [e["kind"] for e in rig.a.events(rig.mid) if e["kind"].startswith("run.")] == [
        "run.opened", "run.closed", "run.opened"]


def test_a_closed_run_holds_its_outcome_and_artifact(rig):
    run, artifact_id = _closed(rig)
    row = rig.ledger.run(rig.mid, run.run_id)
    assert (row["state"], row["completion_state"], row["artifact_id"]) == ("closed", wc.QUALIFIED, artifact_id)
    art = rig.ledger.artifact(rig.mid, artifact_id)
    assert art["content"] == {"total": 6} and art["run_id"] == run.run_id and art["producer"] == run.instance_id
    assert len(art["sha256"]) == 64
    assert rig.ledger.last_artifact(rig.mid, "sum")["artifact_id"] == artifact_id
    assert rig.ledger.instance(rig.mid, run.instance_id)["status"] == "finished"


def test_a_blocked_attempt_stores_no_artifact(rig):
    run = rig.ledger.open(rig.mid, "sum", _worker())
    assert rig.ledger.close(run.run_id, wc.Outcome(wc.BLOCKED, reasons=("refused",)),
                            artifact={"schema": "t", "content": {"x": 1}}) == ""
    assert rig.ledger.last_artifact(rig.mid, "sum") is None


@pytest.mark.parametrize("sql", [
    "UPDATE task_runs SET completion_state='verified'", "DELETE FROM task_runs",
    "INSERT OR REPLACE INTO task_runs SELECT * FROM task_runs",
    "UPDATE worker_instances SET read_only=1", "DELETE FROM worker_instances",
    "UPDATE worker_instances SET status='running'",
])
def test_a_closed_run_and_a_finished_instance_never_change(rig, sql):
    _closed(rig)
    with pytest.raises(sqlite3.DatabaseError), rig.vstore.conn:
        rig.vstore.conn.execute(sql)


def test_a_run_closes_once(rig):
    run, _ = _closed(rig)
    with pytest.raises(ValueError):
        rig.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED))
    with pytest.raises(ValueError):
        rig.ledger.abandon(run.run_id, "late")


def test_a_failed_close_event_leaves_the_run_open_and_no_artifact(rig):
    run = rig.ledger.open(rig.mid, "sum", _worker())
    rig.vstore.conn.execute("CREATE TEMP TRIGGER inject_close BEFORE INSERT ON main.evidence"
                            " WHEN NEW.kind='run.closed' BEGIN SELECT RAISE(ABORT, 'injected'); END;")
    with pytest.raises(sqlite3.DatabaseError):
        rig.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED), artifact={"schema": "t", "content": {"x": 1}})
    assert rig.ledger.run(rig.mid, run.run_id)["state"] == "open"
    assert rig.a.events(rig.mid, kind="artifact") == []


def test_an_artifact_too_large_to_store_inline_is_refused(rig):
    run = rig.ledger.open(rig.mid, "sum", _worker())
    with pytest.raises(ValueError, match="too large"):
        rig.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED), artifact={"schema": "t", "content": {"x": "y" * 70_000}})


# --- verdicts -------------------------------------------------------------------------------------


#: What the verify rung records for an oracle call that was executed and answered.
_ORACLE_CALL = {"component": "solver", "op": "solve", "state": "qualified-draft", "degraded": False, "refused": False,
                "model_calls": 0}


def _record(rig, artifact_id: str, verdict: wc.Verdict, **over) -> wr.VerdictRecord:
    """The record of a verdict on ``artifact_id`` as the verify rung builds it (producer and digest from the
    artifact itself), with any field overridden."""
    art = rig.ledger.artifact(rig.mid, artifact_id)
    fields = {"verifies_key": "sum", "artifact_id": artifact_id, "producer_instance": art["producer"],
              "verdict": verdict, "model_free": True, "artifact_sha": art["sha256"], "authority": "A4"}
    fields.update(over)
    return wr.VerdictRecord(**fields)


def _verdict(rig, calls=(_ORACLE_CALL,), **over):
    _produced, artifact_id = _closed(rig)
    check = rig.ledger.open(rig.mid, "check-sum", _worker(read_only=True))
    fields = {"verifier_kind": "formal", "result": "pass", "executed": True, "independent": True, "model_calls": 0}
    model_free = over.pop("model_free", True)
    authority = over.pop("authority", "A4")
    fields.update(over)
    verdict = wc.Verdict(**fields)
    rig.ledger.close(check.run_id, verdict.outcome(), calls=calls,
                     verdict=_record(rig, artifact_id, verdict, model_free=model_free, authority=authority))
    return rig.ledger.verdicts(rig.mid, "sum")[-1]


def test_a_clean_oracle_pass_is_stored_verified(rig):
    row = _verdict(rig)
    assert row["state"] == wc.VERIFIED and row["result"] == "pass"


def test_a_finding_the_ledger_refuses_keeps_no_elicitation_record(rig):
    """FE §28 (E10c): a check whose finding the ledger refused performed no check on the output, so its close keeps no
    failure elicitation record, as it keeps no mechanisms. Control: the same close, with the finding bound, keeps both."""
    record = {"work_unit_id": "sum", "elicitation_strategies": ["test_execution"]}
    kept = {}
    for case in ("bound", "stale"):
        _produced, artifact_id = _closed(rig)
        if case == "stale":
            _closed(rig, content={"total": 7})  # a newer output of the unit: the one judged is stale, the finding refused
        check = rig.ledger.open(rig.mid, "check-sum", _worker(read_only=True))
        verdict = wc.Verdict(verifier_kind="formal", result="pass", executed=True, independent=True, model_calls=0)
        rig.ledger.close(check.run_id, verdict.outcome(), calls=(_ORACLE_CALL,), verdict=_record(rig, artifact_id, verdict),
                         mechanisms=({"mechanism": "formal_proof_check"},), elicitation=record)
        kept[case] = next(e["detail"] for e in rig.a.events(rig.mid, kind="run.closed")
                          if e["detail"]["run_id"] == check.run_id)
    assert kept["bound"]["elicitation"]["elicitation_strategies"] == ["test_execution"]
    assert kept["bound"]["verification"] == [{"mechanism": "formal_proof_check"}]
    assert "elicitation" not in kept["stale"] and kept["stale"]["verification"] == []


@pytest.mark.parametrize("over", [{"executed": False}, {"independent": False}, {"model_calls": 1},
                                  {"verifier_kind": "model_critic"}, {"model_free": False}, {"authority": "A3"}])
def test_a_pass_that_cannot_verify_is_stored_qualified_never_verified(rig, over):
    assert _verdict(rig, **over)["state"] == wc.QUALIFIED


_RAW = ("INSERT INTO verdicts (verdict_id, run_id, mission_id, unit_key, verifies_key, artifact_id, artifact_sha,"
        " producer_instance, verifier_instance, verifier_kind, executed, independent, model_calls, model_free, result,"
        " state, reasons_json, created_at, authority) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'[]','2026-10-01',?)")


def _checker(rig, *, read_only: bool = True, calls=(_ORACLE_CALL,), outcome: str = wc.VERIFIED):
    """A verify run that closed (with what its oracle call recorded), for a raw verdict row to cite."""
    run = rig.ledger.open(rig.mid, "check-sum", _worker(read_only=read_only))
    rig.ledger.close(run.run_id, wc.Outcome(outcome, source="solver.solve"), calls=calls)
    return run


#: How each case's checker run closed, and which field of the verdict row it breaks.
_BAD = {
    "executed": ({}, {"executed": 0}), "independent": ({}, {"independent": 0}),
    "model_calls": ({}, {"model_calls": 2}), "model_free": ({}, {"model_free": 0}),
    "result": ({}, {"result": "fail"}), "kind": ({}, {"kind": "model_critic"}),
    "same_instance": ({}, {"producer": "<checker>"}), "writer_verifier": ({"read_only": False}, {}),
    "unknown_producer": ({}, {"producer": "nobody"}),
    # Codex review of T4-T6: the row is bound to the artifact it judges and to its own executed run.
    "no_run": ({}, {"run": ""}), "unexecuted_run": ({"calls": ()}, {}),
    "refused_call": ({"calls": ({**_ORACLE_CALL, "refused": True},)}, {}),
    "unverified_run": ({"outcome": wc.QUALIFIED}, {}), "wrong_digest": ({}, {"sha": "0" * 64}),
    "stale_artifact": ({}, {"artifact": "<stale>"}), "foreign_artifact": ({}, {"artifact": "<foreign>"}),
    "authority": ({}, {"authority": "A3"}),
}


@pytest.mark.parametrize("bad", sorted(_BAD))
def test_the_database_refuses_a_verified_verdict_without_its_proof(rig, bad):
    produced, artifact_id = _closed(rig)
    checker_kw, breaks = _BAD[bad]
    checker = _checker(rig, **checker_kw)
    art = rig.ledger.artifact(rig.mid, artifact_id)
    row = {"executed": 1, "independent": 1, "model_calls": 0, "model_free": 1, "result": "pass", "kind": "formal",
           "producer": produced.instance_id, "run": checker.run_id, "sha": art["sha256"], "artifact": artifact_id,
           "authority": "A4"}
    row.update(breaks)
    if row["producer"] == "<checker>":
        row["producer"] = checker.instance_id
    if row["artifact"] == "<stale>":
        _closed(rig, content={"total": 7})  # a newer artifact of the same unit: the one judged is stale
        row["artifact"] = artifact_id
    if row["artifact"] == "<foreign>":  # another mission's artifact, with that artifact's own producer and digest
        other = rig.ledger.open(rig.second_mid, "sum", _worker())
        row["artifact"] = rig.ledger.close(other.run_id, wc.Outcome(wc.QUALIFIED),
                                           artifact={"schema": "total", "content": {"total": 6}})
        row["producer"] = other.instance_id
        row["sha"] = rig.ledger.artifact(rig.second_mid, row["artifact"])["sha256"]

    def values(vid: str, run: str, verifier: str, artifact: str, producer: str, sha: str, **r) -> tuple:
        return (vid, run, rig.mid, "check-sum", "sum", artifact, sha, producer, verifier, r["kind"], r["executed"],
                r["independent"], r["model_calls"], r["model_free"], r["result"], "verified", r["authority"])

    with pytest.raises(sqlite3.IntegrityError), rig.vstore.conn:
        rig.vstore.conn.execute(_RAW, values("v1", verifier=checker.instance_id, **row))
    # The survival control: a clean checker's row with its proof, on the unit's latest artifact, is stored.
    clean = _checker(rig)
    latest = rig.ledger.last_artifact(rig.mid, "sum")
    with rig.vstore.conn:
        rig.vstore.conn.execute(_RAW, values(
            "v2", clean.run_id, clean.instance_id, latest["artifact_id"], latest["producer"], latest["sha256"],
            kind="formal", executed=1, independent=1, model_calls=0, model_free=1, result="pass", authority="A4"))


@pytest.mark.parametrize("bad", ["foreign_owner", "stale", "wrong_digest", "wrong_producer", "no_oracle_call"])
def test_a_verdict_not_bound_to_the_latest_artifact_and_an_executed_oracle_is_never_verified(rig, bad):
    """Codex: a passing VerdictRecord naming another tenant's artifact was stored verified, with calls_json=[]."""
    produced, artifact_id = _closed(rig)
    verdict = wc.Verdict(verifier_kind="formal", result="pass", executed=True, independent=True, model_calls=0)
    record, calls = _record(rig, artifact_id, verdict), (_ORACLE_CALL,)
    if bad == "foreign_owner":
        theirs = rig.other.open(rig.bob_mid, "sum", _worker())
        foreign = rig.other.close(theirs.run_id, wc.Outcome(wc.QUALIFIED), artifact={"schema": "t", "content": {}})
        record = dataclasses.replace(record, artifact_id=foreign, producer_instance=theirs.instance_id)
    elif bad == "stale":
        _closed(rig, content={"total": 7})
    elif bad == "wrong_digest":
        record = dataclasses.replace(record, artifact_sha="0" * 64)
    elif bad == "wrong_producer":
        record = dataclasses.replace(record, producer_instance="nobody")
    else:
        calls = ()
    check = rig.ledger.open(rig.mid, "check-sum", _worker(read_only=True))
    rig.ledger.close(check.run_id, verdict.outcome(), calls=calls, verdict=record)
    row = rig.ledger.verdicts(rig.mid, "sum")[-1]
    assert row["state"] != wc.VERIFIED and rig.ledger.run(rig.mid, check.run_id)["completion_state"] != wc.VERIFIED
    assert produced.instance_id  # the producer stood; only the binding was broken


def test_a_verdict_row_never_outranks_its_run(rig):
    """Codex: the oracle's value held but its reply was blocked; the run was blocked, the verdict row verified."""
    _produced, artifact_id = _closed(rig)
    verdict = wc.Verdict(verifier_kind="formal", result="pass", executed=True, independent=True, model_calls=0)
    check = rig.ledger.open(rig.mid, "check-sum", _worker(read_only=True))
    rig.ledger.close(check.run_id, wc.Outcome(wc.BLOCKED, reasons=("the oracle's reply was blocked-escalated",)),
                     calls=(_ORACLE_CALL,), verdict=_record(rig, artifact_id, verdict))
    row = rig.ledger.verdicts(rig.mid, "sum")[-1]
    assert (row["result"], row["state"]) == ("pass", wc.BLOCKED)
    assert any("blocked-escalated" in r for r in json.loads(row["reasons_json"]))


def test_verdicts_are_append_only(rig):
    _verdict(rig)
    for sql in ("UPDATE verdicts SET state='verified'", "DELETE FROM verdicts",
                "INSERT OR REPLACE INTO verdicts SELECT * FROM verdicts"):
        with pytest.raises(sqlite3.DatabaseError), rig.vstore.conn:
            rig.vstore.conn.execute(sql)


# --- owners ---------------------------------------------------------------------------------------


def test_another_owner_sees_and_moves_nothing(rig):
    run, artifact_id = _closed(rig)
    calls = {
        "open": lambda m: rig.other.open(m, "sum", _worker()),
        "runs": lambda m: rig.other.runs(m), "attempts": lambda m: rig.other.attempts(m, "sum"),
        "run": lambda m: rig.other.run(m, run.run_id), "artifact": lambda m: rig.other.artifact(m, artifact_id),
        "verdicts": lambda m: rig.other.verdicts(m),
    }
    for name, call in calls.items():
        with pytest.raises(LookupError) as foreign:
            call(rig.mid)
        with pytest.raises(LookupError) as unknown:
            call("no-such-mission")
        assert str(foreign.value) == str(unknown.value) == store.NOT_FOUND, name
    for call in (lambda: rig.other.close(run.run_id, wc.Outcome(wc.QUALIFIED)),
                 lambda: rig.other.abandon(run.run_id, "x")):
        with pytest.raises(LookupError):
            call()


def test_the_database_holds_a_unit_to_one_open_attempt_and_runs_to_an_active_mission(rig):
    run = rig.ledger.open(rig.mid, "sum", _worker())
    row = dict(rig.vstore.conn.execute("SELECT * FROM task_runs WHERE run_id=?", (run.run_id,)).fetchone())
    insert = ("INSERT INTO task_runs (run_id, mission_id, unit_key, attempt, instance_id, rung, started_at)"
              " VALUES (?,?,?,?,?,?,?)")
    with pytest.raises(sqlite3.IntegrityError), rig.vstore.conn:  # a second open attempt at the same unit
        rig.vstore.conn.execute(insert, ("r2", rig.mid, "sum", 2, row["instance_id"], "deterministic", "t"))
    with rig.vstore.conn:
        rig.vstore.conn.execute("UPDATE missions SET status='waiting' WHERE mission_id=?", (rig.second_mid,))
    with pytest.raises(sqlite3.IntegrityError), rig.vstore.conn:  # a mission that is not active
        rig.vstore.conn.execute(insert, ("r3", rig.second_mid, "sum", 1, "wi_x", "deterministic", "t"))
    with rig.vstore.conn:  # the survival control: another unit's first attempt on the active mission
        rig.vstore.conn.execute(insert, ("r4", rig.mid, "check-sum", 1, "wi_y", "verify", "t"))
