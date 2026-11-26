import os
import subprocess
import sys
import time

import pytest

from g6.core import component_process
from g6.harness import patch_review
from g6.workforce import repo_tests
from tests._review_changemap1.test_review import repository
from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, oracle
from tests.harness.test_change_map import target

NODE = 'test_api.py::test_call'
WRITE = "os.environ['PATH'] = 'local'"


def checked(tmp_path, source, *, active=False, extra=None):
    test = 'import os\n' + HONEST_TEST.replace('from api', 'from pkg.helper')
    comparison = '==' if active else '!='
    test = test.replace('    assert neighbour()',
                        f"    assert os.environ['PATH'] {comparison} 'local'\n    assert neighbour()")
    root = tmp_path / 'repo'
    before = {'pkg/__init__.py': '', 'pkg/helper.py': HONEST_API,
              'test_api.py': test, 'conftest.py': source, **(extra or {})}
    delta = repository(root, before, {**before, 'pkg/helper.py': HONEST_API.replace('return 1', 'return 2')})
    plain = subprocess.run([sys.executable, '-B', '-m', 'pytest', '--collect-only', '-q',
        '-p', 'no:cacheprovider', '--confcutdir', str(root), '--rootdir', str(root),
        '--basetemp', str(tmp_path / 'plain'), NODE], cwd=root,
        env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
        capture_output=True, text=True, timeout=30)
    assert plain.returncode == 0 and NODE in plain.stdout.splitlines(), plain.stdout + plain.stderr
    authority = repo_tests._collection_evidence(root)[1]
    collected, answer = oracle(root, NODE)
    ran, _ = oracle(root, NODE, collect_only=False)
    record = patch_review._change_neighbourhood(
        delta, (NODE,), (), (), deadline=time.monotonic() + 60,
        collector=lambda *a, **k: answer)
    sibling = target(record, 'neighbour')
    found = {'authority': authority, 'collect_exit': collected.returncode, 'exit': ran.returncode,
             'answer': answer, 'sibling': sibling, 'tail': ran.stdout.strip().splitlines()[-1:]}
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE] and '1 passed' in found['tail'][0], found
    assert sibling['file'] == 'pkg/helper.py', found
    if authority:
        assert NODE in answer.get('codes', {}) and not answer.get('unresolved'), found
        assert sibling['exercised'] is True and sibling['checks'] == [NODE], found
        assert sibling['collection'] == 'available', found
    else:
        assert answer.get('unresolved') == [NODE] and not answer.get('codes'), found
        assert sibling['exercised'] == sibling['collection'] == 'uncertain', found
    return found


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'dataclass-descriptor'])
def test_dataclass_descriptor_default(tmp_path, implicit):
    source = ('import os\nfrom dataclasses import dataclass\nclass Descriptor:\n'
              '    def __get__(self, instance, owner):\n'
              f'        {WRITE}\n        return 1\n')
    if implicit:
        source += '@dataclass\nclass Settings:\n    value: int = Descriptor()\n'
    else:
        source += 'Descriptor.__get__(None, None, None)\n'
    found = checked(tmp_path, source, active=True)
    assert found['authority'] is False, found
    assert found['sibling']['exercised'] == 'uncertain', found


@pytest.mark.parametrize('decorated', [False, True], ids=['control', 'wraps'])
def test_wraps_on_inert_helper(tmp_path, decorated):
    source = 'from functools import wraps\ndef original(): pass\n'
    source += '@wraps(original)\n' if decorated else ''
    source += 'def configure(): pass\nconfigure()\n'
    found = checked(tmp_path, source)
    assert found['authority'] is True, found
    assert found['sibling']['exercised'] is True, found


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'inherited-metaclass'])
def test_inherited_metaclass_getattribute(tmp_path, implicit):
    source = ('import os\nclass Meta(type):\n'
              '    def __getattribute__(cls, name):\n'
              f'        {WRITE}\n        return type.__getattribute__(cls, name)\n'
              'class Base(metaclass=Meta): pass\n'
              'class Settings(Base):\n    def configure(): pass\n')
    source += 'Settings.configure()\n' if implicit else 'Meta.__getattribute__(Settings, "configure")\n'
    found = checked(tmp_path, source, active=True)
    assert found['authority'] is False, found
    assert found['sibling']['exercised'] == 'uncertain', found


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'inherited-metaclass-call'])
def test_inherited_metaclass_call(tmp_path, implicit):
    source = ('import os\nclass Meta(type):\n'
              '    def __call__(cls):\n'
              f'        {WRITE}\n        return None\n'
              'class Base(metaclass=Meta): pass\n'
              'class Settings(Base): pass\n')
    source += 'Settings()\n' if implicit else 'Meta.__call__(Settings)\n'
    found = checked(tmp_path, source, active=True)
    assert found['authority'] is False, found


@pytest.mark.parametrize('default', [False, True], ids=['control', 'default-argument'])
def test_known_argument(tmp_path, default):
    source = ('def configure(value=1):\n    return value + 1\nconfigure()\n' if default
              else 'def configure():\n    return 1 + 1\nconfigure()\n')
    found = checked(tmp_path, source)
    assert found['authority'] is True, found


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'reduce-callback'])
def test_external_callback(tmp_path, implicit):
    source = ('import os\nfrom functools import reduce\ndef configure(left, right):\n'
              f'    {WRITE}\n    return right\n')
    source += 'reduce(configure, [1, 2])\n' if implicit else 'configure(1, 2)\n'
    found = checked(tmp_path, source, active=True)
    assert found['authority'] is False, found


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'runpy-main'])
def test_external_module_execution(tmp_path, implicit):
    cli = 'import os\nif __name__ == "__main__":\n    ' + WRITE + '\n'
    source = 'import runpy\nrunpy.run_module("cli", run_name="__main__")\n' if implicit else 'import os\n' + WRITE + '\n'
    found = checked(tmp_path, source, active=True, extra={'cli.py': cli})
    assert found['authority'] is False, found


@pytest.mark.parametrize('implicit', [False, True], ids=['control', 'extend-iterator'])
def test_owned_list_extend(tmp_path, implicit):
    source = ('import os\nclass Values:\n    def __iter__(self):\n'
              f'        {WRITE}\n        return iter(())\nitems = []\n')
    source += 'items.extend(Values())\n' if implicit else 'Values.__iter__(None)\n'
    found = checked(tmp_path, source, active=True)
    assert found['authority'] is False, found


@pytest.mark.parametrize('future', [False, True], ids=['control', 'postponed'])
def test_postponed_annotation(tmp_path, future):
    source = 'import os\ndef annotation():\n    ' + WRITE + '\n    return int\n'
    if future:
        source = 'from __future__ import annotations\n' + source + 'def configure(value: annotation()): pass\n'
    else:
        source += 'def configure(value: int): pass\n'
    found = checked(tmp_path, source)
    assert found['authority'] is True, found


@pytest.mark.parametrize('hook', ['__init_subclass__', '__prepare__', '__set_name__'])
def test_imported_hooks_stay_unavailable(tmp_path, hook):
    if hook == '__init_subclass__':
        helper = 'import os\nclass Base:\n    def __init_subclass__(cls):\n        ' + WRITE + '\n'
        source = 'from hooks import Base\nclass Settings(Base): pass\n'
    elif hook == '__prepare__':
        helper = ('import os\nclass Base(type):\n    @classmethod\n'
                  '    def __prepare__(cls, name, bases):\n        ' + WRITE + '\n        return {}\n')
        source = 'from hooks import Base\nclass Meta(Base): pass\nclass Settings(metaclass=Meta): pass\n'
    else:
        helper = ('import os\nclass Base:\n    def __set_name__(self, owner, name):\n        ' + WRITE + '\n')
        source = 'from hooks import Base\nclass Descriptor(Base): pass\nclass Settings:\n    value = Descriptor()\n'
    assert checked(tmp_path, source, active=True, extra={'hooks.py': helper})['authority'] is False


def test_inert_conditional_import_candidates(tmp_path):
    source = ('import sys\nif sys.platform == "win32":\n    from one import configure\n'
              'else:\n    from two import configure\nrun = configure\nrun()\n')
    assert checked(tmp_path, source, extra={'one.py': 'def configure(): pass\n',
                                          'two.py': 'def configure(): pass\n'})['authority'] is True


def test_unresolved_import_candidate(tmp_path):
    source = ('import sys\nif sys.platform == "win32":\n    from one import configure\n'
              'else:\n    from one import missing as configure\nconfigure()\n')
    assert checked(tmp_path, source, extra={'one.py': 'def configure(): pass\n'})['authority'] is False


def test_namespace_relative_import(tmp_path):
    assert checked(tmp_path, 'from ns.pkg import setup\n', extra={
        'ns/pkg/__init__.py': '', 'ns/pkg/setup.py': 'from ..helper import VALUE\n',
        'ns/helper.py': 'VALUE = 1\n'})['authority'] is True


@pytest.mark.parametrize('redirect', ['global', 'nonlocal'])
def test_redirected_binding(tmp_path, redirect):
    source = ('def active():\n    ' + WRITE + '\ndef configure(): pass\n'
              'def install():\n    global configure\n    configure = active\ninstall()\nconfigure()\n')
    if redirect == 'nonlocal':
        source = 'def outer():\n' + '\n'.join('    ' + line for line in
                 source.replace('global configure', 'nonlocal configure').splitlines()) + '\nouter()\n'
    assert checked(tmp_path, 'import os\n' + source, active=True)['authority'] is False


def inventory(root, files):
    root.mkdir()
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    token = repo_tests._COLLECTION_INVENTORIES.set({root.resolve(): list(files)})
    try:
        return repo_tests._collection_evidence(root)[1]
    finally:
        repo_tests._COLLECTION_INVENTORIES.reset(token)


def test_depth_deadline_and_delete(tmp_path):
    chain = '\n'.join(f'def step{i}():\n    step{i + 1}()' for i in range(40))
    assert inventory(tmp_path / 'deep', {'conftest.py': chain + '\ndef step40(): pass\nstep0()\n'}) is False
    assert inventory(tmp_path / 'delete', {'conftest.py': 'value = 1\ndel value\n'}) is False
    with component_process.window(0), pytest.raises(component_process.ExecutionInterrupted):
        inventory(tmp_path / 'deadline', {'test_api.py': 'def test_call(): pass\n'})


@pytest.mark.parametrize('mode', ['collected', 'static'])
def test_helper_change_without_context(tmp_path, mode):
    root = tmp_path / 'repo'
    before = {'pkg/__init__.py': '', 'pkg/helper.py': HONEST_API,
              'checks.py': HONEST_TEST.replace('from api', 'from pkg.helper')}
    delta = repository(root, before, {**before, 'pkg/helper.py': HONEST_API.replace('return 1', 'return 2')})
    node = 'checks.py::test_call'
    plain = subprocess.run([sys.executable, '-B', '-m', 'pytest', '--collect-only', '-q',
        '-p', 'no:cacheprovider', '--confcutdir', str(root), '--rootdir', str(root), node], cwd=root,
        env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, capture_output=True, text=True, timeout=30)
    assert plain.returncode == 0 and node in plain.stdout.splitlines(), plain.stdout + plain.stderr
    collected, answer = oracle(root, node)
    assert collected.returncode == 0 and answer['nodes'] == [node] and node in answer['codes'], answer
    if mode == 'static':
        answer = {'status': 'uncertain', 'reason': 'collection unavailable'}
    record = patch_review._change_neighbourhood(delta, (node,), (), (), deadline=time.monotonic() + 60,
                                               collector=lambda *a, **k: answer)
    sibling = target(record, 'neighbour')
    assert sibling['file'] == 'pkg/helper.py' and sibling['checks'] == [node] and sibling['exercised'] is True, sibling
    assert sibling['collection'] == ('not_required' if mode == 'static' else 'available'), sibling
