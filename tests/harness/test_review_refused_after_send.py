# SPDX-License-Identifier: Apache-2.0
"""G6 says a review was "not sent" only when no request was counted in its scope: a consent refusal that comes after a
request went out is a review refused part way, never one that was not sent (review 1 of claude/wire-typed-fix, the
coordinator's ruling on its ruling 1)."""
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import completion_loop as loop


def _advance(tmp_path, monkeypatch, review):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    session = tmp_path / 'session'
    session.mkdir()
    change = SimpleNamespace(repo=tmp_path, sources=('code.py',))
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: change)
    monkeypatch.setattr(loop.repo_tests, 'snapshot', lambda *a: {})
    monkeypatch.setattr(loop, '_patch_now', lambda *a: 'patch')
    monkeypatch.setattr(loop.proposer, 'boundary', lambda *a: '')
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: False)
    monkeypatch.setattr(loop, '_owe_row', lambda *a: None)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)
    monkeypatch.setattr(loop.repo_tests, 'diff_text', lambda *a: 'PLANTED_PATCH')
    monkeypatch.setattr(loop, 'review_round', review)
    critic = SimpleNamespace(complete=lambda *a, **k: 'none')
    step = loop._advance(tmp_path, 'task', 'base', session, None, critic, '', True, '')
    return step, loop._load(session)


def _refused_after(sends, code):
    def review(change, task, critic, shipped, session, reviews, deadline):
        from g6.core.llm import LLMError
        from g6.core.model_calls import note_model_call

        for _ in range(sends):
            note_model_call()                     # a request that went out to the reviewer's provider
        raise LLMError(f"{code}: the provider's sharing refused the next one")
    return review


def test_a_consent_refusal_after_a_counted_request_is_never_said_not_sent(tmp_path, monkeypatch):
    step, state = _advance(tmp_path, monkeypatch, _refused_after(1, cloud.REQUIRED))
    assert step.ending == 'untested' and step.code == cloud.REQUIRED
    assert 'not sent' not in step.text.lower() and 'not sent' not in state['stop'].lower()
    assert 'went out' in state['stop'] and state['requests_sent'] == 1


def test_a_consent_refusal_with_nothing_counted_is_still_not_sent(tmp_path, monkeypatch):
    step, state = _advance(tmp_path, monkeypatch, _refused_after(0, cloud.REQUIRED))
    assert step.code == cloud.REQUIRED and state['stop'].startswith('The review was not sent.')
    assert state['requests_sent'] == 0          # kept at zero: only a count present and zero says "not sent"


def test_a_sharing_state_failure_after_a_request_does_not_say_nothing_was_shared(tmp_path, monkeypatch):
    from g6.core import refusals

    step, state = _advance(tmp_path, monkeypatch, _refused_after(2, cloud.STATE_UNAVAILABLE))
    assert step.code == loop.CONSENT_STATE_AFTER_SEND != cloud.STATE_UNAVAILABLE
    assert 'nothing was shared' not in refusals.layer(step.code).words
    assert 'not sent' not in state['stop'].lower() and state['requests_sent'] == 2


def test_a_loop_reply_after_a_counted_request_is_not_an_unsent_review():
    from g6.harness.workflow import obstacle as ob

    loop_reply = {"ending": "untested", "run_id": "f" * 32, "report": "The review was refused part way.",
                  "error": "[G6_E_CLOUD_CONSENT_REQUIRED] Results were withheld", "requests_sent": 1}
    (found,) = ob.obstacles(None, loop_reply, ())
    assert found.kind == "refused_run"


def test_recorded_loop_ends_after_a_counted_request_are_not_unsent_reviews():
    from g6.harness.workflow import diagnose

    def event(kind, run, at, **data):
        return SimpleNamespace(kind=kind, run=run, at=at, data=data, refs=())

    events = []
    for n, run in enumerate(("r1", "r2")):
        events += [event("run_started", run, 10 * n, origin_kind="loop_session"),
                   event("loop_ended", run, 10 * n + 1, ending="untested", code=cloud.REQUIRED, requests_sent=1)]
    fired = diagnose._g6_setup_blocks(events, None, None, ())
    assert [one[0].kind for one in fired] == ["refused_run"]


@pytest.mark.parametrize("sent, unsent", [(0, True), (None, False), (1, False)])
def test_the_review_loop_reply_counts_as_unsent_only_with_nothing_sent(sent, unsent):
    from g6.mcp import review_loop

    # None: no count in the reply, which is unknown (review 4), never zero
    reply = {"ending": "untested", "code": cloud.REQUIRED, "warning_card": {"code": cloud.REQUIRED},
             "error": f"[{cloud.REQUIRED}] withheld", **({"requests_sent": sent} if sent is not None else {})}
    assert review_loop._unsent(reply) is unsent

