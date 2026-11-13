"""Every exclusion from the copy's own listing is importable or refused, never silently trusted."""
import hashlib
import importlib.machinery
import inspect
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.integrity import in_cache
from g6.metaharness.loop import _importable_caches
from g6.workforce import repo_tests as rt


PATTERNS = inspect.getclosurevars(rt._SKIPPED).nonlocals['patterns']
NAMES = tuple(pattern.replace('*', 'planted') for pattern in PATTERNS)
SUFFIXES = importlib.machinery.all_suffixes()
if os.path.normcase('A') == 'a':
    SUFFIXES = list(dict.fromkeys(SUFFIXES + [suffix.upper() for suffix in SUFFIXES]))


@pytest.mark.parametrize('name', NAMES + ('__PYCACHE__',))
def test_each_pruned_name_holds_importable_source_or_refuses(tmp_path, name):
    root = tmp_path / 'copy'
    folder = root / name
    folder.mkdir(parents=True)
    module = folder / 'helper.py'
    module.write_text('VALUE = 7329\n', encoding='utf-8')
    # Each name can hold Python source, even when it cannot itself be named in an import statement.
    code = 'import sys; sys.path.insert(0, sys.argv[1]); import helper; print(helper.VALUE)'
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(folder)],
                          capture_output=True, text=True, timeout=10, check=True)
    assert ran.stdout.strip() == '7329'
    try:
        manifest = rt._copy_snapshot(root)
    except rt.CopyNotRun as exc:
        assert 'G6_E_EXECUTION_ARTIFACT_UNAVAILABLE' in str(exc)
    else:
        assert manifest.get(module.relative_to(root).as_posix()) == hashlib.sha256(module.read_bytes()).hexdigest()


@pytest.mark.parametrize('suffix', SUFFIXES)
@pytest.mark.parametrize('cache', ['__pycache__', '__PYCACHE__'])
def test_shared_cache_inventory_owns_every_importable_suffix(tmp_path, cache, suffix):
    root = tmp_path / 'copy'
    folder = root / cache
    folder.mkdir(parents=True)
    module = folder / ('helper' + suffix)
    module.write_bytes(b'planted executable bytes')
    shared = _importable_caches(root)
    try:
        manifest = rt._copy_snapshot(root)
    except rt.CopyNotRun as exc:
        assert 'G6_E_EXECUTION_ARTIFACT_UNAVAILABLE' in str(exc)
        if in_cache(module.relative_to(root)):
            assert shared[module.relative_to(root).as_posix()] == hashlib.sha256(module.read_bytes()).hexdigest()
        return
    rel = module.relative_to(root).as_posix()
    assert manifest[rel] == hashlib.sha256(module.read_bytes()).hexdigest()
    if in_cache(module.relative_to(root)):
        assert manifest[rel] == shared[rel]


def test_even_harmless_tagged_cache_is_refused_without_a_second_importability_rule(tmp_path):
    root = tmp_path / 'copy'
    cache = root / '__pycache__'
    cache.mkdir(parents=True)
    tagged = cache / f'helper.{sys.implementation.cache_tag}.pyc'
    tagged.write_bytes(b'interpreter-tagged cache')
    with pytest.raises(rt.CopyNotRun, match='G6_E_EXECUTION_ARTIFACT_UNAVAILABLE'):
        rt._copy_snapshot(root)
    assert _importable_caches(root) == {}
    source = inspect.getsource(rt._copy_snapshot)
    assert 'in_cache(' in source


def test_inventory_pruning_is_derived_from_the_actual_listing(tmp_path):
    root = tmp_path / 'copy'
    root.mkdir()
    assert set(NAMES) == rt._copy_ignored(str(root), list(NAMES))
    tree = __import__('ast').parse(inspect.getsource(rt._copy_snapshot))
    # If a new pruning source is added, it must enter this inventory.
    calls = {__import__('ast').unparse(n.func) for n in __import__('ast').walk(tree)
             if isinstance(n, __import__('ast').Call)}
    assert '_copy_ignored' in calls
    assert not any('ignore_patterns' in call for call in calls)
    pruning = [__import__('ast').unparse(n) for n in __import__('ast').walk(tree)
               if isinstance(n, __import__('ast').Assign)
               and any(__import__('ast').unparse(t) == 'dirs[:]' for t in n.targets)]
    assert pruning == ['dirs[:] = [name for name in dirs if not _linked(Path(folder) / name)]']


def test_shared_rules_remain_byte_identical_to_the_base():
    for rel in ('src/g6/integrity/__init__.py', 'src/g6/metaharness/loop.py'):
        root = __import__('g6').__file__
        path = Path(root).parents[2] / rel
        expected = subprocess.check_output(['git', 'show', '84ce46f:' + rel])
        assert path.read_bytes() == expected


@pytest.mark.parametrize('kind', ['is_symlink', 'is_junction'])
def test_dynamic_link_prunes_are_refused_by_the_full_copy_binding(tmp_path, monkeypatch, kind):
    from g6.harness import behaviour_question as bq, behaviour_runner as runner, executable_scenario as es
    from g6.harness import verification_search as vs
    from tests.repair_question2.helpers import TASK, candidate, repo

    root = repo(tmp_path, extra={'imported/helper.py': 'VALUE = 5\n'})
    (root / 'calc.py').write_text('def add(a,b):\n    from imported.helper import VALUE\n    return VALUE\n')
    child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import sys; sys.path.insert(0,sys.argv[1]); from calc import add; print(add(2,3))', str(root)],
        capture_output=True, text=True, check=True, timeout=10)
    assert child.stdout.strip() == '5'
    change = rt.change(root)
    before = vs.bind_execution(change, rt.snapshot(root), deadline=vs._clock() + 60)
    case = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    source = runner.CONFIRMED + es.Scenario.resolve(root, case).source('A')
    real_copy, real_link = rt.repository_copy, getattr(Path, kind, lambda self: False)
    real_readlink = os.readlink
    planted = []

    @contextmanager
    def copied(*args, **kwargs):
        with real_copy(*args, **kwargs) as copy:
            planted.append(copy / 'imported')
            yield copy

    # The filesystem link kind is a fixture; the copy, manifest, comparator and refusal are real.
    monkeypatch.setattr(Path, kind, lambda path: path in planted or real_link(path), raising=False)
    monkeypatch.setattr(os, 'readlink', lambda path: 'other' if Path(path) in planted else real_readlink(path))
    monkeypatch.setattr(rt, 'repository_copy', copied)
    monkeypatch.setattr(rt, '_run', lambda *a, **k: pytest.fail('the pruned executable input reached a child'))
    got = vs._run_check(change, 'test_owned.py', source, 'test_owned.py', at_base=False,
                        timeout_s=30, artefact=before)
    assert got.get(vs.NOT_THE_ARTEFACT_KEY) is True and got['verdict'] == 'not_run', got
