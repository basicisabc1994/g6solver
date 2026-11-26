"""Repair 3: preserve import ownership and the whole observed confirmation window."""
import json
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_templates as vt
from tests.harness.test_template_tmfu3_1 import ABSENT, _repair2_observe, _zip


@pytest.mark.parametrize('statement', ['import gone.child', 'from gone.child import value'])
@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
def test_deleted_parent_still_confirms_its_own_missing_module(tmp_path, statement, confirmed):
    found = _repair2_observe(tmp_path / 'tree', f'def run(value):\n    {statement}\n',
        base_files={'gone/__init__.py': '', 'gone/child.py': 'value = 1\n'},
        cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}], confirmed=confirmed)
    assert found['result'] == 'passed', found
    assert 'fallback_observation' not in found


@pytest.mark.parametrize('expected', ['ImportError', 'subject.DependencyError', 'RuntimeError'])
@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
def test_load_find_qualifies_only_import_family_expectations(tmp_path, expected, confirmed):
    raised = expected.rsplit('.', 1)[-1]
    source = ('class DependencyError(ImportError):\n    pass\n'
              f'try:\n    import {ABSENT}\nexcept ImportError:\n    available = False\n'
              f'def run(value):\n    raise {raised}("cached dependency failure") from None\n')
    found = _repair2_observe(tmp_path / 'tree', source,
        cases=[{'args': [1], 'raises': expected}], confirmed=confirmed)
    assert found['result'] == ('passed' if expected == 'RuntimeError' else 'error'), found
    if expected == 'RuntimeError':
        assert found['fallback_observation']['missing_imports'] == [ABSENT]
    else:
        assert found['reason_code'] == 'G6_E_OBSERVATION_UNAVAILABLE'
        assert found['import_evidence']['unavailable'] == [0]


def test_cached_find_keeps_the_raising_case_and_its_fresh_record(tmp_path):
    source = ('available = None\ndef run(value):\n    global available\n'
              f'    if available is None:\n        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            available = False\n'
              '    if value and not available:\n        raise ImportError("cached")\n    return value\n')
    found = _repair2_observe(tmp_path / 'tree', source,
        cases=[{'args': [0], 'returns': 0}, {'args': [1], 'raises': 'ImportError'}])
    assert found['result'] == 'error', found
    assert found['import_evidence']['unavailable'] == [1]
    assert len(found['import_evidence']['outcomes']) == 2
    [rerun] = found['case_reruns']
    assert rerun['case'] == 1 and rerun['ran']
    assert rerun['outcome']['outcomes'][0]['import_error_finds']


@pytest.mark.parametrize('spelling', ['pkg', 'Pkg'])
def test_archive_member_ownership_keeps_exact_entry_spelling(tmp_path, spelling):
    source = ('import sys\nsys.path.insert(0, "held.zip")\n'
              f'def run(value):\n    import {spelling}.gone\n')
    found = _repair2_observe(tmp_path / 'tree', source,
        files={'held.zip': _zip({'pkg/__init__.py': '', 'pkg/child.py': ''})},
        cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}])
    assert found['result'] == ('passed' if spelling == 'pkg' else 'error'), found


def test_large_decoded_inventory_uses_a_bounded_request_and_keeps_deleted_owners(tmp_path):
    known = {f'src/catalog/section_{index:04d}/long_module_name.py' for index in range(2000)}
    source = 'def run(value):\n    import catalog.section_1999.long_module_name\n    return int(value)\n'
    sources = {'subject.py': source}
    folder = tmp_path / 'tree'
    folder.mkdir()
    (folder / 'subject.py').write_text(source, encoding='utf-8')
    tree = vt.Tree(folder, SimpleNamespace(files=()), frozenset(sources), frozenset(sources) | known)
    tree._texts.update({(name, vt.BASE): text for name, text in sources.items()})
    definition = vt.REGISTRY['example.v1']
    bound = definition.bindings.model_validate({'function': 'subject.run',
        'cases': [{'args': [1], 'raises': 'ModuleNotFoundError'}]})
    data = definition.applies(tree, bound, vt.PATCH).data
    request = runner.request(data)
    baseline = {key: value for key, value in request.items()
                if key not in ('own_module_files', 'own_module_layout')}
    assert len(json.dumps(request)) <= len(json.dumps(baseline)) * 1.03
    # Execute through the same emitted bootstrap interface as the focused existing checks.
    found = _repair2_observe(tmp_path / 'observed', source,
        base_files={name: '' for name in known},
        cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}])
    assert found['result'] == 'passed', found
    assert 'fallback_observation' not in found


@pytest.mark.parametrize('kind', ['iterator', 'generator', 'iterable', 'list-subclass',
                                 'tuple-subclass', 'namespace-subclass', 'namespace-namesake'])
def test_custom_package_paths_are_left_for_python_to_iterate(tmp_path, kind):
    setup = {
        'iterator': 'pkg.__path__ = iter(entries)\n',
        'generator': 'pkg.__path__ = (entry for entry in entries)\n',
        'iterable': ('class Paths:\n    def __iter__(self):\n        return next(passes)\n'
                     'pkg.__path__ = Paths()\n'),
        'list-subclass': ('class Paths(list):\n    def __iter__(self):\n        return next(passes)\n'
                         'pkg.__path__ = Paths(entries)\n'),
        'tuple-subclass': ('class Paths(tuple):\n    def __iter__(self):\n        return next(passes)\n'
                          'pkg.__path__ = Paths(entries)\n'),
        'namespace-subclass': ('class Paths(type(pkg.__path__)):\n'
                              '    def __iter__(self):\n        return next(passes)\n'
                              'pkg.__path__ = Paths("pkg", entries, lambda *args: None)\n'),
        'namespace-namesake': ('class _NamespacePath:\n'
                              '    def __iter__(self):\n        return next(passes)\n'
                              'pkg.__path__ = _NamespacePath()\n'),
    }[kind]
    source = ('import pkg\nentries = list(pkg.__path__)\npasses = iter([iter(entries), iter(())])\n'
              + setup + 'def run(value):\n    import pkg.child\n    return pkg.child.VALUE\n')
    found = _repair2_observe(tmp_path / 'tree', source, files={'pkg/child.py': 'VALUE = 1\n'},
        cases=[{'args': [1], 'returns': 1}])
    assert found['result'] == 'passed', found
    assert 'fallback_observation' not in found


@pytest.mark.parametrize('kind', ['list', 'tuple', 'namespace', 'list-subclass', 'tuple-subclass',
                                 'namespace-subclass', 'iterator', 'generator', 'iterable'])
def test_only_exact_reusable_package_paths_can_supply_deleted_ownership(tmp_path, kind):
    setup = {
        'list': 'pkg.__path__ = entries\n',
        'tuple': 'pkg.__path__ = tuple(entries)\n',
        'namespace': '',
        'list-subclass': 'class Paths(list):\n    pass\npkg.__path__ = Paths(entries)\n',
        'tuple-subclass': 'class Paths(tuple):\n    pass\npkg.__path__ = Paths(entries)\n',
        'namespace-subclass': ('class Paths(type(pkg.__path__)):\n    pass\n'
                              'pkg.__path__ = Paths("pkg", entries, lambda *args: None)\n'),
        'iterator': 'pkg.__path__ = iter(entries)\n',
        'generator': 'pkg.__path__ = (entry for entry in entries)\n',
        'iterable': ('class Paths:\n    def __iter__(self):\n        return iter(entries)\n'
                     'pkg.__path__ = Paths()\n'),
    }[kind]
    source = ('import pkg\nentries = list(pkg.__path__)\n' + setup
              + 'def run(value):\n    import pkg.deleted\n    return value\n')
    found = _repair2_observe(tmp_path / 'tree', source, files={'pkg/kept.py': ''},
        base_files={'pkg/deleted.py': ''}, cases=[{'args': [1], 'returns': 1}])
    assert found['result'] == ('failed' if kind in ('list', 'tuple', 'namespace') else 'error'), found


def test_closed_package_does_not_own_a_replaced_search_location(tmp_path):
    source = ('import os, pkg\npkg.__path__ = [os.path.join(os.path.dirname(__file__), "elsewhere")]\n'
              'def run(value):\n    import pkg.deleted\n')
    found = _repair2_observe(tmp_path / 'tree', source, files={'pkg/__init__.py': '', 'elsewhere/kept.py': ''},
        base_files={'pkg/deleted.py': ''}, cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}])
    assert found['result'] == 'error', found


def test_archive_member_directory_spelling_cannot_grant_ownership(tmp_path):
    source = ('import os, sys\nsys.path.insert(0, "held.zip")\nimport pkg\n'
              'pkg.__path__ = [os.path.join(os.path.dirname(__file__), "held.zip", "Pkg")]\n'
              'def run(value):\n    import pkg.deleted\n')
    found = _repair2_observe(tmp_path / 'tree', source,
        files={'held.zip': _zip({'pkg/__init__.py': '__path__ = list(__path__)\n', 'pkg/kept.py': ''})},
        base_files={'held.zip': _zip({'pkg/__init__.py': '', 'pkg/deleted.py': ''})},
        cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}])
    assert found['result'] == 'error', found


@pytest.mark.parametrize('shape', ['guarded-package', 'guarded-submodule', 'guarded-in-call'])
@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
@pytest.mark.parametrize('expected', ['returns', 'ImportError'])
def test_swallowed_package_dependency_does_not_own_a_missing_export(tmp_path, shape, confirmed, expected):
    guard = f'try:\n    import {ABSENT}\n    fast = 1\nexcept ImportError:\n    pass\n'
    files = {'held/__init__.py': guard}
    if shape == 'guarded-submodule':
        files = {'held/__init__.py': 'try:\n    from ._speed import fast\nexcept ImportError:\n    pass\n',
                 'held/_speed.py': f'import {ABSENT}\nfast = 1\n'}
    source = ('def run(value):\n    from held import fast\n    return value\n' if shape == 'guarded-in-call'
              else 'from held import fast\ndef run(value):\n    return value\n')
    case = {'args': [1], **({'returns': 1} if expected == 'returns' else {'raises': expected})}
    found = _repair2_observe(tmp_path / 'tree', source, files=files, cases=[case], confirmed=confirmed)
    assert found['result'] == 'error', found
    assert found['reason_code'] == 'G6_E_OBSERVATION_UNAVAILABLE', found
