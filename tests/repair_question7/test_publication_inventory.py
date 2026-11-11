"""Every publication boundary can stop; a fresh invocation can still publish the unasked case."""
import ast
import inspect
import os
import subprocess
import sys

import pytest

from g6.cogarch import _store
from g6.core.storage import state_path
from g6.harness import behaviour_question as bq, executable_scenario as es, question_store as qs
from tests.repair_question2.helpers import TASK, ask, candidate, repo


STEPS = ('mkstemp', 'fdopen', 'write', 'flush', 'fsync', 'close', 'lease', 'replace', 'unlink')


@pytest.mark.parametrize('step', STEPS)
@pytest.mark.parametrize('when', ['before', 'after'])
def test_stop_restart_at_every_publication_step(tmp_path, step, when):
    script = ('from tests.repair_question7.test_publication_inventory import stopped_save; '
              'import sys; from pathlib import Path; stopped_save(Path(sys.argv[1]), *sys.argv[2:])')
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path), step, when],
                            capture_output=True, text=True, timeout=30)
    assert child.returncode == 73, (step, when, child.stdout, child.stderr)
    root = tmp_path / 'repo'
    case = bq.caller_candidates(TASK, [candidate()])[0].question(vs_digest(root))
    scenario = es.Scenario.resolve(root, case)
    target = state_path('scenarios', case.sha256 + '.json')
    state = qs.TaskQuestions(qs.task_identity(root, TASK)).state()
    assert not state.asked and state.left == 1
    if target.exists():
        assert es.load(case).record == scenario.record
    assert ask(root).get('status') == 'confirmed'


def stopped_save(tmp_path, step, when):
    """A real process exit skips every Python cleanup block and releases its descriptors."""
    monkeypatch = pytest.MonkeyPatch()
    from tests.repair_question7.conftest import local_state
    local_state.__wrapped__(tmp_path, monkeypatch)
    root = repo(tmp_path)
    case = bq.caller_candidates(TASK, [candidate()])[0].question(vs_digest(root))
    scenario = es.Scenario.resolve(root, case)
    target = state_path('scenarios', case.sha256 + '.json')
    synced = []

    def event(name, side):
        if (name, side) == (step, when):
            os._exit(73)

    with monkeypatch.context() as fault:
        real_mkstemp, real_fdopen = _store.tempfile.mkstemp, _store.os.fdopen
        real_sync, real_replace = _store.os.fsync, _store.os.replace
        real_lease, real_unlink = _store._check_lease, _store.Path.unlink

        def mkstemp(*args, **kwargs):
            event('mkstemp', 'before')
            fd, name = real_mkstemp(*args, **kwargs)
            assert _store.Path(name).parent == target.parent
            event('mkstemp', 'after')
            return fd, name

        class Stream:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                try:
                    event('close', 'before')
                finally:
                    self.handle.close()
                event('close', 'after')
            def fileno(self):
                return self.handle.fileno()
            def write(self, text):
                event('write', 'before')
                result = self.handle.write(text)
                event('write', 'after')
                return result
            def flush(self):
                event('flush', 'before')
                self.handle.flush()
                event('flush', 'after')

        def fdopen(*args, **kwargs):
            event('fdopen', 'before')
            handle = real_fdopen(*args, **kwargs)
            event('fdopen', 'after')
            return Stream(handle)

        def sync(fd):
            event('fsync', 'before')
            real_sync(fd)
            synced.append(True)
            event('fsync', 'after')

        def replace(part, path):
            event('replace', 'before')
            assert synced and _store.Path(part).read_text() == es.canonical({
                'version': 'scenario-binding.v1', 'question_sha256': case.sha256,
                'scenario_sha256': scenario.sha256, 'scenario': scenario.record.decode('ascii')})
            result = real_replace(part, path)
            event('replace', 'after')
            return result

        def lease(path):
            event('lease', 'before')
            real_lease(path)
            event('lease', 'after')

        def unlink(path, *args, **kwargs):
            event('unlink', 'before')
            result = real_unlink(path, *args, **kwargs)
            event('unlink', 'after')
            return result

        fault.setattr(_store.tempfile, 'mkstemp', mkstemp)
        fault.setattr(_store.os, 'fdopen', fdopen)
        fault.setattr(_store.os, 'fsync', sync)
        fault.setattr(_store.os, 'replace', replace)
        fault.setattr(_store, '_check_lease', lease)
        fault.setattr(_store.Path, 'unlink', unlink)
        es.save(case, scenario)


def vs_digest(root):
    from g6.core.ledger import digest
    from g6.workforce import repo_tests
    return digest(repo_tests.snapshot(root))


@pytest.mark.parametrize('raw', [b'', b'{', b'not JSON', b'null', b'[]', b'{}', b'\xff'])
def test_damaged_sidecar_is_absent_for_a_new_publication(tmp_path, raw):
    root = repo(tmp_path)
    case = bq.caller_candidates(TASK, [candidate()])[0].question(vs_digest(root))
    path = state_path('scenarios', case.sha256 + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    assert ask(root).get('status') == 'confirmed'


def test_inventory_atomic_writer_steps_and_shared_use():
    tree = ast.parse(inspect.getsource(_store.atomic_write))
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert calls == {'path.parent.mkdir', 'tempfile.mkstemp', 'Path', 'os.fdopen', 'stream.write',
                     'stream.flush', 'os.fsync', 'stream.fileno', '_check_lease', 'os.replace', 'part.unlink'}
    assert 'atomic_write(' in inspect.getsource(es.save)
