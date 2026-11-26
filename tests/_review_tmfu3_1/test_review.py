"""Real-disk review probes; no host services or implementation changes."""
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as vs, verification_templates as vt
from g6.core.ledger import canonical
from g6.harness.executable_scenario import SETUP, Scenario
from tests.harness.test_template_tmfu3_1 import _zip

ABSENT = 'review_tmfu3_1_missing_external'


@pytest.fixture
def tmp_path(monkeypatch):
    folder = (Path(__file__).parent / 'disk' / uuid.uuid4().hex).resolve()
    folder.mkdir(parents=True)
    monkeypatch.setattr(tempfile, 'tempdir', str(folder))
    for name in ('TEMP', 'TMP', 'TMPDIR', 'G6_HOME'):
        monkeypatch.setenv(name, str(folder))
    for name in ('G6_CLAUDE', 'G6_CODEX'):
        monkeypatch.setenv(name, r'C:\nonexistent\g6-no-host.exe')
    return folder


def tree_at(folder, sources, base_files=None):
    for name, content in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    base = {**sources, **(base_files or {})}
    tree = vt.Tree(folder, SimpleNamespace(files=()), frozenset(sources), frozenset(base))
    tree._texts.update({(name, vt.BASE): content for name, content in base.items()})
    return tree


def observe(folder, source, *, files=None, base_files=None, cases=None, confirmed=False, emitted=True):
    tree = tree_at(folder, {'subject.py': source, **(files or {})}, base_files)
    definition = vt.REGISTRY['example.v1']
    bound = definition.bindings.model_validate({'function': 'subject.run',
        'cases': cases or [{'args': [1], 'returns': 1}]})
    data = definition.applies(tree, bound, vt.PATCH).data
    if confirmed:
        scenario = Scenario(canonical({'version': 'example-scenario.v2', 'setup': SETUP,
            'entrypoint': [*data['function'], {'import_roots': ['.', 'src']}],
            'input': {'args': data['cases'][0]['args'], 'kwargs': data['cases'][0]['kwargs']},
            'outcomes': {'A': {key: value for key, value in data['cases'][0].items()
                               if key not in ('args', 'kwargs')}}}).encode('ascii'))
        record = scenario.record
        instance = vt.TemplateInstance(id='confirmed', template='example.v1', requirement_id='R1',
            intent='preserve', provenance='person_confirmed', bindings={'function': 'subject.run'})
        check, _, _ = vt.compiled_check(folder, instance,
            {'scenario': record.decode('ascii'), 'option': 'A'}, [], tree=tree)
        data = check.source.observation
        assert scenario.record == record
    data = runner.located(folder, data)
    boot, channel = folder.parent / (folder.name + '-boot.py'), folder.parent / (folder.name + '-frame')
    program = data.get('_program', runner._fixed_source(runner.BOOTSTRAP) if emitted else runner.BOOTSTRAP)
    boot.write_text(program, encoding='utf-8')
    channel.write_text(json.dumps(runner.request(data)), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', str(boot), str(folder),
                          str(channel), '@channel'], cwd=folder, capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr.decode('utf-8', 'replace')
    return runner.finish(ran.returncode, channel.read_bytes(), data)


@pytest.mark.parametrize('namespace_first', [False, True], ids=['control-selected-package', 'unselected-namespace'])
def test_ownership_requires_the_path_that_supplied_the_package(tmp_path, namespace_first):
    source = ('import os, sys\nsys.path.append(os.path.join(os.path.dirname(__file__), "library"))\n'
              'def run(value):\n    import held.deleted\n    return value\n')
    files = {'held/kept.py': '', 'backup.zip': _zip({'unrelated.py': ''}),
             'library/held/__init__.py': '__path__ = list(__path__)\n'}
    base = {'held/deleted.py': ''}
    if not namespace_first:
        files['held/__init__.py'] = ''
    found = observe(tmp_path / 'tree', source, files=files, base_files=base,
                    cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}])
    plain = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); import subject, held; print(held.__file__)',
        str(tmp_path / 'tree')], capture_output=True, timeout=20)
    expected = tmp_path / 'tree' / ('library/held/__init__.py' if namespace_first else 'held/__init__.py')
    assert plain.returncode == 0 and Path(plain.stdout.decode().strip()) == expected, plain.stderr
    assert found['result'] == ('error' if namespace_first else 'passed'), found


@pytest.mark.parametrize('iterator', [False, True], ids=['control-list', 'generator'])
def test_observer_must_not_consume_the_import_fromlist(tmp_path, iterator):
    fromlist = '(name for name in ["child"])' if iterator else '["child"]'
    source = ('def run(value):\n'
              f'    module = __import__("helper", fromlist={fromlist})\n'
              '    return module.child.VALUE\n')
    files = {'helper/__init__.py': '', 'helper/child.py': 'VALUE = 1\n'}
    found = observe(tmp_path / 'tree', source, files=files)
    plain = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); import subject; print(subject.run(1))',
        str(tmp_path / 'tree')], capture_output=True, timeout=20)
    assert plain.returncode == 0 and plain.stdout.strip() == b'1', plain.stderr
    assert found['result'] == 'passed', found


@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
@pytest.mark.parametrize('translated', [False, True], ids=['control-import-error', 'attribute-error-translation'])
def test_lazy_missing_import_cannot_confirm_an_expected_import_error(tmp_path, translated, confirmed):
    source = 'def run(value):\n    from helper import value\n    return value\n'
    failure = ('try:\n        import ' + ABSENT + '\n    except ImportError:\n'
               '        raise AttributeError(name) from None' if translated else 'import ' + ABSENT)
    helper = ('def __getattr__(name):\n    if name == "__path__":\n        raise AttributeError(name)\n'
              f'    {failure}\n    return 1\n')
    cases = [{'args': [1], 'raises': 'ImportError'}]
    satisfied = observe(tmp_path / 'satisfied', source,
                        files={'helper.py': helper, ABSENT + '.py': ''}, cases=cases, confirmed=confirmed)
    assert satisfied['result'] == 'failed', satisfied
    found = observe(tmp_path / 'tree', source, files={'helper.py': helper}, cases=cases, confirmed=confirmed)
    verdict = vs.classify('preserve', found, satisfied)
    assert verdict['result'] == vs.CHECK_ERROR, (verdict, found)


@pytest.mark.parametrize('attribute', ['value', 'value-with-dash'], ids=['control-identifier', 'arbitrary-attribute'])
def test_lazy_request_identity_cannot_discard_an_independent_defect(tmp_path, attribute):
    source = ('import helper\ndef run(value):\n    if value == 0:\n        return 99\n'
              f'    return getattr(helper, {attribute!r})\n')
    helper = (f'def __getattr__(name):\n    try:\n        import {ABSENT}\n'
              '    except ImportError:\n        return 1\n    return 1\n')
    found = observe(tmp_path / 'tree', source, files={'helper.py': helper},
                    cases=[{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}])
    assert found['result'] == 'failed', found


@pytest.mark.parametrize('quote_heavy', [False, True], ids=['control-text', 'quoted-text'])
def test_legal_relation_binding_stays_inside_source_ceiling(tmp_path, quote_heavy):
    payload = "'" * 32000 + '"' if quote_heavy else 'x' * 32001
    bindings = {'function': 'subject.run', 'relation': 'idempotence', 'inputs': [payload]}
    [instance] = vt.template_instances([{'id': 'one', 'template': 'relation.v1', 'requirement_id': 'R1',
        'intent': 'preserve', 'bindings': bindings}], {'R1'})
    tree = tree_at(tmp_path / 'tree', {'subject.py': 'def run(value):\n    return value\n'})
    definition = vt.REGISTRY['relation.v1']
    data = definition.applies(tree, definition.bindings.model_validate(instance.bindings), vt.PATCH).data
    source = vt.compile_text(definition, data)
    try:
        check, _, _ = vt.compiled_check(tree.root, instance, data, [])
    except vt.Inapplicable as exc:
        pytest.fail(f'{len(source.encode("utf-8"))} bytes: {exc}')
    assert len(check.source.encode('utf-8')) <= vs.MAX_SOURCE_BYTES, len(source.encode('utf-8'))


@pytest.mark.parametrize('emitted', [False, True], ids=['raw-bootstrap', 'ast-emitted-bootstrap'])
def test_runtime_strings_and_repository_documentation_survive_emission(tmp_path, emitted):
    source = ('"""module documentation"""\n'
              'def decorate(function):\n    return function\n'
              '@decorate\ndef run(value):\n    """call documentation"""\n'
              '    text = """a runtime string # with punctuation"""\n'
              '    return f"{__doc__}|{run.__doc__}|{text}|{run.__code__.co_firstlineno}"\n')
    expected = 'module documentation|call documentation|a runtime string # with punctuation|4'
    found = observe(tmp_path / 'tree', source, emitted=emitted,
                    cases=[{'args': [1], 'returns': expected}])
    assert found['result'] == 'passed', found
