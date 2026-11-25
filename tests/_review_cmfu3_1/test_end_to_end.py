"""Review 1 of claude/change-map-fu-3: one user path end to end, through the shipped collection bootstrap.

The repository's conftest runs repository code the reader does not follow (a subclass's __setattr__
reached from an inherited __init__). The shipped bootstrap collects for real, ``_read_collection``
reads its report, and the answer either admits the collected code's identity (``codes``) or leaves
the node ``unresolved``. No git, network or model call is made.
"""
import json
import os
import subprocess
import sys

import pytest

from g6.workforce import repo_tests

NODE = 'test_api.py::test_call'
PRELUDE = ("import os\n"
           "def install(*args, **kwargs):\n    os.environ['PATH'] = 'local'\n    return 1\n")
SHAPES = {
    # control: the class that defines __init__ also defines __setattr__
    'control': ('class Child:\n    def __init__(self):\n        self.value = 1\n'
                '    def __setattr__(self, name, value):\n        install()\nChild()\n'),
    'subclass-setattr': ('class Base:\n    def __init__(self):\n        self.value = 1\n'
                         'class Child(Base):\n    def __setattr__(self, name, value):\n        install()\nChild()\n'),
    # neighbour: nothing runs; the answer must keep its identity
    'inert': 'class Base:\n    def __init__(self):\n        self.value = 1\nclass Child(Base):\n    pass\nChild()\n',
}


def answer(tmp_path, monkeypatch, files):
    root = tmp_path / 'repo'
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding='utf-8', newline='')
    own = tmp_path / 'reports'
    own.mkdir()
    counted, variables = own / 'counted.json', own / 'env.json'
    variables.write_text('{}', encoding='utf-8')
    args = [NODE, '-q', '-p', 'no:cacheprovider', '--confcutdir', str(root), '--rootdir', str(root),
            '--basetemp', str(own / 'temp'), '--junitxml=' + str(own / 'report.xml'),
            '--log-file=' + str(own / 'pytest.log'), '--collect-only']
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1')
    env.pop('G6_ALLOW_UNCONFINED', None)
    command = [sys.executable, '-I', '-B', '-X', 'utf8', '-c', repo_tests._COLLECTION_BOOT,
               json.dumps([str(root)]), '0', '', '', str(counted), str(variables),
               str(own / 'loaded.json'), str(repo_tests.LOADED_LIMIT), str(root), *args]
    ran = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    monkeypatch.setattr(repo_tests, '_git', lambda *a, **k: '\0'.join(files).encode() + b'\0')
    return repo_tests._read_collection(own / 'counted.json.collection.json', ran.returncode, root)


@pytest.mark.parametrize('shape', list(SHAPES))
def test_collection_answer_admits_identity_only_when_nothing_unfollowed_ran(tmp_path, monkeypatch, shape):
    found = answer(tmp_path, monkeypatch, {
        'conftest.py': PRELUDE + SHAPES[shape],
        'helper.py': 'def neighbour():\n    return 1\n',
        'test_api.py': 'from helper import neighbour\ndef test_call():\n    assert neighbour() == 1\n'})
    assert found.get('status') == 'available' and found.get('nodes') == [NODE], found
    if shape == 'inert':
        assert NODE in found.get('codes', {}) and not found.get('unresolved'), found
    else:
        assert found.get('unresolved') == [NODE] and not found.get('codes'), found
