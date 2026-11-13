"""Every retained job view keeps the completion snapshot, including failures."""
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core import cloud_consent as cloud
from g6.metaharness.jobs import Jobs


FIELDS = ('text', 'nested', 'items', 'completion_state', 'warning_card', 'evidence', 'request_id', 'run_id')


@pytest.mark.parametrize('detail', ['full', 'compact'])
@pytest.mark.parametrize('field', FIELDS)
def test_every_retained_field_is_a_completion_snapshot(detail, field):
    value = dict(text='shared', nested={'text': 'shared'}, items=['shared'],
                 completion_state='qualified-draft', warning_card='shared', evidence=['shared'],
                 request_id='shared', run_id='shared')
    compact = dict(value)
    source = cloud.Source.text('shared', 'passage', 'shared')
    jobs = Jobs()
    with cloud.collect_sources():
        started = jobs.start(lambda: cloud.bind_output(value, (source,)),
                             compact=lambda *_: cloud.bind_output(compact, (source,)))
    jobs.close()
    target = value if detail == 'full' else compact
    if field == 'nested':
        target[field]['text'] = 'PRIVATE_AFTER_COMPLETION'
    elif field in ('items', 'evidence'):
        target[field].append('PRIVATE_AFTER_COMPLETION')
    else:
        target[field] = 'PRIVATE_AFTER_COMPLETION'
    with cloud.collect_sources():
        reply = jobs.status(started['job_id'], detail)
        assert 'PRIVATE_AFTER_COMPLETION' not in str(reply)
        reply['result']['text'] = 'PRIVATE_POLL_MUTATION'
        assert 'PRIVATE_POLL_MUTATION' not in str(jobs.status(started['job_id'], detail))


@pytest.mark.parametrize('detail', ['full', 'compact'])
def test_corrupt_snapshot_is_never_relabelled(detail):
    jobs = Jobs()
    with cloud.collect_sources():
        started = jobs.start(lambda: cloud.bind_output({'text': 'shared'}, ()), compact=lambda *_: {'text': 'shared'})
    jobs.close()
    ident = started['job_id']
    frozen = jobs._compact[ident] if detail == 'compact' else jobs._jobs[ident].result()
    assert hasattr(frozen, 'body_sha256'), 'retained bytes have no completion digest'
    corrupt = replace(frozen, body='{"ok":true,"status":"completed","result":"PRIVATE_CORRUPT"}')
    if detail == 'compact':
        jobs._compact[ident] = corrupt
    else:
        jobs._jobs[ident]._result = corrupt
    with cloud.collect_sources():
        reply = jobs.status(ident, detail)
        assert 'PRIVATE_CORRUPT' not in str(reply)
        assert cloud.UNKNOWN in cloud.output_sources(reply)


def test_failed_job_freezes_exception_text_at_completion():
    class MutableFailure(Exception):
        text = 'original failure'
        def __str__(self):
            return self.text
    error = MutableFailure()
    def fail():
        raise error
    jobs = Jobs()
    started = jobs.start(fail)
    jobs.close()
    error.text = 'PRIVATE_EXCEPTION_MUTATION'
    assert 'PRIVATE_EXCEPTION_MUTATION' not in str(jobs.status(started['job_id']))
    assert jobs.status(started['job_id'])['status'] == 'failed'
    assert 'G6_E_JOB_FAILED' in str(jobs.status(started['job_id']))


def test_snapshot_receipt_matches_serialized_bytes_without_rereading_the_live_value(monkeypatch):
    freeze = getattr(cloud, 'freeze_output', None)
    assert freeze is not None, 'there is no receipt check for the frozen bytes'
    with cloud.collect_sources():
        value = {'text': 'shared'}
        source = cloud.Source.text('shared', 'passage', 'shared')
        cloud.bind_output(value, (source,))
        original = cloud.to_jsonable_python
        def serialize(obj):
            if obj is value:
                return {'text': 'PRIVATE_DURING_SERIALIZATION'}
            return original(obj)
        monkeypatch.setattr(cloud, 'to_jsonable_python', serialize)
        body, refs, metadata = freeze(value)
        assert 'PRIVATE_DURING_SERIALIZATION' in body
        assert cloud.UNKNOWN in refs and source in refs
        assert metadata == {}
        assert value['text'] == 'shared'


def test_retained_metadata_is_a_detached_completion_snapshot():
    metadata = {'request_id': {'text': 'shared'}}
    jobs = Jobs()
    with cloud.collect_sources():
        started = jobs.start(lambda: cloud.bind_output({'text': 'shared'}, (), metadata=metadata))
    jobs.close()
    metadata['request_id']['text'] = 'PRIVATE_METADATA_MUTATION'
    with cloud.collect_sources():
        jobs.status(started['job_id'])
        controls = cloud.current_capture().job['metadata']
        assert 'PRIVATE_METADATA_MUTATION' not in str(controls)
        assert controls['request_id']['text'] == 'shared'


def test_a_compactor_that_mutates_then_fails_cannot_change_the_full_snapshot():
    source = cloud.Source.text('shared', 'passage', 'shared')
    value = {'text': 'shared'}
    def compact(result, ident):
        result['text'] = 'PRIVATE_COMPACTOR_MUTATION'
        raise RuntimeError('compactor failed')
    jobs = Jobs()
    with cloud.collect_sources():
        started = jobs.start(lambda: cloud.bind_output(value, (source,)), compact=compact)
    jobs.close()
    for detail in ('full', 'compact'):
        assert jobs.status(started['job_id'], detail)['result']['text'] == 'shared'


def test_polling_before_completion_returns_only_operational_status():
    from threading import Event
    ready, finish = Event(), Event()
    def compact(result, ident):
        ready.set()
        assert finish.wait(5)
        return result
    jobs = Jobs()
    try:
        started = jobs.start(lambda: {'text': 'PRIVATE_PENDING'}, compact=compact)
        assert ready.wait(5)
        assert 'PRIVATE_PENDING' not in str(jobs.status(started['job_id']))
    finally:
        finish.set()
        jobs.close()


def test_a_future_without_a_completion_snapshot_withholds_its_exception():
    from concurrent.futures import Future
    jobs = Jobs()
    future = Future()
    future.set_exception(RuntimeError('PRIVATE_UNSNAPSHOTTED_FUTURE'))
    jobs._jobs['cancelled'] = future
    try:
        with cloud.collect_sources():
            reply = jobs.status('cancelled')
            assert reply['status'] == 'failed'
            assert 'PRIVATE_UNSNAPSHOTTED_FUTURE' not in str(reply)
            assert cloud.UNKNOWN in cloud.output_sources(reply)
    finally:
        jobs.close()


def retained_reads(source):
    tree = ast.parse(source)
    return {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr in ('result', 'status')}


def retained_stores(source):
    return {target.attr for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Dict) for target in n.targets if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name) and target.value.id == 'self'}


def test_code_derived_retained_read_inventory(tmp_path):
    root = Path(cloud.__file__).resolve().parents[1]
    found = {}
    for path in root.rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        if 'Jobs' in source or path.name == 'jobs.py' and path.parent.name == 'metaharness':
            reads = {s for s in retained_reads(source) if s in ('job.result', 'jobs.status', '_JOBS.status')}
            if reads:
                found[path.relative_to(root).as_posix()] = reads
    assert found == {'metaharness/jobs.py': {'job.result'}, 'mcp/__init__.py': {'jobs.status'}}
    from g6.metaharness.jobs import DETAILS
    assert set(DETAILS) == {'full', 'compact'}
    source = (root / 'metaharness/jobs.py').read_text(encoding='utf-8')
    assert retained_stores(source) == {'_jobs', '_compact', '_provenance'}
    assert retained_stores(source + '\ndef extra(self):\n    self._extra_retained = {}\n') - retained_stores(source) == {
        '_extra_retained'}
    planted = 'def read(job):\n    return job.result()\n'
    assert retained_reads(planted) == {'job.result'}
