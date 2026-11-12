"""Challenge serialized bytes at each result producer, reader and handoff."""
import asyncio
import json
from pathlib import Path

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import completion_loop as loop
from g6.mcp import review_reply
from g6.mcp.cloud_consent import _source_function
from g6.metaharness.jobs import Jobs, _FrozenResult


def test_mcp_cached_snapshot_still_checks_its_completion_digest():
    from fastmcp.tools.tool import ToolResult
    from g6.mcp.cloud_consent import _matches_return
    original = {'text': 'shared'}
    source = cloud.Source.text('shared', 'passage', 'shared')
    def produce() -> dict:
        return cloud.bind_output(original, (source,))
    wrapped = _source_function(produce)
    with cloud.collect_sources() as capture:
        capture.expected_tool = wrapped._g6_tool_identity
        asyncio.run(wrapped())
        retained = list(capture.returned)
        changed = {'text': 'PRIVATE_REPLACED_CAPTURE'}
        # Preserve each product's native retained representation, and corrupt
        # only its value. Its completion digest and original sources stay put.
        retained[0] = json.dumps(changed) if isinstance(retained[0], str) else changed
        capture.returned = tuple(retained)
        assert not _matches_return(capture.returned, ToolResult(content=changed))


@pytest.mark.parametrize('public', [False, True])
def test_mcp_producer_hands_on_the_snapshot_checked_against_its_receipt(monkeypatch, public):
    value = {'text': 'shared'}
    source = cloud.Source.text('shared', 'passage', 'shared')

    def produce() -> dict:
        return cloud.bind_output(value, () if public else (source,))

    wrapped = _source_function(produce)
    original = cloud.freeze_output
    def freeze(obj, **kwargs):
        result = original(obj, **kwargs)
        if obj is value:
            obj['text'] = 'PRIVATE_AFTER_FREEZE'
        return result
    monkeypatch.setattr(cloud, 'freeze_output', freeze)
    with cloud.collect_sources() as capture:
        capture.expected_tool = wrapped._g6_tool_identity
        sent = asyncio.run(wrapped())
        assert sent == {'text': 'shared'}
        assert value['text'] == 'PRIVATE_AFTER_FREEZE', 'the boundary never froze the producer value'


def test_freeze_mismatch_is_sticky_even_if_the_value_is_rebound():
    with cloud.collect_sources():
        value = cloud.bind_output({'text': 'shared'}, ())
        value['text'] = 'PRIVATE_MUTATION'
        _, refs, _ = cloud.freeze_output(value)
        assert cloud.UNKNOWN in refs
        cloud.bind_output(value, ())
        assert cloud.UNKNOWN in cloud.output_sources(value)


def test_retained_job_read_uses_the_bytes_it_hashed(monkeypatch):
    retained = _FrozenResult.of({'text': 'shared'})
    original = cloud.sha256
    def hashing(body):
        result = original(body)
        if body == retained.body:
            object.__setattr__(retained, 'body', '{"text":"PRIVATE_AFTER_DIGEST"}')
        return result
    monkeypatch.setattr(cloud, 'sha256', hashing)
    assert retained.read() == {'text': 'shared'}


def test_compaction_receives_a_detached_full_snapshot():
    value = {'text': 'shared'}
    detached = []
    def compact(result, ident):
        detached.append(result is not value)
        value['text'] = 'PRIVATE_LIVE_VALUE'
        return result
    jobs = Jobs()
    try:
        with cloud.collect_sources():
            started = jobs.start(lambda: cloud.bind_output(value, ()), compact=compact)
        jobs.close()
        assert jobs.status(started['job_id'])['result'] == {'text': 'shared'}
        assert detached == [True], 'the compactor received a live result'
    finally:
        jobs.close()


def test_review_compaction_and_saved_copy_use_the_same_checked_snapshot(monkeypatch):
    value = {'assessment': {'report': 'shared'}}
    stored = []
    def save(snapshot, job_id):
        stored.append(json.loads(json.dumps(snapshot)))
        value['assessment']['report'] = 'PRIVATE_AFTER_SAVE'
        return None
    monkeypatch.setattr(review_reply, 'save', save)
    with cloud.collect_sources():
        cloud.bind_output(value, ())
        result = review_reply.finish(value, 'job')
        assert result['assessment']['report'] == stored[0]['assessment']['report'] == 'shared'


@pytest.mark.parametrize('damage', ['changed', 'missing_digest', 'malformed_digest', 'unreadable', 'invalid_json'])
def test_retained_state_cannot_reuse_completion_sources(tmp_path, monkeypatch, damage):
    with cloud.collect_sources() as capture:
        cloud.record_sources((cloud.Source.text('shared', 'passage', 'shared'),))
        token = loop._SOURCE_STATE.set(capture)
        try:
            loop._finish(tmp_path, {'sets': {}, 'stop': 'shared'}, 'shared', ending='clean')
        finally:
            loop._SOURCE_STATE.reset(token)
    state = tmp_path / 'state.json'
    receipt = tmp_path / 'state.sha256'
    assert receipt.is_file(), 'the retained state has no independent completion digest'
    if damage == 'changed':
        data = json.loads(state.read_text(encoding='utf-8'))
        data['stop'] = 'PRIVATE_CHANGED_STATE'
        state.write_text(json.dumps(data), encoding='utf-8')
    elif damage == 'missing_digest':
        receipt.unlink()
    elif damage == 'malformed_digest':
        receipt.write_text('invalid', encoding='ascii')
    elif damage == 'invalid_json':
        state.write_text('{', encoding='utf-8')
    else:
        original = Path.read_bytes
        def read(path):
            if path == state:
                raise OSError('unreadable retained state')
            return original(path)
        monkeypatch.setattr(Path, 'read_bytes', read)
    with cloud.collect_sources():
        result = loop._load(tmp_path)
        assert 'PRIVATE_CHANGED_STATE' not in str(result)
        assert cloud.UNKNOWN in loop.source_refs(result)


@pytest.mark.parametrize('stage', ['before_read', 'after_read', 'during_digest'])
def test_saved_report_checks_and_returns_one_read(tmp_path, monkeypatch, stage):
    reader = getattr(loop, 'read_report', None)
    assert reader is not None, 'saved reports have no completion-bound reader'
    with cloud.collect_sources() as capture:
        token = loop._SOURCE_STATE.set(capture)
        try:
            loop._finish(tmp_path, {}, 'shared', ending='clean')
        finally:
            loop._SOURCE_STATE.reset(token)
    state = loop._load(tmp_path)
    report = tmp_path / 'report.md'
    if stage == 'before_read':
        report.write_text('PRIVATE_REPLACEMENT', encoding='utf-8')
    elif stage == 'after_read':
        original = Path.read_bytes
        def read(path):
            body = original(path)
            if path == report:
                path.write_text('PRIVATE_REPLACEMENT', encoding='utf-8')
            return body
        monkeypatch.setattr(Path, 'read_bytes', read)
    else:
        original = cloud.sha256
        def hashing(body):
            result = original(body)
            if body == b'shared':
                report.write_text('PRIVATE_REPLACEMENT', encoding='utf-8')
            return result
        monkeypatch.setattr(cloud, 'sha256', hashing)
    with cloud.collect_sources() as capture:
        result = reader(tmp_path, state)
        assert 'PRIVATE_REPLACEMENT' not in result
        if stage == 'before_read':
            assert cloud.UNKNOWN in capture.sources
        else:
            assert result == 'shared'


@pytest.mark.parametrize('file', ['state', 'report'])
def test_old_completion_without_digests_stays_unknown(tmp_path, file):
    source = cloud.Source.text('shared', 'passage', 'shared')
    from dataclasses import asdict
    state = {'done': True, 'consent_sources': [asdict(source)]}
    (tmp_path / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    (tmp_path / 'report.md').write_text('PRIVATE_LEGACY_REPORT', encoding='utf-8')
    with cloud.collect_sources() as capture:
        result = loop._load(tmp_path) if file == 'state' else loop.read_report(tmp_path, state)
        assert 'PRIVATE_LEGACY_REPORT' not in str(result)
        assert cloud.UNKNOWN in (*capture.sources, *loop.source_refs(result if isinstance(result, dict) else state))
