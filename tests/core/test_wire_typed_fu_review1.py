# SPDX-License-Identifier: Apache-2.0
"""Repair 1 of claude/wire-typed-fu: a run keeps one request count across all its steps. A step's own count never
stands for the run, a state with no count stays unknown, and a step cut short after sending leaves the count unknown,
so "not sent" is said only when no step of the run sent anything."""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.core.ledger import digest
from g6.core.llm import LLMError
from g6.core.model_calls import counting, note_model_call
from g6.harness import completion_hook, completion_loop as loop
from g6.harness.workflow import obstacle
from g6.mcp import review_loop
from g6.mcp.cloud_consent import withheld_reply


def test_resumed_loop_does_not_record_zero_after_an_earlier_send(monkeypatch):
    root, state = Path.cwd(), {}
    tree = ["before repair"]
    monkeypatch.setattr(loop, "_load", lambda *a: state)
    monkeypatch.setattr(loop, "_save", lambda *a, **kw: None)
    monkeypatch.setattr(loop.repo_tests, "checked_repo", lambda *a: root)
    monkeypatch.setattr(loop.repo_tests, "change", lambda *a: SimpleNamespace(repo=root, sources=("code.py",)))
    monkeypatch.setattr(loop.repo_tests, "snapshot", lambda *a: {"code.py": tree[0]})
    monkeypatch.setattr(loop.proposer, "boundary", lambda *a: "")
    monkeypatch.setattr(loop, "_scouts_chosen", lambda: False)
    monkeypatch.setattr(loop, "harness", lambda: SimpleNamespace())
    monkeypatch.setattr(loop, "_patch_now", lambda *a: tree[0])
    monkeypatch.setattr(loop, "_owe_row", lambda *a: None)
    monkeypatch.setattr(loop, "_write_row", lambda *a: None)
    monkeypatch.setattr(loop, "_finish", lambda session, state, text, code="", ending="", **kw:
                        loop.Step(loop.STOP, text, code, ending=ending))
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: None)
    monkeypatch.setattr(completion_hook, "folder", lambda *a: root)
    monkeypatch.setattr(loop, "_held", lambda *a: nullcontext())
    monkeypatch.setattr(review_loop, "_asked", lambda *a: {"repo": str(root), "task": "task", "base": "base"})
    monkeypatch.setattr(loop, "advance", lambda repo, task, base, folder, **kw:
                        loop._advance_inner(repo, task, base, folder, None, SimpleNamespace(), "", True, ""))
    rounds = []

    def review(change, task, critic, shipped, session, reviews, deadline):
        if rounds:
            raise LLMError(f"{cloud.REQUIRED}: sharing was revoked before the next review")
        note_model_call()
        rounds.append("sent")
        reviews.append({"patch": "first patch", "verdict": "defect", "results": {"test_critic.py": "failed"}})
        return {"critic:test_critic.py::test_case"}, "Repair the reproduced failure."

    monkeypatch.setattr(loop, "review_round", review)
    with counting() as sent:
        with cloud.collect_sources():
            first = review_loop.step(str(root), "task", "session", "base")
        assert first["action"] == loop.CONTINUE and sent() == 1
        tree[0] = "after repair"
        with cloud.collect_sources() as capture:
            stopped = review_loop.step(str(root), "task", "session", "base")
            metadata = cloud.output_metadata(stopped)
        assert stopped["ending"] == "untested" and stopped["code"] == cloud.REQUIRED
        job = {"digest": digest(stopped), "job_id": "review-job", "status": "completed",
               "metadata": metadata, "review_unsent": capture.review_unsent}
        rebuilt = withheld_reply(stopped, job=job)["result"]
        observed = (state.get("requests_sent"), stopped.get("requests_sent"),
                    obstacle.obstacles(None, stopped, ())[0].kind,
                    capture.review_unsent, "not sent" in rebuilt["report"].lower())
        assert observed == (sent(), sent(), "refused_run", False, False), observed


def _steps(tmp_path, monkeypatch, review, *, saved=None):
    """``review`` for each step of one run, against a real saved state; ``saved``: a state the run had before."""
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    session = tmp_path / 'session'
    session.mkdir()
    if saved is not None:
        loop._save(session, saved, sources=())
    tree = ["one"]
    change = SimpleNamespace(repo=tmp_path, sources=('code.py',))
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: change)
    monkeypatch.setattr(loop.repo_tests, 'snapshot', lambda *a: {"code.py": tree[0]})
    monkeypatch.setattr(loop, '_patch_now', lambda *a: tree[0])
    monkeypatch.setattr(loop.proposer, 'boundary', lambda *a: '')
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: False)
    monkeypatch.setattr(loop, '_owe_row', lambda *a: None)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)
    monkeypatch.setattr(loop.repo_tests, 'diff_text', lambda *a: 'PLANTED_PATCH')
    monkeypatch.setattr(loop, 'review_round', review)
    critic = SimpleNamespace(complete=lambda *a, **k: 'none')

    def step():
        tree[0] += "+"
        return loop._advance(tmp_path, 'task', 'base', session, None, critic, '', True, '')
    return step, session


def _review(plan):
    """Each call takes the next of ``plan``: ``("send", n)`` sends n and asks for a repair, ``("refuse", n)`` sends n
    then is refused for consent, ``("die", n)`` sends n then fails outside G6's refusals."""
    calls = iter(plan)

    def review(change, task, critic, shipped, session, reviews, deadline):
        what, n = next(calls)
        for _ in range(n):
            note_model_call()
        if what == "refuse":
            raise LLMError(f"{cloud.REQUIRED}: the provider's sharing refused the next one")
        if what == "die":
            raise RuntimeError("the reviewer's process went away")
        reviews.append({"patch": digest(str(len(reviews))), "verdict": "defect", "results": {"t.py": "failed"}})
        return {"critic:t.py::test_one"}, "Repair it."
    return review


def test_the_run_total_adds_every_step_and_a_refusal_with_nothing_new_is_not_unsent(tmp_path, monkeypatch):
    step, session = _steps(tmp_path, monkeypatch, _review([("send", 2), ("send", 1), ("refuse", 0)]))
    assert step().action == loop.CONTINUE and loop._load(session)["requests_sent"] == 2
    assert step().action == loop.CONTINUE and loop._load(session)["requests_sent"] == 3
    stopped, state = step(), loop._load(session)
    assert stopped.code == cloud.REQUIRED and state["requests_sent"] == 3
    assert "not sent" not in state["stop"].lower() and "3 request(s) of this run went out" in state["stop"]


def test_a_run_that_sent_nothing_in_any_step_is_still_not_sent(tmp_path, monkeypatch):
    step, session = _steps(tmp_path, monkeypatch, _review([("refuse", 0)]))
    assert step().code == cloud.REQUIRED
    state = loop._load(session)
    assert state["requests_sent"] == 0 and state["stop"].startswith("The review was not sent.")


def test_a_state_saved_with_no_total_stays_unknown(tmp_path, monkeypatch):
    earlier = {"started": 1e12, "sets": {}, "rounds": [], "reviews": []}      # saved before the run's total was kept
    step, session = _steps(tmp_path, monkeypatch, _review([("send", 1), ("refuse", 0)]), saved=earlier)
    assert step().action == loop.CONTINUE and "requests_sent" not in loop._load(session)
    stopped, state = step(), loop._load(session)
    assert stopped.code == cloud.REQUIRED and "requests_sent" not in state
    assert "not sent" not in state["stop"].lower()
    reply = {"ending": "untested", "run_id": "r", "report": state["stop"], "error": f"[{cloud.REQUIRED}] withheld"}
    assert obstacle.obstacles(None, reply, ())[0].kind == "refused_run"


def test_a_step_cut_short_after_sending_leaves_the_total_unknown(tmp_path, monkeypatch):
    step, session = _steps(tmp_path, monkeypatch, _review([("die", 1), ("refuse", 0)]))
    with pytest.raises(RuntimeError):
        step()
    assert "requests_sent" not in loop._load(session)        # never the 0 it had before the request went out
    stopped, state = step(), loop._load(session)
    assert stopped.code == cloud.REQUIRED and "requests_sent" not in state
    assert "not sent" not in state["stop"].lower()


def test_a_new_run_the_hook_starts_begins_with_nothing_sent(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    run = tmp_path / 'run'
    loop._save(run, {"reviewed": "x", loop.SENT: 0})
    with loop._run_count(run, loop._load(run), fresh=False) as counted:
        note_model_call()
        assert counted.total() == 1
