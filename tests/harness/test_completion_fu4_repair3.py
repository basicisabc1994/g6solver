"""Repair 3: deferred diagnostics use final accounting; hooks tell the person."""
import json
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop as loop


@pytest.mark.parametrize('kind', ['scout', 'critic', 'review'])
@pytest.mark.parametrize('final_bound', [5, None], ids=['known', 'unknown'])
def test_deferred_send_errors_keep_no_count_and_report_the_final_bound(tmp_path, monkeypatch, kind, final_bound):
    early = {loop.ATTEMPTED: 1, 'call_requests': 1}
    monkeypatch.setattr(loop, '_RUN_COUNT', SimpleNamespace(get=lambda: SimpleNamespace(pending=early)))

    def failed(*args, **kwargs):
        raise OSError('session could not start')

    @contextmanager
    def copy(*args):
        yield tmp_path

    change = SimpleNamespace(repo=tmp_path, sources=())
    monkeypatch.setattr(loop, '_base_copy', copy)
    monkeypatch.setattr(loop, '_clock', lambda: 0)
    monkeypatch.setattr(loop, '_checks', lambda *args: ([], {}, []))
    monkeypatch.setattr(loop, '_gate', lambda *args: [])
    monkeypatch.setattr(loop, 'run_set', lambda *args, **kwargs: loop.ProbeRun(None, {}, {}, 'no probes'))
    monkeypatch.setattr(loop, '_shown', lambda *args: SimpleNamespace(masked=''))
    monkeypatch.setattr(loop, '_pack', lambda *args: '')
    monkeypatch.setattr(loop, '_masked_pack', lambda *args: ('', False))
    monkeypatch.setattr(loop, '_named', lambda *args: 'reviewer')
    monkeypatch.setattr(loop.consent, 'guard', lambda caller: SimpleNamespace(complete=failed))
    if kind == 'scout':
        one = loop._scout(change, 'task', '{text}', tmp_path / 'scout', failed, 10_000)
    else:
        one = loop.run_critic(change, 'task', object(), '', tmp_path / 'critic', patch=object())
    assert not re.search(r'Up to \d+ request', json.dumps(one))
    assert one.get('send_error') is True
    state = {loop.ATTEMPTED: 3, 'sets': {}, 'critic': None}
    if kind == 'scout':
        state['sets']['scout'] = one
    else:
        state['critic'] = one

    def final_review(*args):
        state[loop.ATTEMPTED] = final_bound
        return 'final review'

    monkeypatch.setattr(loop, '_final_review', final_review)
    if kind == 'review':
        final_review()
        state.update(reviews=[{**one, 'file': 'test_critic.py', 'results': {}}], rounds=[])
        report = loop._reviews_report(state, 'no review', 'code', 'untested', 'final review')
    else:
        report = loop._report(tmp_path, 'task', 'base', state, 'no probes')
    assert re.findall(r'Up to (\d+) request', report) == (['5'] if final_bound is not None else [])
    assert ('The send count of this run is unknown.' in report) == (final_bound is None)
    assert 'session could not start' in report
