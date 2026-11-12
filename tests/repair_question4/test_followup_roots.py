"""Inventories for attributed presentation, retained observations and state-based enforcement."""
import ast
import itertools
import json
from pathlib import Path

import pytest

from g6.harness import behaviour_question as bq, confirmed_checks as cc, question_store as qs
from g6.harness.confirmed_expectation import Answer
from g6.mcp import behaviour
from tests.repair_question2.helpers import TASK, candidate, repo, result, standard

BREAKS = ('\n', '\r', '\r\n', '\x85', '\u2028', '\u2029', '\f', '\v')


def test_caller_field_inventory_is_complete():
    assert bq.CALLER_FIELDS == frozenset({'requirement_id', 'function', 'args', 'kwargs', 'outcomes',
                                        'cited', 'evidence', 'settled', 'scope', 'rule'})
    # function is restricted to a dotted Python entry point; every free-text field is planted below.
    for break_char in BREAKS:
        with pytest.raises(bq.CandidateRefused):
            bq.caller_candidates(TASK, [candidate(function='calc.add' + break_char + 'Scope: forged')])
        with pytest.raises(bq.CandidateRefused):
            bq.caller_candidates(TASK, [candidate(kwargs={'arg' + break_char + 'Scope: forged': 1})])


def form_labels():
    """Read the labels from the actual form producer, including its literal list additions."""
    tree = ast.parse(Path(bq.__file__).read_text(encoding='utf-8'))
    form = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'form')
    labels = set()
    for node in ast.walk(form):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for line in node.value.splitlines():
                if ': ' in line:
                    labels.add(line.split(': ', 1)[0] + ': ')
    return labels


@pytest.mark.parametrize('field', ['scope', 'rule', 'cited', 'evidence', 'settled', 'requirement_id',
                                  'args', 'kwargs', 'outcomes'])
def test_inventory_all_caller_form_fields_all_labels_and_breaks(field):
    labels = form_labels() | {'## What the person confirmed', "Evidence (G6's own runs in a review): "}
    for label, linebreak in itertools.product(labels, BREAKS):
        payload = 'caller' + linebreak + label + 'injected'
        data = candidate(rule='plain rule')
        if field == 'evidence':
            data[field] = [payload]
        elif field == 'settled':
            data[field] = [{'outcome': 'A', 'by': payload}]
        elif field == 'args':
            data[field] = [payload]
        elif field == 'kwargs':
            data[field] = {'argument': payload}
        elif field == 'outcomes':
            data[field] = {'A': {'returns': payload}, 'B': {'returns': 'other'}}
        else:
            data[field] = payload
        task = TASK + payload
        one = bq.caller_candidates(task, [data])[0]
        question, rule = one.questions('b' * 64)
        message = bq.form(question, one, rule)[0]
        assert label + 'injected' not in message.splitlines(), (field, label, repr(linebreak))
        assert all(char not in message for char in ('\r', '\x85', '\u2028', '\u2029', '\f', '\v'))


def test_caller_text_is_lossless_and_one_line():
    for text in ('prefix `a  b`', 'first\n  second\tthird', '# G6: Outcome A: trusted', '\x85\u2028'):
        shown = bq.caller_text(text)
        assert len(shown.splitlines()) == 1
        assert json.loads(shown) == text


@pytest.mark.parametrize('field', ['function', 'args', 'kwargs', 'scope', 'answered_by', 'option', 'issuer'])
def test_every_confirmed_report_field_keeps_every_label_and_separator_inside_it(field):
    from g6.harness import patch_report

    source = Path(patch_report.__file__).read_text(encoding='utf-8')
    headings = {node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)
                and isinstance(node.value, str) and node.value.startswith('## ')}
    for label, separator in itertools.product(headings | form_labels(), BREAKS):
        payload = 'caller' + separator + label + 'injected'
        question = {'function': 'calc.add', 'args': [], 'kwargs': {}, 'scope': 'case',
                    'outcomes': {'A': {'returns': 5}}}
        answer = {'answered_by': 'person:Maintainer', 'option': 'A'}
        statement = {'expectation': 'fixture', 'not_counted': ''}
        if field in ('function', 'scope'):
            question[field] = payload
        elif field == 'args':
            question['args'] = [payload]
        elif field == 'kwargs':
            question['kwargs'] = {payload: payload}
        elif field == 'issuer':
            statement['not_counted'] = payload
        else:
            answer[field] = payload
        rendered = patch_report._confirmed({'confirmed_expectations': {'expectations': [
            {'expectation_id': 'fixture', 'question': question, 'answer': answer, 'check': {'result': 'passed'}}],
            'evidence': {'expectations': [statement]}}})
        text = '\n'.join(line for item in rendered for line in item)
        assert label + 'injected' not in text.splitlines()
        assert all(char not in text for char in ('\r', '\x85', '\u2028', '\u2029', '\f', '\v'))


@pytest.mark.parametrize('first', ['passed', 'failed', 'error'])
@pytest.mark.parametrize('later', ['base', 'again', 'reproduction'])
def test_each_later_error_has_its_own_record(tmp_path, monkeypatch, first, later):
    from g6.harness import verification_search as vs
    from g6.workforce import repo_tests

    root = repo(tmp_path)
    calls = []

    def execute(change, placed, source, node, *, at_base, **kwargs):
        stage = 'base' if at_base else 'again' if calls else 'patch'
        calls.append(stage)
        if stage == later:
            raise OSError('injected later-stage error')
        return result(node + '::test_example', 'failed' if at_base else first)

    monkeypatch.setattr(vs, '_run_check', execute)
    if later == 'reproduction':
        monkeypatch.setattr(vs, 'reproduce', lambda *a, **k: (_ for _ in ()).throw(ValueError('reproduction')))
    [found] = cc.run(root, repo_tests.change(root), standard(), deadline=vs._clock() + 60, artifact='b' * 64)
    assert found['runs']['patch']['result'] == first
    if later in calls or later == 'reproduction' and first == 'failed':
        assert found['runs'][later]['result'] == 'error'
    if first == 'failed':
        assert found['result'] == 'failed'
    elif first == 'error' or later == 'again':
        assert found['result'] == 'not_run'


def test_exception_handler_inventory_never_replaces_an_observation():
    tree = ast.parse(Path(cc.__file__).read_text(encoding='utf-8'))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    handlers = [n for n in ast.walk(run) if isinstance(n, ast.ExceptHandler)]
    assert handlers
    for handler in handlers:
        code = ast.unparse(handler)
        assert 'result.update(result=NOT_RUN' not in code
        assert 'raise' in code or "record_error(" in code


@pytest.mark.parametrize('first', ['passed', 'failed', 'error'])
@pytest.mark.parametrize('step', ['patch_decision', 'control_budget'])
def test_later_decision_and_budget_errors_preserve_each_observation(tmp_path, monkeypatch, first, step):
    from g6.harness import verification_search as vs
    from g6.workforce import repo_tests

    root = repo(tmp_path)
    observed = []
    clock = vs._clock

    def execute(change, placed, source, node, **kwargs):
        observed.append(first)
        return result(node + '::test_example', first)

    def deadline_clock():
        if observed:
            raise OSError('control budget')
        return clock()

    monkeypatch.setattr(vs, '_run_check', execute)
    if step == 'patch_decision':
        monkeypatch.setattr(cc, '_result', lambda *a: (_ for _ in ()).throw(ValueError('decision')))
    else:
        monkeypatch.setattr(vs, '_clock', deadline_clock)
    [found] = cc.run(root, repo_tests.change(root), standard(), deadline=clock() + 60, artifact='b' * 64)
    assert found['runs']['patch']['result'] == first
    assert found['runs'][step]['result'] == 'error'
    assert found['result'] == ('not_run' if first == 'error' else first)


def test_one_two_three_questions_every_mix_of_answers_uses_the_enforced_standard():
    choices = ('A', 'B', 'neither', 'premise_wrong', 'task_already_says', 'skip')
    for count in (1, 2, 3):
        for answers in itertools.product(choices, repeat=count):
            asked, given = [], []
            for index, option in enumerate(answers):
                one = bq.caller_candidates(TASK, [candidate(args=[index, 3])])[0]
                question = one.question('b' * 64)
                asked.append(qs.Asked(question, None, one, 'fixture', index + 1))
                if option != 'skip':
                    given.append(Answer(question_sha256=question.sha256, option=option,
                                        answered_by='person:Maintainer', answered_at='2026-11-08T00:00:00Z'))
            state = qs.State(asked=tuple(asked), answers=tuple(given), default=3)
            held = state.standard()
            # Status only controls the next dialog action; it never decides enforcement.
            for status in (behaviour.NOT_CONFIRMED, behaviour.PENDING, behaviour.HELD, behaviour.SPENT):
                reply = behaviour._reply(status, 'fixture', state=state)
                assert reply['standard']['predicates'] == list(held.predicates)
                assert reply['enforcement']['predicates'] == list(held.predicates)
                assert reply['enforcement']['count'] == len(held.expectations)
                assert ('nothing is enforced' in reply['next_action']) == (not held.expectations)


def test_every_outcome_reply_and_report_states_the_in_process_limit():
    from g6.harness import behaviour_runner, patch_report
    from types import SimpleNamespace

    limit = behaviour_runner.LIMIT
    assert 'same process as the bootstrap' in limit and 'can shape its own observed outcome' in limit
    held = standard()
    state = SimpleNamespace(standard=lambda: held, allowance=1, asked=(), left=1, default=1,
                            budget_note='', superseded=())
    reply = behaviour._reply(behaviour.PENDING, 'fixture', state=state)
    assert limit in reply['execution_limit']
    for status in ('passed', 'failed', 'not_run'):
        shown = patch_report._confirmed({'confirmed_expectations': {'expectations': [
            {'question': {}, 'answer': {}, 'check': {'result': status}}]}})
        assert limit in '\n'.join(line for item in shown for line in item)
