"""Repair 2 acceptance boundaries; the lead runs these in the shared lane."""
import json
import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as vs, verification_templates as vt
from g6.workforce import repo_tests
from tests.harness.test_template_tmfu2_r1 import ABSENT, GOOD, _probe, _tree


def _observe(folder, source, *, cases=None, template='example.v1', budget_s=10, files=None):
    tree = _tree(folder, source, known=tuple(files or {}))
    for name, text in (files or {}).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    cases = cases or [{'args': [1], 'returns': 1}]
    definition = vt.REGISTRY[template]
    binding = {'function': 'subject.run'}
    binding.update({'cases': cases} if template == 'example.v1' else
                   {'calls': [{'args': case['args']} for case in cases], 'mode': 'outputs'})
    data = runner.located(folder, definition.applies(tree, definition.bindings.model_validate(binding), vt.PATCH).data)
    boot, channel = folder.parent / (folder.name + '-boot.py'), folder.parent / (folder.name + '-frame')
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    request = runner.request(data) if budget_s == 10 else runner.request(data, budget_s=budget_s)
    channel.write_text(json.dumps(request), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(folder), str(channel), '@channel'],
                         capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    return runner.finish(ran.returncode, channel.read_bytes(), data), data, tree


@pytest.mark.parametrize('duplicate', [False, True], ids=['distinct', 'duplicate'])
@pytest.mark.parametrize('moving', [False, True], ids=['same-values', 'other-call-changes'])
def test_repeat_uses_occurrence_even_with_identical_arguments(duplicate, moving):
    first, patch, again = _probe([0, 0], [[], []]), _probe([0, 1], [[], []]), _probe(
        [int(moving), 0], [[], [ABSENT]])
    if duplicate:
        for side in (first, patch, again):
            candidate = side.get('import_evidence', {}).get('reading', side)
            candidate['observations'][1]['input'] = {'args': [0]}
    verdict = vs.classify('preserve', patch, first)
    assert verdict['result'] == vs.BEHAVIOUR_CHANGED
    assert vs._stable(verdict, first, again)['result'] == vs.CHECK_ERROR


@pytest.mark.parametrize('caught', [False, True], ids=['uncaught', 'caught'])
def test_earlier_defect_survives_later_unavailable_call(tmp_path, caught):
    cases = [{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}]
    body = (f'        try:\n            import {ABSENT}\n'
            '        except ImportError:\n            return 1\n' if caught else
            f'        import {ABSENT}\n')
    base, _, _ = _observe(tmp_path / 'base', GOOD, cases=cases)
    patch, _, _ = _observe(tmp_path / 'patch', 'def run(value):\n    if value:\n' + body +
                           '    return 99\n', cases=cases)
    verdict = vs.classify('preserve', patch, base)
    assert verdict['result'] == vs.COUNTEREXAMPLE
    assert '[0]' in verdict['detail']


@pytest.mark.parametrize('shape', ['independent', 'cached-fallback', 'fresh-import', 'no-earlier-mark'])
def test_later_mismatch_requires_fresh_evidence(tmp_path, shape):
    cases = [{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}]
    prefix = (f'        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            available = False\n')
    tail = {'independent': '    return 99\n',
            'cached-fallback': '    return value if available else 99\n',
            'fresh-import': f'    if available:\n        import {ABSENT}\n    return 99\n',
            'no-earlier-mark': '    return 99\n'}[shape]
    source = ('available = True\ndef run(value):\n    global available\n    if value == 0:\n' +
              ('        pass\n' if shape == 'no-earlier-mark' else prefix) +
              ('        return 99\n' if shape == 'independent' else '        return 0\n') + tail)
    base, _, _ = _observe(tmp_path / 'base', GOOD, cases=cases)
    patch, _, _ = _observe(tmp_path / 'patch', source, cases=cases)
    verdict = vs.classify('preserve', patch, base)
    assert verdict['result'] == (vs.COUNTEREXAMPLE if shape in ('independent', 'no-earlier-mark') else vs.CHECK_ERROR)
    if shape == 'no-earlier-mark':
        assert not patch.get('case_reruns')
    else:
        assert patch['import_evidence']['outcomes'] == [[ABSENT], []]
        [rerun] = patch['case_reruns']
        assert rerun['case'] == 1 and rerun['ran'] is True
        assert rerun['result'] == ('reproduced' if shape == 'independent' else 'unavailable'
                                   if shape == 'fresh-import' else 'not_reproduced')
        assert rerun['marks'] == ([ABSENT] if shape == 'fresh-import' else [])
        assert rerun['outcome']
    if shape == 'independent':
        assert '[1]' in verdict['detail']


def test_rerun_without_remaining_budget_cannot_confirm(tmp_path):
    cases = [{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}]
    source = (f'def run(value):\n    if value == 0:\n        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            return 0\n    return 99\n')
    base, _, _ = _observe(tmp_path / 'base', GOOD, cases=cases)
    patch, _, _ = _observe(tmp_path / 'patch', source, cases=cases, budget_s=0)
    assert vs.classify('preserve', patch, base)['result'] == vs.CHECK_ERROR
    [rerun] = patch['case_reruns']
    assert rerun == {'case': 1, 'ran': False, 'result': 'unavailable', 'outcome': None, 'marks': []}


@pytest.mark.parametrize('wrapped', [False, True], ids=['library-finalizer', 'repository-wrapper'])
def test_finalizer_keeps_its_import_mark(tmp_path, wrapped):
    source = ('import weakref, importlib\nclass Item:\n    pass\n'
              f'def load():\n    return importlib.import_module("{ABSENT}")\n'
              'def run(value):\n    item = Item()\n' +
              ('    weakref.finalize(item, load)\n' if wrapped else
               f'    weakref.finalize(item, importlib.import_module, "{ABSENT}")\n') + '    return value\n')
    found, _, _ = _observe(tmp_path / 'tree', source)
    assert found['import_evidence']['outcomes'] == [[ABSENT]]
    assert found['fallback_observation']['missing_imports'] == [ABSENT]


@pytest.mark.parametrize('fallback', [False, True], ids=['ordinary', 'fallback'])
def test_converted_finding_keeps_qualification(tmp_path, monkeypatch, fallback):
    guard = f'try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n' if fallback else ''
    base, _, _ = _observe(tmp_path / 'base', guard + GOOD, template='differential.v1')
    patch, data, tree = _observe(tmp_path / 'patch', guard + GOOD.replace('return value', 'return value + 1'),
                                template='differential.v1')
    instance = vt.TemplateInstance(id='one', template='differential.v1', requirement_id='R1', intent='preserve',
                                   bindings={'function': 'subject.run', 'mode': 'outputs', 'calls': [{'args': [1]}]})
    check, placed, origin = vt.compiled_check(tree.root, instance, data, [])

    def wire(*args, at_base, **kwargs):
        found = base if at_base else patch
        return {'g6_observation': found, 'verdict': repo_tests.OBSERVED_VERDICTS[found['result']], 'said': found['detail'],
                'source_before': '0' * 64, 'seconds': 0.1}

    monkeypatch.setattr(vs, '_run_check', wire)
    done = vs.search(SimpleNamespace(base_commit='a' * 40), [check], {'one': placed},
                     deadline=vs._clock() + 60, artifact='b' * 64, origins={'one': origin})
    [activity], [defect] = done.activities, done.defects
    report = vs.completion([], [], [], [], [], done, artifact='b' * 64, base_commit='a' * 40)
    assert activity['result'] == vs.COUNTEREXAMPLE
    if fallback:
        for found in (activity, defect, defect['reproduction'],
                      report['defects_reproduced']['proposed_checks'][0]):
            assert found['fallback_observation']['missing_imports'] == [ABSENT]
        assert 'fallback was taken' in activity['detail']
        assert 'fallback was taken' in defect['detail']
    else:
        assert 'fallback_observation' not in activity


def test_repeat_mark_drops_only_its_own_occurrence():
    first = _probe([0, 0, 0], [[], [], []])
    patch = _probe([1, 1, 1], [[], [], []])
    again = _probe([0, 0, 0], [[], [ABSENT], []])
    stable = vs._stable(vs.classify('preserve', patch, first), first, again)
    assert stable['result'] == vs.BEHAVIOUR_CHANGED
    assert [one['case'] for one in stable['differences']] == [0, 2]


@pytest.mark.parametrize('marked_side', ['base', 'patch'])
@pytest.mark.parametrize('cached', [False, True], ids=['independent', 'cached'])
def test_producing_probe_requires_fresh_independence(tmp_path, marked_side, cached):
    cases = [{'args': [0]}, {'args': [1]}]
    source = ('available = True\ndef run(value):\n    global available\n    if value == 0:\n'
              f'        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            available = False\n        return 0\n' +
              ('    return value if available else 99\n' if cached else '    return 99\n'))
    marked, _, _ = _observe(tmp_path / 'marked', source, template='differential.v1', cases=cases)
    clean, _, _ = _observe(tmp_path / 'clean', GOOD, template='differential.v1', cases=cases)
    base, patch = (marked, clean) if marked_side == 'base' else (clean, marked)
    verdict = vs.classify('preserve', patch, base)
    assert verdict['result'] == (vs.CHECK_ERROR if cached else vs.BEHAVIOUR_CHANGED)
    if not cached:
        assert [one['case'] for one in verdict['differences']] == [1]


@pytest.mark.parametrize('repeat', [None, 'unstable', 'unavailable'])
def test_stability_exits_keep_fallback_qualification(repeat):
    first, patch = _probe([0], [[ABSENT]]), _probe([1], [[ABSENT]])
    again = None if repeat is None else _probe([2], [[ABSENT]] if repeat == 'unstable' else [[]])
    verdict = vs._stable(vs.classify('preserve', patch, first), first, again)
    assert verdict['result'] == (vs.NOT_REFUTED if repeat == 'unstable' else vs.CHECK_ERROR)
    assert verdict['fallback_observation']['missing_imports'] == [ABSENT]
    assert 'fallback was taken' in verdict['detail']


def test_occurrences_and_reruns_survive_the_report_reader():
    found = _probe([0, 0, 0], [[], [ABSENT], []])
    found['case_reruns'] = [{'case': 2, 'ran': False, 'result': 'unavailable', 'outcome': None, 'marks': []}]
    wire = {'g6_observation': found, 'verdict': repo_tests.OBSERVED_VERDICTS[found['result']], 'said': found['detail'],
            'source_before': '0' * 64, 'seconds': 0.1}
    assert repo_tests.read_observation(wire) is not None
    reading = vs.reading(wire, 'test_probe.py')
    assert reading['case_reruns'] == found['case_reruns']
    for mutate in ('boolean-case', 'negative-case', 'duplicate-case', 'bad-rerun', 'extra-field',
                   'unstarted-claim', 'unreadable-rerun', 'duplicate-rerun', 'malformed-evidence',
                   'malformed-observed-rerun'):
        bad = deepcopy(wire)
        payload = bad['g6_observation']
        pairs = payload['import_evidence']['reading']['observations']
        if mutate == 'boolean-case':
            pairs[0]['case'] = False
        elif mutate == 'negative-case':
            pairs[0]['case'] = -1
        elif mutate == 'duplicate-case':
            pairs[1]['case'] = 0
        elif mutate == 'extra-field':
            pairs[0]['unknown'] = 0
        elif mutate == 'unstarted-claim':
            payload['case_reruns'][0]['result'] = 'reproduced'
        elif mutate == 'unreadable-rerun':
            payload['case_reruns'][0].update(ran=True, outcome={'version': object()})
        elif mutate == 'duplicate-rerun':
            payload['case_reruns'].append(deepcopy(payload['case_reruns'][0]))
        elif mutate == 'malformed-evidence':
            payload['import_evidence']['dependent'] = [False]
        elif mutate == 'malformed-observed-rerun':
            payload['observed'] = {'reruns': [{}]}
        else:
            payload['case_reruns'][0]['result'] = 'confirmed'
        assert repo_tests.read_observation(bad) is None, mutate


def test_pending_rerun_does_not_erase_an_earlier_defect():
    data = {'cases': [{'args': [index], 'expect': 'returns', 'value': index} for index in range(3)]}
    outcomes = [{'returned': json.dumps(value), 'arguments': [json.dumps(index)]}
                for index, value in enumerate([99, 1, 99])]
    outcomes[1].update(missing_import=runner.ObservationUnavailable.DETAIL, missing_imports=[ABSENT])
    pending = {'version': 'observation.v2', 'outcomes': outcomes, 'errors': {},
               'reruns': [{'case': 2, 'ran': True, 'observation': None}]}
    patch = runner.finish(1, runner.frame(pending), data)
    base = {'result': 'passed', 'verdict': 'passed', 'detail': 'all confirmed outcomes matched'}
    verdict = vs.classify('preserve', patch, base)
    assert verdict['result'] == vs.COUNTEREXAMPLE
    assert '[0]' in verdict['detail']
    assert patch['case_reruns'][0]['result'] == 'unavailable'


def test_legacy_probe_lines_keep_their_report_format():
    # Free-form printed observations are not runner calls and supply no call occurrence.
    before = [{'input': [2], 'output': 1}]
    after = [{'input': [2, 3], 'output': 1}]
    assert vs._differences(before, after) == [
        {'input': '[2]', 'base': '1', 'patch': vs.NOT_OBSERVED},
        {'input': '[2, 3]', 'base': vs.NOT_OBSERVED, 'patch': '1'}]


def test_forward_independence_is_required_for_a_mismatch_only():
    base = _probe([0, 1], [[ABSENT], []])
    patch = _probe([0, 1], [[ABSENT], []])
    verdict = vs.classify('preserve', patch, base)
    assert verdict['result'] == vs.NOT_REFUTED
    assert verdict['fallback_observation']['missing_imports'] == [ABSENT]


def test_report_assembly_keeps_existing_qualifications():
    qualification = {'missing_imports': [ABSENT], 'satisfied_import_path': 'not observed'}
    defect = {'code': 'check_failed', 'node': 'test_check.py', 'detail': 'the fallback was taken',
              'reproduction': {'command': 'read the observation'}, 'fallback_observation': qualification}
    report = vs.completion([], [], [], [], [defect], None, artifact='a' * 64, base_commit='b' * 40)
    [found] = report['defects_reproduced']['stated_and_repository_checks']
    assert found['fallback_observation'] == qualification
    assert found['reproduction']['fallback_observation'] == qualification


def test_confirmed_reproduction_keeps_an_earlier_fallback(tmp_path, monkeypatch):
    from g6.harness import confirmed_checks as checks

    tree = _tree(tmp_path / 'tree')
    instance = vt.TemplateInstance(id='one', template='example.v1', requirement_id='R1', intent='preserve',
                                   bindings={'function': 'subject.run', 'cases': [{'args': [1], 'returns': 1}]})
    data = vt.REGISTRY['example.v1'].applies(tree, vt.ExampleBindings.model_validate(instance.bindings), vt.PATCH).data
    check, placed, _ = vt.compiled_check(tree.root, instance, data, [])
    expectation = SimpleNamespace(expectation_id='one', instance=instance, question_sha256='a' * 64,
                                  answer_sha256='b' * 64, option='A', answered_by='person:Maintainer',
                                  requirement_id='R1', scope='one input', provenance='person_confirmed',
                                  check_sha256='c' * 64)
    monkeypatch.setattr(vt, 'prepare', lambda *args, **kwargs: SimpleNamespace(
        checks=[check], placed={'one': placed}, activities=[]))
    monkeypatch.setattr(checks, 'latest_confirmed', lambda *args: [(expectation, False)])
    qualification = {'missing_imports': [ABSENT], 'satisfied_import_path': 'not observed'}
    readings = iter([{'result': 'passed', 'detail': 'matched', 'fallback_observation': qualification},
                     {'result': 'failed', 'detail': 'base mismatch'},
                     {'result': 'failed', 'detail': 'replayed mismatch'}])

    def read(*args, **kwargs):
        found = next(readings)
        return {'g6_observation': found, 'verdict': found['result'], 'said': found['detail'],
                'source_before': '0' * 64, 'seconds': 0.1}

    monkeypatch.setattr(vs, '_run_check', read)
    [found] = checks.run(tree.root, SimpleNamespace(base_commit='d' * 40),
                         SimpleNamespace(expectations=[expectation]), deadline=vs._clock() + 60, artifact='e' * 64)
    assert found['result'] == checks.FAILED
    assert found['reproduction']['fallback_observation'] == qualification
    assert 'fallback was taken' in found['reproduction']['detail']


def test_fresh_load_resets_namespace_module_state(tmp_path):
    cases = [{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}]
    source = ('import held\ndef run(value):\n    if value == 0:\n'
              f'        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            held.available = False\n        return 0\n'
              '    return value if getattr(held, "available", True) else 99\n')
    base, _, _ = _observe(tmp_path / 'base', GOOD, cases=cases)
    patch, _, _ = _observe(tmp_path / 'patch', source, cases=cases, files={'held/kept.py': ''})
    assert vs.classify('preserve', patch, base)['result'] == vs.CHECK_ERROR
    assert patch['case_reruns'][0]['result'] == 'not_reproduced'
