# SPDX-License-Identifier: Apache-2.0
"""Review 4 of claude/wire-typed-fix: a missing request count is unknown, never zero, wherever a reply or a view is
rebuilt ("not sent" only on a count present and zero), and a structured input with a key given twice is refused as the
line grammar refuses an answer given twice."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.core.llm import LLMError
from g6.core.model_calls import note_model_call
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.harness.workflow import adapters, diagnose, obstacle as ob, policy, view

UNSENT_ERROR = f"[{cloud.REQUIRED}] Results were withheld"


def test_stop_hook_does_not_call_a_partly_sent_review_unsent(monkeypatch):
    root, state = Path.cwd(), {}
    monkeypatch.setattr(loop, "_load", lambda *a: state)
    monkeypatch.setattr(loop.repo_tests, "checked_repo", lambda *a: root)
    monkeypatch.setattr(loop.repo_tests, "change", lambda *a: SimpleNamespace(repo=root, sources=("code.py",)))
    monkeypatch.setattr(loop.repo_tests, "snapshot", lambda *a: {})
    monkeypatch.setattr(loop.proposer, "boundary", lambda *a: "")
    monkeypatch.setattr(loop, "_scouts_chosen", lambda: False)
    monkeypatch.setattr(loop, "harness", lambda: SimpleNamespace())
    monkeypatch.setattr(loop, "_patch_now", lambda *a: "patch")
    monkeypatch.setattr(loop, "_owe_row", lambda *a: None)
    monkeypatch.setattr(loop, "_write_row", lambda *a: None)
    monkeypatch.setattr(loop, "_finish", lambda session, state, text, code="", ending="", **kw:
                        loop.Step(loop.STOP, text, code, ending=ending))

    def review(*a):
        note_model_call()
        raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: next provider is unshared"))

    monkeypatch.setattr(loop, "review_round", review)
    step = loop._advance_inner(root, "task", "base", root, None, SimpleNamespace(), "", True, "")
    assert state["requests_sent"] == 1
    assert "not sent" not in step.text.lower()

    original_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: p.name == "task.json" or original_is_file(p))
    monkeypatch.setattr(hook, "folder", lambda *a: root)
    monkeypatch.setattr(hook, "_task", lambda *a: {"repo": str(root), "task": "task", "base": "base"})
    monkeypatch.setattr(hook, "current", lambda *a: root)
    monkeypatch.setattr(hook, "_said", lambda *a: "")
    monkeypatch.setattr(hook, "_model", lambda *a: "synthetic")
    monkeypatch.setattr(loop, "advance", lambda *a, **kw: step)
    monkeypatch.setattr(policy, "enabled", lambda: (True, ""))
    monkeypatch.setenv("G6_WORKFLOW_VIEW", "narrow")
    monkeypatch.setattr(adapters, "observe_step", lambda *a, **kw: "")
    monkeypatch.setattr(adapters, "identities", lambda *a: ("project", "task"))
    monkeypatch.setattr(adapters, "task_view", lambda *a: ())
    monkeypatch.setattr(view, "shared", lambda *a: True)
    monkeypatch.setattr(view, "_confirmed", lambda *a: ((), ()))

    reply = hook.on_stop({"session_id": "synthetic"})
    assert view.HEADING in reply["systemMessage"]
    assert "the review was not sent" not in reply["systemMessage"].lower(), reply["systemMessage"]


@pytest.mark.parametrize("count, kind", [("absent", "refused_run"), (None, "refused_run"), (True, "refused_run"),
                                         (1, "refused_run"), (0, "review_unsent")])
def test_a_loop_reply_is_unsent_only_with_a_count_present_and_zero(count, kind):
    reply = {"ending": "untested", "run_id": "f" * 32, "report": "r", "error": UNSENT_ERROR,
             **({} if count == "absent" else {"requests_sent": count})}
    (found,) = ob.obstacles(None, reply, ())
    assert found.kind == kind


def _event(kind, run, at, **data):
    return SimpleNamespace(kind=kind, run=run, at=at, data=data, refs=())


@pytest.mark.parametrize("count, kind", [("absent", "refused_run"), (1, "refused_run"), (0, "review_unsent")])
def test_recorded_loop_ends_are_unsent_only_with_a_count_present_and_zero(count, kind):
    events = []
    for n, run in enumerate(("r1", "r2")):
        sent = {} if count == "absent" else {"requests_sent": count}
        events += [_event("run_started", run, 10 * n, origin_kind="loop_session"),
                   _event("loop_ended", run, 10 * n + 1, ending="untested", code=cloud.REQUIRED, **sent)]
    assert [one[0].kind for one in diagnose._g6_setup_blocks(events, None, None, ())] == [kind]


def test_review_jobs_refused_for_consent_record_no_count_so_are_never_unsent():
    events = []
    for n, run in enumerate(("j1", "j2")):
        events += [_event("run_started", run, 10 * n, origin_kind="review_job"),
                   _event("review_finished", run, 10 * n + 1, refusal_code=cloud.REQUIRED)]
    assert [one[0].kind for one in diagnose._g6_setup_blocks(events, None, None, ())] == ["refused_run"]


@pytest.mark.parametrize("count, unsent", [("absent", False), (None, False), (0, True), (1, False)])
def test_the_review_loop_reply_is_unsent_only_with_a_count_present_and_zero(count, unsent):
    from g6.mcp import review_loop

    reply = {"ending": "untested", "code": cloud.REQUIRED, "warning_card": {"code": cloud.REQUIRED},
             "error": f"[{cloud.REQUIRED}] withheld", **({} if count == "absent" else {"requests_sent": count})}
    assert review_loop._unsent(reply) is unsent


def _compliance(answers="", records=""):
    from g6.bundles.compliance_gap import run

    return run({"regulations": "GDPR", "as_of": "2026-01-01", "answers": answers, "records": records})


def test_duplicate_structured_answers_do_not_hide_a_reported_gap():
    lines = _compliance("GDPR-30: no\nGDPR-30: yes | a processing register")
    assert lines.completion_state == "blocked-escalated"
    result = _compliance('{"GDPR-30":{"answer":"no","evidence":""},'
                         '"GDPR-30":{"answer":"yes","evidence":"a processing register"}}')
    assert result.completion_state == "blocked-escalated", result.summary
    assert result.code == "G6_E_INPUT_VALIDATION"


def test_a_field_given_twice_inside_one_structured_answer_is_refused():
    result = _compliance('{"GDPR-30":{"answer":"no","answer":"yes","evidence":"a processing register"}}')
    assert (result.completion_state, result.code) == ("blocked-escalated", "G6_E_INPUT_VALIDATION")


def test_a_field_given_twice_in_a_structured_record_does_not_hide_an_overdue_notice():
    record = '[{"regulation":"GDPR","kind":"breach","fields":{"start":"2025-12-01T09:00Z",%s}}]'
    late = '"authority":"2025-12-10T09:00Z"'
    assert _compliance(records=record % late).code == "G6_E_COMPLIANCE_GAP"     # read alone, the late notice is overdue
    result = _compliance(records=record % f'{late},"authority":"2025-12-02T09:00Z"')
    assert (result.completion_state, result.code) == ("blocked-escalated", "G6_E_INPUT_VALIDATION")
