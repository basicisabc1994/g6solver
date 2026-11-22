# SPDX-License-Identifier: Apache-2.0
"""Repair 3: rejection custody without observing arbitrary code."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from g6.harness import behaviour_runner as br
from g6.harness import candidate_standard as cs
from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt

GONE = [{'function': 'calc.add', 'case': {'args': [2, 3], 'kwargs': {}, 'returns': -1}}]


def proposed(source='def test_add():\n    assert False\n'):
    return vs.ProposedCheck(id='C1', requirement_id='R1', intent='preserve',
                            file='test_calc.py', source=source, test='test_add')


def run_report(node, at_base):
    verdict = 'passed' if at_base else 'failed'
    return dict(verdict=verdict, seconds=0.01, said='', rootdir='', output='', collected_ids=[node],
                omitted_ids=[], failed_ids=[] if at_base else [node], setup_errors={},
                failure_messages={} if at_base else {node: 'assert False'},
                test_outcomes={node: dict(setup='passed', call=verdict, teardown='passed')},
                loaded_outside={}, loaded_complete=True)


@pytest.mark.parametrize('kind', ['free_form', 'proposed', 'retained', 'mapped', 'candidate', 'spoofed',
                                  'example.v1', 'differential.v1', 'relation.v1', 'access_rule.v1',
                                  'state_invariant.v1'])
@pytest.mark.parametrize('rejection', [False, True])
def test_each_check_kind_runs_but_only_readable_custody_counts(monkeypatch, kind, rejection):
    check, held = proposed(), []
    origin = replace(vs.FREE_FORM, label=kind)
    if kind.endswith('.v1'):
        bindings = {
            'example.v1': {'function': 'calc.add', 'cases': [{'args': [4, 3], 'returns': 7}]},
            'differential.v1': {'function': 'calc.add', 'mode': 'outputs', 'calls': [{'args': [4, 3]}]},
            'relation.v1': {'function': 'calc.add', 'relation': 'idempotence', 'inputs': [4]},
            'access_rule.v1': {'operation': 'calc.add', 'refusal': 'falsy_return', 'cases': [
                {'who': 'owner', 'resource': 'item', 'args': [4, 3], 'expected': 'allowed'},
                {'who': 'guest', 'resource': 'item', 'args': [1, 3], 'expected': 'refused'}]},
            'state_invariant.v1': {
                'constructor': {'call': 'calc.new'}, 'action': 'act',
                'model': {'variables': {'n': 'Int', 'act': 'Int'}, 'init': '(and (= n 0) (= act 0))',
                          'transition': '(and (= next_act 1) (= next_n (+ n 1)))',
                          'invariants': {'positive': '(>= n 0)'}},
                'actions': {'1': {'call': 'calc.step'}}, 'observe': {'n': {'call': 'calc.value'}}},
        }[kind]
        [instance] = vt.template_instances([dict(id='C1', requirement_id='R1', intent='preserve',
                                               template=kind, bindings=bindings)], ['R1'])
        data = {'function': ['module', 'calc', 'add', 'calc.py']}
        if kind == 'example.v1':
            data['cases'] = [dict(args=[4, 3], kwargs={}, expect='returns', value=7)]
        elif kind == 'differential.v1':
            data.update(mode='outputs', calls=[dict(args=[4, 3], kwargs={})])
        else:
            # These typed relations cannot state which concrete rejected outcome they assert.
            data = bindings
        source = vt.compile_text(vt.REGISTRY[kind], data)
        check = proposed(source)
        held = [(check, instance)]
    elif kind == 'spoofed':
        check = proposed("TEMPLATE = 'example.v1'\nBINDINGS = {'cases': []}\ndef test_add():\n    assert False\n")
    gone = GONE if rejection else []
    kept, skipped, unreadable = cs.screen(gone, [check], lambda ident: origin, held)
    assert kept == [check] and not skipped
    expected_unreadable = rejection and kind not in ('example.v1', 'differential.v1')
    assert unreadable == ({'C1'} if expected_unreadable else set())
    runs = []

    def execute(change, placed, source, node, *, at_base, **kwargs):
        assert 'trace' not in kwargs
        runs.append(at_base)
        return run_report(node, at_base)

    monkeypatch.setattr(vs, '_run_check', execute)
    done = vs.search(SimpleNamespace(repo=Path.cwd(), sources=[], base_commit='b' * 40), kept,
                     {'C1': vs.placement(check)}, deadline=time.monotonic() + 120, artifact='a' * 64,
                     rejected=gone, uncounted=unreadable, origins={'C1': origin})
    assert runs == ([True, False] if kind == 'retained' else [False, True])
    activity = done.activities[0]
    if expected_unreadable:
        assert activity['result'] == vs.ASSERTS_REJECTED
        assert activity['reason'] == 'rejection_unreadable'
        assert not any((done.defects, done.questions, done.outcomes, done.reasons, done.next_actions))
    else:
        assert activity['result'] == vs.COUNTEREXAMPLE
        assert len(done.defects) == len(done.outcomes) == 1


def test_all_tracer_entry_points_are_removed():
    from g6.workforce import repo_tests

    assert not any(hasattr(repo_tests, name) for name in ('CallTrace', '_TRACER', '_BOOT_TRACED', 'read_trace'))


def test_custodial_source_requires_readable_bindings():
    check = proposed().model_copy(update={'source': br.ExecutableSource('def test_add():\n    pass\n', {})})
    kept, skipped, unreadable = cs.screen(GONE, [check], lambda ident: vs.FREE_FORM)
    assert kept == [check] and not skipped and unreadable == {'C1'}


def test_unreadable_compiler_binding_still_runs():
    data = {'cases': []}
    source = vt.compile_text(vt.REGISTRY['example.v1'], data)
    check = proposed(source).model_copy(update={'source': br.ExecutableSource(source, data)})
    kept, skipped, unreadable = cs.screen(GONE, [check], lambda ident: vs.FREE_FORM)
    assert kept == [check] and not skipped and unreadable == {'C1'}
    kept, skipped = cs.gate({'rejected': GONE}, [check], 'proposed check', 'caller')
    assert kept == [check] and not skipped


def test_search_itself_does_not_grant_free_form_checks_authority(monkeypatch):
    check = proposed()
    monkeypatch.setattr(vs, '_run_check', lambda change, placed, source, node, *, at_base, **kwargs:
                        run_report(node, at_base))
    done = vs.search(SimpleNamespace(repo=Path.cwd(), sources=[], base_commit='b' * 40), [check],
                     {'C1': vs.placement(check)}, deadline=time.monotonic() + 120, artifact='a' * 64,
                     rejected=GONE)
    assert done.activities[0]['result'] == vs.ASSERTS_REJECTED
    assert not any((done.defects, done.questions, done.outcomes, done.reasons))


def test_compiler_observation_must_agree_with_readable_literal():
    safe = {'function': ['module', 'calc', 'add', 'calc.py'],
            'cases': [dict(args=[4, 3], kwargs={}, expect='returns', value=7)]}
    wrong = {**safe, 'cases': [dict(args=[2, 3], kwargs={}, expect='returns', value=-1)]}
    text = vt.compile_text(vt.REGISTRY['example.v1'], safe)
    check = proposed(text).model_copy(update={'source': br.ExecutableSource(text, wrong)})
    kept, skipped, unreadable = cs.screen(GONE, [check], lambda ident: vs.FREE_FORM)
    assert kept == [check] and not skipped and unreadable == {'C1'}
    kept, skipped = cs.gate({'rejected': GONE}, [check], 'proposed check', 'caller')
    assert kept == [check] and not skipped


@pytest.mark.parametrize('intent', ['preserve', 'change'])
def test_unreadable_check_errors_are_reported_without_authority(monkeypatch, intent):
    check = proposed().model_copy(update={'intent': intent})

    def execute(*args, **kwargs):
        raise ValueError('synthetic execution error')

    monkeypatch.setattr(vs, '_run_check', execute)
    done = vs.search(SimpleNamespace(repo=Path.cwd(), sources=[], base_commit='b' * 40), [check],
                     {'C1': vs.placement(check)}, deadline=time.monotonic() + 120, artifact='a' * 64,
                     rejected=GONE)
    assert done.activities[0]['reason'] == cs.UNREADABLE
    assert not any((done.defects, done.questions, done.outcomes, done.reasons))


@pytest.mark.parametrize('source', [
    'from calc import add\ndef test_add():\n    assert add(2, 3) == -1\n',
    'from calc import saved\ndef test_add():\n    assert saved == -1\n',
    'import calc, operator\ndef test_add():\n    original = calc.add\n'
    '    try:\n        calc.add = operator.sub\n        assert calc.add(2, 3) == -1\n'
    '    finally:\n        calc.add = original\n',
    'import ctypes\ndef test_add():\n    assert ctypes.CDLL("ucrtbase")._wsystem("exit 0") == 0\n',
    'import calc, other\ndef test_add():\n    assert other.add(2, 3) == -1\n    assert calc.add(2, 3) == 5\n',
], ids=['decorated_export', 'import_time_alias', 'temporary_c_binding', 'native_process', 'same_name_function'])
def test_review3_routes_run_without_counting(monkeypatch, source):
    """Exercise scheduling and authority; the ordinary executor is a recorded stand-in, with no tracer."""
    check = proposed(source)
    runs = []

    def execute(change, placed, source_text, node, *, at_base, **kwargs):
        assert source_text == source and 'trace' not in kwargs
        runs.append(at_base)
        return run_report(node, at_base)

    monkeypatch.setattr(vs, '_run_check', execute)
    done = vs.search(SimpleNamespace(repo=Path.cwd(), sources=[], base_commit='b' * 40), [check],
                     {'C1': vs.placement(check)}, deadline=time.monotonic() + 120, artifact='a' * 64,
                     rejected=GONE)
    assert runs == [False, True]
    assert (done.activities[0]['result'], done.activities[0]['reason']) == (vs.ASSERTS_REJECTED, cs.UNREADABLE)
    assert not any((done.defects, done.questions, done.outcomes, done.reasons, done.next_actions))
