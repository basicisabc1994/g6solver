# SPDX-License-Identifier: Apache-2.0
"""The adapters that write event boundaries from G6's own review paths (design 17.2 "Observe"): once per event however
often a step is replayed, never failing the review they observe, and nothing at all when the record is off."""
from __future__ import annotations

import json

import pytest

from g6.harness import question_store as qs
from g6.harness.workflow import adapters, policy
from g6.harness.workflow import record as rec
from g6.harness.workflow.friction import friction
from tests.workflow.kit import Step, at, loop_state, make_repo, sha

TASK_TEXT = "make the widget count stable"


@pytest.fixture
def repo(tmp_path, home):
    return make_repo(tmp_path / "repo")


def _events(repo):
    project, task = adapters.identities(repo, TASK_TEXT)
    return rec.ProjectRecord(project).events(task=task)


def _kinds(repo):
    return [e.kind for e in _events(repo)]


def test_loop_run_writes_start_rounds_end_and_report_returned_once(repo, tmp_path):
    session = tmp_path / "session-a"
    session.mkdir()
    assert adapters.observe_loop(repo, TASK_TEXT, session, loop_state(1), Step("continue"), at=at(0)) == ""
    assert adapters.observe_loop(repo, TASK_TEXT, session, loop_state(2), Step("continue"), at=at(30)) == ""
    done = loop_state(3, done=True, ending="fixed")
    assert adapters.observe_loop(repo, TASK_TEXT, session, done, Step("stop", "fixed"), at=at(60)) == ""
    assert _kinds(repo) == ["run_started", "loop_round", "loop_round", "loop_round", "loop_ended", "report_returned"]
    events = _events(repo)
    assert [e.data["round"] for e in events if e.kind == "loop_round"] == [1, 2, 3]
    assert events[1].artifact_sha256 == sha("patch 1") and events[1].data["failing"] == 1
    assert events[1].data["open_sha256"] == [sha("g6_review_1/test_a.py")] and events[1].data["verdict"] == "defect"
    assert events[1].data["failing_sha256"] == [sha("tests/test_a.py::test_one")]
    assert "test_a" not in json.dumps([e.to_body() for e in events])        # names are kept as digests only
    assert events[-2].data == {"ending": "fixed", "reviews": 3}
    assert isinstance(events[-1].data["run_seconds"], float) and {e.provenance for e in events} == {"host_observed"}


def test_hook_killed_and_rerun_adds_no_duplicate_event(repo, tmp_path):
    session = tmp_path / "session-a"
    session.mkdir()
    done = loop_state(2, done=True, ending="clean")
    for _ in range(3):                                   # the same state, replayed as a killed hook's rerun would
        assert adapters.observe_loop(repo, TASK_TEXT, session, done, Step("stop", "clean"), at=at(10)) == ""
    assert _kinds(repo) == ["run_started", "loop_round", "loop_round", "loop_ended", "report_returned"]
    adapters.observe_loop(repo, TASK_TEXT, session, done, Step("wait"), at=at(20))      # a step that did nothing
    assert len(_events(repo)) == 5


def test_a_second_session_is_a_second_run_and_a_restart_is_marked(repo, tmp_path, monkeypatch):
    for name in ("session-a", "session-b"):
        (tmp_path / name).mkdir()
        adapters.observe_loop(repo, TASK_TEXT, tmp_path / name, loop_state(1), Step("continue"), at=at(0))
    project, task = adapters.identities(repo, TASK_TEXT)
    assert len(rec.ProjectRecord(project).runs(task)) == 2
    monkeypatch.setattr(adapters, "PROCESS", "another-process")       # the server restarted between two steps
    adapters._started.clear()
    done = loop_state(1, done=True, ending="clean")
    adapters.observe_loop(repo, TASK_TEXT, tmp_path / "session-a", done, Step("stop", "clean"), at=at(50))
    run = [e for e in _events(repo) if e.kind == "run_resumed"]
    assert len(run) == 1
    returned = next(e for e in _events(repo) if e.kind == "report_returned")
    assert "run_seconds" not in returned.data and returned.missing == ("run_seconds",)


def test_review_whose_save_failed_is_recorded_with_missing_review_ref(repo, home):
    reply = {"ok": True, "completion_state": "qualified-draft", "artifact_digest": sha("artefact"),
             "assessment": {"outcome": "defect_detected", "reasons": [{"stage": "integrity", "code": "x", "detail": "d"}],
                            "requirements": [{"id": "R1", "status": "failed"}, {"id": "R2", "status": "passed_on_checks"}],
                            "scope": {"seconds": 12.5, "search": {"model_calls": 2}}}}
    assert adapters.observe_review(repo, TASK_TEXT, "job-unsaved", reply, started_at=at(0), finished_at=at(13),
                                   seconds=13.2) == ""
    finished = next(e for e in _events(repo) if e.kind == "review_finished")
    assert finished.refs == () and "review_ref" in finished.missing
    assert finished.data == {"outcome": "defect_detected", "state": "qualified-draft", "review_seconds": 12.5,
                             "model_calls": 2, "integrity_reasons": 1, "requirements_failed": 1}
    saved = home / "reviews" / "job-saved.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text("{}", encoding="utf-8")
    adapters.observe_review(repo, TASK_TEXT, "job-saved", reply, started_at=at(20), finished_at=at(30), seconds=10,
                            saved=str(saved))
    kept = [e for e in _events(repo) if e.kind == "review_finished"][-1]
    assert kept.refs == (rec.Ref("review", "job-saved", sha("{}")),) and "review_ref" not in kept.missing


def test_a_refused_review_is_recorded_by_its_code_only(repo):
    reply = {"ok": False, "completion_state": "blocked-escalated", "error": "[G6_E_GIT_REPO_INVALID] SECRET-PATH-TEXT"}
    adapters.observe_review(repo, TASK_TEXT, "job-r", reply, started_at=at(0), finished_at=at(1), seconds=None)
    finished = next(e for e in _events(repo) if e.kind == "review_finished")
    assert finished.data["refusal_code"] == "G6_E_GIT_REPO_INVALID" and "SECRET" not in json.dumps(finished.to_body())
    assert next(e for e in _events(repo) if e.kind == "report_returned").missing == ("run_seconds",)


def test_a_retried_call_is_one_event_and_a_repeat_is_another(repo):
    for request in ("r1", "r1", "r2"):
        adapters.observe_call(repo, TASK_TEXT, "g6_review_patch", request, sha("args"), started_at=at(0), seconds=0.25)
    calls = [e for e in _events(repo) if e.kind == "tool_call"]
    assert len(calls) == 2 and calls[0].data == {"tool": "g6_review_patch", "args_sha256": sha("args"), "seconds": 0.25}
    found = friction(_events(repo))
    assert (found.tool_calls, found.tool_retries) == (2, 1)


QUESTION_TASK = "Make total(prices) add the prices. A negative price is refused."


def _ask(repo, *, answer: bool, task_text: str = QUESTION_TASK) -> str:
    """One behaviour question in A's own store for this task, built the way the question slice's tests build one."""
    from g6.harness import behaviour_question as bq
    from g6.harness.confirmed_expectation import Answer

    _project, task = adapters.identities(repo, task_text)
    store = qs.TaskQuestions(task)
    [candidate] = bq.caller_candidates(task_text, [{
        "requirement_id": "R1", "function": "shop.prices.total", "args": [[2, -3]],
        "outcomes": {"A": {"returns": -1}, "B": {"raises": "ValueError"}}, "cited": "A negative price is refused.",
        "evidence": ["shop/prices.py: no check of the sign"]}])
    case, rule = candidate.questions("b" * 64)
    asked = store.ask(case, rule, candidate, at=at(5))
    store.show(asked, "declined", shown_at=at(6), back_at=at(15), seconds=9.0)
    if answer:
        store.show(asked, "answered", shown_at=at(20), back_at=at(24), seconds=4.5)
        store.answer(Answer(question_sha256=asked.question.sha256, option="B", answered_by="person:Ada",
                            answered_at=at(24)))
    return asked.question.sha256


def test_question_events_are_read_from_the_question_store_and_not_copied(repo, home):
    sha256 = _ask(repo, answer=True)
    project, task = adapters.identities(repo, QUESTION_TASK)
    found = adapters.question_events(project, task)
    assert [e.kind for e in found] == ["question_shown", "question_shown", "question_answered"]
    assert [e.data.get("seconds") for e in found[:2]] == [9.0, 4.5] and found[0].refs[0].id == sha256
    assert found[2].provenance == "person_confirmed" and found[0].provenance == "host_observed"
    assert not (home / "workflow").exists()                     # derived on reading; nothing is written
    assert adapters.task_view(project, task) == found
    counted = friction(found)
    assert (counted.questions, counted.question_seconds) == (1, 13.5)


def test_adapter_failure_never_fails_the_review(repo, home, tmp_path, monkeypatch):
    session = tmp_path / "session-a"
    session.mkdir()

    def full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(rec.ProjectRecord, "start_run", full)
    for note in (adapters.observe_loop(repo, TASK_TEXT, session, loop_state(1), Step("continue"), at=at(0)),
                 adapters.observe_review(repo, TASK_TEXT, "job", {}, started_at=at(0), finished_at=at(1)),
                 adapters.observe_call(repo, TASK_TEXT, "tool", "r", sha("a"), started_at=at(0), seconds=1.0),
                 adapters.observe_step(repo, TASK_TEXT, session, Step("stop"))):
        assert note == "the workflow record was not written (OSError)"          # one fixed line, never the error
    monkeypatch.undo()
    monkeypatch.setenv("G6_HOME", str(home))
    monkeypatch.setenv(policy.RECORD_ENV, "on")
    (home / "workflow").parent.mkdir(parents=True, exist_ok=True)
    (home / "workflow").write_text("a file where the record's folder belongs", encoding="utf-8")
    assert adapters.observe_loop(repo, TASK_TEXT, session, loop_state(1), Step("continue"), at=at(0)).startswith(
        "the workflow record was not written")
    assert adapters.observe_loop("relative/path", TASK_TEXT, session, {}, Step("stop"), at=at(0)).startswith(
        "the workflow record was not written")


@pytest.mark.parametrize("value", [None, "off", "sometimes"])
def test_collection_off_writes_nothing(repo, home, tmp_path, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(policy.RECORD_ENV)
    else:
        monkeypatch.setenv(policy.RECORD_ENV, value)
    session = tmp_path / "session-a"
    session.mkdir()
    done = loop_state(1, done=True, ending="clean")
    assert adapters.observe_loop(repo, TASK_TEXT, session, done, Step("stop", "clean"), at=at(0)) != ""
    adapters.observe_review(repo, TASK_TEXT, "job", {}, started_at=at(0), finished_at=at(1))
    adapters.observe_call(repo, TASK_TEXT, "tool", "r", sha("a"), started_at=at(0), seconds=1.0)
    adapters.observe_project(repo, "inspection", "x", at=at(0))
    assert not (home / "workflow").exists()


def test_work_outside_g6_paths_is_reported_unknown(repo):
    project, task = adapters.identities(repo, "a task G6 never saw")
    assert adapters.task_view(project, task) == ()
    assert adapters.NO_OBSERVATION in friction(()).missing and friction(()).wall_seconds is None


def test_a_project_level_inspection_is_counted_against_the_newest_run(repo, tmp_path):
    assert adapters.observe_project(repo, "inspection", "one", at=at(0)) == "no run to count it against"
    (tmp_path / "s").mkdir()
    adapters.observe_loop(repo, TASK_TEXT, tmp_path / "s", loop_state(1), Step("continue"), at=at(0))
    assert adapters.observe_project(repo, "inspection", "two", at=at(5)) == ""
    assert friction(_events(repo)).inspections == 1 and _events(repo)[-1].data == {"scope": "project"}
