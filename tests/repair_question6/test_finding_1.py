"""A valid exception type's optional metadata must not destroy its observation."""
import subprocess
import time

import pytest

from g6.core import confine
from g6.harness import behaviour_question as bq, behaviour_runner as runner
from g6.harness.executable_scenario import Scenario
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, candidate, repo


@pytest.mark.parametrize('option, expected', [('A', 'passed'), ('B', 'failed')])
def test_exception_with_none_module_is_still_an_observable_exception(tmp_path, monkeypatch, option, expected):
    root = repo(tmp_path)
    (root / 'calc.py').write_text(
        'class DomainError(Exception):\n'
        '    __module__ = None\n'
        'def add(a, b):\n'
        '    raise DomainError()\n', encoding='utf-8')
    item = bq.caller_candidates(TASK, [candidate(outcomes={
        'A': {'raises': 'calc.DomainError'}, 'B': {'returns': 5}})])[0]
    question = item.question('b' * 64)
    scenario = Scenario.resolve(root, question)
    message, _ = bq.form(question, item, scenario=scenario)
    assert 'raises calc.DomainError' in message
    logs = []

    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=20)
        logs.append(child.stderr.decode('utf-8', errors='replace'))
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('test-boundary', '', spec))
    monkeypatch.setattr(rt, '_bounded', execute)
    got = runner.run(root, {**scenario.comparison(option), '_program': scenario.program},
                     'test_example.py', timeout_s=20, deadline=time.monotonic() + 30)
    assert got.get('g6_observation', {}).get('result') == expected, (
        'the offered exception was raised, but its diagnostic erased the observation', got, logs)
