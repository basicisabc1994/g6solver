"""Real-disk regressions for review 1 of claude/change-map-fu-2.

The shared scenario runs the shipped bootstrap with --collect-only -q, reads
its identities, builds the change map, and runs pytest again on the same repo.
Only api.changed changes; test_call really reaches its sibling api.neighbour.
"""
import os
import subprocess

import pytest

from g6.workforce import repo_tests
from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, scenario

NODE = 'test_api.py::test_call'


def state(found):
    return {
        'authority': found.get('authority'),
        'nodes': found.get('answer', {}).get('nodes'),
        'unresolved': found.get('answer', {}).get('unresolved'),
        'exercised': found['sibling']['exercised'],
        'checks': found['sibling']['checks'],
        'collection': found['sibling']['collection'],
    }


def collected_and_passed(found):
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [NODE], found
    assert '1 passed' in found['tail'][0], found


def package_spec():
    return {
        'api': HONEST_API,
        'selection': NODE,
        'files': {
            '__init__.py': 'from . import helper\n',
            'helper.py': 'VALUE = 2\n',
            'test_api.py': HONEST_TEST,
        },
    }


@pytest.mark.parametrize('relative', [False, True], ids=['empty-control', 'relative'])
def test_root_package_keeps_identity(tmp_path, relative):
    spec = package_spec()
    if not relative:
        spec['files']['__init__.py'] = ''
    found = scenario({**spec, 'root': str(tmp_path / 'repo')})
    collected_and_passed(found)
    assert found['authority'] is True, state(found)
    assert found['sibling']['exercised'] is True, state(found)
    assert found['sibling']['checks'] == [NODE], state(found)
    assert found['sibling']['collection'] == 'available', state(found)


@pytest.mark.parametrize('key', ['APP_ENV', 'PATH'], ids=['app-control', 'runner-PATH'])
def test_environment_admission_uses_runner_key_rule(tmp_path, key):
    found = scenario({
        'root': str(tmp_path / 'repo'),
        'api': HONEST_API,
        'selection': NODE,
        'files': {
            'test_api.py': HONEST_TEST,
            'conftest.py': f'import os\nos.environ[{key!r}] = "local"\n',
        },
    })
    collected_and_passed(found)
    expected = not repo_tests._runner_name(key)
    assert found['authority'] is expected, state(found)
    assert found['sibling']['exercised'] == (True if expected else 'uncertain'), state(found)

@pytest.mark.parametrize('config', [
    'python_files = check_*.py',
    'python_classes = Check*',
    'python_functions = check_*',
    'testpaths = checks',
    'norecursedirs = excluded',
    'addopts = -p no:terminal',
    'unknown_key = yes',
])
def test_control_collection_options_remain_unavailable(tmp_path, config):
    found = scenario({
        'root': str(tmp_path / 'repo'), 'api': HONEST_API,
        'files': {'test_api.py': HONEST_TEST, 'pytest.ini': '[pytest]\n' + config + '\n'},
    })
    assert found['authority'] is False, state(found)
    assert found['sibling']['exercised'] == 'uncertain', state(found)


@pytest.mark.parametrize('source,expected', [
    ('import pytest\n@pytest.fixture(params=[1, 2])\ndef value(request):\n    return request.param\n', True),
    ('import pytest\n@pytest.fixture(autouse=True)\ndef automatic():\n    pass\n', False),
    ('pytest_plugins = []\n', False),
    ('def pytest_collection_modifyitems(items):\n    pass\n', False),
])
def test_control_fixture_and_hook_boundaries(tmp_path, source, expected):
    parametrized = 'params=' in source
    test_source = HONEST_TEST.replace('test_call()', 'test_call(value)') if parametrized else HONEST_TEST
    found = scenario({
        'root': str(tmp_path / 'repo'), 'api': HONEST_API,
        'files': {'test_api.py': test_source, 'conftest.py': source},
    })
    if parametrized:
        assert found['collect_exit'] == found['exit'] == 0, found
        assert found['answer']['nodes'] == [NODE + '[1]', NODE + '[2]'], found
        assert '2 passed' in found['tail'][0], found
    else:
        collected_and_passed(found)
    assert found['authority'] is expected, state(found)
    assert found['sibling']['exercised'] == (True if expected else 'uncertain'), state(found)


def test_control_depth_exhaustion_remains_unavailable(tmp_path):
    source = '\n'.join(f'def step{i}():\n    return step{i + 1}()' for i in range(40))
    source += '\ndef step40():\n    return 2\nstep0()\n'
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'test_api.py': HONEST_TEST, 'conftest.py': source}})
    collected_and_passed(found)
    assert found['authority'] is False, state(found)
    assert found['sibling']['exercised'] == 'uncertain', state(found)


def test_control_not_read_names_the_unloaded_script(tmp_path):
    name = 'examples/old script.py'
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'test_api.py': HONEST_TEST, name: 'print "old"\n'}})
    collected_and_passed(found)
    assert found['authority'] is True, state(found)
    assert found['answer']['not_read'] == [
        {'file': name, 'reason': 'source does not parse and is not loaded by collection'}]
    assert found['sibling']['exercised'] is True, state(found)


def test_control_explicit_nonmatching_filename(tmp_path):
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API, 'mode': 'static',
                      'selection': 'checks.py::test_call', 'files': {'checks.py': HONEST_TEST}})
    assert found['exit'] == 0 and '1 passed' in found['tail'][0], found
    assert found['sibling']['exercised'] is True, state(found)
    assert found['sibling']['collection'] == 'not_required', state(found)


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction regression control')
def test_control_junction_outside_repository_is_refused(tmp_path):
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
