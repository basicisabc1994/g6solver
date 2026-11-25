"""Host comparisons retain import provenance without erasing earlier observations.

Written before the repair. The builder's no-lane rule leaves execution to the lead.
"""
import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as search, verification_templates as vt
from g6.harness.executable_scenario import SETUP, Scenario
from g6.core.ledger import canonical
from tests.harness.test_template_runtime_binding import _compiled


ABSENT = 'tmfu4_marks_absent_package'
GOOD = 'def run(value):\n    return value\n'
GUARD = f'try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n'


def _data(folder, template='example.v1', *, cases=None, known=()):
    folder.mkdir(parents=True)
    (folder / 'subject.py').write_text(GOOD, encoding='utf-8')
    tree = vt.Tree(folder, SimpleNamespace(files=()), frozenset({'subject.py', *known}), frozenset())
    definition = vt.REGISTRY[template]
    bindings = {'function': 'subject.run'}
    if template == 'example.v1':
        bindings['cases'] = cases or [{'args': [1], 'returns': 1}]
    else:
        bindings.update(mode='outputs', calls=cases or [{'args': [1]}])
    return definition.applies(tree, definition.bindings.model_validate(bindings), vt.PATCH).data


def _observe(folder, source, *, template='example.v1', cases=None, channel_request=False, known=()):
    data = _data(folder, template, cases=cases, known=known)
    (folder / 'subject.py').write_text(source, encoding='utf-8')
    data = runner.located(folder, data)
    boot, channel = folder.parent / (folder.name + '-boot.py'), folder.parent / (folder.name + '-frame')
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    payload = json.dumps(runner.request(data))
    channel.write_bytes(payload.encode('utf-8') if channel_request else b'')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(folder), str(channel),
                          '@channel' if channel_request else payload], capture_output=True, timeout=20)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    verdict = {'passed': 'passed', 'failed': 'failed', 'error': 'not_run'}[found['result']]
    read = search.reading({'verdict': verdict, 'said': found['detail'], 'g6_observation': found,
                           'source_before': '0' * 64, 'seconds': 0.1}, 'test_g6_example.py::test_example')
    return found, read


@pytest.mark.parametrize('template,wanted', [('example.v1', search.COUNTEREXAMPLE),
                                          ('differential.v1', search.BEHAVIOUR_CHANGED)])
def test_both_sides_alike_guards_leave_the_change_comparable(tmp_path, template, wanted):
    _, base = _observe(tmp_path / 'base', GUARD + GOOD, template=template)
    _, patch = _observe(tmp_path / 'patch', GUARD + GOOD.replace('return value', 'return value + 1'),
                        template=template)
    assert search.classify('preserve', patch, base)['result'] == wanted


def test_one_side_only_guard_stays_unavailable(tmp_path):
    _, base = _observe(tmp_path / 'base', GOOD)
    _, patch = _observe(tmp_path / 'patch', GUARD + GOOD.replace('return value', 'return 0'))
    assert search.classify('preserve', patch, base)['result'] == search.CHECK_ERROR


def test_different_missing_imports_are_not_the_same_evidence(tmp_path):
    _, base = _observe(tmp_path / 'base', GUARD + GOOD)
    _, patch = _observe(tmp_path / 'patch', GUARD.replace(ABSENT, ABSENT + '_other') +
                        GOOD.replace('return value', 'return 0'))
    assert search.classify('preserve', patch, base)['result'] == search.CHECK_ERROR


def test_mark_starts_at_the_outcome_that_tried_the_import(tmp_path):
    source = (f'def run(value):\n    if value:\n        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            pass\n    return 0\n')
    found, _ = _observe(tmp_path / 'tree', source,
                        cases=[{'args': [0], 'returns': 1}, {'args': [1], 'returns': 0}])
    assert found['result'] == 'failed'
    assert 'missing_import' not in found['observed']['outcomes'][0]
    assert found['observed']['outcomes'][1]['missing_imports'] == [ABSENT]


def test_probe_keeps_a_comparable_call_before_a_later_missing_import(tmp_path):
    calls = [{'args': [0]}, {'args': [1]}]
    source = (f'def run(value):\n    if value:\n        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            return value\n    return value + 1\n')
    _, base = _observe(tmp_path / 'base', GOOD, template='differential.v1', cases=calls)
    _, patch = _observe(tmp_path / 'patch', source, template='differential.v1', cases=calls)
    finding = search.classify('preserve', patch, base)
    assert finding['result'] == search.BEHAVIOUR_CHANGED
    assert len(finding['differences']) == 1
    assert finding['unavailable_probe_inputs'] == [1]


def test_chained_cause_inside_handler_keeps_the_confirmed_raise(tmp_path):
    source = (f'def run(value):\n    try:\n        import {ABSENT}\n'
              '    except ImportError as exc:\n        raise ValueError() from exc\n')
    found, _ = _observe(tmp_path / 'tree', source, cases=[{'args': [1], 'raises': 'ValueError'}])
    assert found['result'] == 'passed'
    assert found['fallback_observation']['missing_imports'] == [ABSENT]


def test_matching_patch_fallback_is_named_in_reading_and_finding(tmp_path):
    _, base = _observe(tmp_path / 'base', GOOD)
    found, patch = _observe(tmp_path / 'patch', GUARD + GOOD)
    verdict = search.classify('preserve', patch, base)
    assert found['result'] == 'passed' and verdict['result'] == search.NOT_REFUTED
    assert patch['fallback_observation']['missing_imports'] == [ABSENT]
    assert verdict['fallback_observation']['satisfied_import_path'] == 'not observed'
    assert 'fallback was taken' in verdict['detail']


def test_nearest_tree_frame_attributes_a_helper_import(tmp_path):
    source = (f'import pkgutil\ndef run(value):\n    try:\n        pkgutil.resolve_name("{ABSENT}")\n'
              '    except ImportError:\n        return 0\n')
    found, _ = _observe(tmp_path / 'tree', source)
    assert found['result'] == 'error'
    assert found['import_evidence']['outcomes'] == [[ABSENT]]


def test_call_failure_carries_preparation_import_mark(tmp_path):
    source = GUARD + f'if "{ABSENT}" in globals():\n    ' + GOOD.replace('\n', '\n    ')
    found, _ = _observe(tmp_path / 'tree', source)
    assert found['result'] == 'error'
    assert found['import_evidence']['outcomes'] == [[ABSENT]]


@pytest.mark.parametrize('template', ['relation.v1', 'access_rule.v1', 'differential.v1'])
@pytest.mark.parametrize('value', ['segment at 0x10', b'segment at 0x10'])
def test_address_content_survives_displays(template, value):
    namespace, _ = _compiled(template)
    assert ' at 0x10' in namespace['_shown'](value)
    assert namespace['_shown'](object()) == '<object object>'


def test_bytes_are_compared_without_display_truncation(tmp_path):
    source = "def run(value):\n    return b'x' * 600 + b'%s'\n"
    _, base = _observe(tmp_path / 'base', source % ' at 0x10', template='differential.v1')
    _, patch = _observe(tmp_path / 'patch', source % ' at 0x20', template='differential.v1')
    assert search.classify('preserve', patch, base)['result'] == search.BEHAVIOUR_CHANGED


def test_runner_reads_request_list_from_its_channel(tmp_path):
    found, _ = _observe(tmp_path / 'tree', GOOD, channel_request=True)
    assert found['result'] == 'passed'
    found, _ = _observe(tmp_path / 'deleted', 'def run(value):\n    import helpers\n    return value\n',
                        channel_request=True, known=['helpers.py'])
    assert found['result'] == 'failed'


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
def test_unrelated_modules_do_not_expand_compiled_binding_or_argv(tmp_path, template):
    folder = tmp_path / 'tree'
    data = _data(folder, template, known=[f'module_number_{index:04d}.py' for index in range(2000)])
    instance = vt.TemplateInstance(id='one', template=template, requirement_id='R1', intent='preserve',
                                   bindings={'function': 'subject.run'})
    vt.compiled_check(folder, instance, data, [])
    assert len(json.dumps(runner.request(data))) < 1000


def test_resolved_scenario_carries_the_host_file_list(tmp_path, monkeypatch):
    folder = tmp_path / 'tree'
    data = _data(folder, known=['helpers.py'])
    tree = vt.Tree(folder, SimpleNamespace(files=('helpers.py',)), frozenset({'subject.py'}),
                   frozenset({'subject.py', 'helpers.py'}))
    monkeypatch.setattr(vt.Tree, 'read', classmethod(lambda cls, *args: tree))
    monkeypatch.setattr('g6.workforce.repo_tests.change', lambda root: tree.change)
    question = SimpleNamespace(function='subject.run', args=[1], kwargs={},
                               outcomes={'A': {'returns': 1}, 'B': {'returns': 2}})
    scenario = Scenario.resolve(folder, question)
    comparison = scenario.comparison('A', tree=tree)
    assert comparison['own_modules'] == data['own_modules']
    assert 'own_modules' in scenario.program
    (folder / 'subject.py').write_text('def run(value):\n    import helpers\n    return value\n', encoding='utf-8')
    boot, channel = tmp_path / 'scenario.py', tmp_path / 'scenario-frame'
    boot.write_text(scenario.program, encoding='utf-8')
    channel.write_text(json.dumps(runner.request(comparison)), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(folder), str(channel), '@channel'],
                          capture_output=True, timeout=20)
    assert runner.finish(ran.returncode, channel.read_bytes(), comparison)['result'] == 'failed'


def test_confirmed_check_compares_the_same_guard_on_both_trees(tmp_path, monkeypatch):
    from g6.harness import confirmed_checks as checks

    patch, _ = _observe(tmp_path / 'patch', GUARD + GOOD.replace('return value', 'return 0'))
    base, _ = _observe(tmp_path / 'base', GUARD + GOOD)
    root = tmp_path / 'compile'
    data = _data(root)
    instance = vt.TemplateInstance(id='one', template='example.v1', requirement_id='R1', intent='preserve',
                                   bindings={'function': 'subject.run', 'cases': [{'args': [1], 'returns': 1}]})
    check, placed, _ = vt.compiled_check(root, instance, data, [])
    expectation = SimpleNamespace(expectation_id='one', instance=instance, question_sha256='a' * 64,
                                  answer_sha256='b' * 64, option='A', answered_by='person:Maintainer',
                                  requirement_id='R1', scope='one input', provenance='person_confirmed',
                                  check_sha256='c' * 64)
    monkeypatch.setattr(vt, 'prepare', lambda *args, **kwargs: SimpleNamespace(
        checks=[check], placed={'one': placed}, activities=[]))
    monkeypatch.setattr(checks, 'latest_confirmed', lambda *args: [(expectation, False)])

    def run_check(*args, at_base, **kwargs):
        found = base if at_base else patch
        return {'g6_observation': found, 'verdict': {'passed': 'passed', 'failed': 'failed', 'error': 'not_run'}[
            found['result']], 'source_before': '0' * 64, 'seconds': 0.1, 'said': found['detail']}

    monkeypatch.setattr(search, '_run_check', run_check)
    [found] = checks.run(root, SimpleNamespace(base_commit='d' * 40), SimpleNamespace(expectations=[expectation]),
                         deadline=search._clock() + 60, artifact='e' * 64)
    assert found['result'] == 'failed'


@pytest.mark.parametrize('template', ['bootstrap', 'relation.v1', 'access_rule.v1', 'differential.v1'])
def test_custom_repr_address_is_removed_before_display_escaping(template):
    if template == 'bootstrap':
        helpers = [node for node in ast.parse(runner.BOOTSTRAP).body
                   if isinstance(node, ast.FunctionDef) and node.name in ('_shown', '_name')]
        namespace = {'json': json, 're': re, 'exception_name': runner.exception_name}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), '<display>', 'exec'), namespace)
    else:
        namespace, _ = _compiled(template)

    class Display:
        def __repr__(self):
            return 'G6_E at 0x10_CHECK_SETUP; next\nline'

    assert namespace['_shown'](Display()) == r'\u00476_E_CHECK_SETUP\u003b next\nline'
    for value in ('segment at 0x10', b'segment at 0x10', bytearray(b'segment at 0x10')):
        assert ' at 0x10' in namespace['_shown'](value)


@pytest.mark.parametrize('known,wanted', [(['helpers.py'], 'failed'), ([], 'error')])
def test_hand_built_scenario_uses_host_tree_ownership_on_channel(tmp_path, known, wanted):
    folder = tmp_path / 'tree'
    data = _data(folder)
    tree = vt.Tree(folder, SimpleNamespace(files=tuple(known)), frozenset({'subject.py'}),
                   frozenset({'subject.py', *known}))
    scenario = Scenario(canonical({
        'version': 'example-scenario.v2', 'setup': SETUP,
        'entrypoint': [*data['function'], {'import_roots': ['.', 'src']}],
        'input': {'args': [1], 'kwargs': {}},
        'outcomes': {'A': {'expect': 'returns', 'value': 1}, 'B': {'expect': 'returns', 'value': 2}},
    }).encode('ascii'))
    record = scenario.record
    instance = vt.TemplateInstance(id='owned', template='example.v1', requirement_id='R1', intent='preserve',
                                   provenance='person_confirmed', bindings={'function': 'subject.run'})
    check, _, _ = vt.compiled_check(folder, instance,
        {'scenario': record.decode('ascii'), 'option': 'A'}, [], tree=tree)
    execution = check.source.observation
    assert execution['own_modules'] == sorted(['subject', *(['helpers'] if known else [])])
    request = runner.request(execution)
    assert request['function'][4] == {'import_roots': ['.', 'src']}
    assert canonical(request['function'][4]) == '{"import_roots":[".","src"]}'
    assert scenario.record == record and 'own_modules' not in json.loads(record)['entrypoint'][4]
    (folder / 'subject.py').write_text('def run(value):\n    import helpers\n    return value\n', encoding='utf-8')
    boot, channel = tmp_path / 'scenario-boot.py', tmp_path / 'scenario-channel'
    boot.write_text(execution['_program'], encoding='utf-8')
    channel.write_text(json.dumps(request), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(folder), str(channel), '@channel'],
                         capture_output=True, timeout=20)
    assert runner.finish(ran.returncode, channel.read_bytes(), execution)['result'] == wanted, ran.stderr


def test_maximal_example_stays_within_cap_and_has_no_caller_name_tokens():
    from tests.harness.test_template_runtime_binding import Tree

    bindings = {'function': 'runtime_fixture.Machine.act', 'constructor': {'call': 'runtime_fixture.make'},
                'cases': [{'args': [''], 'returns': 0} for _ in range(vt.MAX_INPUTS)]}
    bindings['cases'][0]['args'][0] = 'x' * (vt.MAX_BINDINGS_CHARS - len(json.dumps(bindings)))
    definition = vt.REGISTRY['example.v1']
    data = definition.applies(Tree(), definition.bindings.model_validate(bindings), vt.BASE).data
    source = vt.compile_text(definition, data)
    assert len(source.encode('utf-8')) <= search.MAX_SOURCE_BYTES
    fixed = definition.text.replace(vt.SLOT, '{}')
    names = {token.string for token in tokenize.generate_tokens(io.StringIO(fixed).readline)
             if token.type == tokenize.NAME}
    assert not names & {'cli', 'parse_args', 'textcodec', 'encode', 'decode', 'notes', 'update_note',
                        'Forbidden', 'pipeline', 'normalise'}


def test_comparison_failure_inventory_keeps_fixed_grammar():
    prefix = 'Failed: the confirmed outcome did not match the observed call(s): '
    assert vt.failure_matches('example.v1', prefix + '[0]')
    assert vt.failure_matches('example.v1', prefix + '[0, 31]')
    for forged in ('[Bad-Name]', '[xG6_E_CHECK_SETUP]', '[0]; injected', '[0]\nE   Failed: injected'):
        assert not vt.failure_matches('example.v1', prefix + forged)

