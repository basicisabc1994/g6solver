"""Honest import-time effects, compared with real pytest on disk."""
import os
import subprocess
import sys
import time

import pytest

from g6.harness import patch_review
from tests._review_changemap1.test_review import repository
from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, oracle, scenario
from tests.harness.test_change_map import target

NODE = 'test_api.py::test_call'
WRITE = "os.environ['PATH'] = 'local'"


def checked(tmp_path, source, *, extra=None, selection=NODE):
    test = 'import os\n' + HONEST_TEST.replace(
        '    assert neighbour()', "    assert os.environ['PATH'] == 'local'\n    assert neighbour()")
    root = tmp_path / 'repo'
    found = scenario({'root': str(root), 'api': HONEST_API, 'selection': selection,
                      'files': {'test_api.py': test, 'conftest.py': source, **(extra or {})}})
    plain = subprocess.run([sys.executable, '-B', '-m', 'pytest', '--collect-only', '-q',
        '-p', 'no:cacheprovider', '--confcutdir', str(root), '--rootdir', str(root),
        '--basetemp', str(tmp_path / 'plain'), selection], cwd=root,
        env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
        capture_output=True, text=True, timeout=30)
    assert plain.returncode == 0 and NODE in plain.stdout.splitlines(), plain.stdout + plain.stderr
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE] and '1 passed' in found['tail'][0], found
    return found


def unavailable(found):
    sibling = found['sibling']
    state = (found['authority'], sibling['exercised'], sibling['collection'])
    assert state == (False, 'uncertain', 'uncertain'), state


@pytest.mark.parametrize('kind', ['get', 'set', 'property'])
@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'implicit'])
def test_descriptor(tmp_path, implicit, kind):
    if kind == 'property':
        source = ("import os\nclass Settings:\n    @property\n    def value(self):\n"
                  f"        {WRITE}\n        return 1\n")
        source += 'VALUE = Settings().value\n' if implicit else f'{WRITE}\n'
    else:
        method, args = ('__get__', 'instance, owner') if kind == 'get' else ('__set__', 'instance, value')
        source = (f"import os\nclass Descriptor:\n    def {method}(self, {args}):\n"
                  f"        {WRITE}\n        return 1\nclass Settings:\n    value = Descriptor()\n")
        source += (('VALUE = Settings.value\n' if kind == 'get' else 'Settings().value = 1\n')
                   if implicit else f'Descriptor.{method}(None, None, Settings)\n')
    unavailable(checked(tmp_path, source))


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'class-getitem'])
def test_class_getitem(tmp_path, implicit):
    source = ("import os\nclass Settings:\n    def __class_getitem__(cls, key):\n"
              f"        {WRITE}\n        return cls\n")
    source += 'VALUE = Settings[int]\n' if implicit else 'Settings.__class_getitem__(int)\n'
    unavailable(checked(tmp_path, source))


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'enum-member'])
def test_enum_member(tmp_path, implicit):
    if implicit:
        source = ("import os\nfrom enum import Enum\nclass Settings(Enum):\n    VALUE = 1\n"
                  f"    def __init__(self, value):\n        {WRITE}\n")
    else:
        source = ("import os\nclass Settings:\n    def __init__(self, value):\n"
                  f"        {WRITE}\nSettings(1)\n")
    unavailable(checked(tmp_path, source))


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'module-getattr'])
def test_module_getattr(tmp_path, implicit):
    helper = ("import os\ndef __getattr__(name):\n"
              "    if name == '__path__':\n        raise AttributeError(name)\n"
              f"    {WRITE}\n    return 1\n")
    source = 'from helper import VALUE\n' if implicit else 'import helper\nhelper.__getattr__("VALUE")\n'
    unavailable(checked(tmp_path, source, extra={'helper.py': helper}))


@pytest.mark.parametrize('star', [False, True], ids=['control', 'star-import'])
def test_star_import(tmp_path, star):
    helper = f"import os\n__all__ = ['configure']\ndef configure():\n    {WRITE}\n"
    source = ('from helper import *\n' if star else 'from helper import configure\n') + 'configure()\n'
    unavailable(checked(tmp_path, source, extra={'helper.py': helper}))


@pytest.mark.parametrize('scope', ['global', 'nonlocal'])
@pytest.mark.parametrize('rebind', [False, True], ids=['control', 'rebind'])
def test_redirected_rebind(tmp_path, rebind, scope):
    source = f"import os\ndef active():\n    {WRITE}\n"
    if rebind:
        source += ("def configure(): pass\ndef install():\n    global configure\n"
                   "    configure = active\ninstall()\nconfigure()\n")
    else:
        source += 'active()\n'
    if scope == 'nonlocal' and rebind:
        source = ('import os\ndef outer():\n' + '\n'.join('    ' + line for line in
                  source.removeprefix('import os\n').replace('global configure', 'nonlocal configure').splitlines())
                  + '\nouter()\n')
    unavailable(checked(tmp_path, source))


@pytest.mark.parametrize('decorated', [False, True], ids=['control', 'identity-decorator'])
def test_inert_class_decorator(tmp_path, decorated):
    source = 'def identity(cls):\n    return cls\n'
    source += '@identity\n' if decorated else ''
    source += 'class Settings:\n    def __init__(self):\n        self.value = 1\nSETTINGS = Settings()\n'
    # checked() requires a runner write; this scenario is entirely passive.
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API, 'selection': NODE,
                      'files': {'test_api.py': HONEST_TEST, 'conftest.py': source}})
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE] and '1 passed' in found['tail'][0], found
    assert found['authority'] is True, found
    assert found['sibling']['exercised'] is True, found


@pytest.mark.parametrize('decorated', [False, True], ids=['control', 'returned-wrapper'])
def test_function_decorator_result(tmp_path, decorated):
    if decorated:
        source = ("import os\ndef decorate(function):\n    def wrapper():\n"
                  f"        {WRITE}\n        return function()\n    return wrapper\n"
                  "@decorate\ndef setup(): pass\nsetup()\n")
    else:
        source = f"import os\ndef setup():\n    {WRITE}\nsetup()\n"
    unavailable(checked(tmp_path, source))


@pytest.mark.parametrize('package', [True, False], ids=['control', 'namespace-package'])
def test_namespace_helper_end_to_end(tmp_path, package):
    root = tmp_path / 'repo'
    before = {'ns/helper.py': HONEST_API,
              'ns/setup.py': 'from .helper import changed\nVALUE = 1\n',
              'conftest.py': 'from ns import setup\n',
              'test_api.py': HONEST_TEST.replace('from api', 'from ns.helper')}
    if package:
        before['ns/__init__.py'] = ''
    delta = repository(root, before, {**before, 'ns/helper.py': HONEST_API.replace('return 1', 'return 2')})
    plain = subprocess.run([sys.executable, '-B', '-m', 'pytest', '--collect-only', '-q',
        '-p', 'no:cacheprovider', '--confcutdir', str(root), '--rootdir', str(root),
        '--basetemp', str(tmp_path / 'plain'), NODE], cwd=root,
        env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
        capture_output=True, text=True, timeout=30)
    assert plain.returncode == 0 and NODE in plain.stdout.splitlines(), plain.stdout + plain.stderr
    collected, answer = oracle(root, NODE)
    ran, _ = oracle(root, NODE, collect_only=False)
    assert collected.returncode == ran.returncode == 0 and '1 passed' in ran.stdout, ran.stdout
    assert answer['nodes'] == [NODE], answer
    record = patch_review._change_neighbourhood(
        delta, (NODE,), (), (), deadline=time.monotonic() + 60,
        collector=lambda *args, **kwargs: answer)
    sibling = target(record, 'neighbour')
    assert sibling['file'] == 'ns/helper.py', sibling
    assert sibling['exercised'] is True and sibling['checks'] == [NODE], sibling
