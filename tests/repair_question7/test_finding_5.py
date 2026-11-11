"""Ignored directory names must not hide executable inputs planted in a review copy."""
import subprocess
from contextlib import contextmanager

from g6.core import confine
from g6.harness import behaviour_question as bq, behaviour_runner as runner, patch_review, verification_search as vs
from g6.harness.executable_scenario import Scenario
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import REQUIREMENTS, TASK, ask, candidate, repo, stated


def test_python_source_in_cache_named_directory_is_bound_or_refused(tmp_path, monkeypatch):
    root = repo(tmp_path)
    (root / 'calc.py').write_text('''def add(a, b):
    try:
        from __pycache__.helper import VALUE
    except ModuleNotFoundError:
        return -1
    return VALUE
''', encoding='utf-8')
    assert ask(root).get('status') == 'confirmed'
    change = rt.change(root)
    before = vs.bind_execution(change, rt.snapshot(root), deadline=vs._clock() + 60)
    question = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    source = runner.CONFIRMED + Scenario.resolve(root, question).source('A')
    real_copy = rt.repository_copy
    plant = []
    consumed = []

    @contextmanager
    def copied(*args, **kwargs):
        with real_copy(*args, **kwargs) as copy:
            if plant:
                # An outside writer, before G6 validates the initial copy; no child writes a file.
                folder = copy / '__pycache__'
                folder.mkdir()
                (folder / '__init__.py').write_text('', encoding='utf-8')
                (folder / 'helper.py').write_bytes(b'VALUE = 5\n')
            yield copy

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        if plant:
            consumed.append((cwd / '__pycache__/helper.py').read_bytes())
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(rt, 'repository_copy', copied)
    monkeypatch.setattr(rt, '_bounded', execute)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('test-boundary', '', spec))

    def run():
        return vs._run_check(change, 'test_g6_example.py', source, 'test_g6_example.py',
                             at_base=False, timeout_s=20, artefact=before)

    control = run()
    assert control['g6_observation']['result'] == 'failed', control
    plant.append(True)
    got = run()
    if got['verdict'] == 'passed':
        assert consumed == [b'VALUE = 5\n']
        assert '__pycache__/helper.py' not in got.get('execution_artifact', {}).get('files', {})
    else:
        return  # Refusing the unexpected directory before executing it is safe.

    # Ordinary stated checks are a fixture; confirmed patch/control/replay and the issuer are real.
    stated(monkeypatch)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK,
                                requirements=REQUIREMENTS, timeout_s=60)
    confirmed = reply['assessment']['confirmed_expectations']
    [entry] = confirmed['expectations']
    assert [entry['check']['runs'][name]['result'] for name in ('patch', 'base', 'again')] == [
        'passed', 'failed', 'passed']
    [statement] = confirmed['evidence']['expectations']
    assert not statement['checked'], 'issuer counted a pass supplied by unmanifested Python source'
