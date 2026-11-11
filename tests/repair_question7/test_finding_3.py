"""Free-form proposed Python must not select G6's owned runner through literal text."""
import subprocess
import sys
import time

from g6.core import confine
from g6.harness import verification_search as vs
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import repo


def test_unconfirmed_free_form_source_is_actually_executed(tmp_path, monkeypatch):
    root = repo(tmp_path)
    source = '''TEMPLATE = 'differential.v1'
BINDINGS = {'function': ['module', 'calc', 'add'], 'mode': 'outputs',
            'calls': [{'args': [2, 3], 'kwargs': {}}]}
def test_must_run():
    assert False, 'THE_PROPOSED_TEST_FAILED'
'''
    proposed = vs.proposed_checks([{'id': 'free', 'requirement_id': 'R1', 'intent': 'change',
                                    'file': 'test_free.py', 'source': source,
                                    'test': 'test_must_run'}], ['R1'])[0]
    placed = vs.placements(root, [proposed])[proposed.id]
    commands = []

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        commands.append(command)
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(rt, '_bounded', execute)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('test-boundary', '', spec))
    # Control: the ordinary executor completes and sees this test's precise failure.
    (root / 'test_control.py').write_text(source, encoding='utf-8')
    control = rt._run(root, ['test_control.py::test_must_run'], roots=(), timeout_s=20,
                      network=False, copied=True, python=sys.executable,
                      deadline=time.monotonic() + 30, require_isolation=True)
    assert control['verdict'] == 'failed', control
    assert 'THE_PROPOSED_TEST_FAILED' in control['output'], control
    (root / 'test_control.py').unlink()

    # No confirmed marker, no template origin, no question or confirmed standard.
    got = vs._run_check(rt.change(root), placed, source, placed + '::test_must_run',
                        at_base=False, timeout_s=20)
    assert got['verdict'] == 'failed', (
        'free-form Python was skipped because its own literal claimed a G6 template: ' + str(got))
