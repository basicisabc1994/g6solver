"""Plant each interpreter import suffix and cache location in actual review copies."""
import importlib.machinery
import importlib.util
import marshal
import struct
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.core import confine
from g6.harness import behaviour_question as bq, behaviour_runner as runner, verification_search as vs
from g6.harness.executable_scenario import Scenario
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, candidate, repo


SUFFIXES = importlib.machinery.all_suffixes()
CACHES = ['timestamp', 'checked_hash', 'unchecked_hash', 'legacy', 'package_legacy', 'src_legacy']


@pytest.mark.parametrize('planted', SUFFIXES + CACHES + ['race', 'removed', 'added'])
def test_every_importable_suffix_cache_and_mutation_is_bound_or_unused(tmp_path, monkeypatch, planted):
    sourceless = planted in ('legacy', '.pyc', 'package_legacy', 'src_legacy')
    root = repo(tmp_path, extra={} if sourceless else {'helper.py': 'VALUE = -1\n'})
    (root / 'calc.py').write_text('from helper import VALUE\ndef add(a,b):\n    return VALUE\n')
    change = rt.change(root)
    before = vs.bind_execution(change, rt.snapshot(root), deadline=vs._clock() + 60)
    question = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    source = runner.CONFIRMED + Scenario.resolve(root, question).source('A')
    real_copy = rt.repository_copy
    @contextmanager
    def copied(*args, **kwargs):
        with real_copy(*args, **kwargs) as copy:
            helper = copy / 'helper.py'
            if planted in CACHES or planted == '.pyc':
                name = str(helper)
                stat = helper.stat() if helper.exists() else None
                if planted in ('checked_hash', 'unchecked_hash'):
                    header = struct.pack('<I', 3 if planted == 'checked_hash' else 1)
                    header += importlib.util.source_hash(helper.read_bytes())
                else:
                    header = struct.pack('<III', 0, int(stat.st_mtime) & 0xffffffff if stat else 0,
                                         stat.st_size if stat else 0)
                cache = Path(importlib.util.cache_from_source(name))
                if planted in ('legacy', '.pyc'):
                    cache = copy / 'helper.pyc'
                elif planted == 'package_legacy':
                    cache = copy / 'helper/__init__.pyc'
                elif planted == 'src_legacy':
                    cache = copy / 'src/helper.pyc'
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(importlib.util.MAGIC_NUMBER + header + marshal.dumps(
                    compile('VALUE = 5\n', name, 'exec')))
            elif planted in SUFFIXES:
                (copy / ('foreign' + planted)).write_bytes(b'planted import bytes')
            yield copy
    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        if planted == 'race':
            (cwd / 'helper.py').write_text('VALUE = 5\n')
        elif planted == 'removed':
            (cwd / 'helper.py').unlink()
        elif planted == 'added':
            (cwd / 'added.py').write_text('VALUE = 5\n')
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=20)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode
    monkeypatch.setattr(rt, 'repository_copy', copied)
    monkeypatch.setattr(rt, '_bounded', execute)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('test-boundary', '', spec))
    got = vs._run_check(change, 'test_example.py', source, 'test_example.py', at_base=False,
                        timeout_s=30, artefact=before)
    assert got['verdict'] != 'passed', (planted, got)
    if planted in ('race', 'removed', 'added'):
        assert got['verdict'] == rt.CHANGED, got


def test_inventory_owned_runner_uses_ordinary_byte_comparator():
    import inspect
    source = inspect.getsource(runner.run)
    assert source.count('rt._copy_snapshot(') >= 2
    assert 'rt._moved(' in source
