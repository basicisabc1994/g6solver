"""Real-disk, honest-code collection identity review regressions."""
import os
import subprocess
import sys
import time

import pytest

from g6.workforce import repo_tests
from g6.core import component_process
from g6.harness import patch_review
from tests._review_changemap1.test_review import repository
from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, oracle, scenario
from tests.harness.test_change_map import target

NODE = 'test_api.py::test_call'


def state(found):
    answer, sibling = found.get('answer', {}), found['sibling']
    return {'authority': found.get('authority'), 'nodes': answer.get('nodes'),
            'unresolved': answer.get('unresolved'), 'codes': sorted(answer.get('codes', {})),
            'exercised': sibling['exercised'], 'checks': sibling['checks'],
            'collection': sibling['collection']}


@pytest.mark.parametrize('folder', ['nested', 'src'], ids=['control', 'src-package'])
def test_root_package_relative_depth(tmp_path, folder):
    found = scenario({
        'root': str(tmp_path / 'repo'), 'api': HONEST_API,
        'files': {
            '__init__.py': f'from .{folder}.pkg import helper\n',
            f'{folder}/__init__.py': '',
            f'{folder}/pkg/__init__.py': '',
            f'{folder}/pkg/helper.py': 'from ... import values\nVALUE = values.VALUE\n',
            'values.py': 'VALUE = 2\n',
            'test_api.py': HONEST_TEST,
        },
    })
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE], found
    assert '1 passed' in found['tail'][0], found
    assert found['authority'] is True, state(found)
    assert found['sibling']['exercised'] is True, state(found)


@pytest.mark.parametrize('folder', ['nested', 'src'], ids=['control', 'src-package'])
def test_root_package_relative_import_follows_actual_helper(tmp_path, folder):
    found = scenario({
        'root': str(tmp_path / 'repo'), 'api': HONEST_API,
        'files': {
            '__init__.py': f'from .{folder}.pkg import helper\n',
            f'{folder}/__init__.py': "import os\ndef configure():\n    os.environ['PATH'] = 'local'\n",
            f'{folder}/pkg/__init__.py': '',
            f'{folder}/pkg/helper.py': 'from .. import configure\nconfigure()\n',
            'test_api.py': "import os\n" + HONEST_TEST.replace(
                '    assert neighbour()', "    assert os.environ['PATH'] == 'local'\n    assert neighbour()"),
        },
    })
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE], found
    assert '1 passed' in found['tail'][0], found
    assert found['authority'] is False, state(found)
    assert found['sibling']['exercised'] == 'uncertain', state(found)


def inventory(root, files, *, selections=(), roots=()):
    root.mkdir(parents=True)
    for file, source in files.items():
        path = root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    token = repo_tests._COLLECTION_INVENTORIES.set({root.resolve(): list(files)})
    try:
        return repo_tests._collection_evidence(root, selections=selections, roots=roots)
    finally:
        repo_tests._COLLECTION_INVENTORIES.reset(token)


@pytest.mark.parametrize('conditional', [False, True], ids=['control', 'platform-branch'])
def test_conditional_setup_keeps_runner_write_unavailable(tmp_path, conditional):
    source = "import os\nimport sys\ndef configure():\n    os.environ['PATH'] = 'local'\n"
    if conditional:
        source = ("import os\nimport sys\nif sys.platform == 'win32':\n"
                  "    def configure():\n        os.environ['PATH'] = 'local'\n"
                  "else:\n    def configure():\n        pass\n")
    source += 'configure()\n'
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'test_api.py': path_test(), 'conftest.py': source}})
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE], found
    assert '1 passed' in found['tail'][0], found
    assert found['authority'] is False, state(found)
    assert found['sibling']['exercised'] == 'uncertain', state(found)


@pytest.mark.parametrize('dataclass', [False, True], ids=['control', 'dataclass'])
def test_generated_constructor_keeps_runner_write_unavailable(tmp_path, dataclass):
    header = 'from dataclasses import dataclass\n@dataclass\n' if dataclass else ''
    method = '__post_init__' if dataclass else '__init__'
    source = (f"import os\n{header}class Settings:\n    def {method}(self):\n"
              "        os.environ['PATH'] = 'local'\nSETTINGS = Settings()\n")
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'test_api.py': path_test(), 'conftest.py': source}})
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE], found
    assert '1 passed' in found['tail'][0], found
    assert found['authority'] is False, state(found)
    assert found['sibling']['exercised'] == 'uncertain', state(found)


def path_test():
    return "import os\n" + HONEST_TEST.replace(
        '    assert neighbour()', "    assert os.environ['PATH'] == 'local'\n    assert neighbour()")


@pytest.mark.parametrize('prepare', [False, True], ids=['control', 'prepare'])
def test_metaclass_prepare_keeps_runner_write_unavailable(tmp_path, prepare):
    method = ("@classmethod\n    def __prepare__(mcls, name, bases):\n"
              "        os.environ['PATH'] = 'local'\n        return {}\n") if prepare else (
              "def __new__(mcls, name, bases, attrs):\n"
              "        os.environ['PATH'] = 'local'\n"
              "        return type.__new__(mcls, name, bases, attrs)\n")
    source = f'import os\nclass Meta(type):\n    {method}class Settings(metaclass=Meta):\n    pass\n'
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'test_api.py': path_test(), 'conftest.py': source}})
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE], found
    assert '1 passed' in found['tail'][0], found
    assert found['authority'] is False, state(found)
    assert found['sibling']['exercised'] == 'uncertain', state(found)


@pytest.mark.parametrize('mode', ['collected', 'static'])
def test_control_helper_change_through_collection_and_map(tmp_path, mode):
    root = tmp_path / 'repo'
    before = {'helper.py': HONEST_API, 'api.py': 'from helper import neighbour\n',
              'checks.py': HONEST_TEST}
    delta = repository(root, before, {**before, 'helper.py': HONEST_API.replace('return 1', 'return 2')})
    node = 'checks.py::test_call'
    plain = subprocess.run([sys.executable, '-B', '-m', 'pytest', '--collect-only', '-q',
        '-p', 'no:cacheprovider', '--confcutdir', str(root), '--rootdir', str(root),
        '--basetemp', str(tmp_path / 'plain-pytest'), node], cwd=root,
        env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
        capture_output=True, text=True, timeout=30)
    assert plain.returncode == 0 and node in plain.stdout.splitlines(), plain.stdout + plain.stderr
    collected, answer = oracle(root, node)
    ran, _ = oracle(root, node, collect_only=False)
    assert collected.returncode == ran.returncode == 0, collected.stdout + ran.stdout
    assert answer['nodes'] == [node] and answer['codes'].get(node), answer
    assert '1 passed' in ran.stdout, ran.stdout
    if mode == 'static':
        answer = {'status': 'uncertain', 'reason': 'collection unavailable'}
    record = patch_review._change_neighbourhood(
        delta, (node,), (), (), deadline=time.monotonic() + 60,
        collector=lambda *args, **kwargs: answer)
    sibling = target(record, 'neighbour')
    assert sibling['file'] == 'helper.py' and sibling['exercised'] is True, record
    assert sibling['checks'] == [node], record
    assert sibling['collection'] == ('not_required' if mode == 'static' else 'available'), record


@pytest.mark.parametrize('key,expected', [
    ('PATH', False), ('path', False), ('LD_PRELOAD', False), ('PYTEST_ADDOPTS', False),
    ('PYTHONPATH', False), ('', False), ('A=B', False), ('APP_ENV', True), ('PYTESTAPP', True),
])
@pytest.mark.parametrize('write', ["os.environ[{key!r}] = 'local'",
                                  "os.environ.setdefault({key!r}, 'local')",
                                  "os.environ.update({{{key!r}: 'local'}})"])
def test_control_constant_environment_keys(tmp_path, key, expected, write):
    _, usable = inventory(tmp_path / 'repo', {'conftest.py': 'import os\n' + write.format(key=key),
                                             'test_api.py': 'def test_call(): pass\n'})
    assert usable is expected


@pytest.mark.parametrize('source', ['from . import helper\n', 'from .helper import VALUE\n'])
def test_control_invalid_import_stays_loaded(tmp_path, source):
    sources, usable = inventory(tmp_path / 'repo', {'__init__.py': source,
        'helper.py': 'print "old"\n', 'test_api.py': 'def test_call(): pass\n'})
    assert usable is False and sources.not_read == []


def test_control_not_read_and_fixture_params(tmp_path):
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API, 'files': {
        'test_api.py': HONEST_TEST.replace('test_call()', 'test_call(value)'),
        'conftest.py': 'import pytest\n@pytest.fixture(params=[1, 2])\ndef value(request): return request.param\n',
        'examples/old script.py': 'print "old"\n'}})
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE + '[1]', NODE + '[2]'], found
    assert '2 passed' in found['tail'][0], found
    assert found['answer']['not_read'] == [{'file': 'examples/old script.py',
        'reason': 'source does not parse and is not loaded by collection'}], found
    assert found['authority'] is True and found['sibling']['exercised'] is True, state(found)


@pytest.mark.parametrize('entry', [
    ('conftest.py', 'pytest_plugins = []\n'),
    ('conftest.py', 'collect_ignore = []\n'),
    ('conftest.py', 'def pytest_collection_modifyitems(items): pass\n'),
    ('conftest.py', 'import pytest\n@pytest.fixture(autouse=True)\ndef automatic(): pass\n'),
    *[('pytest.ini', '[pytest]\n' + option + '\n') for option in (
        'python_files = checks.py', 'python_classes = Check*', 'python_functions = check_*',
        'testpaths = checks', 'norecursedirs = ignored', 'addopts = -p no:terminal',
        'addopts = --rootdir=other', 'addopts = -c other.ini', 'unknown_key = yes')],
])
def test_control_collection_configuration(tmp_path, entry):
    name, source = entry
    _, usable = inventory(tmp_path / 'repo', {name: source, 'test_api.py': 'def test_call(): pass\n'})
    assert usable is False


def test_control_depth_cycle_and_deadline(tmp_path):
    chain = '\n'.join(f'def step{i}():\n    return step{i + 1}()' for i in range(40))
    chain += '\ndef step40():\n    return 2\nstep0()\n'
    for index, source in enumerate((chain, 'def step():\n    step()\nstep()\n')):
        assert inventory(tmp_path / str(index), {'test_api.py': source})[1] is False
    with component_process.window(0), pytest.raises(component_process.ExecutionInterrupted):
        inventory(tmp_path / 'deadline', {'test_api.py': 'def test_call(): pass\n'})


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction boundary')
def test_control_junction_outside_repository(tmp_path):
    root, outside = tmp_path / 'repo', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'test_api.py').write_text(HONEST_TEST, encoding='utf-8')
    made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(root / 'linked'), str(outside)],
                          capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    token = repo_tests._COLLECTION_INVENTORIES.set({root.resolve(): ['linked/test_api.py']})
    try:
        assert repo_tests._collection_evidence(root)[1] is False
    finally:
        repo_tests._COLLECTION_INVENTORIES.reset(token)


@pytest.mark.parametrize('file,source,expected', [
    ('__init__.py', 'from . import values\n', True),
    ('__init__.py', 'from .. import values\n', False),
    ('helper.py', 'from . import values\n', True),
    ('helper.py', 'from .. import values\n', False),
    ('pkg/__init__.py', 'from .. import values\n', True),
    ('pkg/mod.py', 'from . import values\n', True),
    ('pkg/deep/__init__.py', 'from ... import values\n', True),
    ('pkg/deep/mod.py', 'from ... import values\n', True),
    ('pkg/deep/mod.py', 'from .... import values\n', False),
])
def test_control_relative_import_levels(tmp_path, file, source, expected):
    files = {'__init__.py': '', 'pkg/__init__.py': '', 'pkg/deep/__init__.py': '',
             'values.py': 'VALUE = 2\n', 'pkg/values.py': 'VALUE = 2\n',
             'test_api.py': 'def test_call(): pass\n'}
    files[file] = source
    assert inventory(tmp_path / 'repo', files)[1] is expected


def test_control_non_test_body_in_explicit_filename(tmp_path):
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API, 'mode': 'static',
        'selection': 'checks.py::utility',
        'files': {'checks.py': HONEST_TEST.replace('test_call', 'utility')}})
    assert found['exit'] == 4, found
    assert found['sibling']['exercised'] == 'uncertain', state(found)
