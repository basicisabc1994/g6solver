"""Build 1 boundary regressions; the lead runs these in the shared test lane."""
import ast
import base64
import inspect
import io
import json
import subprocess
import sys
import zipfile
import zlib
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as vs, verification_templates as vt
from g6.core.ledger import canonical
from g6.harness.executable_scenario import SETUP, Scenario
from tests.harness.test_template_tmfu2_r1 import _probe

ABSENT = 'tmfu3_1_absent_dependency'
GOOD = 'def run(value):\n    return value\n'


def _zip(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _observe(folder, source, *, files=None, base_files=None, cases=None):
    sources = {'subject.py': source, **(files or {})}
    for name, content in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    base = {**sources, **(base_files or {})}
    tree = vt.Tree(folder, SimpleNamespace(files=()), frozenset(sources), frozenset(base))
    tree._texts.update({(name, vt.BASE): content for name, content in base.items()})
    definition = vt.REGISTRY['example.v1']
    binding = {'function': 'subject.run', 'cases': cases or [{'args': [1], 'returns': 1}]}
    data = runner.located(folder, definition.applies(tree, definition.bindings.model_validate(binding), vt.PATCH).data)
    boot, channel = folder.parent / (folder.name + '-boot.py'), folder.parent / (folder.name + '-frame')
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_text(json.dumps(runner.request(data)), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', str(boot), str(folder), str(channel), '@channel'],
                         cwd=folder, capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr.decode('utf-8', 'replace')
    return runner.finish(ran.returncode, channel.read_bytes(), data), data


@pytest.mark.parametrize('repeat', ['independent', 'dependent-stable', 'dependent-shaky'])
def test_repeat_requires_the_same_independence_as_a_producing_run(repeat):
    dependent = repeat != 'independent'
    first = _probe([0, 0], [[], []])
    patch = _probe([0, 1], [[], []])
    again = _probe([0, 1] if repeat == 'dependent-shaky' else [0, 0],
                   [[ABSENT], []] if dependent else [[], []])
    if dependent:
        assert again['import_evidence']['dependent'] == [1]
    verdict = vs.classify('preserve', patch, first)
    assert verdict['result'] == vs.BEHAVIOUR_CHANGED
    stable = vs._stable(verdict, first, again)
    assert stable['result'] == (vs.CHECK_ERROR if repeat == 'dependent-shaky' else vs.BEHAVIOUR_CHANGED)
    assert 'unstable' not in stable
    if repeat != 'dependent-shaky':
        assert [one['case'] for one in stable['differences']] == [1]
    if dependent:
        # Matching dependent output retains the producing comparison, without
        # turning a missing fresh observation into independent repeat evidence.
        assert again['import_evidence']['dependent'] == [1]
        assert again['case_reruns'] == [
            {'case': 1, 'ran': False, 'result': 'unavailable', 'outcome': None, 'marks': []}]


@pytest.mark.parametrize('active', [False, True], ids=['backup-beside-path', 'archive-on-path'])
def test_archive_ownership_requires_the_runs_path_entry(tmp_path, active):
    prefix = ('import os, sys\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor.zip"))\n'
              if active else '')
    source = prefix + f'def run(value):\n    import {ABSENT}\n    return value\n'
    files = {'vendor.zip': _zip({'kept.py': ''})}
    base_files = {'vendor.zip': _zip({'kept.py': '', ABSENT + '.py': ''})}
    found, _ = _observe(tmp_path / 'tree', source, files=files, base_files=base_files,
                        cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}])
    assert found['result'] == ('passed' if active else 'error'), found


@pytest.mark.parametrize('position', ['before', 'after'])
def test_archive_inventory_respects_an_earlier_package_spec(tmp_path, position):
    # A namespace in the archive inventory must not claim a child of a different,
    # earlier regular package. The earlier package extends its path: absence is ambiguous.
    paths = ['vendor.zip', 'library'] if position == 'before' else ['library', 'vendor.zip']
    source = ('import os, sys\n' + ''.join(
        f'sys.path.append(os.path.join(os.path.dirname(__file__), {part!r}))\n' for part in paths) +
        'def run(value):\n    import held.deleted\n    return value\n')
    files = {'vendor.zip': _zip({'held/__init__.py': '', 'held/kept.py': ''}),
             'library/held/__init__.py': '__path__ = list(__path__)\n'}
    base_files = {'vendor.zip': _zip({'held/__init__.py': '', 'held/kept.py': '', 'held/deleted.py': ''})}
    found, _ = _observe(tmp_path / 'tree', source, files=files, base_files=base_files,
                        cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}])
    assert found['result'] == ('passed' if position == 'before' else 'error'), found


@pytest.mark.parametrize('failure', ['find', 'raise', 'named-raise', 'module-not-found-raise'])
def test_initializer_missingness_needs_an_observed_failed_find(tmp_path, failure):
    helper = {
        'find': f'import {ABSENT}\n',
        'raise': 'raise ImportError("unsupported configuration")\n',
        'named-raise': f'raise ImportError("unsupported configuration", name={ABSENT!r})\n',
        'module-not-found-raise': f'raise ModuleNotFoundError("unsupported configuration", name={ABSENT!r})\n',
    }[failure]
    base, _ = _observe(tmp_path / 'base', GOOD)
    found, _ = _observe(tmp_path / 'tree', 'def run(value):\n    import helper\n    return value\n',
                        files={'helper.py': helper})
    verdict = vs.classify('preserve', found, base)
    assert verdict['result'] == (vs.CHECK_ERROR if failure == 'find' else vs.COUNTEREXAMPLE), (verdict, found)
    if failure != 'find':
        assert 'import_evidence' not in found


@pytest.mark.parametrize('route', ['import', 'reload', 'lazy-from', 'lazy-attribute'])
@pytest.mark.parametrize('same', [True, False], ids=['same-request', 'different-request'])
def test_comparability_uses_the_outer_request_not_the_inner_missing_name(tmp_path, route, same):
    helper = f'import {ABSENT}\n'
    prefix = ''
    if route == 'reload':
        helper = f'if globals().get("loaded"):\n    import {ABSENT}\nloaded = True\n'
        prefix = 'import importlib, helper_a, helper_b\n'
    elif route.startswith('lazy-'):
        helper = f'def __getattr__(name):\n    import {ABSENT}\n    return 1\n'
        prefix = 'import helper_a, helper_b\n'

    def source(module, fallback):
        operation = {'import': f'import {module}', 'reload': f'importlib.reload({module})',
                     'lazy-from': f'from {module} import value', 'lazy-attribute': f'{module}.value'}[route]
        return (prefix + f'def run(value):\n    try:\n        {operation}\n'
                f'    except ImportError:\n        return {fallback}\n    return value\n')

    files = {'helper_a.py': helper, 'helper_b.py': helper}
    base, _ = _observe(tmp_path / 'base', source('helper_a', 1), files=files)
    patch, _ = _observe(tmp_path / 'patch', source('helper_a' if same else 'helper_b', 99), files=files)
    satisfied, _ = _observe(tmp_path / 'satisfied', source('helper_a' if same else 'helper_b', 99),
                            files={**files, ABSENT + '.py': ''})
    assert satisfied['result'] == 'passed', satisfied
    verdict = vs.classify('preserve', patch, base)
    assert verdict['result'] == (vs.COUNTEREXAMPLE if same else vs.CHECK_ERROR), (verdict, base, patch)
    if same:
        assert base['fallback_observation']['missing_imports']


def test_preparation_lazy_request_keeps_legacy_names_but_records_outer_identity(tmp_path):
    source = GOOD + f'del run\ndef __getattr__(name):\n    import {ABSENT}\n'
    found, _ = _observe(tmp_path / 'tree', source)
    assert found['import_evidence']['outcomes'] == [[ABSENT]]
    assert found['import_evidence']['requests'] == [['attribute:subject:run']], found


def test_maximal_example_keeps_readable_bindings_without_compact_fallback():
    from tests.harness.test_template_runtime_binding import Tree

    bindings = {'function': 'runtime_fixture.Machine.act', 'constructor': {'call': 'runtime_fixture.make'},
                'cases': [{'args': [''], 'returns': 0} for _ in range(vt.MAX_INPUTS)]}
    bindings['cases'][0]['args'][0] = 'x' * (vt.MAX_BINDINGS_CHARS - len(json.dumps(bindings)))
    definition = vt.REGISTRY['example.v1']
    data = definition.applies(Tree(), definition.bindings.model_validate(bindings), vt.BASE).data
    # Ownership takes the old compiler's compact branch instead of merely returning oversized text.
    data['own_modules'] = ['runtime_fixture']
    source = vt.compile_text(definition, data)
    assert len(definition.text.replace(vt.SLOT, ascii(data)).encode('utf-8')) <= vs.MAX_SOURCE_BYTES
    assert not source.startswith(runner.COMPACT)
    assert runner.bindings(source) == ('example.v1', data)
    assert source == definition.text.replace(vt.SLOT, ascii(data))


def test_confirmed_scenario_remains_readable_without_expansion():
    scenario = Scenario(canonical({
        'version': 'example-scenario.v1', 'setup': SETUP,
        'entrypoint': ['module', 'fixture', 'run', 'fixture.py'],
        'input': {'args': [0], 'kwargs': {}}, 'outcomes': {'A': {'expect': 'returns', 'value': 0}},
    }).encode('ascii'))
    source = runner.CONFIRMED + scenario.source('A')
    assert not source.startswith(runner.CONFIRMED + runner.COMPACT)
    assert "TEMPLATE = 'example.v1'" in source
    assert runner.bindings(source) == ('example.v1', {
        **scenario.comparison('A'), '_program': scenario.program})
    assert len(source.encode('utf-8')) <= vs.MAX_SOURCE_BYTES


def test_fixed_source_preserves_runtime_strings_while_omitting_documentation():
    source = '''"""Module documentation."""
# Fixed-code explanation.
class Sample:
    """Class documentation."""
    def value(self):
        """Method documentation."""
        return "literal # content and docstring-like text"
'''
    emitted = runner._fixed_source(source)
    namespace = {}
    exec(compile(emitted, '<fixed-source>', 'exec'), namespace)
    assert namespace['Sample']().value() == 'literal # content and docstring-like text'
    assert 'documentation.' not in emitted
    assert 'Fixed-code explanation.' not in emitted


@pytest.mark.parametrize('kind', ['example', 'scenario'])
def test_bootstrap_keeps_its_source_owner_while_emission_omits_documentation(kind):
    assert inspect.getsource(runner._exception_observation) in runner.BOOTSTRAP
    assert runner.BOOTSTRAP.count('outcomes.append(_exception_observation(') == 1
    if kind == 'example':
        source = runner.example_text('{}')
        assignment = next(node for node in ast.parse(source).body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'BOOTSTRAP'
                                  for target in node.targets))
        emitted = eval(compile(ast.Expression(assignment.value), '<emitted-bootstrap>', 'eval'),
                       {'base64': base64, 'zlib': zlib})
    else:
        scenario = Scenario(canonical({
            'version': 'example-scenario.v1', 'setup': SETUP,
            'entrypoint': ['module', 'fixture', 'run', 'fixture.py'],
            'input': {'args': [0], 'kwargs': {}}, 'outcomes': {'A': {'expect': 'returns', 'value': 0}},
        }).encode('ascii'))
        emitted = scenario.program
    assert inspect.getdoc(runner._exception_observation).splitlines()[0] not in emitted
    assert emitted.count('outcomes.append(_exception_observation(') == 1


def _repair2_observe(folder, source, *, files=None, base_files=None, cases=None, confirmed=False):
    """Exercise emitted templates and the immutable confirmed-scenario program alike."""
    sources = {'subject.py': source, **(files or {})}
    for name, content in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    base = {**sources, **(base_files or {})}
    tree = vt.Tree(folder, SimpleNamespace(files=()), frozenset(sources), frozenset(base))
    tree._texts.update({(name, vt.BASE): content for name, content in base.items()})
    definition = vt.REGISTRY['example.v1']
    binding = definition.bindings.model_validate({'function': 'subject.run', 'cases': cases})
    data = definition.applies(tree, binding, vt.PATCH).data
    if confirmed:
        scenario = Scenario(canonical({'version': 'example-scenario.v2', 'setup': SETUP,
            'entrypoint': [*data['function'], {'import_roots': ['.', 'src']}],
            'input': {'args': data['cases'][0]['args'], 'kwargs': data['cases'][0]['kwargs']},
            'outcomes': {'A': {key: value for key, value in data['cases'][0].items()
                               if key not in ('args', 'kwargs')}}}).encode('ascii'))
        record = scenario.record
        instance = vt.TemplateInstance(id='repair2', template='example.v1', requirement_id='R1',
            intent='preserve', provenance='person_confirmed', bindings={'function': 'subject.run'})
        check, _, _ = vt.compiled_check(folder, instance,
            {'scenario': record.decode('ascii'), 'option': 'A'}, [], tree=tree)
        data = check.source.observation
        assert scenario.record == record
    data = runner.located(folder, data)
    boot, channel = folder.parent / (folder.name + '-boot.py'), folder.parent / (folder.name + '-frame')
    boot.write_text(data.get('_program', runner._fixed_source(runner.BOOTSTRAP)), encoding='utf-8')
    channel.write_text(json.dumps(runner.request(data)), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', str(boot), str(folder),
                          str(channel), '@channel'], cwd=folder, capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr.decode('utf-8', 'replace')
    return runner.finish(ran.returncode, channel.read_bytes(), data)


@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
@pytest.mark.parametrize('translated', [False, True], ids=['direct', 'translated'])
def test_repair2_lazy_find_blocks_only_import_error_confirmation(tmp_path, translated, confirmed):
    failure = (f'try:\n        import {ABSENT}\n    except ImportError:\n'
               '        raise AttributeError(name) from None' if translated else f'import {ABSENT}')
    helper = ('def __getattr__(name):\n    if name == "__path__":\n        raise AttributeError(name)\n'
              f'    {failure}\n    return 1\n')
    source = 'def run(value):\n    from helper import value\n    return value\n'
    cases = [{'args': [1], 'raises': 'ImportError'}]
    found = _repair2_observe(tmp_path / 'tree', source, files={'helper.py': helper}, cases=cases,
                             confirmed=confirmed)
    satisfied = _repair2_observe(tmp_path / 'satisfied', source,
        files={'helper.py': helper, ABSENT + '.py': ''}, cases=cases, confirmed=confirmed)
    assert satisfied['result'] == 'failed', satisfied
    assert found['result'] == 'error', found
    assert ABSENT in found['import_evidence']['details'][0], found
    assert vs.classify('preserve', found, satisfied)['result'] == vs.CHECK_ERROR


@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
@pytest.mark.parametrize('expected', ['ImportError', 'ModuleNotFoundError', 'subject.DependencyError'])
def test_repair2_caught_find_blocks_expected_import_error_family(tmp_path, expected, confirmed):
    raised = 'DependencyError' if expected.startswith('subject.') else expected
    source = ('class DependencyError(ImportError):\n    pass\ndef run(value):\n'
              f'    try:\n        import {ABSENT}\n    except ImportError:\n        pass\n'
              f'    raise {raised}("deliberate code exception") from None\n')
    found = _repair2_observe(tmp_path / 'tree', source, cases=[{'args': [1], 'raises': expected}],
                             confirmed=confirmed)
    assert found['result'] == 'error', found
    assert ABSENT in found['import_evidence']['details'][0], found


@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
def test_repair2_recorded_deleted_find_still_confirms_module_not_found(tmp_path, confirmed):
    found = _repair2_observe(tmp_path / 'tree', f'def run(value):\n    import {ABSENT}\n',
        base_files={ABSENT + '.py': ''}, cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}],
        confirmed=confirmed)
    assert found['result'] == 'passed', found
    assert 'fallback_observation' not in found


@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
@pytest.mark.parametrize('expected', ['returns', 'RuntimeError'])
def test_repair2_outside_fallback_return_and_runtime_error_still_confirm(tmp_path, expected, confirmed):
    ending = 'return value' if expected == 'returns' else 'raise RuntimeError("fallback") from None'
    source = (f'def run(value):\n    try:\n        import {ABSENT}\n'
              f'    except ImportError:\n        {ending}\n    return value\n')
    case = {'args': [1], **({'returns': 1} if expected == 'returns' else {'raises': expected})}
    found = _repair2_observe(tmp_path / 'tree', source, cases=[case], confirmed=confirmed)
    assert found['result'] == 'passed', found
    assert found['fallback_observation'] == {'missing_imports': [ABSENT],
                                              'satisfied_import_path': 'not observed'}
    assert 'fallback was taken' in found['detail']


@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
@pytest.mark.parametrize('selected', [False, True], ids=['unselected-namespace', 'selected-package'])
def test_repair2_ownership_uses_only_the_find_parent_path(tmp_path, selected, confirmed):
    source = ('import os, sys\nsys.path.append(os.path.join(os.path.dirname(__file__), "library"))\n'
              'def run(value):\n    import held.deleted\n')
    files = {'held/kept.py': '', 'backup.zip': _zip({'unrelated.py': ''}),
             'library/held/__init__.py': '__path__ = list(__path__)\n'}
    if selected:
        files['held/__init__.py'] = ''
    found = _repair2_observe(tmp_path / 'tree', source, files=files,
        base_files={'held/deleted.py': ''}, cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}],
        confirmed=confirmed)
    assert found['result'] == ('passed' if selected else 'error'), found


def test_repair2_find_window_is_per_call_and_expected_class_controls_it(tmp_path):
    source = ('def run(value):\n    if value:\n        try:\n'
              f'            import {ABSENT}\n        except ImportError:\n            pass\n'
              '    raise ImportError("code exception") from None\n')
    found = _repair2_observe(tmp_path / 'tree', source, cases=[
        {'args': [0], 'raises': 'ImportError'}, {'args': [1], 'raises': 'ImportError'}])
    assert found['result'] == 'error', found
    assert found['import_evidence']['unavailable'] == [1], found
    # A broader class is outside R1' even when the observed type is ImportError.
    broad = _repair2_observe(tmp_path / 'broad', source, cases=[{'args': [1], 'raises': 'Exception'}])
    assert broad['result'] == 'passed', broad
    assert broad['fallback_observation']['missing_imports'] == [ABSENT]


def test_repair2_initializer_export_failure_keeps_its_selected_spec(tmp_path):
    # Python removes a module whose initializer raises from sys.modules. Its
    # selected spec still supplies the inherited own-export failure decision.
    found = _repair2_observe(tmp_path / 'tree', 'def run(value):\n    import helper\n    return value\n',
        files={'helper/__init__.py': 'from . import gone\n'}, cases=[{'args': [1], 'returns': 1}])
    assert found['result'] == 'failed', found
