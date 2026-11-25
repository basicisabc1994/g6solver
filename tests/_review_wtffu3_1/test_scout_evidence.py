import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from g6.harness import completion_hook as hook, completion_loop as loop


@pytest.mark.parametrize('unusable', [False, True], ids=['control-failed-test', 'no-test-ran'])
def test_resumed_scouts_do_not_call_an_unrun_probe_observed(tmp_path, key_file, monkeypatch, unusable):
    key_file('G6_LOOP_MODE=scouts\n')
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'mod.py').write_text('value = 2\n')
    session = hook.folder('scout-evidence')
    (session / '.step').mkdir(parents=True)
    loop.store.home_path(*loop.OWING).mkdir()
    change = SimpleNamespace(repo=repo, base_commit='base', sources=('mod.py',))
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda root: repo)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *args: change)
    monkeypatch.setattr(loop.repo_tests, 'snapshot', lambda root: {'mod.py': (repo / 'mod.py').read_text()})
    monkeypatch.setattr(loop.repo_tests, 'diff_text', lambda change: 'patch')
    monkeypatch.setattr(loop.proposer, 'boundary', lambda root: '')
    monkeypatch.setattr(loop, 'scout_host', lambda: object())
    monkeypatch.setattr(loop, 'critic_host', lambda: None)
    monkeypatch.setattr(loop, '_told', lambda *args: '')
    monkeypatch.setattr(loop, 'review_patch', lambda *args, **kwargs: {'assessment': {'report': 'Final review stand-in.'}})

    def scout(change, task, text, where, host, deadline):
        return {'left_out': '', 'where': str(where), 'refused': [],
                'at_base': asdict(loop.ProbeRun(0, {'test_p.py::test_value': 'passed'}, {}))}

    probe = (loop.ProbeRun(None, {}, {}, 'runner could not start') if unusable else
             loop.ProbeRun(1, {'test_p.py::test_value': 'failed'}, {}))
    monkeypatch.setattr(loop, '_scout', scout)
    monkeypatch.setattr(loop, 'run_set', lambda *args, **kwargs: probe)
    # A process died after persisting a refusal's debt, before the terminal report.
    with loop._writing(session):
        loop._save(session, {'attempted': 0, 'stop': 'interrupted refusal',
                            'row_due': json.dumps({'session': session.name, 'cause': loop.UNCONFINED})}, sources=())
    assert loop.advance(repo, 'check value', 'base', session).action == loop.CONTINUE
    assert loop.advance(repo, 'check value', 'base', session).action == loop.STOP
    state = loop._load(session)
    assert state['done'] and state['rounds'][0]['failing']
    assert all(count == (0 if unusable else 1) for count in state['ran'].values())
    rows = [json.loads(line) for line in loop.store.home_path(*loop.FAILURES).read_text().splitlines()]
    assert len(rows) == 1 and rows[0]['session'] == session.name
    assert rows[0]['independently_observed'] is (not unusable), rows[0]
