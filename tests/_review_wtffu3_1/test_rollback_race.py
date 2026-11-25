import threading
from pathlib import Path

import pytest

from g6.harness import completion_hook as hook, completion_loop as loop


@pytest.mark.parametrize('failed_stop', [False, True], ids=['control', 'concurrent-failed-stop'])
def test_prompt_binding_survives_an_older_failed_stop(tmp_path, monkeypatch, failed_stop):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(hook, '_here', lambda cwd: (tmp_path, 'base'))
    monkeypatch.setattr(loop, 'prepare', lambda *args: '')
    monkeypatch.setattr(hook, '_hint', lambda task: '')
    event = {'session_id': 'rollback-race', 'cwd': str(tmp_path)}
    session = hook.folder(event['session_id'])
    (session / '.prompt').mkdir(parents=True)
    waiting, release = threading.Event(), threading.Event()
    real_write = Path.write_bytes
    failures = []

    def write(path, body):
        if threading.current_thread().name == 'failed-stop' and path.name == 'task.json.new':
            waiting.set()
            assert release.wait(10)
            raise OSError('first Stop publication failed before writing its spare')
        return real_write(path, body)

    def stop():
        try:
            hook.on_stop(event)
        except OSError as exc:
            failures.append(str(exc))

    monkeypatch.setattr(Path, 'write_bytes', write)
    worker = threading.Thread(target=stop, name='failed-stop') if failed_stop else None
    if worker:
        worker.start()
    try:
        if worker:
            assert waiting.wait(10)
        hook.on_prompt({**event, 'prompt': 'the task the person actually submitted'})
        assert hook._task(session)['task'] == 'the task the person actually submitted'
    finally:
        release.set()
        if worker:
            worker.join(10)
            assert not worker.is_alive()
    assert failures == (['first Stop publication failed before writing its spare'] if failed_stop else [])
    assert (session / 'task.json').is_file(), 'the failed Stop rollback deleted the successful prompt binding'
    assert hook._task(session)['task'] == 'the task the person actually submitted'
