import errno
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.core.llm import LLMError
from g6.core.model_calls import note_model_call
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.harness.workflow import policy
from g6.mcp import review_loop


class ProcessDied(BaseException):
    pass


@pytest.fixture
def session_world(tmp_path, key_file, monkeypatch):
    key_file('G6_LOOP_MODE=scouts\n')
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'mod.py').write_text('value = 2\n')
    event = {'cwd': str(repo), 'session_id': 'mcp:trace', 'last_assistant_message': 'finished'}
    session = hook.folder(event['session_id'])
    (session / '.prompt').mkdir(parents=True)
    (session / '.step').mkdir()
    loop.store.home_path(*loop.OWING).mkdir()
    change = SimpleNamespace(repo=repo, base_commit='base', sources=('mod.py',))
    monkeypatch.setattr(hook, '_here', lambda cwd: (repo, 'base'))
    monkeypatch.setattr(hook, '_hint', lambda task: '')
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda root: repo)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *args: change)
    monkeypatch.setattr(loop.repo_tests, 'diff_text', lambda change: 'patch')
    monkeypatch.setattr(loop.proposer, 'boundary', lambda root: '')
    monkeypatch.setattr(loop, 'scout_host', lambda: object())
    monkeypatch.setattr(loop, 'critic_host', lambda: None)
    monkeypatch.setattr(policy, 'enabled', lambda: (False, ''))
    return repo, event, session


def test_prompt_stop_refusal_killed_task_publication_prompt_and_mcp(session_world, monkeypatch):
    repo, event, session = session_world
    hook.on_prompt({**event, 'prompt': 'first task'})

    def refused(*args):
        note_model_call()
        raise cloud._refusal(LLMError(f'{cloud.REQUIRED}: the next request was refused'))

    monkeypatch.setattr(loop, '_scout', refused)
    first = hook.on_stop(event)['systemMessage']
    assert cloud.REQUIRED in first and 'up to 1 request(s)' in first
    assert loop.attempted(loop._load(session)) == 1
    assert hook.on_stop(event) == {'systemMessage': loop.ALREADY_ENDED}
    repeat = review_loop.step(str(repo), 'first task', 'trace', 'base')
    assert repeat['code'] == cloud.REQUIRED and repeat['requests_sent'] == 1
    assert 'did not run' in repeat['note']
    original = loop.store.os.replace

    def die(source, target):
        if target == session / 'task.sha256':
            raise ProcessDied('killed between task content and receipt replacements')
        return original(source, target)

    with monkeypatch.context() as cut:
        cut.setattr(loop.store.os, 'replace', die)
        with pytest.raises(ProcessDied):
            hook.on_prompt({**event, 'prompt': 'interrupted next task'})
    torn = (session / 'task.json').read_bytes(), (session / 'task.sha256').read_bytes()
    message = hook.on_stop(event)['systemMessage']
    assert loop.store.TASK_UNREADABLE in message and cloud.REQUIRED not in message
    blocked = review_loop.step(str(repo), 'first task', 'trace', 'base')
    assert blocked['code'] == loop.store.TASK_UNREADABLE and 'new session' in blocked['recovery']
    hook.on_prompt({**event, 'prompt': 'recovered task'})
    asked = hook._task(session)
    assert asked['task'] == 'recovered task' and asked['run'] == 2
    assert ((session / 'task.json').read_bytes(), (session / 'task.sha256').read_bytes()) == torn
    assert 'up to 1 request(s)' in hook.on_stop(event)['systemMessage']
    assert loop.attempted(loop._load(session)) == 1
    assert loop.attempted(loop._load(hook.current(event['session_id'], asked))) == 1
    blocked = review_loop.step(str(repo), 'recovered task', 'trace', 'base')
    assert blocked['code'] == loop.store.TASK_UNREADABLE and 'new session' in blocked['recovery']


def test_reservation_sharing_failure_never_dispatches(session_world, monkeypatch):
    repo, event, session = session_world
    sent = []
    original = loop.store.os.replace

    def fail_receipt(source, target):
        if target == session / 'state.sha256':
            raise PermissionError(errno.EACCES, 'receipt held by a reader')
        return original(source, target)

    def scout(*args):
        note_model_call()
        sent.append('request dispatched')
        raise AssertionError('dispatch must be blocked')

    monkeypatch.setattr(loop, '_scout', scout)
    with monkeypatch.context() as held:
        held.setattr(loop.store.os, 'replace', fail_receipt)
        reply = review_loop.step(str(repo), 'reservation task', 'trace', 'base')
    assert reply['code'] == loop.RESERVATION_UNSAVED and sent == []
    assert not loop._load(session).get('done')


@pytest.mark.parametrize('surface', ['hook', 'mcp'])
def test_settling_call_keeps_its_refusal_when_report_write_fails(session_world, monkeypatch, surface):
    repo, event, session = session_world
    if surface == 'hook':
        hook.on_prompt({**event, 'prompt': 'publication task'})
    original = loop.store.write_bytes

    def scout(*args):
        note_model_call()
        raise cloud._refusal(LLMError(f'{cloud.REQUIRED}: next request refused'))

    def publish(path, body):
        if path.name == 'report.md':
            raise OSError('report held')
        return original(path, body)

    monkeypatch.setattr(loop, '_scout', scout)
    monkeypatch.setattr(loop.store, 'write_bytes', publish)
    reply = (hook.on_stop(event) if surface == 'hook' else
             review_loop.step(str(repo), 'publication task', 'trace', 'base'))
    text = reply['systemMessage'] if surface == 'hook' else reply['report']
    assert cloud.REQUIRED in text and 'up to 1 request(s)' in text and 'report held' in text
    assert loop.PUBLICATION_UNSAVED in text and 'did not run' not in reply.get('note', '')
    assert loop.attempted(loop._load(session)) == 1


def test_kill_during_restore_withholds_mixed_publication(tmp_path, monkeypatch):
    target = tmp_path / 'task.json'
    store = loop.store
    store.write_retained(target, b'old')
    original = store.os.replace
    store.write_retained(target, b'new')
    old = {target: b'old', target.with_suffix('.sha256'): cloud.sha256(b'old').encode('ascii')}

    def die(source, destination):
        if destination == target.with_suffix('.sha256'):
            raise ProcessDied('killed restoring receipt')
        return original(source, destination)

    monkeypatch.setattr(store.os, 'replace', die)
    with pytest.raises(ProcessDied):
        store._restore_retained(target, old)
    with pytest.raises(ValueError, match='matching completion digest'):
        cloud.read_retained(target, target.with_suffix('.sha256').read_text())
