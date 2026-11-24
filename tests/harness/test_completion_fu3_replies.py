# SPDX-License-Identifier: Apache-2.0
"""Each call and each retained ending keep their own identity and words."""
import pytest

from g6.core.llm import LLMError

from g6.core import cloud_consent as cloud
from g6.harness import completion_loop as loop
from g6.workspace._envelope import OUTSIDE_ROOT
from g6.workspace._envelope import WorkspaceRefused
from g6.core.model_calls import note_model_call
from g6.mcp import review_loop
from tests._review_wtffu2b4.test_rev4_finding_3 import _killed_before_the_report
from tests._review_wtffu2b_3.rev3_world import TASK, quiet_scouts
from tests._review_wtffu2b4.rev4_world import rows_of
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def test_resumed_scouts_replace_interrupted_endings_row(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    _killed_before_the_report(monkeypatch, repo, base, session)
    monkeypatch.setattr(loop.proposer, "boundary", lambda _: "")
    loop.advance(repo, TASK, base, session)
    state = loop._load(session)
    assert "did not run" not in state["stop"]
    assert [row["cause"] for row in rows_of(session)] != [loop.UNCONFINED]
    assert "row_due" not in state
    assert not loop.store.home_path(*loop.OWING, session.name).exists()


def test_settled_call_carries_ending_beside_publication_failure(tmp_path, monkeypatch):
    state = {"done": True, "code": cloud.REQUIRED, "stop": "sharing refused", "ending": "untested", "attempted": 1}
    monkeypatch.setattr(loop, "reply_state", lambda _: state)
    step = loop.Step(loop.STOP, "report target refused", OUTSIDE_ROOT, call_ran=True, settled_here=True, refusal=True)
    reply, _, why = loop.reply_for(tmp_path, step, str(tmp_path), "base")
    assert reply.code == OUTSIDE_ROOT and "report target refused" in why
    assert "up to 1 request(s)" in reply.text and "g6_cloud_grant" in reply.text
    assert "did not run" not in reply.note
    assert cloud.REQUIRED in reply.text


@pytest.mark.parametrize("code", [OUTSIDE_ROOT, loop.NO_CLEAN_REVIEW, cloud.REQUIRED])
def test_same_code_refused_repeat_keeps_this_calls_reason(tmp_path, monkeypatch, code):
    monkeypatch.setattr(loop, "reply_state", lambda _: {
        "done": True, "code": code, "stop": "old stop", "attempted": 2, "ended_patch": "before"})
    monkeypatch.setattr(loop, "_patch_now", lambda *args: "after")
    step = loop.Step(loop.STOP, "new target refused", code, call_ran=False, refusal=True)
    reply, _, why = loop.reply_for(tmp_path, step, str(tmp_path), "base")
    assert "new target refused" in why and "old stop" not in reply.text
    assert reply.note == loop.CHANGED


def test_hook_keeps_coded_report_including_its_own_code_text(tmp_path, monkeypatch):
    report = "## What the reviews found\n\n[G6_E_NO_CLEAN_REVIEW] belongs here\nG6_E_NO_CLEAN_REVIEW: quoted\n"
    monkeypatch.setattr(loop, "reply_state", lambda _: {
        "done": True, "code": loop.NO_CLEAN_REVIEW, "attempted": 1, "ending": "untested"})
    step = loop.Step(loop.STOP, report, loop.NO_CLEAN_REVIEW, call_ran=True)
    reply, _, _ = loop.reply_for(tmp_path, step, str(tmp_path), "base", hook=True,
                               report_reader=lambda *args, **kwargs: report)
    assert reply.text == report


def test_same_code_publication_failure_keeps_both_causes(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, 'reply_state', lambda _: {
        'done': True, 'code': OUTSIDE_ROOT, 'stop': 'original boundary refusal', 'attempted': 1})
    step = loop.Step(loop.STOP, 'report target refused', OUTSIDE_ROOT,
                     call_ran=True, settled_here=True, refusal=True)
    reply, _, why = loop.reply_for(tmp_path, step, str(tmp_path), 'base')
    assert 'original boundary refusal' in reply.text and 'report target refused' in reply.text
    assert 'report target refused' in why and not reply.note


def test_body_refusal_and_report_refusal_keep_their_distinct_reasons(disk_run, monkeypatch):
    repo, base, session = disk_run
    real = loop.store.write_bytes

    def scout(*args):
        note_model_call()
        raise WorkspaceRefused(OUTSIDE_ROOT, 'scout output refused')

    def publish(path, body):
        if path.name == 'report.md':
            raise WorkspaceRefused(OUTSIDE_ROOT, 'report publication refused')
        return real(path, body)

    monkeypatch.setattr(loop, '_scout', scout)
    monkeypatch.setattr(loop.store, 'write_bytes', publish)
    reply = review_loop.step(str(repo), TASK, 'disk-review2', base)
    assert reply['code'] == OUTSIDE_ROOT and reply['requests_sent'] == 1
    assert 'scout output refused' in reply['report']
    assert 'report publication refused' in reply['error']
    assert 'did not run' not in reply.get('note', '')


def test_hook_still_codes_a_retained_refusal_reason(tmp_path, monkeypatch):
    reason = 'The output path was refused.'
    monkeypatch.setattr(loop, 'reply_state', lambda _: {
        'done': True, 'code': OUTSIDE_ROOT, 'stop': reason, 'attempted': 0, 'ending': 'untested'})
    step = loop.Step(loop.STOP, reason, OUTSIDE_ROOT, call_ran=True, settled_here=True)
    reply, _, _ = loop.reply_for(tmp_path, step, str(tmp_path), 'base', hook=True,
                               report_reader=lambda *args, **kwargs: reason)
    assert reply.text == f'[{OUTSIDE_ROOT}] {reason}'


def test_settled_sharing_refusal_survives_a_report_oserror(disk_run, monkeypatch):
    repo, base, session = disk_run
    real = loop.store.write_bytes

    def scout(*args):
        note_model_call()
        raise cloud._refusal(LLMError(f'{cloud.REQUIRED}: next request refused'))

    def publish(path, body):
        if path.name == 'report.md':
            raise PermissionError('report locked')
        return real(path, body)

    monkeypatch.setattr(loop, '_scout', scout)
    monkeypatch.setattr(loop.store, 'write_bytes', publish)
    reply = review_loop.step(str(repo), TASK, 'disk-review2', base)
    assert reply['code'] == 'G6_E_COMPLETION_PUBLICATION_UNSAVED' and reply['requests_sent'] == 1
    assert cloud.REQUIRED in reply['report'] and 'up to 1 request(s)' in reply['report']
    assert 'report locked' in reply['error'] and 'note' not in reply
