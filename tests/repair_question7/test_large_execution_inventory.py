"""The largest admitted historical input reaches a real child without repeating it on the command line."""
import subprocess

import pytest

from g6.core import confine
from g6.harness import behaviour_runner as runner, executable_scenario as es, verification_templates as vt
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import repo
from tests.repair_question7.test_admission_inventory import KINDS, historical_compile, item, largest_historical


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('size', ['reported', 'largest_historical'])
def test_real_child_enforces_large_scenario_without_duplicate_argv(tmp_path, monkeypatch, kind, size):
    root = repo(tmp_path)
    code = {'returns': 'def add(value):\n    return 5\n',
            'raises': 'def add(value):\n    raise ValueError()\n',
            'effect': "def add(value):\n    value[:] = ['done']\n"}[kind]
    (root / 'calc.py').write_text(code, encoding='utf-8')
    tree = vt.Tree.read(root, rt.change(root))
    if size == 'reported':
        case = item(kind, 'x' * 12000).question('b' * 64)
    else:
        case, _, _, _, _ = largest_historical(root, kind)
    historical_compile(root, case, tree)
    scenario = es.Scenario.resolve(root, case)
    source = scenario.source('A')
    _, data = runner.bindings(source)
    commands = []

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        commands.append(command)
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(rt, '_bounded', execute)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('fixture-boundary', '', spec))
    deadline = runner.time.monotonic() + 60
    with rt.repository_copy(root, deadline=deadline) as copy:
        got = runner.run(copy, data, 'test_owned.py', timeout_s=20, deadline=deadline)
        assert got['verdict'] == 'passed', got
        namespace = {}
        exec(compile(source, '<portable canonical check>', 'exec'), namespace)
        with monkeypatch.context() as local:
            local.chdir(copy)
            namespace['_check_example']()
    assert commands[0][-1] == '{}', 'the canonical program already holds the input; argv must not repeat it'
    assert got['bootstrap_sha256'] == scenario.sha256
