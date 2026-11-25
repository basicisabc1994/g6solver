"""Real-disk collection checks; no confinement bypass, network, or model calls."""
import os
import subprocess
import sys

import pytest

from g6.workforce import repo_tests


PRELUDE = "import os\ndef install():\n    os.environ['PATH'] = 'cmfu3-review2'\n    return 1\n"
CHILD = ("import os, sys, pytest\n"
         "code = pytest.main(sys.argv[1:])\n"
         "print('EFFECT=' + str(os.environ.get('PATH') == 'cmfu3-review2'))\n"
         "sys.exit(int(code))\n")


def collected(tmp_path, files, node='test_api.py::test_call'):
    root = tmp_path / 'repo'
    files = {'test_api.py': 'def test_call(): pass\n', **files}
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1')
    ran = subprocess.run(
        [sys.executable, '-B', '-c', CHILD, '--collect-only', '-q', '-p', 'no:cacheprovider',
         '--confcutdir', str(root), '--rootdir', str(root), '--basetemp', str(tmp_path / 'plain')],
        cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0 and node in ran.stdout.splitlines(), ran.stdout + ran.stderr
    token = repo_tests._COLLECTION_INVENTORIES.set({root.resolve(): list(files)})
    try:
        sources, certain = repo_tests._collection_evidence(root)
    finally:
        repo_tests._COLLECTION_INVENTORIES.reset(token)
    assert set(sources) == set(files)
    return 'EFFECT=True' in ran.stdout.splitlines(), certain


@pytest.mark.parametrize('route', ['declared-control', 'alias', 'test-attribute'])
def test_class_collection_uses_binding_name(tmp_path, route):
    name = 'TestSettings' if route == 'declared-control' else 'Settings'
    source = (PRELUDE + 'class ' + name + ':\n'
              '    def __getattribute__(self, name):\n        install()\n'
              '        return object.__getattribute__(self, name)\n'
              '    def test_call(self): pass\n')
    if route == 'alias':
        source += 'TestSettings = Settings\n'
    elif route == 'test-attribute':
        source = source.replace('class Settings:\n', 'class Settings:\n    __test__ = True\n')
    collected_name = 'Settings' if route == 'test-attribute' else 'TestSettings'
    wrote, certain = collected(tmp_path, {'test_api.py': source}, 'test_api.py::' + collected_name + '::test_call')
    assert wrote, 'precondition: pytest ran the instance lookup hook during collection'
    assert certain is False, 'unfollowed class instance lookup was read certain'


@pytest.mark.parametrize('method', ['test_call', 'runTest'], ids=['named-control', 'runTest'])
def test_unittest_fallback_method_is_collected(tmp_path, method):
    source = (PRELUDE + 'from unittest import TestCase\nclass Settings(TestCase):\n'
              '    def addTypeEqualityFunc(self, typeobj, function):\n        install()\n'
              '    def ' + method + '(self): pass\n')
    wrote, certain = collected(tmp_path, {'test_api.py': source}, 'test_api.py::Settings::' + method)
    assert wrote, 'precondition: pytest ran the unittest constructor callback during collection'
    assert certain is False, 'unfollowed unittest constructor callback was read certain'


@pytest.mark.parametrize('explicit', [False, True], ids=['decorator-control', 'constructor'])
@pytest.mark.parametrize('consumer', ['pytest', 'dataclass'])
def test_metaclass_property_constructor_is_a_descriptor(tmp_path, explicit, consumer):
    attribute = 'setup_class' if consumer == 'pytest' else 'value'
    if explicit:
        member = ('    def load(cls):\n        install()\n        return None\n'
                  '    ' + attribute + ' = property(load)\n')
    else:
        member = ('    @property\n    def ' + attribute + '(cls):\n'
                  '        install()\n        return None\n')
    source = PRELUDE + 'class Meta(type):\n' + member
    if consumer == 'pytest':
        source += 'class TestSettings(metaclass=Meta):\n    def test_call(self): pass\n'
        files, node = {'test_api.py': source}, 'test_api.py::TestSettings::test_call'
    else:
        source += ('from dataclasses import dataclass\n@dataclass\n'
                   'class Settings(metaclass=Meta):\n    value: int\n')
        files, node = {'conftest.py': source}, 'test_api.py::test_call'
    wrote, certain = collected(tmp_path, files, node)
    assert wrote, 'precondition: collection ran the metaclass property getter'
    assert certain is False, 'unfollowed metaclass property getter was read certain'


@pytest.mark.parametrize('imported', [False, True], ids=['module-control', 'imported-module'])
def test_pytest_looks_up_attributes_on_imported_modules(tmp_path, imported):
    lazy = PRELUDE + 'def __getattr__(name):\n    install()\n    raise AttributeError(name)\n'
    if imported:
        files = {'helper.py': lazy, 'test_api.py': 'import helper\ndef test_call(): pass\n'}
    else:
        files = {'test_api.py': lazy + 'def test_call(): pass\n'}
    wrote, certain = collected(tmp_path, files)
    assert wrote, 'precondition: pytest ran the bound module lookup hook during collection'
    assert certain is False, 'unfollowed imported module lookup was read certain'
