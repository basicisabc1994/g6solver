# SPDX-License-Identifier: Apache-2.0
"""Plain-request acceptance; FakeLLM and synthetic executor observations only."""
from __future__ import annotations

import json

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM
from g6.harness import verification_templates as vt
from g6.mcp import mission
from tests.harness.test_proposed_checks import _repo

#: Every case below is written in the task as Python; no word of the task is read for meaning.
TASK = 'Fix add(a, b) so that add(2, 3) == 5 and add(-2, -3) == -5 without changing the existing API'
NODE = 'tests/test_calc.py::test_add'
SOURCES = {'calc.py': 'def add(a, b):\n    return a + b\n',
    'tests/test_calc.py': 'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n'}


def stating(got, args):
    """The candidate whose one case is the call with ``args``."""
    return next(c for c in got['standard'] if c['kind'] == 'example' and [k['args'] for k in c['cases']] == [args])


def test_memory_candidate_templates_have_no_normative_provenance():
    from g6.harness import candidate_standard as cs
    with semantic.request(llm=None):
        got = cs.from_sources(SOURCES, TASK, repo='fixture')
    negative = stating(got, [-2, -3])
    vt.template_instances(negative['cheapest_check']['arguments']['template_instances'],
                          [c['id'] for c in got['standard']])
    _, instances, _, _ = cs.checks(got, SOURCES, before=SOURCES)
    assert instances and all(one.provenance == 'model_hypothesis' for one in instances)


@pytest.mark.parametrize('odd', [b'odd\xff', b'odd\nname'])
def test_memory_git_names_are_shown_or_refused_without_becoming_text(odd):
    from g6.harness import candidate_standard as cs
    from g6.workforce import repo_tests

    names = repo_tests._paths(b'calc.py\0tests/test_' + odd + b'.py\0' + odd + b'.py\0')
    texts = dict(zip(names, [SOURCES['calc.py'], SOURCES['tests/test_calc.py'], SOURCES['calc.py']], strict=True))
    escaped = 'odd\\377' if odd == b'odd\xff' else 'odd\\nname'
    test_file = '"tests/test_' + escaped + '.py"'
    with semantic.request(llm=None):
        got = cs.from_sources(texts, TASK, repo='fixture')
    assert got['standard'] and all(c['function'] == 'calc.add' for c in got['standard'])
    assert got['test_files'] == [test_file]
    positive = stating(got, [2, 3])
    assert positive['checks'] == [test_file + '::test_add']
    assert got['question']['candidate']['evidence'] == ['calc.py: defines calc.add; the task states add(2, 3) == 5']
    checks, _, _, _ = cs.checks(got, texts, before=texts)
    signature, = [c for c in checks if c.id.endswith('.signature')]
    namespace = {}
    exec(signature.source, namespace)
    assert namespace['BINDINGS'][0] == 'calc.py'


def test_memory_model_receives_repository_symbol_context():
    from g6.harness import candidate_standard as cs
    def answer(system, user):
        assert 'calc.add' in user
        return '{"value": []}'
    with semantic.request(llm=FakeLLM(answer)):
        got = cs.from_sources(SOURCES, TASK, repo='fixture')
    assert got['mechanisms'][0]['by'] == 'model'


def test_memory_inventory_guards_links_before_opening(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from g6.harness import candidate_standard as cs
    from g6.workforce import repo_tests
    monkeypatch.setattr(repo_tests, '_git', lambda *a: b'calc.py\0')
    monkeypatch.setattr(repo_tests, '_linked', lambda path: True)
    path = SimpleNamespace(is_file=lambda: True, stat=lambda: SimpleNamespace(st_size=12),
                           read_bytes=lambda: pytest.fail('opened a linked path'))
    monkeypatch.setattr(repo_tests, '_disk', lambda *a: path)
    assert cs.sources(Path('fixture')) == {}


def test_memory_original_signature_comes_from_git_base(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from g6.harness import candidate_standard as cs
    from g6.workforce import repo_tests
    root = Path('fixture')
    change = SimpleNamespace(base_commit='a' * 40, before={})
    monkeypatch.setattr(repo_tests, '_listed_at', lambda *a: {'calc.py': ('100644', 'blob', 'b' * 40, '32')})
    monkeypatch.setattr(repo_tests, 'blob_bytes', lambda *a: SOURCES['calc.py'].encode())
    original = cs.baseline(root, change, {'calc.py': 'def add(a, b, c=0):\n    return a+b+c\n'})
    assert original['calc.py'] == SOURCES['calc.py']


def test_memory_invalid_thin_checks_refused_before_repository_access(monkeypatch):
    from g6.harness import patch_review
    from g6.workforce import repo_tests
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda *a: pytest.fail('repository accessed'))
    got = patch_review.review_patch('fixture', task=TASK, template_instances='invalid')
    assert got['completion_state'] == 'blocked-escalated'


def test_memory_question_confirms_exactly_its_own_boundary_case():
    from g6.harness import behaviour_question as bq, candidate_standard as cs
    from g6.harness.confirmed_expectation import ConfirmedExpectation, ExpectationStandard
    with semantic.request(llm=None):
        got = cs.from_sources(SOURCES, TASK, repo='fixture')
    question = got['question']['candidate']
    item = next(c for c in got['standard'] if c['id'] == question['requirement_id'])
    assert item['kind'] == 'boundary'
    assert item['cases'][0]['args'] == question['args']
    one, = bq.caller_candidates(TASK, [question])
    case, rule = one.questions('b' * 64)
    read = bq.read('accept', {'choice': 'B', 'name': 'Fixture'}, case, rule,
                   answered_at='2026-11-19T00:00:00Z')
    held = ExpectationStandard().with_expectation(ConfirmedExpectation(case, read.answer))
    shown = cs.with_confirmed(got, held)
    stated = [c for c in shown['standard'] if c['status'] == 'stated']
    assert len(stated) == 1 and stated[0]['id'] == item['id']
    assert stated[0]['cases'] == [{'args': [2.0, 3], 'raises': 'TypeError'}]
    # the call it offers is the confirmed case too, never the alternative the person rejected
    offered, = stated[0]['cheapest_check']['arguments']['template_instances']
    assert offered['bindings']['cases'] == [{'args': [2.0, 3], 'raises': 'TypeError'}]
    assert all(c['status'] == 'candidate' for c in shown['standard'] if c['id'] != item['id'])
    assert all(c['status'] == 'candidate' for c in got['standard'])


def test_memory_rebound_alias_is_not_concrete_coverage():
    from g6.harness import candidate_standard as cs
    texts = {**SOURCES, 'tests/test_calc.py': 'from calc import add\n\ndef test_fake():\n'
             '    add = lambda a, b: -5\n    assert add(-2, -3) == -5\n'}
    with semantic.request(llm=None):
        got = cs.from_sources(texts, TASK, repo='fixture')
    negative = stating(got, [-2, -3])
    assert negative['checks'] == 'missing'


def test_memory_float_literals_do_not_cover_integer_case():
    from g6.harness import candidate_standard as cs
    texts = {**SOURCES, 'tests/test_calc.py': 'from calc import add\n\ndef test_float():\n'
             '    assert add(-2.0, -3.0) == -5.0\n'}
    with semantic.request(llm=None):
        got = cs.from_sources(texts, TASK, repo='fixture')
    negative = stating(got, [-2, -3])
    assert negative['checks'] == 'missing'


@pytest.mark.parametrize('task', [
    'Add validation to multiply(a, b) for positive integers',
    'Make multiply(a, b) add up the sum of two integers, positive or negative',
    'Keep multiply unchanged; it adds nothing and sums nothing',
])
def test_memory_no_word_of_the_task_is_read_for_meaning(task):
    """Owner direction 2026-11-17: without a stated example or a model proposal, no behaviour is invented."""
    from g6.harness import candidate_standard as cs
    texts = {'calc.py': 'def multiply(a, b):\n    return a * b\n'}
    with semantic.request(llm=None):
        got = cs.from_sources(texts, task, repo='fixture')
    assert got['standard'] and all(c['cases'] == [] for c in got['standard'])
    assert got['question'] is None
    assert all(c['kind'] == 'preservation' for c in got['standard'] if 'signature' in c['text'])
    assert [c['kind'] for c in got['standard']].count('preservation') == ('multiply(a, b)' in task)


@pytest.mark.parametrize('task, case', [
    ('multiply(2, 3) == 6 must hold', {'args': [2, 3], 'returns': 6}),
    ('Ensure multiply(2, b=3) == 6.', {'args': [2], 'kwargs': {'b': 3}, 'returns': 6}),
    ('Then multiply(-1.5, 2) == -3.0; done', {'args': [-1.5, 2], 'returns': -3.0}),
    ("Want multiply('ab', 2) == 'abab' too", {'args': ['ab', 2], 'returns': 'abab'}),
])
def test_memory_a_stated_example_is_parsed_as_python(task, case):
    from g6.harness import candidate_standard as cs
    texts = {'calc.py': 'def multiply(a, b):\n    return a * b\n'}
    with semantic.request(llm=None):
        got = cs.from_sources(texts, task, repo='fixture')
    examples = [c for c in got['standard'] if c['kind'] == 'example' and c['cases']]
    assert [c['cases'] for c in examples] == [[case]]
    assert examples[0]['origin'] == 'task_text'


def test_memory_two_stated_signatures_propose_no_preservation():
    from g6.harness import candidate_standard as cs
    with semantic.request(llm=None):
        got = cs.from_sources(SOURCES, 'Change add(a, b) into add(a, b, c)', repo='fixture')
    assert not any(c['kind'] == 'preservation' for c in got['standard'])


def test_memory_signature_check_needs_the_base_to_have_the_stated_parameters():
    from g6.harness import candidate_standard as cs
    with semantic.request(llm=None):
        got = cs.from_sources(SOURCES, 'Fix add(x, y)', repo='fixture')
    keep, = [c for c in got['standard'] if c['kind'] == 'preservation']
    checks, _, _, skipped = cs.checks(got, SOURCES, before=SOURCES)
    assert not any(c.id.endswith('.signature') for c in checks)
    assert any(s['candidate_id'] == keep['id'] and 'stated signature' in s['reason'] for s in skipped)


def test_memory_the_question_comes_from_a_repository_example_when_the_task_states_none():
    """The e2e task names add(a, b) and states no example: the repository's own assert is the explicit example."""
    from g6.harness import behaviour_question as bq, candidate_standard as cs
    task = 'Change add(a, b) in calc.py so it adds two integers and returns their sum as an int.'
    with semantic.request(llm=None):
        got = cs.from_sources(SOURCES, task, repo='fixture')
    candidate = got['question']['candidate']
    assert candidate['args'] == [2.0, 3]
    assert candidate['outcomes'] == {'A': {'returns': 5}, 'B': {'raises': 'TypeError'}}
    assert candidate['evidence'] == [f'{NODE}: asserts add(2, 3) == 5']
    item = next(c for c in got['standard'] if c['id'] == candidate['requirement_id'])
    assert item['origin'] == 'repository_test' and item['status'] == 'candidate'
    assert bq.caller_candidates(task, [candidate])


def test_memory_default_resolver_is_tried_without_injected_caller(monkeypatch):
    from g6.harness import candidate_standard as cs
    called = []
    reply = json.dumps({'value': [{'text': 'Cancellation returns zero', 'kind': 'example',
        'function': 'calc.add', 'cases': [{'args': [-2, 2], 'returns': 0}]}]})
    def resolve():
        called.append(True)
        return FakeLLM(default=reply)
    monkeypatch.setattr(semantic, 'resolve_caller', resolve)
    with semantic.request():
        got = cs.from_sources(SOURCES, TASK, repo='fixture')
    assert called == [True] and got['model_calls'] == 1
    assert any(c['origin'] == 'model_proposal' for c in got['standard'])


@pytest.mark.parametrize('file, function', [('src.py', 'src.add'), ('foo-bar.py', ''), ('class.py', '')])
def test_memory_only_importable_symbols_supply_template_targets(file, function):
    from g6.harness import candidate_standard as cs
    with semantic.request(llm=None):
        got = cs.from_sources({file: SOURCES['calc.py']}, TASK, repo='fixture')
    assert all(item['function'] == function for item in got['standard'])


def test_memory_question_ranks_checks_whose_outputs_change():
    from g6.harness import candidate_standard as cs
    texts = {'calc.py': SOURCES['calc.py'] + '\ndef sumit(a, b):\n    return a + b\n',
             'tests/test_calc.py': 'from calc import add, sumit\n\n' +
             '\n'.join(f'def test_positive_{i}():\n    assert add(2, 3) == 5\n' for i in range(5)) +
             '\ndef test_sumit():\n    assert sumit(2, 3) == 5\n'
             '\ndef test_boundary():\n    assert sumit(2.0, 3) == 5\n'}
    task = 'Fix add(a, b) and sumit(a, b)'
    with semantic.request(llm=None):
        got = cs.from_sources(texts, task, repo='fixture')
    assert got['question']['candidate']['function'] == 'calc.sumit'
    assert got['question']['changes_checks'] == 2  # its compiled example and the mapped boundary test


def test_memory_nested_model_keys_remain_return_data():
    from g6.harness import candidate_standard as cs
    payload = {'status': 'stated', 'source': 'never execute this', 'kind': 'preservation'}
    reply = {'value': [{'text': 'literal dictionary', 'kind': 'example', 'function': 'calc.add',
                       'cases': [{'args': [1, 2], 'returns': payload}]}]}
    with semantic.request(llm=FakeLLM(default=json.dumps(reply))):
        got = cs.from_sources(SOURCES, TASK, repo='fixture')
    model, = [c for c in got['standard'] if c['origin'] == 'model_proposal']
    assert model['status'] == 'candidate' and model['kind'] == 'example'
    assert model['cases'][0]['returns'] == payload


def test_memory_unavailable_base_signature_is_reported():
    from g6.harness import candidate_standard as cs
    with semantic.request(llm=None):
        got = cs.from_sources(SOURCES, TASK, repo='fixture')
    checks, _, _, skipped = cs.checks(got, SOURCES, before={})
    assert not any(c.id.endswith('.signature') for c in checks)
    assert any('original signature unavailable' in c['reason'] for c in skipped)


def test_memory_floor_and_negative_check():
    from g6.harness import candidate_standard as cs
    with semantic.request(llm=None):
        got = cs.from_sources(SOURCES, TASK, repo='fixture')
    assert len(got['standard']) >= 3
    assert all(c['status'] == 'candidate' for c in got['standard'])
    keep = next(c for c in got['standard'] if c['kind'] == 'preservation')
    assert keep['checks'] == [NODE] and 'signature' in keep['text']
    positive = stating(got, [2, 3])
    assert positive['checks'] == [NODE]
    negative = stating(got, [-2, -3])
    assert negative['checks'] == 'missing'
    vt.template_instances(negative['cheapest_check']['arguments']['template_instances'],
                          [c['id'] for c in got['standard']])
    from g6.harness import behaviour_question as bq
    assert bq.caller_candidates(TASK, [got['question']['candidate']])


@pytest.mark.parametrize('reply, origin', [
    (json.dumps({'value': [{'text': 'Cancellation returns zero', 'kind': 'example',
        'function': 'calc.add', 'cases': [{'args': [-2, 2], 'returns': 0}]}]}), 'model_proposal'),
    ('{"value": [], "status": "stated"}', 'task_text'),
])
def test_memory_model_contract_and_floor(reply, origin):
    from g6.harness import candidate_standard as cs
    with semantic.request(llm=FakeLLM(default=reply)):
        got = cs.from_sources(SOURCES, TASK, repo='fixture')
    assert any(c['origin'] == origin for c in got['standard'])
    assert any(c['kind'] == 'preservation' for c in got['standard'])
    assert all(c['status'] == 'candidate' for c in got['standard'])
    assert got['model_calls'] == 1
    if origin == 'task_text':
        assert any('model reading failed' in m.get('reason', '') for m in got['mechanisms'])


@pytest.mark.parametrize('model', [False, True])
def test_memory_review_omission_uses_only_proposals(monkeypatch, model):
    from pathlib import Path
    from types import SimpleNamespace
    from g6.harness import candidate_standard as cs, outcome_history, patch_review
    from g6.workforce import repo_tests
    root = Path('fixture')
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda repo: root)
    monkeypatch.setattr(repo_tests, 'snapshot', lambda repo: {})
    monkeypatch.setattr(repo_tests, 'change', lambda repo, base: SimpleNamespace(repo=root))
    monkeypatch.setattr(cs, 'sources', lambda repo: SOURCES)
    monkeypatch.setattr(cs, 'baseline', lambda *a: SOURCES)
    monkeypatch.setattr(outcome_history, 'retained_checks', lambda *a: ([], {}, []))
    monkeypatch.setattr(patch_review, '_import_roots', lambda *a: ())
    monkeypatch.setattr('g6.harness.verification_search.placements',
                        lambda root, checks: {c.id: c.file for c in checks})
    seen = {}

    def assess(root, change, stated, ids, *args, **kwargs):
        seen.update(kwargs)
        assert stated == [] and ids == []
        assert kwargs['proposed'] and kwargs['instances']
        assert all(one.provenance == 'model_hypothesis' for one in kwargs['instances'])
        assert all(o.about['origin'] == 'task_text' for o in kwargs['candidate_origins'].values())
        return {'assessment': {'requirements': [], 'outcome': 'insufficient_evidence',
            'completion': {'activities': [], 'demonstrated': []}, 'report': 'existing report'}}

    monkeypatch.setattr(patch_review, '_assess', assess)
    with semantic.request(llm=FakeLLM(default='{"value": []}') if model else None):
        got = patch_review.review_patch('fixture', task=TASK, claim='all cases work')
    assert got['assessment']['standard']
    lines = got['assessment']['report'].splitlines()
    assert lines[0].startswith('G6 ran') and lines[1] == 'The agent asserted: all cases work'
    assert lines[2].startswith('Still candidate:')
    assert all(c['status'] == 'candidate' for c in got['assessment']['standard'])
    checks = seen['proposed']
    assert any('signature' in c.source for c in checks)
    assert any('test_add' in c.source for c in checks)
    assert seen['candidate_calls'] == int(model)


@pytest.mark.parametrize('broken', [True, False])
def test_task_only_review_reproduces_negative_failure_without_requirement_authority(addition, monkeypatch, broken):
    import sys
    import types
    from g6.harness import patch_review, verification_search as vs
    from tests.harness.test_proposed_checks import result
    code = 'def add(a, b):\n    return a + b if a > 0 else a - b\n' if broken else SOURCES['calc.py'] + '\n'
    (addition / 'calc.py').write_text(code, encoding='utf-8')
    base = addition.parent / 'base-copy'   # the base's files, so a base run never reads the patched calc.py
    base.mkdir()
    (base / 'calc.py').write_text(SOURCES['calc.py'], encoding='utf-8')

    def execute(change, placed, source, node, *, at_base, **kwargs):
        module = types.ModuleType('calc')
        exec(compile(SOURCES['calc.py'] if at_base else code, 'calc.py', 'exec'), module.__dict__)
        with monkeypatch.context() as local:
            local.setitem(sys.modules, 'calc', module)
            # The example check resolves its entry point against the working directory: the base copy or the patch.
            local.chdir(base if at_base else addition)
            # Signature check reads source files; its real executor is covered by repository suites.
            if 'ast.parse' in source:
                return result([node])
            namespace = {}
            exec(compile(source, placed, 'exec'), namespace)
            try:
                for name, check in namespace.items():
                    if name.startswith('test_'):
                        check()
            except (AssertionError, pytest.fail.Exception):
                return result(failed=[node], messages={node: 'AssertionError: add(-2, -3) differs from -5'})
        return result([node])

    monkeypatch.setattr(vs, '_run_check', execute)
    monkeypatch.setattr(patch_review, '_execute', lambda root, ids, **kw: result(ids))
    monkeypatch.setattr(patch_review, '_execute_at_base', lambda change, ids, **kw: result(ids))
    with semantic.request(llm=None):
        got = patch_review.review_patch(addition, task=TASK)
    assessment = got['assessment']
    assert assessment['outcome'] == 'insufficient_evidence'
    assert assessment['requirements'] == [] and assessment['completion']['demonstrated'] == []
    defects = assessment['completion']['defects_reproduced']['proposed_checks']
    assert bool(defects) == broken
    if broken:
        assert '-5' in defects[0]['reproduction']['text']
        assert defects[0]['origin'] == 'task_text'


@pytest.fixture
def addition(tmp_path):
    return _repo(tmp_path, {'calc.py': 'def add(a, b):\n    return a + b\n',
        'tests/test_calc.py': 'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n'})


def standard(root, llm=None):
    with semantic.request(llm=llm):
        return mission.premortem(str(root), TASK)


def test_task_floor_maps_preservation_but_does_not_invent_negative_coverage(addition):
    got = standard(addition)
    assert got['standard'], got
    keep = next(c for c in got['standard'] if c['kind'] == 'preservation')
    negative = stating(got, [-2, -3])
    assert keep['status'] == 'candidate' and keep['origin'] == 'task_text'
    assert 'signature' in keep['text'] and NODE in keep['checks']
    assert negative['checks'] == 'missing'
    assert any(c['id'] == negative['id'] for c in got['unknowns'])
    call = negative['cheapest_check']
    assert call['tool'] == 'g6_review_patch'
    instances = vt.template_instances(call['arguments']['template_instances'], [c['id'] for c in got['standard']])
    assert instances[0].template == 'example.v1'
    assert got['question'] and len(got['questions']) == 1
    assert got['question']['candidate']['outcomes']['A'] != got['question']['candidate']['outcomes']['B']


def test_model_reading_uses_shared_reader_and_retains_floor(addition):
    llm = FakeLLM(default=json.dumps({'value': [{'text': 'add returns zero when signs cancel', 'kind': 'example',
        'function': 'calc.add', 'cases': [{'args': [-2, 2], 'returns': 0}]}]}))
    got = standard(addition, llm)
    assert any(c['origin'] == 'model_proposal' for c in got['standard'])
    assert any(c['kind'] == 'preservation' and c['origin'] == 'task_text' for c in got['standard'])
    assert all(c['status'] == 'candidate' for c in got['standard'])
    assert got['model_calls'] == 1
    assert any(m['kind'] == 'candidate_standard' and m['by'] == 'model' for m in got['mechanisms'])


@pytest.mark.parametrize('reply', ['not json', '{"value": [], "status": "stated"}',
    '{"value": [{"text": "escape", "kind": "example", "function": "calc.add", "source": "boom"}]}'])
def test_malformed_model_keeps_task_items_and_explains_failure(addition, reply):
    got = standard(addition, FakeLLM(default=reply))
    assert got['standard'] and all(c['origin'] == 'task_text' for c in got['standard'])
    assert any('model reading failed' in m.get('reason', '') for m in got['mechanisms'])


def test_question_is_accepted_by_existing_behaviour_door(addition):
    from g6.harness import behaviour_question as bq
    got = standard(addition)
    picked = bq.caller_candidates(TASK, [got['question']['candidate']])
    assert len(picked) == 1
    assert picked[0].to_dict()['requirement_id'] in {c['id'] for c in got['standard']}


def test_alias_mapping_and_concrete_input_coverage(addition):
    (addition / 'tests/test_calc.py').write_text('from calc import add as plus\n\n'
        'def test_add():\n    assert plus(2, 3) == 5\n\n'
        'def test_negative():\n    assert plus(-2, -3) == -5\n', encoding='utf-8')
    got = standard(addition)
    negative = stating(got, [-2, -3])
    assert negative['checks'] == ['tests/test_calc.py::test_negative']


def test_confirmation_door_states_exactly_one_case(addition, monkeypatch):
    from tests.harness.test_question_store_bound import ask
    monkeypatch.setenv('G6_HOME', str(addition.parent / 'confirmation-home'))
    got = standard(addition)
    case = got['question']['candidate']
    confirmed = ask(addition, task=TASK, data=[case])
    assert confirmed['status'] == 'confirmed', confirmed
    after = standard(addition)
    stated = [c for c in after['standard'] if c['status'] == 'stated']
    assert len(stated) == 1 and stated[0]['id'] == case['requirement_id']
    assert all(c['status'] == 'candidate' for c in after['standard'] if c['id'] != case['requirement_id'])


def test_negative_cheapest_call_is_accepted_by_the_review_door(addition, monkeypatch):
    from g6.harness import patch_review
    got = standard(addition)
    negative = stating(got, [-2, -3])
    def assess(*args, **kwargs):
        assert all(one.provenance == 'model_hypothesis' for one in kwargs['instances'])
        return {'assessment': {'completion': {'activities': []}, 'report': 'fixture'}}
    monkeypatch.setattr(patch_review, '_assess', assess)
    with semantic.request(llm=None):
        reviewed = patch_review.review_patch(**negative['cheapest_check']['arguments'])
    assert reviewed['assessment']['standard']


def _thin_review(monkeypatch):
    """A thin review whose repository and execution boundary are stubbed; what reached the assessment is kept."""
    from pathlib import Path
    from types import SimpleNamespace
    from g6.harness import candidate_standard as cs, outcome_history, patch_review, verification_search as vs
    from g6.workforce import repo_tests
    root = Path('fixture')
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda repo: root)
    monkeypatch.setattr(repo_tests, 'snapshot', lambda repo: {})
    monkeypatch.setattr(repo_tests, 'change', lambda repo, base: SimpleNamespace(repo=root))
    monkeypatch.setattr(cs, 'sources', lambda repo: SOURCES)
    monkeypatch.setattr(cs, 'baseline', lambda *a: SOURCES)
    monkeypatch.setattr(patch_review, '_import_roots', lambda *a: ())
    monkeypatch.setattr(outcome_history, 'retained_checks', lambda *a: ([], {}, []))
    monkeypatch.setattr(vs, 'placements', lambda root, checks: {c.id: c.file for c in checks})
    monkeypatch.setattr(vs, 'bind_execution', lambda change, before, **kwargs: before)
    seen = {}

    def assess(*args, **kwargs):
        seen.update(kwargs)
        return {'ok': True, 'assessment': {'requirements': [], 'completion': {'activities': []}, 'report': 'stub'}}

    monkeypatch.setattr(patch_review, '_assess', assess)
    return seen


def test_model_candidates_advertised_call_needs_no_second_identical_model_reply(monkeypatch):
    from g6.harness import candidate_standard as cs, patch_review
    seen = _thin_review(monkeypatch)
    proposal = {'text': 'Opposite inputs cancel', 'kind': 'example', 'function': 'calc.add',
                'cases': [{'args': [-2, 2], 'returns': 0}]}
    with semantic.request(llm=FakeLLM(default=json.dumps({'value': [proposal]}))):
        found = cs.from_sources(SOURCES, TASK, repo='fixture')
    model, = [c for c in found['standard'] if c['origin'] == 'model_proposal']
    for later in ('{"value": []}', 'not json', json.dumps({'value': [{**proposal, 'text': 'Signs cancel out'}]})):
        seen.clear()
        with semantic.request(llm=FakeLLM(default=later)):
            reply = patch_review.review_patch(**model['cheapest_check']['arguments'])
        assert seen, reply.get('warning_card')
        sent = [one for one in seen['instances'] if one.requirement_id == model['id']]
        assert [one.bindings['cases'] for one in sent] == [[{'args': [-2, 2], 'returns': 0}]]
        shown, = [c for c in reply['assessment']['standard'] if c['id'] == model['id']]
        assert shown['origin'] == 'model_proposal' and shown['status'] == 'candidate'


@pytest.mark.parametrize('change', [
    {'cases': [{'args': [-2, 2], 'returns': 1}]},          # another expectation under the advertised id
    {'function': 'calc.missing'},                          # a function the repository does not define
    {'template': 'relation.v1'},
])
def test_an_altered_advertised_call_names_no_candidate(monkeypatch, change):
    from g6.harness import candidate_standard as cs, patch_review
    seen = _thin_review(monkeypatch)
    proposal = {'text': 'Opposite inputs cancel', 'kind': 'example', 'function': 'calc.add',
                'cases': [{'args': [-2, 2], 'returns': 0}]}
    with semantic.request(llm=FakeLLM(default=json.dumps({'value': [proposal]}))):
        found = cs.from_sources(SOURCES, TASK, repo='fixture')
    model, = [c for c in found['standard'] if c['origin'] == 'model_proposal']
    arguments = json.loads(json.dumps(model['cheapest_check']['arguments']))
    one = arguments['template_instances'][0]
    one.update({k: v for k, v in change.items() if k == 'template'})
    one['bindings'].update({k: v for k, v in change.items() if k != 'template'})
    with semantic.request(llm=FakeLLM(default='{"value": []}')):
        reply = patch_review.review_patch(**arguments)
    assert not seen and reply['completion_state'] == 'blocked-escalated'


def test_confirmed_alternative_is_the_check_the_review_schedules(monkeypatch):
    from g6.harness import behaviour_question as bq, candidate_standard as cs, patch_review
    from g6.harness.confirmed_expectation import ConfirmedExpectation, ExpectationStandard
    seen = _thin_review(monkeypatch)
    with semantic.request(llm=None):
        found = cs.from_sources(SOURCES, TASK, repo='fixture')
    candidate = found['question']['candidate']
    one, = bq.caller_candidates(TASK, [candidate])
    question, rule = one.questions('b' * 64)
    reading = bq.read('accept', {'choice': 'B', 'name': 'Fixture'}, question, rule, answered_at='2026-11-19T00:00:00Z')
    held = ExpectationStandard().with_expectation(ConfirmedExpectation(question, reading.answer))
    with semantic.request(llm=None):
        reply = patch_review.review_patch('fixture', task=TASK, confirmed=held)
    shown = next(c for c in reply['assessment']['standard'] if c['id'] == candidate['requirement_id'])
    assert shown['status'] == 'stated' and shown['cases'] == [{'args': [2.0, 3], 'raises': 'TypeError'}]
    sent = [case for instance in seen['instances'] if instance.requirement_id == candidate['requirement_id']
            for case in instance.bindings['cases']]
    assert sent == [{'args': [2.0, 3], 'raises': 'TypeError'}]


@pytest.mark.parametrize('stop', ['ExecutionInterrupted', 'ContainmentFailure'])
def test_premortem_lets_a_trusted_stop_from_the_reader_reach_the_door(addition, stop):
    from g6.core import component_process
    error = getattr(component_process, stop)('stopped while the reader asked')

    def fail(system, user):
        raise error

    with pytest.raises(type(error)):
        standard(addition, FakeLLM(fail))

