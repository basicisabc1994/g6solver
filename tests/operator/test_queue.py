# SPDX-License-Identifier: Apache-2.0
"""The approval queue: no self-approval (audit 011 VEN-003), no auto-approval (VEN-013), generated optional decoys
(VEN-011) and guard promotion only after a person adopts the guard (VEN-012)."""
from __future__ import annotations

import random
import sqlite3

import pytest

from g6.operator import ActionQueue, ExecutionResult, Guard, QueueError, SimulatedExecutor, Task, route, tick
from g6.operator.router import Context

REVIEW = {"state": "qualified-draft", "code": "G6_E_OPERATOR_CHECKS_PASSED"}


def _draft(queue, venture, plan, now, actor="agent-1"):
    email = {"type": "email", "to": "b1", "body": "Hi Sam, the pilot starts Monday.", "verification_contract": plan}
    executor = SimulatedExecutor({"t1": ExecutionResult("drafted", proposals=(email,))}, actor=actor)
    reply = tick(queue, venture, Task("t1", "draft", reserve=0.25), executor, context=Context(contacts=frozenset({"b1"})),
                 now=now)
    [action_id] = reply["queued"]
    return action_id


def test_ven003_the_drafting_actor_cannot_approve_or_send_its_own_draft(queue, venture, plan, now):
    # Archive: venture_report, venture_approve and venture_mark_sent shared one MCP tool set, and the decision
    # recorded that same caller as the reviewer.
    action_id = _draft(queue, venture, plan, now, actor="agent-1")
    for reviewer in ("agent-1", "AGENT-1 ", "", None, "auto:T2"):
        with pytest.raises(QueueError):
            queue.approve(venture, action_id, reviewer=reviewer)
    with pytest.raises(QueueError, match="only after a person approves it"):
        queue.mark_sent(venture, action_id, reviewer="dana", channel="email")
    assert queue.get(venture, action_id)["status"] == "proposed"
    assert queue.decisions(venture) == []
    queue.approve(venture, action_id, reviewer="dana")
    with pytest.raises(QueueError, match="cannot decide it"):
        queue.mark_sent(venture, action_id, reviewer="agent-1", channel="email")
    assert queue.mark_sent(venture, action_id, reviewer="dana", channel="email") is True
    assert [(d["outcome"], d["reviewer"]) for d in queue.decisions(venture)] == [("approved", "dana"), ("sent", "dana")]


def test_ven003_the_database_refuses_a_status_change_without_a_person_decision(queue, venture, plan, now):
    action_id = _draft(queue, venture, plan, now)
    conn = queue.scope.conn
    with pytest.raises(sqlite3.IntegrityError, match="decision recorded by someone other than the drafter"):
        conn.execute("UPDATE actions SET status='approved' WHERE action_id=?", (action_id,))
    conn.rollback()
    conn.execute("INSERT INTO decisions (action_id, venture_id, outcome, reviewer, decoy, text_before, text_after, note,"
                 " at) VALUES (?, ?, 'approved', 'agent-1', 0, '', '', '', 'now')", (action_id, venture))
    with pytest.raises(sqlite3.IntegrityError):   # a decision by the drafter does not count
        conn.execute("UPDATE actions SET status='approved' WHERE action_id=?", (action_id,))
    conn.rollback()
    queue.approve(venture, action_id, reviewer="dana")
    for sql in ("UPDATE decisions SET reviewer='x'", "DELETE FROM decisions"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)
        conn.rollback()


def test_ven013_nothing_is_approved_automatically(queue, venture, plan, now):
    # Archive: a type granted T2 was inserted already "approved" by "auto:T2", even when its draft was unchecked.
    action_id = _draft(queue, venture, plan, now)
    row = queue.get(venture, action_id)
    assert row["status"] == "proposed" and row["review"]["code"] == "G6_E_OPERATOR_NOT_MEASURED"
    assert queue.list(venture, status="approved") == [] and queue.decisions(venture) == []
    with pytest.raises(sqlite3.IntegrityError, match="enters the queue as proposed"):
        queue.scope.conn.execute("INSERT INTO actions (action_id, venture_id, task_id, action_type, body, meta_json,"
                                 " drafted_by, state, review_json, status, decoy, created_at) VALUES ('x', ?, '',"
                                 " 'email', 'b', '{}', 'a', 's', '{}', 'approved', 0, 'now')", (venture,))
    queue.scope.conn.rollback()


def test_reject_needs_a_reason_and_a_decided_draft_is_not_decided_again(queue, venture, plan, now):
    action_id = _draft(queue, venture, plan, now)
    with pytest.raises(QueueError, match="Say why"):
        queue.reject(venture, action_id, reviewer="dana", reason=" ")
    assert queue.reject(venture, action_id, reviewer="dana", reason="wrong day")["status"] == "rejected"
    with pytest.raises(QueueError, match="it cannot be approved"):
        queue.approve(venture, action_id, reviewer="dana")


def _fill(queue, venture, n, prefix="Draft"):
    for i in range(n):
        queue.enqueue(venture, task_id=f"t{i}", action={"type": "post", "body": f"{prefix} number {i}. More text."},
                      drafted_by="agent-1", review=REVIEW)


def test_ven011_decoys_are_off_by_default_and_never_fixed_strings(queue, venture):
    # Archive: every 20th item was one of three fixed G6-marketing decoys ("SOC 2 Type II", "founder of Anthropic").
    _fill(queue, venture, 45)
    rows = queue.list(venture)
    assert len(rows) == 45 and queue.decoy_stats(venture)["planted"] == 0
    assert rows[19]["body"] == "Draft number 19. More text.\n"


def test_ven011_decoys_come_from_the_ventures_own_drafts_at_a_random_cadence(scope):
    a = scope.create_venture("a", caps={"model": 1.0, "cash": 0.0})["venture_id"]
    b = scope.create_venture("b", caps={"model": 1.0, "cash": 0.0})["venture_id"]
    always = ActionQueue(scope, decoy_rate=1.0, rng=random.Random(1))
    _fill(always, a, 1, prefix="Garden tools")
    _fill(always, b, 1, prefix="Tax help")
    decoys = [next(r["body"] for r in always.list(v) if "A customer told us" in r["body"]) for v in (a, b)]
    assert decoys[0] != decoys[1] and "Garden tools" in decoys[0] and "Tax help" in decoys[1]
    for text in decoys:
        assert "SOC 2" not in text and "Anthropic" not in text
    c = scope.create_venture("c", caps={"model": 1.0, "cash": 0.0})["venture_id"]
    _fill(ActionQueue(scope, decoy_rate=0.2, rng=random.Random(7)), c, 80)
    seqs = [i for i, r in enumerate(always.list(c)) if "A customer told us" in r["body"]]
    gaps = {later - earlier for earlier, later in zip(seqs, seqs[1:])}
    assert len(seqs) >= 5 and len(gaps) > 1   # not a fixed modulus


def test_ven011_an_unedited_approval_of_a_decoy_is_counted_and_revealed(scope, venture):
    q = ActionQueue(scope, decoy_rate=1.0, rng=random.Random(3))
    _fill(q, venture, 1)
    decoy = next(r for r in q.list(venture) if "A customer told us" in r["body"])
    out = q.approve(venture, decoy["action_id"], reviewer="dana")
    assert out["decoy"] is True and out["status"] == "voided"
    assert q.decoy_stats(venture) == {"planted": 1, "caught": 0, "approved_unedited": 1}
    with pytest.raises(QueueError, match="planted decoy"):
        q.mark_sent(venture, decoy["action_id"], reviewer="dana", channel="email")
    assert q.decisions(venture) == []   # a test of the reviewer, not a decision about the drafts


def test_ven012_a_shadow_guard_is_not_promoted_until_a_person_adopts_it(queue, venture):
    # Archive: venture_harness_promote checked only guard.status == "shadow" and never read the adoption row.
    def no_discount(action):
        return "discount" not in str(action.get("body", "")).lower()

    guard = Guard("no_discount", no_discount)
    row = queue.propose_guard(venture, guard, proposed_by="learner")
    with pytest.raises(QueueError, match="a person must adopt it"):
        queue.promote_guard(venture, row["action_id"], guard)
    with pytest.raises(QueueError, match="cannot decide it"):
        queue.approve(venture, row["action_id"], reviewer="learner")
    draft = {"type": "post", "body": "A 50% discount for everyone."}
    shadow = route(draft, Context(guards=(guard,)))
    assert shadow.state == "qualified-draft" and shadow.shadow == ({"guard": "no_discount", "would_fire": True},)
    assert queue.approve(venture, row["action_id"], reviewer="dana")["status"] == "adopted"
    with pytest.raises(QueueError, match="not the guard that was adopted"):
        queue.promote_guard(venture, row["action_id"], Guard("no_discount", lambda a: True))
    promoted = queue.promote_guard(venture, row["action_id"], guard)
    assert route(draft, Context(guards=(promoted,))).fired == ("guard:no_discount",)
    with pytest.raises(QueueError, match="internal to the operator"):
        queue.mark_sent(venture, row["action_id"], reviewer="dana", channel="email")
