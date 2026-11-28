"""Real-disk review 3 reproductions; no runner or confinement bypass."""
import os
import subprocess
import sys

import pytest

from g6.workforce import repo_tests


CHILD = """import os, sys, pytest
code = pytest.main(sys.argv[1:])
print('EFFECT=' + str(os.environ.get('PATH') == 'cmfu3-review3'))
sys.exit(int(code))
"""


def collect(tmp_path, files, node):
    root = tmp_path / 'repo'
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    ran = subprocess.run(
        [sys.executable, '-B', '-c', CHILD, '--collect-only', '-q', '-p', 'no:cacheprovider',
         '--confcutdir', str(root), '--rootdir', str(root),
         '--basetemp', str(tmp_path / 'plain')],
        cwd=root, env=dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
                          PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0 and node in ran.stdout.splitlines(), ran.stdout + ran.stderr
    token = repo_tests._COLLECTION_INVENTORIES.set({root.resolve(): list(files)})
    try:
        sources, certain = repo_tests._collection_evidence(root)
    finally:
        repo_tests._COLLECTION_INVENTORIES.reset(token)
    assert set(sources) == set(files)
    return 'EFFECT=True' in ran.stdout.splitlines(), certain


@pytest.mark.parametrize('route', ['alias-control', 'subscript', 'with-target'])
def test_collection_name_survives_value_binding(tmp_path, route):
    source = (
        "import os\nclass Settings:\n"
        "    def __getattribute__(self, name):\n"
        "        os.environ['PATH'] = 'cmfu3-review3'\n"
        "        return object.__getattribute__(self, name)\n"
        "    def test_call(self): pass\n")
    source += {
        'alias-control': 'TestSettings = Settings\n',
        'subscript': 'classes = [Settings]\nTestSettings = classes[0]\n',
        'with-target': ('from contextlib import nullcontext\n'
                        'with nullcontext(Settings) as TestSettings:\n    pass\n'),
    }[route]
    wrote, certain = collect(tmp_path, {'test_api.py': source},
                             'test_api.py::TestSettings::test_call')
    assert wrote, 'pytest must run the repository hook during collection'
    assert certain is False, 'collection hook ran but the reader answered certain'


@pytest.mark.parametrize('route', ['subscript', 'with-target'])
def test_inert_value_binding_control(tmp_path, route):
    source = 'class Settings:\n    def test_call(self): pass\n'
    source += ('classes = [Settings]\nTestSettings = classes[0]\n' if route == 'subscript' else
               'from contextlib import nullcontext\nwith nullcontext(Settings) as TestSettings:\n    pass\n')
    wrote, certain = collect(tmp_path, {'test_api.py': source},
                             'test_api.py::TestSettings::test_call')
    assert (wrote, certain) == (False, True)


@pytest.mark.parametrize('route', ['loop', 'conditional', 'star', 'reexport'])
def test_other_binding_routes_refuse_effect(tmp_path, route):
    body = (
        "import os\nclass Settings:\n"
        "    def __getattribute__(self, name):\n"
        "        os.environ['PATH'] = 'cmfu3-review3'\n"
        "        return object.__getattribute__(self, name)\n"
        "    def test_call(self): pass\n")
    files = {'test_api.py': body + {
        'loop': 'for TestSettings in [Settings]:\n    pass\n',
        'conditional': 'if True:\n    TestSettings = Settings\n',
        'star': '', 'reexport': '',
    }[route]}
    if route in ('star', 'reexport'):
        files = {'helper.py': body + "TestSettings = Settings\n__all__ = ['TestSettings']\n",
                 'test_api.py': ('from helper import *\n' if route == 'star' else
                                 'from exports import TestSettings\n')}
        if route == 'reexport':
            files['exports.py'] = 'from helper import TestSettings\n'
    assert collect(tmp_path, files, 'test_api.py::TestSettings::test_call') == (True, False)


@pytest.mark.parametrize('route', ['setUpClass', 'mixin-setUp', 'fixture-ids'])
def test_lifecycle_and_fixture_boundaries(tmp_path, route):
    effect = "os.environ['PATH'] = 'cmfu3-review3'"
    if route == 'fixture-ids':
        source = ('import os, pytest\ndef case_id(value):\n    ' + effect + '\n    return "case"\n'
                  '@pytest.fixture(params=[1], ids=case_id)\ndef value(request): return request.param\n'
                  'def test_call(value): pass\n')
        node, expected = 'test_api.py::test_call[case]', (True, False)
    else:
        source = 'import os\nfrom unittest import TestCase\n'
        if route == 'setUpClass':
            source += ('class Settings(TestCase):\n    @classmethod\n'
                       '    def setUpClass(cls):\n        ' + effect + '\n')
        else:
            source += ('class Mixin:\n    def setUp(self):\n        ' + effect + '\n'
                       'class Settings(Mixin, TestCase):\n')
        source += '    def runTest(self): pass\n'
        node, expected = 'test_api.py::Settings::runTest', (False, True)
    assert collect(tmp_path, {'test_api.py': source}, node) == expected


@pytest.mark.parametrize('route', ['container', 'default', 'closure'])
def test_indirect_repository_input_to_installed_call_is_refused(tmp_path, route):
    source = (
        'import os\nclass Meta(type):\n'
        '    def __getattr__(cls, name):\n'
        "        os.environ['PATH'] = 'cmfu3-review3'\n"
        '        raise AttributeError(name)\n'
        'class Settings(metaclass=Meta): pass\n')
    source += {
        'container': 'TestSettings = type([Settings()][0])\n',
        'default': ('def kind(value=Settings()): return type(value)\n'
                    'TestSettings = kind()\n'),
        'closure': ('def factory():\n    held = Settings()\n'
                    '    def kind(): return type(held)\n    return kind\n'
                    'kind = factory()\nTestSettings = kind()\n'),
    }[route]
    source += 'def test_call(): pass\n'
    assert collect(tmp_path, {'test_api.py': source}, 'test_api.py::test_call') == (True, False)
