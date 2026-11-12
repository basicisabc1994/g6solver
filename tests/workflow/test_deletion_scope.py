# SPDX-License-Identifier: Apache-2.0
"""Deletion by run or task reaches every store of the storage policy (review revs17a, finding 2; class: a guard that
covers less than it guards). The rule for each file comes from one table (``policy.DELETION``); an entry bound to the
deleted run or task is removed wherever it is kept, and what is kept by design is named in the reply."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from g6.harness.workflow import lifecycle, policy
from g6.harness.workflow import record as rec
from g6.harness.workflow.decisions import Decisions
from g6.harness.workflow.feedback import FeedbackStore
from tests.workflow.kit import OTHER_TASK, PROJECT, T0, TASK, at, event, sha, started

MARK = "OKAPI-MARKER-3307"


def _folder(home):
    return home / "workflow" / PROJECT


def _populate(task: str, origin: str, *, seconds: float = 0) -> str:
    """One run of ``task`` with an annotation, a task and a project decision, volunteered feedback, and an invitation
    that was closed: an entry bound to the task (and the run) in every store that holds entries."""
    record = rec.ProjectRecord(PROJECT)
    run = started(record, origin, task=task, seconds=seconds)
    first = record.events(run=run)[0]
    record.annotate(event(run, "annotation", origin, task=task, seconds=seconds + 1, provenance="attributed_label",
                          data={"target": first.event_id, "note": f"{MARK} {origin}", "by": "P"}))
    decisions = Decisions(PROJECT)
    decisions.record(sha(origin + "t"), "dismissed", task=task, by="P", at=at(seconds + 2))
    decisions.record(sha(origin + "p"), "dismissed", task=task, scope="project", by="P", at=at(seconds + 3))
    store = FeedbackStore(PROJECT)
    store.unsolicited(task, f"{MARK} {origin}", relayed_by="the agent", at=at(seconds + 4))
    invitation = store.reserve_invitation(task, now=T0 + timedelta(days=8 * (seconds > 0), seconds=seconds + 5))
    store.close(invitation, "skipped", option="", shown_at=at(seconds + 5), back_at=at(seconds + 6), seconds=1.0)
    return run


def _bound(home, *, task: str = "", run: str = "") -> list[str]:
    """Every entry in every chained file of the project that names ``task`` or ``run`` as its own."""
    found = []
    for path in sorted(_folder(home).rglob("*.jsonl")):
        if path.name == policy.RECEIPTS:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            body = json.loads(line)["body"]
            if (task and body.get("task") == task) or (run and body.get("run") == run):
                found.append(f"{path.relative_to(_folder(home))}: {json.loads(line)['kind']}")
    return found


def test_every_file_of_the_policy_has_a_deletion_rule_and_each_entry_store_a_binding():
    assert set(policy.DELETION) == set(policy.FILES)
    assert set(policy.DELETION.values()) <= {"file", "entries", "rebuilt", "kept", "person", "sidecar"}
    entries = {name for name, rule in policy.DELETION.items() if rule == "entries"}
    assert entries == set(lifecycle.BINDINGS)
    assert {name for name, rule in policy.DELETION.items() if rule == "sidecar"} == set(policy.SIDECARS)
    assert {name for name, rule in policy.DELETION.items() if rule == "person"} == set(policy.PERSON_FILES)
    assert {name for name, rule in policy.DELETION.items() if rule == "rebuilt"} == {policy.INSPECTION}


def test_deleting_a_run_removes_every_entry_bound_to_it_in_every_store(home):
    run = _populate(TASK, "session-a")
    other = _populate(TASK, "session-b", seconds=100)
    assert _bound(home, run=run) and _bound(home, run=other)
    result = lifecycle.delete(PROJECT, scope="run", ident=run, by="P", now=T0 + timedelta(days=30))
    assert result["status"] == "deleted"
    assert _bound(home, run=run) == []
    assert len(_bound(home, run=other)) == 2                         # its run_started and its annotation stay
    record = rec.ProjectRecord(PROJECT)
    assert [n.run for n in record.annotations(TASK)] == [other]
    assert MARK + " session-a" not in json.dumps([n.to_body() for n in record.annotations(TASK)])
    assert set(result["kept"]) == {policy.RECEIPTS, policy.CORRECTIONS}


def test_deleting_a_task_removes_every_entry_bound_to_it_in_every_store(home):
    _populate(TASK, "session-a")
    _populate(OTHER_TASK, "session-c", seconds=100)
    result = lifecycle.delete(PROJECT, scope="task", ident=TASK, by="P", now=T0 + timedelta(days=30))
    assert result["status"] == "deleted"
    assert _bound(home, task=TASK) == []
    assert not (_folder(home) / "tasks" / TASK).exists()
    store = FeedbackStore(PROJECT)
    assert {e["kind"] for e in store.entries()} == {"unsolicited", "invited", "closed"}
    assert all(e["body"].get("task") in (None, OTHER_TASK) for e in store.entries())
    assert MARK + " session-a" not in json.dumps(store.entries())
    assert {b["intervention"] for b in Decisions(PROJECT).held(task=OTHER_TASK)} == {sha("session-cp"), sha("session-ct")}
    assert set(result["kept"]) == {policy.RECEIPTS, policy.CORRECTIONS}


def test_a_task_with_only_feedback_can_be_deleted_by_task_scope(home):
    FeedbackStore(PROJECT).unsolicited(TASK, MARK, relayed_by="the agent", at=at())
    FeedbackStore(PROJECT).opt("in", by="P", at=at(1), via="corrections")
    result = lifecycle.delete(PROJECT, scope="task", ident=TASK, by="P", now=T0)
    assert result["status"] == "deleted" and result["removed"] >= 1
    assert MARK.encode() not in (_folder(home) / policy.FEEDBACK).read_bytes()
    assert [e["kind"] for e in FeedbackStore(PROJECT).entries()] == ["opt"]      # bound to no task: kept
    assert lifecycle.delete(PROJECT, scope="task", ident=TASK, by="P", now=T0)["status"] == "refused"


def test_a_corrections_file_can_delete_a_task_that_has_only_feedback(home):
    FeedbackStore(PROJECT).unsolicited(TASK, MARK, relayed_by="the agent", at=at())
    (_folder(home) / policy.CORRECTIONS).write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [
        {"action": "delete", "scope": "task", "id": TASK, "by": "P"}]}), encoding="utf-8")
    assert lifecycle.apply_corrections(PROJECT, now=T0)["applied"] == 1
    assert FeedbackStore(PROJECT).entries() == []


def test_deleting_the_last_entries_of_a_store_removes_its_files(home):
    FeedbackStore(PROJECT).unsolicited(TASK, MARK, relayed_by="the agent", at=at())
    lifecycle.delete(PROJECT, scope="task", ident=TASK, by="P", now=T0)
    left = sorted(p.name for p in _folder(home).iterdir() if not p.name.endswith(policy.LOCK))
    assert left == [policy.RECEIPTS]


def test_a_store_that_cannot_be_read_is_removed_whole_and_named(home):
    run = _populate(TASK, "session-a")
    path = _folder(home) / policy.FEEDBACK
    path.write_bytes(path.read_bytes()[:-20])
    with pytest.raises(rec.RecordUnreadable):
        FeedbackStore(PROJECT).entries()
    result = lifecycle.delete(PROJECT, scope="run", ident=run, by="P", now=T0)
    assert result["status"] == "deleted" and result.get("unreadable_removed") in (None, [])     # a run binds no feedback
    result = lifecycle.delete(PROJECT, scope="task", ident=TASK, by="P", now=T0)
    assert result["status"] == "deleted" and result["unreadable_removed"] == [policy.FEEDBACK]
    assert not path.exists() and FeedbackStore(PROJECT).entries() == []
