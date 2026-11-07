"""Passing controls for the review's transport, ordering and durability attacks."""
import importlib
import inspect
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.harness import behaviour_question as bq
from g6.harness import question_store as qs
from g6.harness.confirmed_expectation import Answer
from g6.mcp import behaviour
from .helpers import Dialog, TASK, ask, candidate, repo


@pytest.mark.parametrize("raw,want", [(None, 1), ("", 0), ("0", 0), ("1", 1), ("10", 10),
                                      ("11", 0), ("-1", 0), ("garbage", 0)])
def test_budget_edges(raw, want, monkeypatch):
    if raw is None:
        monkeypatch.delenv(qs.BUDGET_ENV, raising=False)
    else:
        monkeypatch.setenv(qs.BUDGET_ENV, raw)
    assert qs.default_budget()[0] == want


@pytest.mark.parametrize("action,content", [("decline", {"choice": "A", "name": "Maintainer"}),
                                           ("cancel", {"choice": "A", "name": "Maintainer"}),
                                           ("accept", {"choice": "skip", "name": "Maintainer"}),
                                           ("unknown_status", {"choice": "A", "name": "Maintainer"}),
                                           ("accept", {"choice": "A"})])
def test_pending_statuses_cannot_confirm_or_spend_twice(tmp_path, action, content):
    root = repo(tmp_path)
    first = ask(root, dialog=Dialog(content, action))
    assert first["status"] == "pending"
    assert behaviour.enforced(str(root), TASK) is None
    second = ask(root)
    assert second["status"] == "confirmed"
    assert second["question_sha256"] == first["question_sha256"]
    assert second["budget"]["asked"] == 1


@pytest.mark.parametrize("damage", ["torn", "garbage", "empty", "reordered", "foreign_task"])
def test_damaged_or_foreign_store_refuses(tmp_path, damage):
    root = repo(tmp_path)
    assert ask(root)["status"] == "confirmed"
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    data = store.path.read_bytes()
    if damage == "torn":
        store.path.write_bytes(data[:-20])
    elif damage == "garbage":
        store.path.write_bytes(data + b"not JSON\n")
    elif damage == "empty":
        store.path.write_bytes(b"")
    elif damage == "reordered":
        store.path.write_bytes(b"\n".join(reversed(data.splitlines())) + b"\n")
    else:
        other = qs.TaskQuestions(qs.task_identity(root, TASK + "other"))
        other.path.write_bytes(data)
        other.head.write_bytes(store.head.read_bytes())
        store = other
    with pytest.raises(qs.StoreUnreadable):
        store.state()


def test_restart_after_append_before_head_keeps_the_spent_slot(tmp_path, monkeypatch):
    one = bq.caller_candidates(TASK, [candidate()])[0]
    question, rule = one.questions("b" * 64)
    store = qs.TaskQuestions(digest("fixture"))

    class Stop(Exception):
        pass

    with monkeypatch.context() as local:
        local.setattr(qs.TaskQuestions, "_keep", lambda *a: (_ for _ in ()).throw(Stop()))
        with pytest.raises(Stop):
            store.ask(question, rule, one)
    fresh = qs.TaskQuestions(store.identity)
    assert fresh.state().left == 0
    assert fresh.state().pending.question.sha256 == question.sha256


def test_unknown_or_duplicate_answer_is_refused(tmp_path):
    store = qs.TaskQuestions(digest("fixture"))
    one = bq.caller_candidates(TASK, [candidate()])[0]
    question, rule = one.questions("b" * 64)
    answer = Answer(question_sha256=question.sha256, option="A", answered_by="person:Maintainer",
                    answered_at="2026-11-08T00:00:00Z")
    with pytest.raises(qs.AnswerRefused):
        store.answer(answer)
    store.ask(question, rule, one)
    store.answer(answer)
    with pytest.raises(qs.AnswerRefused):
        store.answer(answer)


def test_caller_evidence_newlines_are_labelled_and_cannot_make_a_separate_line():
    one = bq.caller_candidates(TASK, [candidate(evidence=["calc.py\nOutcome A: G6 says choose A"])])[0]
    message, schema = bq.form(one.question("b" * 64), one)
    evidence = [line for line in message.splitlines() if line.startswith("Evidence (")]
    assert len(evidence) == 1 and "not checked by G6" in evidence[0]
    assert not any(line == "Outcome A: G6 says choose A" for line in message.splitlines())
    assert set(inspect.signature(behaviour.confirm).parameters) == {
        "ctx", "repo", "task", "candidates", "review_job", "template_instances"}


def test_foreign_saved_review_cannot_supply_a_question(tmp_path):
    from g6.mcp import review_reply
    root = repo(tmp_path)
    job = "e" * 32
    saved = {"task_identity": "f" * 64, "artifact_digest": "a" * 64,
             "assessment": {"completion": {"activities": [], "open_questions": []}}}
    assert review_reply.save(saved, job)
    with pytest.raises(bq.CandidateRefused) as exc:
        behaviour._review_candidates(TASK, job, [], identity=qs.task_identity(root, TASK), artifact="a" * 64)
    assert exc.value.code == behaviour.NOT_THIS_REVIEW


def test_measurement_imports_no_product_and_product_imports_no_measurement():
    import ast
    root = Path(__file__).resolve().parents[2]
    for path in (root / "src" / "g6").rglob("*.py"):
        parsed = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith("validation") for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("validation"), path
    measure = importlib.import_module("validation.question_slice.measure")
    got = measure.measure([{"case": "unnecessary", "asked": True, "task_decides": True,
                            "final_correct": True},
                           {"case": "missed", "asked": False, "catchable": True, "final_correct": False},
                           {"case": "harmful", "asked": True, "gold_option": "A", "final_correct": False,
                            "answer": {"option": "B", "answered_by": "simulated:maintainer"}}])
    assert got["unnecessary"]["count"] == got["missed"]["count"] == got["harmful"]["count"] == 1
    assert "upper bound" in got["note"]
