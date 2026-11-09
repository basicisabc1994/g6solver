"""Only the scenario shown with an answer can support it, across ordering and restart."""
import json
import ast
import inspect
from pathlib import Path

import pytest

from g6.harness import behaviour_question as bq, question_store as qs
from g6.harness.confirmed_expectation import Answer
from g6.harness.executable_scenario import Scenario, save
from tests.repair_question2.helpers import TASK, Dialog, ask, candidate, repo


@pytest.mark.parametrize('order', ['before_ask', 'after_ask', 'after_answer', 'none'])
def test_sidecar_presence_is_never_answer_custody(tmp_path, order):
    root = repo(tmp_path)
    one = bq.caller_candidates(TASK, [candidate()])[0]
    case = one.question('b' * 64)
    scenario = Scenario.resolve(root, case)
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    if order == 'before_ask':
        save(case, scenario)
    store.ask(case, None, one)
    if order == 'after_ask':
        save(case, scenario)
    answer = Answer(question_sha256=case.sha256, option='A', answered_by='person:Maintainer', answered_at=qs.now())
    store.answer(answer)
    if order == 'after_answer':
        save(case, scenario)
    entries = [json.loads(line) for line in store.path.read_text().splitlines()]
    body = next(e['body'] for e in entries if e['kind'] == qs.ANSWER)
    assert body.get('scenario_sha256') == '', 'sidecar presence cannot say what the form showed'


@pytest.mark.parametrize('choice', ['A', 'B', 'neither'])
def test_product_answer_records_the_exact_program_shown(tmp_path, choice):
    root = repo(tmp_path)
    got = ask(root, dialog=Dialog({'choice': choice, 'name': 'Maintainer'}))
    assert got.get('answer'), got
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    entries = [json.loads(line) for line in store.path.read_text().splitlines()]
    body = next(e['body'] for e in entries if e['kind'] == qs.ANSWER)
    state = store.state()
    from g6.harness.executable_scenario import load
    assert body.get('scenario_sha256') == load(state.asked[0].question).sha256
    assert state.answer_scenarios[state.answers[0].sha256] == body['scenario_sha256']


@pytest.mark.parametrize('action', ['decline', 'cancel'])
def test_declined_or_cancelled_forms_add_no_answer_custody(tmp_path, action):
    root = repo(tmp_path)
    ask(root, dialog=Dialog(action=action))
    state = qs.TaskQuestions(qs.task_identity(root, TASK)).state()
    assert not state.answers
    assert state.answer_scenarios == {}


def test_answer_and_scenario_custody_survive_a_stop_before_the_retained_head(tmp_path, monkeypatch):
    root = repo(tmp_path)
    one = bq.caller_candidates(TASK, [candidate()])[0]
    case = one.question('b' * 64)
    scenario = Scenario.resolve(root, case)
    save(case, scenario)
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    store.ask(case, None, one)
    answer = Answer(question_sha256=case.sha256, option='A', answered_by='person:Maintainer', answered_at=qs.now())
    def stopped(*args):
        raise OSError('stop after answer append')
    with monkeypatch.context() as scoped:
        scoped.setattr(store, '_keep', stopped)
        with pytest.raises(OSError, match='stop after answer append'):
            store.answer(answer, scenario=scenario)
    recovered = qs.TaskQuestions(store.identity).state()
    assert recovered.answer_scenarios == {answer.sha256: scenario.sha256}


def test_inventory_every_product_answer_writer_and_scenario_reader():
    from g6.harness import confirmed_checks
    from g6.mcp import behaviour
    package = Path(qs.__file__).parents[1]
    writers = []
    for folder in ('harness', 'mcp'):
        for path in (package / folder).rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'answer':
                    writers.append(path.relative_to(package).as_posix())
                    assert {k.arg: ast.unparse(k.value) for k in node.keywords}.get('scenario') == 'scenario'
    assert writers == ['mcp/behaviour.py']
    assert 'scenario=scenario' in inspect.getsource(behaviour.confirm)
    source = inspect.getsource(confirmed_checks.run)
    assert 'answer_scenarios.get(one.answer_sha256' in source
    assert 'scenario.sha256 != held_scenario' in source
