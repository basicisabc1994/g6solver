"""Missing, foreign, stale and malformed sidecars never support a different answered form."""
import json
import subprocess

import pytest

from g6.core import confine
from g6.core.ledger import canonical
from g6.core.storage import state_path
from g6.harness import executable_scenario as es, patch_review, question_store as qs
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import REQUIREMENTS, TASK, ask, repo, stated


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'foreign', 'stale'])
def test_answer_requires_its_recorded_scenario_digest(tmp_path, monkeypatch, damage):
    root = repo(tmp_path)
    assert ask(root)['status'] == 'confirmed'
    state = qs.TaskQuestions(qs.task_identity(root, TASK)).state()
    question = state.asked[0].question
    path = state_path('scenarios', question.sha256 + '.json')
    if damage == 'missing':
        path.unlink()
    elif damage == 'malformed':
        path.write_bytes(b'{')
    else:
        record = json.loads(path.read_bytes())
        if damage == 'foreign':
            record['question_sha256'] = '0' * 64
        else:
            value = json.loads(record['scenario'])
            value['entrypoint'][3] = 'src/calc.py'
            scenario = es.Scenario(canonical(value).encode('ascii'))
            record.update(scenario=scenario.record.decode('ascii'), scenario_sha256=scenario.sha256)
        path.write_text(canonical(record), encoding='ascii')
    stated(monkeypatch)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('test-boundary', '', spec))
    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=20)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode
    monkeypatch.setattr(rt, '_bounded', execute)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    [statement] = reply['assessment']['confirmed_expectations']['evidence']['expectations']
    assert statement['checked'] is False, statement
    assert 'G6_E_SCENARIO_UNAVAILABLE' in statement.get('not_counted', ''), statement
