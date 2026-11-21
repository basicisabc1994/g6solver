# SPDX-License-Identifier: Apache-2.0
"""The repair of the typed-reader branch: a declared field is read first; where none is declared, the base's exact
reading stays; a declaration may only tighten a gate, never open one. Each test here failed at the branch tip."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

# --- slice 1: actor kind and principals (causes A, A2, A3) -------------------------------------------------------------

H1, H2 = "a" * 64, "b" * 64


def _proposal():
    from g6.core.adaptation_gate import AdaptationProposal

    return AdaptationProposal(proposal_id="p1", proposer="learner", source_case_ids=("c1",),
                              target_component="harness.hints", intended_change="add hint h3",
                              predicted_benefit="fewer unit errors", falsifiers=("no change",),
                              test_plan="held-out split", candidate_hash=H1, baseline_hash=H2,
                              transfer_hypothesis="similar tasks", cost_cap=1.0, rollback="drop h3")


def _decide(reviewer, **extra):
    from g6.core.adaptation_gate import Control, decide_promotion, validate_locally

    p = _proposal()
    controls = [Control("neg", "negative", {"bad": True}, "task-author"),
                Control("pos", "positive", {"bad": False}, "task-author")]
    v = validate_locally(p, lambda case: bool(case["bad"]), controls)
    return decide_promotion(p, v, policy_version="v", reviewer=reviewer, owner_confirmed=True, regression="pass",
                            budget="pass", security="pass", **extra)


def test_the_auto_marker_decides_and_a_declared_kind_only_tightens():
    from g6.core.adaptation_gate import is_automated

    assert is_automated("auto:loop", actor_kind="person") is True
    assert is_automated(" AUTO:loop") is True
    assert is_automated("Ada") is False
    assert is_automated("Ada", actor_kind="person") is False
    assert is_automated("Ada", actor_kind="automated") is True


@pytest.mark.parametrize("reviewer, disposition, reasons, digest", [
    # literals from the base's decide_promotion for the same inputs
    ("owner", "shadow_approved", ("all mandatory inputs passed; shadow use only",),
     "f1c4a1af73a49b1b42803c3c37ff4e4d514ba1108d48c8461fff1d25720367e0"),
    ("auto:ci-bot", "blocked", ("the reviewer is an automated name (auto:), not a person",),
     "89e5de4fc4304516c0d3249d95d6994fc955ab45e65581e1ea8cef1e569b14b9"),
    ("Learner ", "blocked", ("the reviewer is the proposer",),
     "a058131a3a745ca9144677e0a29b09e52397f2e538f6034da79c53c19c0d88b9"),
])
def test_a_promotion_decision_for_base_inputs_is_the_base_decision(reviewer, disposition, reasons, digest):
    d = _decide(reviewer)
    assert (d.disposition, d.reasons, d.digest) == (disposition, reasons, digest)


def test_a_declared_kind_other_than_person_blocks_a_promotion():
    d = _decide("owner", reviewer_kind="automated")
    assert d.disposition == "blocked" and any("automated" in r for r in d.reasons)


def test_a_shadow_adoption_that_names_a_person_is_refused():
    from g6.metaharness import shadow

    body = {"by": "Ada", "scope": shadow.SCOPE, "version": 1, "decision_digest": "d", "profile": {"x": 1}}
    assert "an adoption is the loop's own: it never names a person" in shadow._rules("adopt", "1", body, {})
    body["by"] = shadow.ADOPTER
    assert shadow._rules("adopt", "1", body, {}) == []


def test_a_stored_cycle_log_with_a_persons_rows_is_read_back(tmp_path):
    from g6.core.ledger import ChainLedger
    from g6.self_maintenance.envelope import CycleLog

    path = tmp_path / "cycles.jsonl"
    plain = ChainLedger(path)
    plain.append("enrolled", "1", {"by": "Ada"})
    plain.append("paused", "2", {"reason": "a person paused it"})
    assert [kind for kind, _ in CycleLog(path)._rows()] == ["enrolled", "paused"]


def test_a_store_activation_without_a_proposer_reports_and_never_raises():
    from g6.metaharness.store import _store_rules

    problems = _store_rules("version", "0", {"kind": "activate", "version": 0, "accepted_by": "Ada"}, {})
    assert problems and "activate: the acceptor is the proposer" not in problems


def _queue(tmp_path):
    from g6.operator import ActionQueue, OperatorStore

    store = OperatorStore(tmp_path / "operator.db")
    scope = store.for_owner("dana")
    venture = scope.create_venture("newsletter", caps={"model": 1.0, "cash": 500.0})["venture_id"]
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    return store, ActionQueue(scope, clock=lambda: now), venture


def _draft(queue, venture):
    reply = queue.enqueue(venture, task_id="t1", action={"type": "post", "body": "The pilot starts Monday."},
                          drafted_by="agent-1",
                          review={"state": "qualified-draft", "code": "G6_E_OPERATOR_CHECKS_PASSED"})
    return reply["action_id"]


def test_an_auto_name_declared_a_person_cannot_decide_and_a_named_person_can(tmp_path):
    from g6.operator import QueueError
    from g6.operator.queue import _person

    with pytest.raises(QueueError, match="never recorded"):
        _person("auto:bot", "drafter", actor_kind="person")
    with pytest.raises(QueueError):
        _person("Bob", "drafter", actor_kind="automated")
    store, queue, venture = _queue(tmp_path)
    try:
        first, second = _draft(queue, venture), _draft(queue, venture)
        with pytest.raises(QueueError):
            queue.approve(venture, first, reviewer="auto:bot", reviewer_kind="person")
        assert queue.get(venture, first)["status"] == "proposed"
        assert queue.approve(venture, second, reviewer="dana")["status"] in ("approved", "voided", "caught")
    finally:
        store.close()


def test_the_queue_trigger_refuses_an_auto_name_in_sql():
    import sqlite3

    from g6.operator.queue import _SCHEMA

    with sqlite3.connect(":memory:") as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO actions (action_id, venture_id, task_id, action_type, body, meta_json, drafted_by, "
                     "state, review_json, status, decoy, created_at) VALUES ('a', 'v', 't', 'email', 'd', '{}', "
                     "'Alice', 'qualified-draft', '{}', 'proposed', 0, 'now')")
        conn.execute("INSERT INTO decisions (action_id, venture_id, outcome, reviewer, reviewer_kind, decoy, "
                     "text_before, text_after, note, at) VALUES ('a', 'v', 'approved', 'auto:bot', 'person', 0, 'd', "
                     "'d', '', 'now')")
        with pytest.raises(sqlite3.DatabaseError, match="needs a decision recorded"):
            conn.execute("UPDATE actions SET status='approved' WHERE action_id='a'")
        # a pure-SQL trigger: no connection needs a Python function registered to change a status
        conn.execute("INSERT INTO decisions (action_id, venture_id, outcome, reviewer, reviewer_kind, decoy, "
                     "text_before, text_after, note, at) VALUES ('a', 'v', 'approved', 'Bob', 'unknown', 0, 'd', "
                     "'d', '', 'now')")
        conn.execute("UPDATE actions SET status='approved' WHERE action_id='a'")
        assert conn.execute("SELECT status FROM actions").fetchone()[0] == "approved"

