"""Focused suspicions that remain correctly unavailable or determined."""
import os
import subprocess
import time

import pytest

from g6.core import component_process
from g6.harness import patch_review
from g6.workforce import repo_tests
from tests._review_changemap1.test_review import repository
from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, oracle
from tests.harness.test_change_map import target


def inventory(root, files, **kwargs):
    root.mkdir(parents=True)
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    token = repo_tests._COLLECTION_INVENTORIES.set({root.resolve(): list(files)})
    try:
        return repo_tests._collection_evidence(root, **kwargs)[1]
    finally:
        repo_tests._COLLECTION_INVENTORIES.reset(token)


@pytest.mark.parametrize('hook', ['__init_subclass__', '__set_name__', '__prepare__'])
def test_imported_inherited_hooks(tmp_path, hook):
    if hook == '__init_subclass__':
        helper = "import os\nclass Base:\n    def __init_subclass__(cls):\n        os.environ['PATH'] = 'local'\n"
        source = 'from helper import Base\nclass Child(Base): pass\n'
    elif hook == '__set_name__':
        helper = "import os\nclass Base:\n    def __set_name__(self, owner, name):\n        os.environ['PATH'] = 'local'\n"
        source = 'from helper import Base\nclass Descriptor(Base): pass\nclass Settings:\n    value = Descriptor()\n'
    else:
        helper = ("import os\nclass Base(type):\n    @classmethod\n"
                  "    def __prepare__(cls, name, bases):\n        os.environ['PATH'] = 'local'\n        return {}\n")
        source = 'from helper import Base\nclass Meta(Base): pass\nclass Settings(metaclass=Meta): pass\n'
    assert inventory(tmp_path / 'repo', {'helper.py': helper, 'conftest.py': source}) is False


def test_every_inert_import_candidate(tmp_path):
    assert inventory(tmp_path / 'repo', {
        'one.py': 'def setup(): pass\n', 'two.py': 'def setup(): pass\n',
        'conftest.py': 'import sys\nif sys.platform == "win32":\n    from one import setup\nelse:\n    from two import setup\nrun = setup\nrun()\n',
    }) is True


def test_depth_and_deadline(tmp_path):
    chain = '\n'.join(f'def step{i}():\n    return step{i + 1}()' for i in range(40))
    chain += '\ndef step40(): pass\nstep0()\n'
    assert inventory(tmp_path / 'deep', {'conftest.py': chain}) is False
    with component_process.window(0), pytest.raises(component_process.ExecutionInterrupted):
        inventory(tmp_path / 'deadline', {'test_api.py': 'def test_call(): pass\n'})


def test_wrapped_effect_not_admitted(tmp_path):
    source = ("import os\nfrom functools import wraps\ndef decorate(function):\n"
              "    @wraps(function)\n    def wrapper():\n        os.environ['PATH'] = 'local'\n"
              "        return function()\n    return wrapper\n@decorate\ndef setup(): pass\nsetup()\n")
    assert inventory(tmp_path / 'repo', {'conftest.py': source}) is False


@pytest.mark.parametrize('mode', ['collected', 'static'])
def test_helper_change_full_path(tmp_path, mode):
    root = tmp_path / 'repo'
    before = {'helper.py': HONEST_API, 'api.py': 'from helper import neighbour\n',
              'checks.py': HONEST_TEST}
    delta = repository(root, before, {**before, 'helper.py': HONEST_API.replace('return 1', 'return 2')})
    node = 'checks.py::test_call'
    collected, answer = oracle(root, node)
    ran, _ = oracle(root, node, collect_only=False)
    assert collected.returncode == ran.returncode == 0 and '1 passed' in ran.stdout
    assert answer['nodes'] == [node] and answer['codes'].get(node), answer
    if mode == 'static':
        answer = {'status': 'uncertain', 'reason': 'collection unavailable'}
    record = patch_review._change_neighbourhood(delta, (node,), (), (), deadline=time.monotonic() + 60,
                                               collector=lambda *a, **k: answer)
    sibling = target(record, 'neighbour')
    assert sibling['file'] == 'helper.py' and sibling['exercised'] is True, sibling
    assert sibling['checks'] == [node], sibling
    assert sibling['collection'] == ('not_required' if mode == 'static' else 'available'), sibling


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction boundary')
def test_junction_containment(tmp_path):
    root, outside = tmp_path / 'repo', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'test_api.py').write_text('def test_call(): pass\n', encoding='utf-8')
    made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(root / 'linked'), str(outside)],
                          capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    token = repo_tests._COLLECTION_INVENTORIES.set({root.resolve(): ['linked/test_api.py']})
    try:
        assert repo_tests._collection_evidence(root)[1] is False
    finally:
        repo_tests._COLLECTION_INVENTORIES.reset(token)
