"""The enforcing compiler decides admission before any durable question or dialog."""
import dataclasses
import json

import pytest

from g6.harness import behaviour_question as bq, executable_scenario as es, question_store as qs
from g6.harness import verification_search as vs, verification_templates as vt
from tests.repair_question2.helpers import TASK, Dialog, ask, candidate, repo


KINDS = ('returns', 'raises', 'effect')


def case_repo(parent, kind='returns'):
    root = repo(parent)
    code = {'returns': 'def add(value):\n    return 5\n',
            'raises': 'def add(value):\n    raise ValueError()\n',
            'effect': "def add(value):\n    value[:] = ['done']\n"}[kind]
    (root / 'calc.py').write_text(code, encoding='utf-8')
    return root


def item(kind, text):
    outcomes = {'returns': {'A': {'returns': 5}, 'B': {'returns': -1}},
                'raises': {'A': {'raises': 'ValueError'}, 'B': {'returns': 0}},
                'effect': {'A': {'effect': {'argument': 0, 'equals': ['done']}},
                           'B': {'effect': {'argument': 0, 'equals': []}}}}[kind]
    return bq.caller_candidates(TASK, [candidate(args=[[text]] if kind == 'effect' else [text],
                                               outcomes=outcomes)])[0]


def raw_question(kind, text):
    from g6.harness.confirmed_expectation import Question
    record = item(kind, '').question('b' * 64).to_dict()
    record.pop('options')
    record['args'] = [[text]] if kind == 'effect' else [text]
    return Question(**record)


def historical_compile(root, question, tree):
    from g6.harness.confirmed_expectation import confirm
    instance = confirm(question, 'A', answered_by='person:Maintainer').instance
    definition = vt.TEMPLATES['example.v1']
    applied = definition.applies(tree, vt.ExampleBindings.model_validate(instance.bindings), vt.PATCH)
    check, _, _ = vt.compiled_check(root, instance, applied.data, [], definition=definition)
    return instance, check


def largest_historical(root, kind):
    tree = vt.Tree.read(root, vt.repo_tests.change(root))
    low, high = 0, vs.MAX_SOURCE_BYTES + 1
    while low + 1 < high:
        middle = (low + high) // 2
        try:
            historical_compile(root, raw_question(kind, 'x' * middle), tree)
        except ValueError:
            high = middle
        else:
            low = middle
    question = raw_question(kind, 'x' * low)
    instance, check = historical_compile(root, question, tree)
    return question, instance, check, tree, low


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_same_compiler_byte_boundary_before_dialog(tmp_path, monkeypatch, kind, offset):
    root = case_repo(tmp_path, kind)
    one = item(kind, '\u96ea\U0001f642')
    case = one.question(vs.digest(vt.repo_tests.snapshot(root)))
    scenario = es.Scenario.resolve(root, case)
    sizes = [len(scenario.source(option).encode('utf-8')) for option in case.outcomes]
    # Move the compiler's bound around the exact source byte count, not an independent form limit.
    monkeypatch.setattr(vs, 'MAX_SOURCE_BYTES', max(sizes) - offset)
    dialog = Dialog()
    got = ask(root, dialog=dialog, data=[{k: v for k, v in one.to_dict().items() if k in bq.CALLER_FIELDS}])
    state = qs.TaskQuestions(qs.task_identity(root, TASK)).state()
    if offset > 0:
        assert not state.asked and not dialog.messages, got
        assert es.UNAVAILABLE in str(got)
    else:
        assert got.get('status') == 'confirmed', got
        for option in case.outcomes:
            vt.compiled_check(root, state.standard().expectations[0].instance,
                              {'scenario': scenario.record.decode('ascii'), 'option': option}, [])


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('text', ['x' * 12000, '\u96ea\U0001f642' * 1500])
def test_historical_admissible_cases_remain_admissible(tmp_path, kind, text):
    root = case_repo(tmp_path, kind)
    case = item(kind, text).question('b' * 64)
    scenario = es.Scenario.resolve(root, case)
    from g6.harness.confirmed_expectation import confirm
    instance = confirm(case, 'A', answered_by='person:Maintainer').instance
    tree = vt.Tree.read(root, vt.repo_tests.change(root))
    applied = vt.TEMPLATES['example.v1'].applies(tree, vt.ExampleBindings.model_validate(instance.bindings), vt.PATCH)
    vt.compiled_check(root, instance, applied.data, [], definition=vt.TEMPLATES['example.v1'])
    vt.compiled_check(root, instance, {'scenario': scenario.record.decode('ascii'), 'option': 'A'}, [])


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('input_bytes', [32767, 32768, 32769, 65535, 65536, 65537])
def test_real_input_byte_boundaries_are_enforceable_or_refused_before_asking(tmp_path, kind, input_bytes):
    root = case_repo(tmp_path, kind)
    data = {k: v for k, v in item(kind, '').to_dict().items() if k in bq.CALLER_FIELDS}
    data['args'] = [['x' * input_bytes]] if kind == 'effect' else ['x' * input_bytes]
    dialog = Dialog()
    got = ask(root, dialog=dialog, data=[data])
    state = qs.TaskQuestions(qs.task_identity(root, TASK)).state()
    if got.get('status') != 'confirmed':
        assert any(code in str(got) for code in (es.UNAVAILABLE, bq.INVALID))
        assert not state.asked and state.left == 1 and not dialog.messages
        return
    assert 'x' * input_bytes in dialog.messages[0]
    [enforced] = state.standard().expectations
    scenario = es.load(enforced.question)
    for option in enforced.question.outcomes:
        check, _, _ = vt.compiled_check(root, enforced.instance,
            {'scenario': scenario.record.decode('ascii'), 'option': option}, [])
        assert len(check.source.encode('utf-8')) <= vs.MAX_SOURCE_BYTES


@pytest.mark.parametrize('kind', KINDS)
def test_largest_historical_compiler_case(tmp_path, kind):
    root = case_repo(tmp_path, kind)
    case, instance, check, tree, length = largest_historical(root, kind)
    assert len(check.source.encode('utf-8')) <= vs.MAX_SOURCE_BYTES
    with pytest.raises(ValueError):
        historical_compile(root, raw_question(kind, 'x' * (length + 1)), tree)
    scenario = es.Scenario.resolve(root, case)
    vt.compiled_check(root, instance, {'scenario': scenario.record.decode('ascii'), 'option': 'A'}, [])


def test_inventory_outcome_kinds_and_every_option(tmp_path, monkeypatch):
    from g6.harness.confirmed_expectation import OUTCOME_FIELDS
    assert set(KINDS) == set(OUTCOME_FIELDS)
    root = repo(tmp_path)
    seen = []
    real = vt.compiled_check
    def compile_check(root, one, data, notes, **kwargs):
        seen.append(data.get('option'))
        if data.get('option') == 'B':
            raise vt.Inapplicable('planted second-option compiler refusal')
        return real(root, one, data, notes, **kwargs)
    monkeypatch.setattr(vt, 'compiled_check', compile_check)
    dialog = Dialog()
    got = ask(root, dialog=dialog)
    assert seen == ['A', 'B']
    assert not dialog.messages and not qs.TaskQuestions(qs.task_identity(root, TASK)).state().asked, got


def test_input_is_held_once_in_reproduction_and_program(tmp_path):
    root = case_repo(tmp_path)
    text = 'INPUT_CUSTODY_SENTINEL_654321'
    case = item('returns', text).question('b' * 64)
    scenario = es.Scenario.resolve(root, case)
    assert scenario.program.count(text) == 1
    assert scenario.source('A').count(text) <= 1
    assert json.loads(scenario.record)['input']['args'] == [text]


def test_compiler_checks_bytes_even_for_multibyte_generated_source(tmp_path, monkeypatch):
    root = case_repo(tmp_path)
    from g6.harness.confirmed_expectation import confirm
    instance = confirm(item('returns', '').question('b' * 64), 'A', answered_by='person:Maintainer').instance
    definition = dataclasses.replace(vt.TEMPLATES['example.v1'], text='\u96ea' * (vs.MAX_SOURCE_BYTES // 2))
    tree = vt.Tree.read(root, vt.repo_tests.change(root))
    applied = vt.TEMPLATES['example.v1'].applies(tree, vt.ExampleBindings.model_validate(instance.bindings), vt.PATCH)
    with pytest.raises(vt.Inapplicable):
        vt.compiled_check(root, instance, applied.data, [], definition=definition)
