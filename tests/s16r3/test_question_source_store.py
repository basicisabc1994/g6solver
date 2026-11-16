"""Store-level regressions exercise provenance independently of repository subprocess confinement."""
import json

import pytest

from g6.core import cloud_consent as cloud
from g6.core.ledger import digest
from g6.core.storage import g6_home
from g6.harness import behaviour_question as bq, behaviour_runner, question_store as qs
from g6.mcp import behaviour, review_binding, review_reply
from tests.s16r3.test_pending_question_sources import ACTIVITY, CANDIDATE, CODEX, INSTANCES, SECRET, TASK
from tests.s16r3.test_review_binding_reaches_confirm import _share


@pytest.mark.parametrize('retry', ['same', 'other-session', 'other-provider', 'lost-identity', 'revoke', 'source-revoke'])
def test_stored_question_carries_sources_and_only_its_asking_session_inputs(tmp_path, monkeypatch, retry):
    provider, session = CODEX, object()
    source = cloud.Source.text('jobs', 'review', 'recorded review')
    _share(('jobs',), provider)
    store = qs.TaskQuestions(qs.task_identity(tmp_path, TASK))
    candidate = bq.caller_candidates(TASK, [CANDIDATE])[0]
    question, rule = candidate.questions('b' * 64)
    with cloud.request_sources((source,)), cloud.collect_sources() as capture:
        capture.client = provider, session
        capture.supplied = {cloud.input_source(SECRET)}
        cloud.record_sources((source,))
        store.ask(question, rule, candidate)
    pending = qs.TaskQuestions(store.identity).state().pending
    assert getattr(pending, 'provenance', None) is not None, 'asked entry lost its source and client provenance'
    if retry == 'revoke':
        cloud.revoke(cloud.input_source(SECRET), provider)
    if retry == 'source-revoke':
        cloud.revoke('jobs', provider)
    if retry == 'lost-identity':
        monkeypatch.setattr(qs, '_clients', {})   # a fresh process has no retained session objects
    with cloud.request_sources((source,)), cloud.collect_sources() as capture:
        capture.client = ('anthropic:claude_code' if retry == 'other-provider' else provider,
                          object() if retry == 'other-session' else session)
        if retry in ('same', 'other-session', 'lost-identity'):   # per source and provider, not per session
            behaviour._pending(pending)
            assert source in capture.sources
            assert cloud.input_source(SECRET) in capture.supplied
            assert cloud.input_source(SECRET) in {s.source_id for s in capture.sources}
        else:
            with pytest.raises(bq.CandidateRefused) as refused:
                behaviour._pending(pending)
            assert refused.value.code == cloud.REQUIRED


def test_legacy_store_entry_with_no_provenance_cannot_be_relabelled_as_current_input(tmp_path):
    candidate = bq.caller_candidates(TASK, [CANDIDATE])[0]
    question, rule = candidate.questions('b' * 64)
    store = qs.TaskQuestions(qs.task_identity(tmp_path, TASK))
    store.ask(question, rule, candidate)   # a legacy producer had no capture
    with cloud.collect_sources() as capture:
        capture.client = CODEX, object()
        capture.supplied = {cloud.input_source(SECRET)}
        with pytest.raises(bq.CandidateRefused) as refused:
            behaviour._pending(store.state().pending)
        assert refused.value.code == cloud.REQUIRED


def test_activity_custody_survives_finish_without_changing_saved_json():
    one = behaviour_runner.Activity(ACTIVITY)
    one.owned_runs = [True, True, True]
    one.execution_artifacts = [{'owned-execution': 'retained'}]
    reply = behaviour.bound({'assessment': {'completion': {'activities': [one]}}}, 'identity')
    original = json.loads(json.dumps(reply))
    review_reply.finish(reply.custody(json.loads(json.dumps(reply))), 'b' * 32)
    saved = g6_home() / 'reviews' / ('b' * 32 + '.json')
    assert json.loads(saved.read_bytes()) == original
    assert saved.with_suffix('.observations.json').is_file(), 'detached copy lost owned probe custody'
    record = json.loads(saved.with_suffix('.observations.json').read_bytes())
    assert record['probes'] == {'D1': digest(ACTIVITY)}
    assert record['executions'] == {'D1': [{'owned-execution': 'retained'}]}


def test_sources_must_bind_the_checked_review_bytes_before_a_swap(monkeypatch):
    # The checked reply is retained in the reader while a different valid saved/source pair replaces it.
    job = 'c' * 32
    old = behaviour.bound({'artifact_digest': 'tree', 'assessment': {'completion': {'activities': [ACTIVITY]}}},
                          'identity')
    new = {'assessment': {'completion': {}}}
    saved = review_reply.save(old, job)
    from pathlib import Path
    _share(('jobs',), CODEX)
    review_reply.record(job, old, (cloud.Source.text(cloud.input_source(SECRET), 'original', SECRET),))
    original = review_binding.matches

    def swapping(path, identity, *, data=None):
        matched = original(path, identity, data=data)
        Path(saved).write_bytes(json.dumps(new, ensure_ascii=False, indent=1).encode('utf-8'))
        review_reply.record(job, new, (cloud.Source.text('jobs', 'replacement', 'public'),))
        assert review_reply.carried(job) is not None, 'replacement source pair must itself be valid'
        assert matched, 'the original binding must pass before the swap'
        return matched
    monkeypatch.setattr(review_binding, 'matches', swapping)
    monkeypatch.setattr(behaviour_runner, 'read_probes', lambda *args: {'D1': digest(ACTIVITY)})
    with cloud.request_sources((cloud.Source.text('jobs', 'caller', 'synthetic caller'),)), \
            cloud.collect_sources() as capture:
        capture.client = CODEX, object()
        cloud.refuse_unshared(CODEX, (cloud.Source.text('jobs', 'caller', 'synthetic caller'),))
        with pytest.raises(bq.CandidateRefused) as refused:
            behaviour._review_candidates(TASK, job, INSTANCES, identity='identity', artifact='tree')
        assert refused.value.code == cloud.REQUIRED
