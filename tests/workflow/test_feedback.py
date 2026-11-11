# SPDX-License-Identifier: Apache-2.0
"""The optional feedback question (design 17.3; local contract 5 of 17.5): never without the person's opt-in, never
before the report, never while a behaviour question waits, never beyond A's budget, at most once a week for a
project, reserved durably before it is shown, never retried, and with no effect on any review."""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import question_store as qs
from g6.harness.workflow import adapters, feedback as fb, lifecycle, policy, view
from g6.harness.workflow import record as rec
from g6.harness.workflow.friction import friction
from g6.mcp import workflow as tools
from tests.workflow.kit import Step, at, loop_state, make_repo
from tests.workflow.test_adapters import QUESTION_TASK, _ask

TASK_TEXT = "make the widget count stable"
NOW = datetime.now(UTC)


@pytest.fixture
def repo(tmp_path, home, monkeypatch):
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "suggestions")           # as the change recording G4's result would
    monkeypatch.setenv(fb.OPT_IN_ENV, "on")
    monkeypatch.delenv(qs.BUDGET_ENV, raising=False)
    folder = make_repo(tmp_path / "repo")
    _report(folder, tmp_path)
    return folder


def _report(repo, tmp_path, task=TASK_TEXT, name="s"):
    (tmp_path / name).mkdir(exist_ok=True)
    done = loop_state(1, done=True, ending="clean")
    assert adapters.observe_loop(repo, task, tmp_path / name, done, Step("stop", "clean"), at=at(0)) == ""


def _ids(repo, task=TASK_TEXT):
    return adapters.identities(repo, task)


def ctx(action="accept", content=None, *, dialog=True, seen=None, during=None):
    async def elicit_form(**kwargs):
        if seen is not None:
            seen.append(kwargs)
        if during is not None:
            during()
        return SimpleNamespace(action=action, content=content)
    capabilities = SimpleNamespace(elicitation=SimpleNamespace() if dialog else None)
    return SimpleNamespace(session=SimpleNamespace(elicit_form=elicit_form, client_params=SimpleNamespace(
        capabilities=capabilities)), request_id="r")


def _ask_feedback(repo, context=None, task=TASK_TEXT, text=""):
    with cloud.collect_sources():
        return asyncio.run(tools.feedback(str(repo), task, context or ctx(content={"answer": "easier"}), text=text))["result"]


def _entries(repo):
    return [(e["kind"], e["body"]) for e in fb.FeedbackStore(_ids(repo)[0]).entries()]


def _invited(repo) -> int:
    return sum(kind == "invited" for kind, _body in _entries(repo))


def test_no_invitation_without_opt_in(repo, monkeypatch):
    seen = []
    for value in (None, "off", "yes", ""):
        monkeypatch.delenv(fb.OPT_IN_ENV, raising=False) if value is None else monkeypatch.setenv(fb.OPT_IN_ENV, value)
        result = _ask_feedback(repo, ctx(content={"answer": "easier"}, seen=seen))
        assert result["status"] == "not_asked" and result["asked"] is False, value
    assert seen == [] and _invited(repo) == 0
    assert not qs.TaskQuestions(_ids(repo)[1]).exists()                   # no slot was taken, no opt-in question asked
    project = _ids(repo)[0]
    fb.FeedbackStore(project).opt("in", by="A person", at=at(0), via="corrections")     # opted in by their own file
    monkeypatch.delenv(fb.OPT_IN_ENV, raising=False)
    assert _ask_feedback(repo)["status"] == "answered"


def test_no_invitation_before_the_report(repo, tmp_path):
    other = "a task with no report yet"
    (tmp_path / "t").mkdir()
    adapters.observe_loop(repo, other, tmp_path / "t", loop_state(1), Step("continue"), at=at(0))
    result = _ask_feedback(repo, task=other)
    assert result == {"status": "not_asked", "asked": False, "why": "the task has not reached its report"}
    assert _ask_feedback(repo, task="a task G6 never saw")["status"] == "not_asked" and _invited(repo) == 0


def test_no_invitation_while_an_intent_question_is_pending(repo, tmp_path):
    _report(repo, tmp_path, task=QUESTION_TASK, name="q")
    _ask(repo, answer=False)                                              # asked, shown, not answered
    result = _ask_feedback(repo, task=QUESTION_TASK)
    assert result["status"] == "not_asked" and "pending" in result["why"] and _invited(repo) == 0


def test_no_invitation_when_the_budget_is_spent(repo, tmp_path):
    _report(repo, tmp_path, task=QUESTION_TASK, name="q")
    _ask(repo, answer=True)                                               # the task's one question was asked and answered
    result = _ask_feedback(repo, task=QUESTION_TASK)
    assert result["status"] == "not_asked" and "no room" in result["why"] and _invited(repo) == 0


def test_no_questions_setting_is_honoured(repo, monkeypatch):
    for value in ("0", "many"):
        monkeypatch.setenv(qs.BUDGET_ENV, value)
        assert _ask_feedback(repo)["status"] == "not_asked" and _invited(repo) == 0


def test_reservation_is_on_disk_before_the_dialog_opens(repo):
    project, task = _ids(repo)
    found = {}

    def during():
        found["slot"] = qs.TaskQuestions(task).state().reserved
        found["invited"] = _invited(repo)
        found["closed"] = sum(kind == "closed" for kind, _b in _entries(repo))
    seen = []
    result = _ask_feedback(repo, ctx(content={"answer": "harder"}, seen=seen, during=during))
    assert len(found["slot"]) == 1 and found["invited"] == 1 and found["closed"] == 0
    assert result == {"status": "answered", "asked": True, "retried": False}
    (shown,) = seen
    assert shown["message"].startswith(fb.QUESTION) and TASK_TEXT not in shown["message"]
    assert shown["requestedSchema"]["properties"]["answer"]["enum"] == list(fb.OPTIONS)
    closed = [body for kind, body in _entries(repo) if kind == "closed"]
    assert len(closed) == 1 and closed[0]["result"] == "answered" and closed[0]["option"] == "harder"


def test_behaviour_question_and_feedback_share_one_slot(repo, tmp_path):
    # feedback first: the behaviour question is then refused by A's own budget
    _report(repo, tmp_path, task=QUESTION_TASK, name="q")
    assert _ask_feedback(repo, task=QUESTION_TASK)["status"] == "answered"
    assert qs.TaskQuestions(_ids(repo, QUESTION_TASK)[1]).state().left == 0
    with pytest.raises(qs.BudgetSpent):
        _ask(repo, answer=False)
    # the behaviour question first, in another repository: feedback is then refused
    second = make_repo(tmp_path / "second")
    _report(second, tmp_path, task=QUESTION_TASK, name="second-session")
    _ask(second, answer=True)
    refused = _ask_feedback(second, task=QUESTION_TASK)
    assert refused["status"] == "not_asked" and _invited(second) == 0


def test_two_concurrent_reservations_give_one_invitation(repo, tmp_path):
    project = _ids(repo)[0]
    tasks = [f"concurrent task {n}" for n in range(8)]
    for number, task in enumerate(tasks):
        _report(repo, tmp_path, task=task, name=f"c{number}")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda task: _ask_feedback(repo, task=task)["status"], tasks))
    assert results.count("answered") == 1 and results.count("not_asked") == 7
    assert _invited(repo) == 1

    def reserve(_):
        try:
            return fb.FeedbackStore(project).reserve_invitation("a" * 64, now=NOW + timedelta(days=8))
        except fb.InvitationRefused:
            return None
    with ThreadPoolExecutor(8) as pool:
        assert sum(one is not None for one in pool.map(reserve, range(8))) == 1


def test_cooldown_holds_across_restart_and_across_tasks_of_one_project(repo, tmp_path):
    project, task = _ids(repo)
    store = fb.FeedbackStore(project)
    first = store.reserve_invitation(task, now=NOW)
    for later in (timedelta(seconds=1), timedelta(days=6), timedelta(days=6, hours=23)):
        with pytest.raises(fb.InvitationRefused):
            fb.FeedbackStore(project).reserve_invitation(_ids(repo, "another task")[1], now=NOW + later)     # a fresh object
    state = qs.State()
    assert fb.may_invite(project, task, questions=state, report_reached=True, dialog=True, now=NOW + timedelta(days=6)) == (
        False, "this project had a feedback invitation in the last 7 days")
    assert fb.may_invite(project, task, questions=state, report_reached=True, dialog=True, now=NOW + timedelta(days=8)) == (True, "")
    assert fb.FeedbackStore(project).reserve_invitation(task, now=NOW + timedelta(days=8)) != first
    other = make_repo(tmp_path / "other-project")                         # another project has its own week
    assert fb.FeedbackStore(_ids(other)[0]).reserve_invitation("a" * 64, now=NOW + timedelta(days=1))


@pytest.mark.parametrize("action,content,result", [("accept", {"answer": "skip"}, "skipped"), ("decline", None, "declined"),
                                                   ("cancel", None, "cancelled"), ("unavailable", None, "unavailable"),
                                                   ("accept", {"answer": "wonderful"}, "invalid"), ("accept", None, "invalid")])
def test_skip_cancel_and_unavailable_are_not_retried(repo, action, content, result):
    seen = []
    context = ctx(action, content, seen=seen)
    if action == "unavailable":
        async def broken(**kwargs):
            seen.append(kwargs)
            raise RuntimeError("no dialog")
        context.session.elicit_form = broken
    assert _ask_feedback(repo, context) == {"status": result, "asked": True, "retried": False}
    again = _ask_feedback(repo, ctx(content={"answer": "easier"}, seen=seen))
    assert again["status"] == "not_asked" and len(seen) == 1              # the slot and the week stay spent
    kinds = [kind for kind, _body in _entries(repo)]
    assert kinds.count("invited") == 1 and kinds.count("closed") == 1
    with pytest.raises(rec.EventRefused):
        fb.FeedbackStore(_ids(repo)[0]).close(next(e["id"] for e in fb.FeedbackStore(_ids(repo)[0]).entries()
                                                   if e["kind"] == "invited"), "answered", option="easier",
                                              shown_at=at(0), back_at=at(1), seconds=1.0)      # closed once, for good


def test_opt_out_persists_and_stops_invitations(repo, monkeypatch):
    project = _ids(repo)[0]
    assert _ask_feedback(repo, ctx(content={"answer": "easier", "do_not_ask_again": True}))["status"] == "answered"
    assert fb.FeedbackStore(project).opted_in() == (False, "the person opted out of the feedback question")
    assert fb.may_invite(project, _ids(repo)[1], questions=qs.State(), report_reached=True, dialog=True,
                         now=NOW + timedelta(days=30))[0] is False        # the environment still says on
    (rec.ProjectRecord(project).root / "corrections.json").write_text(json.dumps({
        "schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [{"action": "feedback", "choice": "in", "by": "A person"}]}),
        encoding="utf-8")
    assert lifecycle.apply_corrections(project)["applied"] == 1 and lifecycle.apply_corrections(project)["already"] == 1
    assert fb.FeedbackStore(project).opted_in() == (True, "")


def test_unsupported_dialog_solicits_nothing_and_never_asks_the_agent(repo):
    seen = []
    result = _ask_feedback(repo, ctx(content={"answer": "easier"}, dialog=False, seen=seen))
    assert result["status"] == "not_asked" and "never put to the agent" in result["why"] and seen == []
    assert _invited(repo) == 0 and not qs.TaskQuestions(_ids(repo)[1]).exists()
    assert not ({"answer", "option", "response"} & set(json.dumps(result).split('"')))


def test_response_never_confirms_behaviour(repo, tmp_path, monkeypatch):
    monkeypatch.setenv(qs.BUDGET_ENV, "2")
    _report(repo, tmp_path, task=QUESTION_TASK, name="q")
    _ask(repo, answer=True)
    store = qs.TaskQuestions(_ids(repo, QUESTION_TASK)[1])
    before = store.state()
    assert _ask_feedback(repo, task=QUESTION_TASK)["status"] == "answered"
    after = store.state()
    assert after.standard().sha256 == before.standard().sha256 and after.answers == before.answers
    assert after.asked == before.asked and len(after.reserved) == 1 and after.left == 0


def test_response_never_changes_completion_state_or_reopens_the_task(repo):
    project, task = _ids(repo)
    before = [e for e in rec.ProjectRecord(project).events(task=task) if e.data.get("origin_kind") != "tool_calls"]
    assert _ask_feedback(repo, ctx(content={"answer": "harder"}))["status"] == "answered"
    events = rec.ProjectRecord(project).events(task=task)
    review = [e for e in events if e.kind in ("run_started", "loop_round", "loop_ended", "report_returned")
              and e.data.get("origin_kind") != "tool_calls"]
    assert review == before                                               # no run was added, nothing was reopened
    added = [e.kind for e in events if e not in before and e.kind != "run_started"]
    assert sorted(added) == ["feedback_answered", "feedback_invited", "feedback_shown"]
    assert next(e for e in events if e.kind == "feedback_answered").data == {"option": "harder"}
    monkey_view = view._value_result(events)
    assert monkey_view == "self_reported"                                 # a self-report, never a measured benefit


def test_invitations_nonresponses_and_opt_outs_are_all_recorded(repo, tmp_path):
    project, task = _ids(repo)
    _ask_feedback(repo, ctx("cancel"))
    counted = friction(adapters.task_view(project, task))
    assert counted.feedback_invitations == 1 and counted.question_seconds is not None      # its time counts as friction
    kinds = [kind for kind, _body in _entries(repo)]
    assert kinds == ["invited", "closed"]
    fb.FeedbackStore(project).opt("out", by="A person", at=at(5), via="corrections")
    assert [kind for kind, _body in _entries(repo)] == ["invited", "closed", "opt"]
    assert all(body["at"] and body["project"] == project for _kind, body in _entries(repo))


def test_unsolicited_feedback_spends_no_budget(repo, monkeypatch):
    project, task = _ids(repo)
    monkeypatch.setenv(fb.OPT_IN_ENV, "off")                             # volunteered feedback needs no opt-in
    seen = []
    result = _ask_feedback(repo, ctx(seen=seen), text="  the block was too long  ")
    assert result == {"status": "kept", "asked": False, "provenance": "attributed_label",
                      "note": "relayed by the agent, not authenticated"}
    assert seen == [] and not qs.TaskQuestions(task).exists() and _invited(repo) == 0
    ((kind, body),) = _entries(repo)
    assert kind == "unsolicited" and body["text"] == "the block was too long" and body["task"] == task
    assert "too long" not in json.dumps(result)                          # kept, not echoed


def test_unreadable_feedback_store_invites_nothing(repo):
    project, task = _ids(repo)
    fb.FeedbackStore(project).opt("in", by="A person", at=at(0), via="corrections")
    path = rec.ProjectRecord(project).root / "feedback.jsonl"
    path.write_bytes(path.read_bytes()[:-20])
    assert fb.may_invite(project, task, questions=qs.State(), report_reached=True, dialog=True, now=NOW) == (
        False, "the feedback G6 holds for this project cannot be read, so it asks nothing")
    seen = []
    result = _ask_feedback(repo, ctx(content={"answer": "easier"}, seen=seen))
    assert result["status"] == "not_asked" and seen == [] and not qs.TaskQuestions(task).exists()
    path.write_bytes(b"")                                                 # cut back to nothing: still not "no cooldown"
    assert fb.may_invite(project, task, questions=qs.State(), report_reached=True, dialog=True, now=NOW)[0] is False


def test_the_flow_is_not_reachable_before_its_gate(repo, monkeypatch):
    for level in ("narrow", "diagnosis"):
        monkeypatch.setattr(view, "ADMITTED_LEVEL", level)
        seen = []
        result = _ask_feedback(repo, ctx(content={"answer": "easier"}, seen=seen))
        assert result["status"] == "not_asked" and "not switched on" in result["why"] and seen == []
    assert _invited(repo) == 0
    assert _ask_feedback(repo, text="volunteered early")["status"] == "kept"      # unsolicited feedback is always taken
    assert policy.FILES[policy.FEEDBACK] == "feedback" and policy.RETENTION_DAYS["feedback"] == 180
