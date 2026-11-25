"""Repair-1 boundaries, written first; execution belongs to the lead's lane."""
import ast
import json
import re
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as vs, verification_templates as vt


ABSENT = 'tmfu2_r1_absent_dependency'
GOOD = 'def run(value):\n    return value\n'


def _tree(folder, source=GOOD, *, known=(), deleted=()):
    folder.mkdir(parents=True)
    (folder / 'subject.py').write_text(source, encoding='utf-8')
    tree = vt.Tree(folder, SimpleNamespace(files=tuple(deleted)),
                   frozenset({'subject.py', *known}), frozenset(deleted))
    # The host already holds these immutable base texts; no fixture needs git writes.
    tree._texts.update({(name, vt.BASE): '' for name in deleted})
    tree._texts[('subject.py', vt.BASE)] = source
    return tree


def _observe(folder, source, *, template='example.v1', cases=None, known=(), deleted=()):
    tree = _tree(folder, source, known=known, deleted=deleted)
    for name in known:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('', encoding='utf-8')
    definition = vt.REGISTRY[template]
    binding = {'function': 'subject.run'}
    binding.update({'cases': cases or [{'args': [1], 'returns': 1}]} if template == 'example.v1' else
                   {'calls': [{'args': case['args']} for case in cases] if cases else [{'args': [1]}],
                    'mode': 'outputs'})  # a differential call has no expected value of its own
    data = definition.applies(tree, definition.bindings.model_validate(binding), vt.PATCH).data
    data = runner.located(folder, data)
    boot, channel = folder.parent / (folder.name + '-boot.py'), folder.parent / (folder.name + '-frame')
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_text(json.dumps(runner.request(data)), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(folder), str(channel), '@channel'],
                         capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    return runner.finish(ran.returncode, channel.read_bytes(), data)


def _probe(outputs, marks):
    calls = [{'args': [index]} for index in range(len(outputs))]
    outcomes = [{'returned': json.dumps(value), 'arguments': [json.dumps(index)],
                 'probe': {'returned': value},
                 **({'missing_import': runner.ObservationUnavailable.DETAIL, 'missing_imports': names}
                    if names else {})}
                for index, (value, names) in enumerate(zip(outputs, marks, strict=True))]
    raw = json.dumps({'version': 'observation.v2', 'errors': {}, 'outcomes': outcomes}).encode('utf-8')
    return runner.finish(0, runner.MAGIC + struct.pack('>I', len(raw)) + raw, {'calls': calls})


def test_repeat_must_compare_every_retained_difference():
    first = _probe([0, 0], [[], []])
    patch = _probe([0, 1], [[], []])
    again = _probe([1, 0], [[], [ABSENT]])
    verdict = vs.classify('preserve', patch, first)
    assert verdict['result'] == vs.BEHAVIOUR_CHANGED
    assert vs._stable(verdict, first, again)['result'] == vs.CHECK_ERROR


def test_repeat_keeps_an_independent_comparable_difference():
    first = _probe([0, 0, 0], [[], [], []])
    patch = _probe([1, 1, 1], [[], [], []])
    again = _probe([0, 0, 0], [[], [ABSENT], []])
    verdict = vs.classify('preserve', patch, first)
    stable = vs._stable(verdict, first, again)
    assert stable['result'] == vs.BEHAVIOUR_CHANGED
    assert [json.loads(one['input'])['args'] for one in stable['differences']] == [[0], [2]]


def test_stability_keeps_the_fallback_detail_and_field():
    first = _probe([0], [[ABSENT]])
    patch = _probe([1], [[ABSENT]])
    again = _probe([0], [[ABSENT]])
    verdict = vs.classify('preserve', patch, first)
    stable = vs._stable(verdict, first, again)
    assert stable['result'] == vs.BEHAVIOUR_CHANGED
    assert stable['fallback_observation']['missing_imports'] == [ABSENT]
    assert 'fallback was taken' in stable['detail']


@pytest.mark.parametrize('marked_index', [0, 1])
def test_confirmed_mismatch_is_judged_per_case(tmp_path, marked_index):
    cases = [{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}]
    base = _observe(tmp_path / 'base', GOOD, cases=cases)
    source = (f'def run(value):\n    if value == {marked_index}:\n'
              f'        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            pass\n    return 99\n')
    patch = _observe(tmp_path / 'patch', source, cases=cases)
    found = vs.classify('preserve', patch, base)
    assert found['result'] == vs.COUNTEREXAMPLE
    assert str([1 - marked_index]) in found['detail']


@pytest.mark.parametrize('call', ["loader(''.join(['he', 'lpers']))", "aliases.loader('helpers')",
                                "__import__('helpers')"])
def test_unknown_importer_never_prunes_ownership(tmp_path, call):
    source = 'loader = __import__\ndef run(value):\n    ' + call + '\n    return value\n'
    tree = _tree(tmp_path / 'tree', source,
                 known=[f'module_number_{index:04d}.py' for index in range(600)], deleted=['helpers.py'])
    data = vt._own_modules(tree, vt.resolve(tree, 'subject.run', vt.PATCH), vt.PATCH)
    assert 'helpers' in data['own_modules']


@pytest.mark.parametrize('regular', [False, True])
@pytest.mark.parametrize('statement', ['import owned.nested.deleted', 'from owned.nested import deleted'])
def test_deleted_nested_owner_is_failed_with_host_evidence(tmp_path, regular, statement):
    known = ['owned/nested/kept.py']
    if regular:
        known += ['owned/__init__.py', 'owned/nested/__init__.py']
    found = _observe(tmp_path / 'tree', f'def run(value):\n    {statement}\n    return value\n',
                     known=known, deleted=['owned/nested/deleted.py'])
    assert found['result'] == 'failed'


def test_inventory_names_namespace_modules_at_every_depth(tmp_path):
    tree = _tree(tmp_path / 'tree', known=['space/part/kept.py'], deleted=['space/part/deleted.py'])
    data = vt._own_modules(tree, vt.resolve(tree, 'subject.run', vt.PATCH), vt.PATCH)
    assert {'space', 'space.part', 'space.part.kept', 'space.part.deleted'} <= set(data['own_modules'])


def test_worker_library_callback_marks_unsatisfied_import(tmp_path):
    source = ('import concurrent.futures\nimport importlib\ndef run(value):\n'
              '    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:\n'
              f'        future = pool.submit(importlib.import_module, "{ABSENT}")\n'
              '        try:\n            future.result()\n        except ImportError:\n            return 0\n'
              '    return value\n')
    found = _observe(tmp_path / 'tree', source)
    assert found['result'] == 'error'
    assert found['import_evidence']['outcomes'] == [[ABSENT]]


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
def test_admitted_readings_keep_fallback_qualification(tmp_path, template):
    source = f'try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n' + GOOD
    base = _observe(tmp_path / 'base', source, template=template)
    patch = _observe(tmp_path / 'patch', source, template=template)
    found = vs.classify('preserve', patch, base)
    assert found['result'] == vs.NOT_REFUTED
    assert found['fallback_observation'] == {'missing_imports': [ABSENT], 'satisfied_import_path': 'not observed'}
    assert 'fallback was taken' in found['detail']


def _display(template):
    source = runner.BOOTSTRAP if template == 'bootstrap' else vt.REGISTRY[template].text.replace(vt.SLOT, '{}')
    shown = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == '_shown')
    namespace = {'json': json, 're': re, 'sys': sys}
    exec(compile(ast.Module(body=[shown], type_ignores=[]), '<display>', 'exec'), namespace)
    return namespace['_shown']


@pytest.mark.parametrize('template', ['bootstrap', 'relation.v1', 'access_rule.v1', 'differential.v1'])
@pytest.mark.parametrize('content', ['segment at 0x10', b'segment at 0x10', bytearray(b'segment at 0x10')])
def test_nested_content_is_preserved_and_objects_are_normalized(template, content):
    shown = _display(template)
    value = {'key at 0x20': ([content], object())}
    text = shown(value)
    assert 'key at 0x20' in text and 'segment at 0x10' in text
    assert '<object object>' in text


@pytest.mark.parametrize('template', ['bootstrap', 'relation.v1'])
def test_recursive_container_display_terminates(template):
    value = ['segment at 0x10']
    value.append(value)
    assert _display(template)(value) == "['segment at 0x10', [...]]"


@pytest.mark.parametrize('count', [370, 371, 372, 600])
def test_source_ceiling_includes_ownership_and_maximal_bindings(tmp_path, count):
    tree = _tree(tmp_path / 'tree', known=[f'module_number_{index:04d}.py' for index in range(count)])
    bindings = {'function': 'subject.run', 'cases': [{'args': [''], 'returns': 0} for _ in range(vt.MAX_INPUTS)]}
    bindings['cases'][0]['args'][0] = 'x' * (vt.MAX_BINDINGS_CHARS - len(json.dumps(bindings)))
    definition = vt.REGISTRY['example.v1']
    data = definition.applies(tree, definition.bindings.model_validate(bindings), vt.PATCH).data
    instance = vt.TemplateInstance(id='one', template='example.v1', requirement_id='R1', intent='preserve',
                                   bindings=bindings)
    assert len(vt.compile_text(definition, data).encode('utf-8')) <= vs.MAX_SOURCE_BYTES
    check, _, _ = vt.compiled_check(tree.root, instance, data, [])
    assert runner.bindings(check.source)[1]['cases'] == data['cases']
    if isinstance(check.source, runner.ExecutableSource):
        assert check.source.observation['own_modules'] == data['own_modules']


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
@pytest.mark.parametrize('marked_index,wanted', [(0, [[ABSENT], []]), (1, [[], [ABSENT]])])
def test_call_import_evidence_does_not_spread_to_independent_cases(tmp_path, template, marked_index, wanted):
    source = (f'def run(value):\n    if value == {marked_index}:\n'
              f'        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            pass\n    return 99\n')
    found = _observe(tmp_path / 'tree', source, template=template,
                     cases=[{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}])
    assert found['import_evidence']['outcomes'] == wanted


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
def test_preparation_import_evidence_still_applies_to_every_case(tmp_path, template):
    source = f'try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n' + GOOD
    found = _observe(tmp_path / 'tree', source, template=template,
                     cases=[{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}])
    assert found['import_evidence']['outcomes'] == [[ABSENT], [ABSENT]]
